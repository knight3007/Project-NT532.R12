"""Gói các hàm thị giác thành một đối tượng giữ trạng thái, để orchestrator gọi.

vision = Vision.from_site()           # nạp hiệu chuẩn và kết quả commissioning đã lưu
vision.commission(read_frames(cap, 15))
vision.save()
...
if not vision.blockers("s1"):
    targets = vision.targets(frame, sensor="s1")
"""

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
from .localize import localize as _localize
from .localize import project
from .spot import find_spot as _find_spot
from .tags import median_corners, reprojection_error, solve_camera
from .tags import tag_poses as _tag_poses
from .types import Camera, Detection, Intrinsics, TagPose, Target

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
        self.intrinsics = intrinsics
        self._detector = detector
        self.state: Commissioning | None = None
        self.stale: set[str] = set()
        node_ids = site["tags"]["nodes"]["ids"]
        self._node_of = {tag_id: name for name, tag_id in node_ids.items()}

    @classmethod
    def from_site(cls, site: dict[str, Any] | None = None, detector: Detector | None = None):
        """Nạp intrinsics từ site.yaml và kết quả commissioning nếu đã có file."""
        site = site or load_site()
        vision = cls(site, load_intrinsics(REPO_ROOT / site["camera"]["intrinsics"]), detector)
        path = REPO_ROOT / site["vision"]["commissioning"]
        if path.exists():
            vision.load(path)
        return vision

    # --- trạng thái -------------------------------------------------------------------------

    @property
    def camera(self) -> Camera:
        if self.state is None:
            raise RuntimeError("chưa commissioning: chưa biết pose camera")
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

    def check(self, frames: np.ndarray | list[np.ndarray]) -> Health:
        """So cảnh hiện tại với lúc commissioning; đánh dấu hết hạn phần bị xê dịch.

        Đánh dấu chỉ được xóa khi commissioning lại. Không thấy tag (bị che) thì không kết luận gì.
        """
        corners, _ = median_corners(_frames(frames))
        refs_seen = sum(i in corners for i in self._reference_ids())
        shift = reprojection_error(corners, self.camera, self.site) if refs_seen >= 2 else None
        if shift is not None and shift > self.cfg["camera_shift_px"]:
            self.stale.add("camera")
        moved = {}
        for tag_id, pose in _tag_poses(corners, self.camera, self.site).items():
            name = self._node_of[tag_id]
            if name not in self.nodes:
                continue
            moved[name] = float(np.linalg.norm(pose.position - self.nodes[name].position))
            if moved[name] > self.cfg["node_moved_m"]:
                self.stale.add(name)
        return Health(shift, moved, sorted(self.stale))

    def blockers(self, node: str | None = None) -> list[str]:
        """Lý do chưa được ngắm (rỗng là ngắm được). Truyền `node` để xét cả pose của node đó."""
        if self.state is None:
            return ["chưa commissioning"]
        reasons = []
        if "camera" in self.stale:
            reasons.append("camera bị xê dịch, cần commissioning lại")
        if node is not None:
            if node not in self.nodes:
                reasons.append(f"không có pose của node {node}")
            elif node in self.stale:
                reasons.append(f"node {node} bị dời, cần commissioning lại")
        return reasons

    # --- bốn hàm bàn giao -------------------------------------------------------------------

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Tìm lửa trong ảnh. Khi đã biết pose camera thì chỉ xét vùng bảng bia.

        Cắt vùng bảng trước khi đưa vào YOLO giúp thẻ bia nhỏ chiếm nhiều pixel hơn sau khi
        ảnh bị thu về cỡ `imgsz`. Tọa độ trả về luôn tính trên ảnh gốc.
        """
        if self.state is None:
            return self._detect(frame)
        x0, y0, x1, y1 = self.board_roi(frame.shape)
        found = self._detect(frame[y0:y1, x0:x1])
        return [Detection(d.u + x0, d.v + y0, d.w, d.h, d.conf) for d in found]

    def localize(self, pixel: tuple[float, float]) -> np.ndarray | None:
        """Tọa độ sa bàn của pixel trên bảng bia, hoặc None nếu rơi ra ngoài bảng."""
        point = _localize(pixel, self.camera, self.site["board"]["plane_y"])
        if point is None or not self.on_board(point):
            return None
        return point

    def tag_poses(self, frame: np.ndarray | list[np.ndarray]) -> dict[str, TagPose]:
        """Pose hiện tại của các node nhìn thấy trong ảnh, theo tên node (s1, s2)."""
        corners, _ = median_corners(_frames(frame))
        poses = _tag_poses(corners, self.camera, self.site)
        return {self._node_of[i]: p for i, p in poses.items()}

    def find_spot(self, on: np.ndarray, off: np.ndarray) -> np.ndarray | None:
        """Tọa độ sa bàn của vết laser trên bảng, từ một ảnh bật và một ảnh tắt laser."""
        mask = np.zeros(on.shape[:2], np.uint8)
        cv2.fillPoly(mask, [self.board_polygon().astype(np.int32)], 255)
        pixel = _find_spot(on, off, min_rise=self.cfg["laser_min_rise"], mask=mask)
        return None if pixel is None else self.localize(pixel)

    # --- ghép lại ---------------------------------------------------------------------------

    def targets(self, frame: np.ndarray, sensor: str | None = None) -> list[Target]:
        """Bia trên bảng, sắp theo độ tin cậy giảm dần.

        Có `sensor` thì chỉ giữ bia cách sensor đó không quá `sensor_match_radius_x` theo X.
        """
        radius = self.site["targeting"]["sensor_match_radius_x"]
        if sensor is not None and sensor not in self.nodes:
            raise ValueError(f"không có pose của sensor {sensor}")
        out = []
        for d in self.detect(frame):
            point = self.localize((d.u, d.v))
            if point is None:
                continue
            if sensor is not None and abs(point[0] - self.nodes[sensor].position[0]) > radius:
                continue
            out.append(Target(d, point))
        return out

    # --- hình học bảng ----------------------------------------------------------------------

    def on_board(self, point: np.ndarray) -> bool:
        board, m = self.site["board"], self.cfg["board_margin_m"]
        return bool(-m <= point[0] <= board["width"] + m and -m <= point[2] <= board["height"] + m)

    def board_polygon(self, per_edge: int = 12) -> np.ndarray:
        """Viền bảng bia chiếu lên ảnh, (N, 2) pixel. Lấy nhiều điểm mỗi cạnh vì méo ống kính."""
        board = self.site["board"]
        w, h, y = board["width"], board["height"], board["plane_y"]
        corners = [(0, 0), (w, 0), (w, h), (0, h), (0, 0)]
        pts = []
        for (xa, za), (xb, zb) in pairwise(corners):
            for s in np.linspace(0, 1, per_edge, endpoint=False):
                pts.append(project([xa + s * (xb - xa), y, za + s * (zb - za)], self.camera))
        return np.array(pts)

    def board_roi(self, shape: tuple[int, ...], pad: float = 0.05) -> tuple[int, int, int, int]:
        """Hình chữ nhật bao vùng bảng trên ảnh, nới thêm `pad` mỗi phía: (x0, y0, x1, y1)."""
        poly = self.board_polygon()
        (x0, y0), (x1, y1) = poly.min(axis=0), poly.max(axis=0)
        dx, dy = (x1 - x0) * pad, (y1 - y0) * pad
        height, width = shape[:2]
        x0, x1 = int(max(x0 - dx, 0)), int(min(x1 + dx, width))
        y0, y1 = int(max(y0 - dy, 0)), int(min(y1 + dy, height))
        if x1 - x0 < 32 or y1 - y0 < 32:
            return 0, 0, width, height  # bảng không nằm trong khung: xét cả ảnh
        return x0, y0, x1, y1

    # --- lưu và nạp -------------------------------------------------------------------------

    def save(self, path: str | Path | None = None) -> Path:
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
