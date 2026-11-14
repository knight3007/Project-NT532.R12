"""Gói các hàm thị giác thành một đối tượng giữ trạng thái, để orchestrator gọi.

vision = Vision.from_site()           # nạp hiệu chuẩn và kết quả commissioning đã lưu
vision.commission(read_frames(cap, 15))
vision.save()
...
if not vision.blockers("s1"):
    targets = vision.targets(frame, sensor="s1")
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from itertools import pairwise
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from ..config import REPO_ROOT, load_site
from .calibration import load_intrinsics
from .dynamic_mapping import DynamicMap, estimate_reference_maps
from .localize import localize_surfaces, project
from .spot import find_spot as _find_spot
from .tags import detect_tags, median_corners, reprojection_error, solve_camera
from .tags import tag_poses as _tag_poses
from .types import Camera, Detection, Intrinsics, TagPose, Target

log = logging.getLogger(__name__)

Detector = Callable[[np.ndarray], list[Detection]]


@dataclass
class Commissioning:
    """Kết quả một lần đo pose camera và pose các node."""

    camera: Camera
    nodes: dict[str, TagPose]
    reprojection_px: float
    tag_rate: dict[int, float]
    missing: list[str] = field(default_factory=list)  # node không thấy đủ khung
    time: float = field(default_factory=time.time)


@dataclass
class Health:
    """Kết quả một lần kiểm tra camera và node có bị xê dịch so với lúc commissioning không."""

    camera_shift_px: float | None  # None nếu không thấy đủ 2 tag tham chiếu
    node_moved_m: dict[str, float]  # chỉ gồm node nhìn thấy trong lần kiểm tra
    stale: list[str]  # "camera" và tên các node đang bị đánh dấu hết hạn
    # Có khi camera xê dịch mà còn thấy đủ tag tham chiếu và đã tự tính lại pose:
    # {"shift_px", "moved_m", "rotated_deg", "nodes"} (nodes: tên node đã đo lại pose).
    repose: dict[str, Any] | None = None


def _frames(frames: np.ndarray | list[np.ndarray]) -> list[np.ndarray]:
    return [frames] if isinstance(frames, np.ndarray) else list(frames)


class Vision:
    def __init__(
        self,
        site: dict[str, Any],
        intrinsics: Intrinsics,
        detector: Detector | None = None,
    ) -> None:
        self.site = site
        self.cfg = site["vision"]
        self.reference_mode = self.cfg.get("reference_mode", "fixed")
        if self.reference_mode not in ("fixed", "auto"):
            raise ValueError("vision.reference_mode must be fixed or auto")
        self.intrinsics = intrinsics
        self._detector = detector
        self.state: Commissioning | None = None
        self.reference_map: DynamicMap | None = None
        self.stale: set[str] = set()
        node_ids = site["tags"]["nodes"]["ids"]
        self._node_of = {tag_id: name for name, tag_id in node_ids.items()}

    @classmethod
    def from_site(cls, site: dict[str, Any] | None = None, detector: Detector | None = None):
        """Nạp intrinsics từ site.yaml và kết quả commissioning nếu đã có file."""
        site = site or load_site()
        vision = cls(site, load_intrinsics(REPO_ROOT / site["camera"]["intrinsics"]), detector)
        path = REPO_ROOT / site["vision"]["commissioning"]
        if vision.reference_mode == "fixed" and path.exists():
            vision.load(path)
        return vision

    # --- trạng thái -------------------------------------------------------------------------

    @property
    def camera(self) -> Camera:
        if self.state is None:
            raise RuntimeError("chưa commissioning: chưa biết pose camera")
        if self.reference_mode == "auto" and "camera" in self.stale:
            raise RuntimeError("auto reference map không tin cậy")
        return self.state.camera

    @property
    def nodes(self) -> dict[str, TagPose]:
        return {} if self.state is None else self.state.nodes

    def commission(self, frames: list[np.ndarray]) -> Commissioning:
        """Đo pose camera từ tag tham chiếu và pose đế từng node, lấy trung vị trên nhiều khung.

        Xóa mọi đánh dấu hết hạn. Node nào hiện trong ít hơn `min_tag_rate` số khung thì bỏ,
        ghi vào `missing`. Báo lỗi nếu không thấy đủ 2 tag tham chiếu.
        """
        frames = _frames(frames)
        if self.reference_mode == "auto":
            if any(frame.shape[1::-1] != self.intrinsics.size for frame in frames):
                self.reference_map = None
                self.stale.add("camera")
                raise ValueError("image size differs from camera intrinsics calibration")
            return self.commission_corners([detect_tags(frame) for frame in frames])
        corners, rate = median_corners(frames)
        refs = {i: c for i, c in corners.items() if rate[i] >= self.cfg["min_tag_rate"]}
        camera = solve_camera(refs, self.intrinsics, self.site)
        poses = _tag_poses(refs, camera, self.site)
        nodes = {self._node_of[i]: p for i, p in poses.items()}
        self.state = Commissioning(
            camera=camera,
            nodes=nodes,
            reprojection_px=reprojection_error(refs, camera, self.site),
            tag_rate=rate,
            missing=sorted(set(self._node_of.values()) - set(nodes)),
        )
        self.stale.clear()
        return self.state

    def commission_corners(self, observations: list[dict[int, np.ndarray]]) -> Commissioning:
        """Build an auto map from independent frame observations; never persist it."""
        if self.reference_mode != "auto":
            raise RuntimeError("commission_corners requires auto reference mode")
        try:
            mapping = estimate_reference_maps(
                observations, self.intrinsics, self.site["tags"]["reference"]["size"]
            )
        except ValueError:
            self.reference_map = None
            self.stale.add("camera")
            raise
        rate = {i: sum(i in frame for frame in observations) / len(observations)
                for i in set().union(*(frame.keys() for frame in observations))}
        latest = observations[-1]
        eligible = {i: c for i, c in latest.items()
                    if rate[i] >= self.cfg["min_tag_rate"]}
        poses = _tag_poses(eligible, mapping.camera, self.site)
        nodes = {self._node_of[i]: p for i, p in poses.items()}
        state = Commissioning(
            camera=mapping.camera, nodes=nodes, reprojection_px=mapping.reprojection_px,
            tag_rate=rate, missing=sorted(set(self._node_of.values()) - set(nodes)),
        )
        self.reference_map = mapping
        self.state = state
        self.stale.clear()
        return state

    def check(self, frames: np.ndarray | list[np.ndarray]) -> Health:
        """So cảnh hiện tại với lúc commissioning; đánh dấu hết hạn phần bị xê dịch.

        Đánh dấu chỉ được xóa khi commissioning lại. Không thấy tag (bị che) thì không kết luận gì.
        Camera lệch quá ngưỡng mà còn thấy đủ tag tham chiếu (đủ `min_tag_rate`) thì, nếu bật
        `vision.auto_repose_camera`, tính lại pose camera ngay từ các khung này thay vì đánh dấu
        hết hạn; `Health.repose` ghi lại thay đổi. Chưa lưu file: gọi `save()` nếu muốn giữ.
        """
        if self.reference_mode == "auto":
            old_nodes = dict(self.nodes)
            try:
                self.commission(_frames(frames))
            except ValueError:
                self.reference_map = None
                self.stale.add("camera")
                return Health(None, {}, sorted(self.stale))
            moved = {name: float(np.linalg.norm(pose.position - old_nodes[name].position))
                     for name, pose in self.nodes.items() if name in old_nodes}
            return Health(None, moved, [], None)
        corners, rate = median_corners(_frames(frames))
        refs_seen = sum(i in corners for i in self._reference_ids())
        shift = reprojection_error(corners, self.camera, self.site) if refs_seen >= 2 else None
        repose = None
        if shift is not None and shift > self.cfg["camera_shift_px"]:
            if self.cfg.get("auto_repose_camera", False):
                repose = self._repose_camera(corners, rate)
            if repose is None:
                self.stale.add("camera")
        moved = {}
        for tag_id, pose in _tag_poses(corners, self.camera, self.site).items():
            name = self._node_of[tag_id]
            if name not in self.nodes:
                continue
            moved[name] = float(np.linalg.norm(pose.position - self.nodes[name].position))
            if moved[name] > self.cfg["node_moved_m"]:
                self.stale.add(name)
            elif repose is not None:
                self.state.nodes[name] = pose  # đo lại bằng pose camera mới
                repose["nodes"].append(name)
        return Health(shift, moved, sorted(self.stale), repose)

    def blockers(self, node: str | None = None) -> list[str]:
        """Lý do chưa được ngắm (rỗng là ngắm được). Truyền `node` để xét cả pose của node đó."""
        if self.state is None:
            return ["chưa commissioning"]
        reasons = []
        if "camera" in self.stale:
            reasons.append("auto reference map không tin cậy" if self.reference_mode == "auto"
                           else "camera bị xê dịch, cần commissioning lại")
        if node is not None:
            if node not in self.nodes:
                reasons.append(f"không có pose của node {node}")
            elif node in self.stale:
                reasons.append(f"node {node} bị dời, cần commissioning lại")
        return reasons

    # --- bốn hàm bàn giao -------------------------------------------------------------------

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Tìm lửa trong ảnh. Khi đã biết pose camera thì chỉ xét vùng bia (bảng đứng và nền).

        Cắt vùng bảng trước khi đưa vào YOLO giúp thẻ bia nhỏ chiếm nhiều pixel hơn sau khi
        ảnh bị thu về cỡ `imgsz`. Tọa độ trả về luôn tính trên ảnh gốc.
        """
        if self.state is None or self.reference_mode == "auto":
            return self._detect(frame)
        x0, y0, x1, y1 = self.target_roi(frame.shape)
        found = self._detect(frame[y0:y1, x0:x1])
        return [Detection(d.u + x0, d.v + y0, d.w, d.h, d.conf) for d in found]

    def localize(self, pixel: tuple[float, float]) -> np.ndarray | None:
        """Tọa độ sa bàn của pixel trên vùng bia (bảng đứng hoặc nền), None nếu rơi ra ngoài.

        Lấy giao điểm đầu tiên của tia nhìn với các mặt, nên pixel thuộc bảng ở phía sau thì ra
        điểm trên bảng, pixel thuộc nền phía trước bảng thì ra điểm trên nền.
        """
        hit = self.localize_surface(pixel)
        return None if hit is None else hit[0]

    def localize_surface(self, pixel: tuple[float, float]) -> tuple[np.ndarray, str] | None:
        """Như `localize` nhưng kèm tên mặt: (điểm, "wall" | "floor"), hoặc None."""
        if self.reference_mode == "auto":
            point = self.localize_table(pixel)
            return None if point is None else (point, "floor")
        return localize_surfaces(
            pixel, self.camera, self.site["board"], self._floor(), self.cfg["board_margin_m"]
        )

    def localize_table(self, pixel: tuple[float, float]) -> np.ndarray | None:
        """Independent of YOLO: intersect one pixel with the current ID 2 table plane."""
        if self.reference_mode != "auto" or self.reference_map is None or self.blockers():
            return None
        point = self.reference_map.table_point(pixel)
        return point if point is not None and self._in_auto_area(point) else None

    def _in_auto_area(self, point: np.ndarray) -> bool:
        refs = self.reference_map.references
        hull = cv2.convexHull(np.array([p[:2] for p in refs.values()], np.float32))
        margin = self.cfg.get("auto_extrapolation_m", 0.20)
        return cv2.pointPolygonTest(hull, tuple(map(float, point[:2])), True) >= -margin

    def localize_detection(self, d: Detection) -> tuple[np.ndarray, str] | None:
        """Vị trí sa bàn của một hộp lửa, kèm mặt.

        Quy tắc điểm neo: ngọn lửa trên nền chạm mặt nền ở ĐÁY hộp, còn thẻ bia dán trên bảng
        thì lấy tâm hộp. Nên tính giao điểm đầu tiên của đáy-giữa hộp; rơi vào nền thì dùng điểm
        đó, ngược lại dùng giao điểm đầu tiên của tâm hộp (thường là bảng).
        """
        bottom = self.localize_surface((d.u, d.v + d.h / 2))
        if bottom is not None and bottom[1] == "floor":
            return bottom
        return self.localize_surface((d.u, d.v))

    def tag_poses(self, frame: np.ndarray | list[np.ndarray]) -> dict[str, TagPose]:
        """Pose hiện tại của các node nhìn thấy trong ảnh, theo tên node (s1, s2)."""
        corners, _ = median_corners(_frames(frame))
        poses = _tag_poses(corners, self.camera, self.site)
        return {self._node_of[i]: p for i, p in poses.items()}

    def find_spot(self, on: np.ndarray, off: np.ndarray, exclude: list | None = None,
                  exclude_px: int = 45) -> np.ndarray | None:
        """Tọa độ sa bàn của vết laser trên bảng, từ một ảnh bật và một ảnh tắt laser.

        `exclude`: điểm 3D (hệ sa bàn) cần bỏ qua, ví dụ đầu phát laser của vòi: camera nhìn thẳng vào đầu
        phát thấy nó sáng đỏ khi bật, mạnh hơn cả vết trên nền, và chiếu xuống nền ra một tọa độ giả cố định.
        Mỗi điểm che một vòng tròn bán kính `exclude_px` pixel quanh hình chiếu của nó."""
        mask = np.zeros(on.shape[:2], np.uint8)
        for poly in (self.board_polygon(), self.floor_polygon()):
            if len(poly):
                cv2.fillPoly(mask, [poly.astype(np.int32)], 255)
        for p in exclude or ():
            try:
                u, v = project(np.asarray(p, float), self.camera)
            except (cv2.error, ValueError, TypeError):
                continue
            if np.isfinite(u) and np.isfinite(v):
                cv2.circle(mask, (round(u), round(v)), int(exclude_px), 0, -1)
        pixel = _find_spot(on, off, min_rise=self.cfg["laser_min_rise"], mask=mask)
        return None if pixel is None else self.localize(pixel)

    # --- ghép lại ---------------------------------------------------------------------------

    def targets(self, frame: np.ndarray, sensor: str | None = None) -> list[Target]:
        """Bia trên bảng đứng và trên nền, sắp theo độ tin cậy giảm dần.

        Có `sensor` thì chỉ giữ bia cách sensor đó không quá `sensor_match_radius_x` theo X.
        """
        if self.reference_mode == "auto" and self.blockers():
            return []
        radius = self.site["targeting"]["sensor_match_radius_x"]
        if sensor is not None and sensor not in self.nodes:
            raise ValueError(f"không có pose của sensor {sensor}")
        out = []
        for d in self.detect(frame):
            hit = self.localize_detection(d)
            if hit is None:
                continue
            point, surface = hit
            # Ghép theo X: bia trên nền cũng chỉ được khớp theo X như bia trên bảng (vùng trái/phải
            # của sensor). Pha 4 sẽ xét lại cho nền (khoảng cách thật tới sensor, không chỉ X).
            if sensor is not None and abs(point[0] - self.nodes[sensor].position[0]) > radius:
                continue
            out.append(Target(d, point, surface))
        return out

    # --- hình học bảng ----------------------------------------------------------------------

    def _floor(self) -> dict[str, float] | None:
        """Nền nằm ngang làm vùng bia: {width, depth}; None nếu site không có hoặc tắt (chỉ bảng)."""
        floor = self.site.get("floor")
        if not floor or not floor.get("enabled", True):
            return None
        board = self.site["board"]
        return {
            "width": floor.get("width", board["width"]),
            "depth": floor.get("depth", board["plane_y"]),
        }

    def in_target_area(self, point: np.ndarray) -> bool:
        """Điểm nằm trên bảng đứng hoặc trên nền (trong lề `board_margin_m`)."""
        if self.reference_mode == "auto":
            return (self.reference_map is not None and not self.blockers()
                    and abs(float(point[2])) <= self.cfg["board_margin_m"]
                    and self._in_auto_area(point))
        m = self.cfg["board_margin_m"]
        x, y, z = (float(c) for c in point)
        board = self.site["board"]
        if (
            abs(y - board["plane_y"]) <= m
            and -m <= x <= board["width"] + m
            and -m <= z <= board["height"] + m
        ):
            return True
        floor = self._floor()
        return floor is not None and (
            abs(z) <= m and -m <= x <= floor["width"] + m and -m <= y <= floor["depth"] + m
        )

    def on_board(self, point: np.ndarray) -> bool:
        """Tên cũ của `in_target_area` (nay gồm cả nền); orchestrator dùng để chặn bia ngoài vùng."""
        return self.in_target_area(point)

    def _outline(self, corners: list[list[float]], per_edge: int) -> np.ndarray:
        pts = []
        for a, b in pairwise([*corners, corners[0]]):
            for s in np.linspace(0, 1, per_edge, endpoint=False):
                pts.append(project(np.array(a) + s * (np.array(b) - np.array(a)), self.camera))
        return np.array(pts)

    def board_polygon(self, per_edge: int = 12) -> np.ndarray:
        """Viền bảng bia đứng chiếu lên ảnh, (N, 2) pixel. Lấy nhiều điểm mỗi cạnh vì méo ống kính."""
        if self.reference_mode == "auto":
            return np.empty((0, 2))  # board pose is unknown in the ID 2 frame
        board = self.site["board"]
        w, h, y = board["width"], board["height"], board["plane_y"]
        return self._outline([[0, y, 0], [w, y, 0], [w, y, h], [0, y, h]], per_edge)

    def floor_polygon(self, per_edge: int = 12) -> np.ndarray:
        """Viền vùng nền chiếu lên ảnh, (N, 2) pixel; mảng rỗng (0, 2) nếu không có nền."""
        if self.reference_mode == "auto":
            if self.reference_map is None or self.blockers():
                return np.empty((0, 2))
            refs = np.array([p[:2] for p in self.reference_map.references.values()], np.float32)
            hull = cv2.convexHull(refs).reshape(-1, 2)
            return np.array([project((x, y, 0), self.camera) for x, y in hull])
        floor = self._floor()
        if floor is None:
            return np.empty((0, 2))
        w, d = floor["width"], floor["depth"]
        return self._outline([[0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0]], per_edge)

    def target_roi(self, shape: tuple[int, ...], pad: float = 0.05) -> tuple[int, int, int, int]:
        """Hình chữ nhật bao cả bảng đứng lẫn nền trên ảnh, nới `pad` mỗi phía: (x0, y0, x1, y1)."""
        if self.reference_mode == "auto":
            return 0, 0, shape[1], shape[0]
        poly = np.concatenate([self.board_polygon(), self.floor_polygon()])
        (x0, y0), (x1, y1) = poly.min(axis=0), poly.max(axis=0)
        dx, dy = (x1 - x0) * pad, (y1 - y0) * pad
        height, width = shape[:2]
        x0, x1 = int(max(x0 - dx, 0)), int(min(x1 + dx, width))
        y0, y1 = int(max(y0 - dy, 0)), int(min(y1 + dy, height))
        if x1 - x0 < 32 or y1 - y0 < 32:
            return 0, 0, width, height  # vùng bia không nằm trong khung: xét cả ảnh
        return x0, y0, x1, y1

    def board_roi(self, shape: tuple[int, ...], pad: float = 0.05) -> tuple[int, int, int, int]:
        """Tên cũ của `target_roi` (nay phủ cả nền); trùng đúng vùng cắt của `detect`."""
        return self.target_roi(shape, pad)

    # --- lưu và nạp -------------------------------------------------------------------------

    def save(self, path: str | Path | None = None) -> Path:
        if self.reference_mode == "auto":
            raise RuntimeError("auto reference map is transient; do not save commissioning")
        if self.state is None:
            raise RuntimeError("chưa commissioning, không có gì để lưu")
        state = self.state
        path = Path(path or REPO_ROOT / self.cfg["commissioning"])
        data = {
            "date": datetime.fromtimestamp(state.time).astimezone().isoformat(timespec="seconds"),
            "reprojection_px": round(state.reprojection_px, 3),
            "camera_position": np.round(state.camera.position, 4).tolist(),
            "camera": {"R": state.camera.R.tolist(), "t": state.camera.t.tolist()},
            "nodes": {
                name: {
                    "tag_id": p.tag_id,
                    "position": p.position.tolist(),
                    "rotation": p.rotation.tolist(),
                }
                for name, p in state.nodes.items()
            },
            "missing": state.missing,
            "tag_rate": {int(i): round(r, 3) for i, r in sorted(state.tag_rate.items())},
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def load(self, path: str | Path) -> None:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        camera = Camera(
            self.intrinsics,
            np.array(data["camera"]["R"], dtype=np.float64),
            np.array(data["camera"]["t"], dtype=np.float64),
        )
        nodes = {
            name: TagPose(n["tag_id"], np.array(n["position"]), np.array(n["rotation"]))
            for name, n in data["nodes"].items()
        }
        when = datetime.fromisoformat(data["date"]).timestamp()
        self.state = Commissioning(
            camera, nodes, data["reprojection_px"], data["tag_rate"], data["missing"], when
        )
        self.stale.clear()

    # --- nội bộ -----------------------------------------------------------------------------

    def _repose_camera(self, corners: dict, rate: dict) -> dict[str, Any] | None:
        """Tính lại pose camera từ tag tham chiếu như `commission`. None nếu không đủ tag hoặc
        pose mới vẫn lệch quá ngưỡng (khi đó phải commissioning lại)."""
        refs = {i: c for i, c in corners.items() if rate[i] >= self.cfg["min_tag_rate"]}
        try:
            camera = solve_camera(refs, self.intrinsics, self.site)
        except ValueError:
            return None
        error = reprojection_error(refs, camera, self.site)
        if error > self.cfg["camera_shift_px"]:
            return None
        old = self.camera
        cos = (np.trace(old.R.T @ camera.R) - 1) / 2
        info = {
            "shift_px": round(reprojection_error(refs, old, self.site), 2),
            "moved_m": round(float(np.linalg.norm(camera.position - old.position)), 4),
            "rotated_deg": round(float(np.degrees(np.arccos(np.clip(cos, -1, 1)))), 2),
            "nodes": [],
        }
        self.state = Commissioning(
            camera=camera,
            nodes=dict(self.state.nodes),
            reprojection_px=error,
            tag_rate=self.state.tag_rate,
            missing=self.state.missing,
        )
        log.warning(
            "camera xê dịch %.2f px, đã tính lại pose: dời %.1f cm, xoay %.2f độ",
            info["shift_px"], info["moved_m"] * 100, info["rotated_deg"],
        )
        return info

    def _reference_ids(self) -> list[int]:
        positions = self.site["tags"]["reference"]["positions"]
        return [int(i) for i, c in positions.items() if c is not None]

    def _detect(self, frame: np.ndarray) -> list[Detection]:
        if self._detector is None:
            from .detect import detect  # kéo theo ultralytics, chỉ nạp khi thật sự cần

            self._detector = partial(
                detect,
                weights=REPO_ROOT / self.cfg["weights"],
                conf=self.cfg["detect_conf"],
                imgsz=self.cfg["imgsz"],
            )
        return self._detector(frame)
