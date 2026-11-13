"""Sa bàn ảo: dựng ảnh như webcam thật nhìn sa bàn chữ L, kèm ground truth.

    scene = Scene()                       # camera, bảng, 4 tag tham chiếu, hai node s1 và s2
    scene.add_card(0.40, 0.25)            # thẻ bia 7 cm tại (x, z) trên bảng
    scene.add_card_3d((0.50, 0.30, 0.0), "floor")   # thẻ đứng trên nền (mặt bàn) tại (x, y)
    scene.aim("s1", pan, tilt); scene.set_laser("s1", True)
    frame = scene.render()                # BGR uint8, mỗi lần gọi nhiễu khác nhau

Hệ tọa độ theo kế hoạch: gốc ở góc trước bên trái mặt bàn, X sang phải, Y về phía bảng bia, Z lên
trên, đơn vị mét. Bảng bia là mặt Y = board.plane_y, X từ 0 tới width, Z từ 0 tới height; nền là
mặt bàn Z = 0, X từ 0 tới table.width, Y từ 0 tới table.depth. Thẻ trên bảng dán phẳng; thẻ trên nền
dựng ĐỨNG (như ngọn lửa mọc lên) và quay mặt về camera, đáy thẻ chạm nền tại điểm tiếp xúc.
Camera đặt cao, nhìn chếch xuống như camera phòng (xem `CAMERA_PRESETS`), có thể xê dịch giữa các lần
thử (`jitter_camera`).

Cái khác với thật (đọc trước khi tin con số): ánh sáng phẳng, không bóng, không phản xạ, không
nước; vết laser là một đốm gauss đặt đúng nơi tia cắt bảng; servo chỉ có sai lệch hệ thống, không
có rơ hay rung; mặt bàn và bảng là ảnh sinh ra chứ không phải vật liệu thật.
"""

import copy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import REPO_ROOT, read_site
from ..vision.calibration import load_intrinsics, save_intrinsics
from ..vision.localize import project
from ..vision.types import Camera, Intrinsics, TagPose
from .render import camera_rays, in_frame, render_quad, tag_quad, yaw_matrix
from .render import look_at as make_camera

CARDS_DIR = REPO_ROOT / "data" / "sim" / "cards"
SIM_DIR = REPO_ROOT / "data" / "sim"
CARD_SIZE = 0.07

# 4 tag tham chiếu trên mặt bàn, tâm (x, y). Rộng hơn "giữa bàn" một chút cho pose camera chính
# xác hơn. Với bố cục corners (node ở hai góc trước bàn) tag nằm trong bàn, cách hai trụ góc, và cả 3 preset
# front/left/right đều thấy đủ 4 tag tham chiếu lẫn 2 tag node; với bố cục posts (hai trụ sát bảng) thì
# preset left/right dò thiếu một tag tham chiếu (tag 3 / tag 2; chưa tìm nguyên nhân).
DEFAULT_REFERENCE = {0: (0.30, 0.28), 1: (0.90, 0.28), 2: (0.15, 0.62), 3: (1.05, 0.62)}
# Tâm tag trên đế (x, y, z) và góc xoay (độ) của bố cục "posts": cách bảng 0,25 m, lệch trái và phải.
DEFAULT_NODES = {"s1": ((0.30, 0.55, 0.25), 10.0), "s2": ((0.90, 0.55, 0.25), -10.0)}
# Bố cục node: tên -> (tâm tag, góc xoay tag, góc hướng pan = 0), độ, xoay ngược chiều kim đồng hồ
# nhìn từ trên xuống so với +Y. Tag dán cứng trên node nên hướng pan = 0 xoay cùng tag (đúng quy ước
# yaw của orchestrator). "corners" (mặc định): hai góc trước bàn, pan = 0 hướng về giữa bảng (tag và
# hướng pan xoay cùng node). "posts": bố cục cũ, hai trụ sát bảng, tag xoay 10 và -10 độ (đủ nhỏ để pan
# còn với tới mép bảng); giữ để so sánh.
_CORNER_YAW = float(np.degrees(np.arctan2(0.6, 0.8)))  # 36,87 độ
NODE_LAYOUTS = {
    "posts": {n: (pos, yaw, yaw) for n, (pos, yaw) in DEFAULT_NODES.items()},
    "corners": {
        "s1": ((0.0, 0.0, 0.25), -_CORNER_YAW, -_CORNER_YAW),
        "s2": ((1.2, 0.0, 0.25), _CORNER_YAW, _CORNER_YAW),
    },
}
DEFAULT_LAYOUT = "corners"
# Tâm quay pan–tilt trong hệ tag (đo từ tâm tag). Hướng 0 của pan–tilt được giả định song song
# trục Y sa bàn, không theo yaw của tag.
DEFAULT_PIVOT_OFFSET = (0.0, 0.0, 0.08)
# Camera đặt cao ở phía trước bàn, nhìn chếch xuống: (vị trí, điểm nhìn). Với f ~ 900 px (khung
# dọc ~ 44 độ) phải lùi ra cỡ 0,8 m trước mép bàn và cao ~0,9 m mới thấy hết nền, bảng, hai node
# và 4 tag tham chiếu, mà tag node vẫn đủ lớn để đo vị trí ~1 cm (cao hơn thì đo tag kém đi).
# "legacy" là camera thấp cũ (chỉ thấy tag và bảng, không thấy hết nền).
CAMERA_PRESETS = {
    "front": ((0.60, -0.80, 0.90), (0.60, 0.40, 0.20)),
    "left": ((0.30, -0.85, 0.95), (0.60, 0.40, 0.20)),
    "right": ((0.95, -0.85, 0.95), (0.60, 0.40, 0.20)),
    "legacy": ((0.60, -0.50, 0.70), (0.60, 0.80, 0.10)),
}
DEFAULT_PRESET = "front"
FRAME_MARGIN_PX = 30
SUPERSAMPLE = 4  # tag dựng mịn hơn để góc tag không lệch theo răng cưa
_RAY_CACHE: dict = {}  # tia nhìn của các camera đã dùng, tính một lần cho mỗi pose
POST_HALF = 0.05  # nửa cạnh trụ hộp dưới tag node


def default_intrinsics(size: tuple[int, int] = (1280, 720), focal: float = 900.0) -> Intrinsics:
    """Webcam ảo 1280x720, f ~ 900 px (HFOV ~ 70 độ), méo nhẹ."""
    K = np.array([[focal, 0, size[0] / 2], [0, focal, size[1] / 2], [0, 0, 1]], dtype=np.float64)
    return Intrinsics(K, np.array([-0.05, 0.01, 0.0, 0.0, 0.0]), size)


def fill_reference(site: dict[str, Any]) -> dict[str, Any]:
    """Bản sao của `site` đã điền vị trí 4 tag tham chiếu còn null bằng bộ mặc định của sim."""
    site = copy.deepcopy(site)
    positions = site["tags"]["reference"]["positions"]
    for tag_id, center in DEFAULT_REFERENCE.items():
        if positions.get(tag_id) is None:
            positions[tag_id] = list(center)
    return site


def sim_site(site: dict[str, Any] | None = None, write: bool = True) -> dict[str, Any]:
    """Site dùng cho sa bàn ảo: vị trí tag tham chiếu đã điền, file hiệu chuẩn và commissioning
    trỏ vào data/sim/ để không đụng calibration/ thật.

    Có calibration/camera.yaml thì dùng nó làm intrinsics của camera ảo; không thì dùng bộ mặc định
    (ghi ra data/sim/camera.yaml nếu `write`, để `Vision.from_site` nạp được).
    """
    site = fill_reference(site if site is not None else read_site())
    if not (REPO_ROOT / site["camera"]["intrinsics"]).exists():
        if write:
            SIM_DIR.mkdir(parents=True, exist_ok=True)
            save_intrinsics(SIM_DIR / "camera.yaml", default_intrinsics(), 0.0, 0)
        site["camera"]["intrinsics"] = "data/sim/camera.yaml"
    site["vision"]["commissioning"] = "data/sim/commissioning.yaml"
    # tự hiệu chuẩn: file riêng cho sim, và không tự quét lúc khởi động (bật lại bằng site["selfcal"]["startup_sweep"])
    site["selfcal"] = {**site.get("selfcal", {}), "file": "data/sim/nodes.yaml", "startup_sweep": False}
    return site


def aim_angles(pivot, target, facing_deg: float = 0.0) -> tuple[float, float]:
    """Góc (pan, tilt), độ, để tia từ `pivot` đi qua `target`. Hai điểm trong hệ sa bàn.

    CHỈ LÀ BẢN THAM CHIẾU CHO MÔ PHỎNG, khớp quy ước của `Node.direction`: pan = 0 và tilt = 0 là
    hướng +Y; pan dương quay về phía -X; tilt dương ngẩng lên +Z. Công thức chính thức (có bảng
    hiệu chỉnh servo) do orchestrator viết (Hậu). `facing_deg`: hướng pan = 0 của node so với +Y.
    """
    v = np.asarray(target, float) - np.asarray(pivot, float)
    v = yaw_matrix(-facing_deg) @ v
    pan = np.degrees(np.arctan2(-v[0], v[1]))
    tilt = np.degrees(np.arctan2(v[2], np.hypot(v[0], v[1])))
    return float(pan), float(tilt)


@dataclass
class Node:
    """Một node: tag trên đế cố định và cụm pan–tilt có laser."""

    name: str
    tag_id: int
    position: np.ndarray  # tâm tag (x, y, z)
    yaw_deg: float
    pivot_offset: np.ndarray = field(default_factory=lambda: np.array(DEFAULT_PIVOT_OFFSET))
    pan: float = 0.0  # góc đã ra lệnh, độ
    tilt: float = 0.0
    bias: tuple[float, float] = (0.0, 0.0)  # sai lệch hệ thống của servo (pan, tilt), độ
    laser: bool = False
    facing_deg: float = 0.0  # hướng pan = 0, độ so với +Y (xoay ngược chiều kim đồng hồ)

    @property
    def pose(self) -> TagPose:
        return TagPose(self.tag_id, self.position.copy(), yaw_matrix(self.yaw_deg))

    @property
    def pivot(self) -> np.ndarray:
        return self.pose.to_world(self.pivot_offset)

    @property
    def direction(self) -> np.ndarray:
        """Hướng thật của tia (đã cộng sai lệch servo)."""
        pan, tilt = np.radians([self.pan + self.bias[0], self.tilt + self.bias[1]])
        local = np.array([-np.sin(pan) * np.cos(tilt), np.cos(pan) * np.cos(tilt), np.sin(tilt)])
        return yaw_matrix(self.facing_deg) @ local


@dataclass
class Card:
    """Thẻ bia. `surface="wall"`: dán phẳng trên bảng tại (x, z). `"floor"`: dựng đứng trên nền tại
    (x, y), z = 0. `y` của thẻ trên bảng không dùng (lấy board.plane_y)."""

    x: float
    z: float
    index: int
    size: float = CARD_SIZE
    surface: str = "wall"
    y: float = 0.0


def _rays(camera: Camera, size: tuple[int, int]) -> np.ndarray:
    k = camera.intrinsics
    key = (camera.R.tobytes(), camera.t.tobytes(), k.K.tobytes(), k.dist.tobytes(), size)
    if key not in _RAY_CACHE:
        if len(_RAY_CACHE) >= 3:
            _RAY_CACHE.pop(next(iter(_RAY_CACHE)))
        _RAY_CACHE[key] = camera_rays(camera, (size[1], size[0]))
    return _RAY_CACHE[key]


@lru_cache(maxsize=4)
def _textures(tw: int, td: int, bw: int, bh: int) -> tuple[np.ndarray, np.ndarray]:
    """Ảnh mặt bàn (tw x td mm) và mặt bảng (bw x bh mm), 1 pixel = 1 mm. Dùng chung giữa các Scene."""
    rng = np.random.default_rng(1234)  # vân cố định giữa các lần chạy

    def noise(h, w, sigma):
        return cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), sigma)

    grain = cv2.GaussianBlur(rng.normal(0, 1, (td, tw)).astype(np.float32), (0, 0), 1.2)
    grain = cv2.GaussianBlur(grain, (0, 0), sigmaX=30, sigmaY=0.01)  # vân dọc theo X
    shade = grain * 120 + noise(td, tw, 40) * 120 + rng.normal(0, 2, (td, tw))
    wood = np.array([75, 112, 150], np.float32)  # BGR, gỗ vừa
    table = np.clip(wood + shade[..., None], 0, 255).astype(np.uint8)
    cv2.rectangle(table, (6, 6), (tw - 7, td - 7), (150, 200, 225), 5)  # băng dán viền vùng bia nền

    light = np.array([196, 200, 192], np.float32)  # BGR; kênh đỏ ~192 còn chỗ cho laser tăng
    shade = noise(bh, bw, 60) * 150 + rng.normal(0, 1.5, (bh, bw))
    board = np.clip(light + shade[..., None], 0, 255).astype(np.uint8)
    cv2.rectangle(board, (0, 0), (bw - 1, bh - 1), (110, 115, 110), 8)  # viền bảng
    return table, board


class Scene:
    def __init__(
        self,
        site: dict[str, Any] | None = None,
        intrinsics: Intrinsics | None = None,
        camera: Camera | None = None,
        cards_dir: str | Path | None = None,
        seed: int | None = 0,
        noise_sigma: float = 2.0,
        brightness_jitter: float = 0.015,
        blur_sigma: float = 0.0,
        spot_sigma_px: float = 2.5,
        node_height: float | None = None,
        card_gain: float = 0.8,
        camera_pose: "str | tuple | None" = None,
        node_layout: "str | dict | None" = None,
    ) -> None:
        self.site = sim_site(write=False) if site is None else fill_reference(site)
        self.board = self.site["board"]
        self.table = self.site.get("table", {"width": 1.20, "depth": 0.80})
        self.intrinsics = intrinsics or self._intrinsics_from_site()
        self.size = self.intrinsics.size  # (rộng, cao)
        self.cards_dir = Path(cards_dir) if cards_dir else CARDS_DIR
        self.rng = np.random.default_rng(seed)
        self.noise_sigma = noise_sigma
        self.brightness_jitter = brightness_jitter
        self.blur_sigma = blur_sigma
        self.spot_sigma_px = spot_sigma_px
        self.card_gain = (
            card_gain  # thẻ in không trắng tuyệt đối: nhân độ sáng ảnh thẻ với hệ số này
        )

        layout = NODE_LAYOUTS[node_layout or DEFAULT_LAYOUT] if not isinstance(node_layout, dict) else (
            node_layout)
        self.node_layout = (node_layout or DEFAULT_LAYOUT) if not isinstance(node_layout, dict) else "custom"
        self.nodes: dict[str, Node] = {}
        for name, tag_id in self.site["tags"]["nodes"]["ids"].items():
            pos, yaw, facing = layout[name]
            pos = np.array(pos, float)
            if node_height is not None:
                pos[2] = node_height
            self.nodes[name] = Node(name, tag_id, pos, yaw, facing_deg=facing)
        self.cards: dict[int, Card] = {}
        self._next_card = 0
        self._images: list[np.ndarray] | None = None

        self._look_at = np.zeros(3)
        if camera is not None:
            self.camera = camera
        elif camera_pose is not None:
            pos, target = CAMERA_PRESETS[camera_pose] if isinstance(camera_pose, str) else camera_pose
            self.camera = make_camera(pos, target, self.intrinsics)
            self._look_at = np.asarray(target, float).copy()
        else:
            self.camera = self.default_camera()
        if camera_pose is None or camera is not None:
            self._look_at = self.camera.position + self.camera.R[2] * 1.5
        self._rays: np.ndarray | None = None
        self._base: np.ndarray | None = None
        self._occluder: np.ndarray | None = None
        self._table_tex, self._board_tex = _textures(
            int(self.table["width"] * 1000),
            int(self.table["depth"] * 1000),
            int(self.board["width"] * 1000),
            int(self.board["height"] * 1000),
        )

    # --- camera -----------------------------------------------------------------------------

    def _intrinsics_from_site(self) -> Intrinsics:
        path = self.site.get("camera", {}).get("intrinsics")
        if path and (REPO_ROOT / path).exists():
            return load_intrinsics(REPO_ROOT / path)
        return default_intrinsics()

    def _tag_quads(self) -> tuple[dict[int, np.ndarray], dict[str, np.ndarray]]:
        """Bốn góc ngoài (kể cả viền trắng) của từng tag tham chiếu và từng tag node."""
        ref = self.site["tags"]["reference"]
        h = ref["size"] / 2 * 1.25
        refs = {
            int(i): np.array([[c[0] + a, c[1] + b, 0.0] for a in (-h, h) for b in (-h, h)])
            for i, c in ref["positions"].items()
        }
        h = self.site["tags"]["nodes"]["size"] / 2 * 1.25
        nodes = {
            n: np.array([node.position + node.pose.rotation @ [a, b, 0] for a in (-h, h)
                         for b in (-h, h)])
            for n, node in self.nodes.items()
        }
        return refs, nodes

    def tag_corner_points(self) -> np.ndarray:
        """Bốn góc bảng và bốn góc ngoài (kể cả viền trắng) của mọi tag."""
        board = self.board
        pts = [[x, board["plane_y"], z] for x in (0, board["width"]) for z in (0, board["height"])]
        refs, nodes = self._tag_quads()
        return np.vstack([np.array(pts), *refs.values(), *nodes.values()])

    def surface_points(self, nx: int = 13) -> tuple[np.ndarray, np.ndarray]:
        """Lưới điểm lấy mẫu trên nền và trên bảng (để đo phần diện tích nằm trong khung)."""
        tw, td = self.table["width"], self.table["depth"]
        floor = [[x, y, 0.0] for x in np.linspace(0, tw, nx) for y in np.linspace(0, td, 9)]
        bw, bh, y = self.board["width"], self.board["height"], self.board["plane_y"]
        wall = [[x, y, z] for x in np.linspace(0, bw, nx) for z in np.linspace(0, bh, 7)]
        return np.array(floor), np.array(wall)

    def default_camera(self) -> Camera:
        """Camera preset "front" (cao 0,9 m, lùi 0,8 m trước bàn, nhìn chếch xuống); nếu với
        intrinsics này không thấy hết tag, nền và bảng thì lùi ra xa thêm từng 5 cm."""
        pos, target = CAMERA_PRESETS[DEFAULT_PRESET]
        pts = np.vstack([self.tag_corner_points(), *self.surface_points()])
        for dy in np.arange(0.0, 2.0, 0.05):
            camera = make_camera([pos[0], pos[1] - dy, pos[2]], target, self.intrinsics)
            if in_frame(pts, camera, self.size, FRAME_MARGIN_PX).all():
                return camera
        raise RuntimeError("không đặt được camera thấy hết nền, bảng và tag với intrinsics này")

    def frame_check(self, margin: float = 0.0) -> bool:
        """Bốn góc bảng và mọi tag đều nằm trong khung."""
        return bool(in_frame(self.tag_corner_points(), self.camera, self.size, margin).all())

    def set_camera(self, position, look_at=None) -> None:
        """Đặt camera tại `position` nhìn về `look_at` (up = +Z). Cũng nhận một `Camera` dựng sẵn."""
        if isinstance(position, Camera):
            self.camera = position
            self._sync_look_at()
        else:
            self.camera = make_camera(position, look_at, self.intrinsics)
            self._look_at = np.asarray(look_at, float).copy()
        self._rays = None
        self._dirty()

    def _sync_look_at(self, dist: float | None = None) -> None:
        """Điểm nhìn suy ra từ camera: dọc trục quang, cách máy `dist` (mặc định 1,5 m)."""
        if dist is None:
            dist = float(np.linalg.norm(self._look_at - self.camera.position)) or 1.5
        self._look_at = self.camera.position + self.camera.R[2] * dist

    def camera_pose(self) -> tuple[np.ndarray, np.ndarray]:
        """(vị trí, điểm nhìn) hiện tại của camera, hệ sa bàn."""
        return self.camera.position.copy(), self._look_at.copy()

    def use_preset(self, name: str) -> None:
        self.set_camera(*CAMERA_PRESETS[name])

    def jitter_camera(self, rng, max_shift_m: float = 0.03, max_rot_deg: float = 2.0) -> dict:
        """Giả lập camera bị va giữa hai lần thử: dịch ngẫu nhiên không quá `max_shift_m` và xoay
        hướng nhìn (yaw, pitch, không lăn) không quá `max_rot_deg`. Trả {"shift_m", "rot_deg"}."""
        pos, target = self.camera_pose()
        v = rng.normal(size=3)
        v *= max_shift_m * rng.uniform(0.3, 1.0) / np.linalg.norm(v)
        ang = rng.uniform(-1, 1, 2)
        ang *= max_rot_deg * rng.uniform(0.3, 1.0) / max(np.linalg.norm(ang), 1e-9)
        d = target - pos
        dist = float(np.linalg.norm(d))
        a, b = np.radians(ang)
        Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
        axis = np.cross(d, [0, 0, 1.0])
        axis /= np.linalg.norm(axis)
        K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
        Rp = np.eye(3) + np.sin(b) * K + (1 - np.cos(b)) * (K @ K)
        new_d = Rp @ Rz @ d
        self.set_camera(pos + v, pos + v + new_d / np.linalg.norm(new_d) * dist)
        cos = float(d @ new_d / dist**2)
        rot = float(np.degrees(np.arccos(min(cos, 1.0))))
        return {"shift_m": float(np.linalg.norm(v)), "rot_deg": rot}

    def coverage(self, margin: float = 10.0) -> dict:
        """Camera thấy gì: tag tham chiếu và tag node nào trong khung (cả bốn góc, cách mép
        `margin` px), phần nền và bảng nằm trong khung, kèm cảnh báo dạng chữ."""
        refs, nodes = self._tag_quads()

        def seen(quad):
            return bool(in_frame(quad, self.camera, self.size, margin).all())

        ref_ok = {i: seen(q) for i, q in refs.items()}
        node_ok = {n: seen(q) for n, q in nodes.items()}
        floor, wall = self.surface_points()
        f = float(in_frame(floor, self.camera, self.size, 0.0).mean())
        w = float(in_frame(wall, self.camera, self.size, 0.0).mean())
        warnings = [f"thiếu tag tham chiếu {i}" for i, ok in ref_ok.items() if not ok]
        warnings += [f"thiếu tag node {n}" for n, ok in node_ok.items() if not ok]
        if f < 1.0:
            warnings.append("không thấy hết sàn")
        if w < 1.0:
            warnings.append("không thấy hết bảng")
        return {"reference_tags": ref_ok, "node_tags": node_ok, "floor": f, "wall": w,
                "ok": not warnings, "warnings": warnings}

    def shift_camera(self, delta=(0.0, 0.0, 0.0), yaw_deg: float = 0.0, pitch_deg: float = 0.0):
        """Xê dịch camera: tịnh tiến `delta` (hệ sa bàn) rồi xoay thêm quanh trục dọc/ngang máy."""
        cam = self.camera
        position = cam.position + np.asarray(delta, float)
        a, b = np.radians([yaw_deg, pitch_deg])
        Ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
        Rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
        R = Ry @ Rx @ cam.R
        dist = float(np.linalg.norm(self._look_at - cam.position)) or 1.5
        self.set_camera(Camera(cam.intrinsics, R, -R @ position))
        self._sync_look_at(dist)

    # --- thao tác trên cảnh -----------------------------------------------------------------

    def _dirty(self) -> None:
        self._base = None

    def add_card(self, x: float, z: float, index: int | None = None, size: float = CARD_SIZE):
        """Dán thẻ bia tâm (x, z) lên bảng, trả id thẻ. `index` chọn ảnh thẻ (mặc định lần lượt)."""
        return self.add_card_3d((x, self.board["plane_y"], z), "wall", index, size)

    def add_card_3d(self, position, surface: str | None = None, index: int | None = None,
                    size: float = CARD_SIZE) -> int:
        """Đặt thẻ bia tại điểm 3D `position` (x, y, z), trả id thẻ.

        `surface`: "wall" (dán phẳng trên bảng, y = plane_y) hoặc "floor" (dựng đứng trên nền, đáy
        thẻ chạm nền tại (x, y), z = 0). Bỏ trống thì suy ra: y = plane_y là bảng, còn lại là nền.
        """
        x, y, z = (float(c) for c in position)
        plane_y = self.board["plane_y"]
        if surface is None:
            surface = "wall" if abs(y - plane_y) < 1e-6 else "floor"
        if surface not in ("wall", "floor"):
            raise ValueError(f"surface phải là 'wall' hoặc 'floor', không phải {surface!r}")
        if surface == "wall":
            y = plane_y
        else:
            z = 0.0
        n = len(self.card_images())
        card_id = self._next_card
        self._next_card += 1
        self.cards[card_id] = Card(x, z, card_id % n if index is None else index % n, size,
                                   surface, y)
        self._dirty()
        return card_id

    def remove_card(self, card_id: int) -> None:
        self.cards.pop(card_id)
        self._dirty()

    def clear_cards(self) -> None:
        self.cards.clear()
        self._dirty()

    def card_images(self) -> list[np.ndarray]:
        if self._images is None:
            files = sorted(self.cards_dir.glob("*.png")) if self.cards_dir.exists() else []
            if not files:
                raise FileNotFoundError(
                    f"không có ảnh thẻ bia trong {self.cards_dir}; tạo bằng "
                    "`uv run --no-sync python scripts/make_target_cards.py`"
                )
            images = [cv2.imread(str(f), cv2.IMREAD_COLOR) for f in files]
            self._images = [(im * self.card_gain).astype(np.uint8) for im in images]
        return self._images

    def aim(self, name: str, pan: float, tilt: float) -> None:
        """Ra lệnh góc pan, tilt (độ) cho node; hướng thật còn cộng sai lệch servo."""
        self.nodes[name].pan, self.nodes[name].tilt = float(pan), float(tilt)

    def set_laser(self, name: str, on: bool) -> None:
        self.nodes[name].laser = bool(on)

    def set_servo_bias(self, name: str, pan: float, tilt: float) -> None:
        """Sai lệch hệ thống của servo (độ): hướng thật = góc ra lệnh + sai lệch."""
        self.nodes[name].bias = (float(pan), float(tilt))

    def move_node(self, name: str, position=None, delta=None, yaw_deg: float | None = None):
        node = self.nodes[name]
        if position is not None:
            node.position = np.asarray(position, float)
        if delta is not None:
            node.position = node.position + np.asarray(delta, float)
        if yaw_deg is not None:
            node.yaw_deg = float(yaw_deg)
        self._dirty()

    # --- ground truth -----------------------------------------------------------------------

    def spot_surface(self, name: str) -> tuple[np.ndarray, str] | None:
        """Điểm đầu tiên tia của node chạm vào bảng hoặc nền (bất kể laser bật hay tắt), kèm tên
        mặt ("wall" | "floor"); None nếu tia không chạm mặt nào trong phạm vi."""
        node = self.nodes[name]
        d, p = node.direction, node.pivot
        hits = []
        if d[1] > 1e-9:
            s = (self.board["plane_y"] - p[1]) / d[1]
            q = p + s * d
            if s > 0 and 0 <= q[0] <= self.board["width"] and 0 <= q[2] <= self.board["height"]:
                hits.append((s, q, "wall"))
        if d[2] < -1e-9:
            s = -p[2] / d[2]
            q = p + s * d
            if s > 0 and 0 <= q[0] <= self.table["width"] and 0 <= q[1] <= self.table["depth"]:
                hits.append((s, q, "floor"))
        if not hits:
            return None
        _, point, surface = min(hits, key=lambda h: h[0])
        return point, surface

    def spot(self, name: str) -> np.ndarray | None:
        """Điểm tia laser của node chạm bảng hoặc nền (bất kể laser bật hay tắt); None nếu ra ngoài."""
        hit = self.spot_surface(name)
        return None if hit is None else hit[0]

    def card_position(self, card_id: int) -> np.ndarray:
        """Điểm 3D của thẻ: tâm thẻ trên bảng, hoặc điểm đáy chạm nền với thẻ trên nền."""
        c = self.cards[card_id]
        if c.surface == "floor":
            return np.array([c.x, c.y, 0.0])
        return np.array([c.x, self.board["plane_y"], c.z])

    def card_quad(self, card_id: int) -> np.ndarray:
        """Bốn góc thẻ (TL, TR, BR, BL) trong hệ sa bàn. Thẻ trên nền dựng đứng, quay mặt về
        camera (xoay quanh trục Z), đáy ở Z = 0."""
        c = self.cards[card_id]
        r = c.size / 2
        if c.surface == "wall":
            y = self.board["plane_y"]
            return np.array([[c.x - r, y, c.z + r], [c.x + r, y, c.z + r],
                             [c.x + r, y, c.z - r], [c.x - r, y, c.z - r]])
        p = np.array([c.x, c.y, 0.0])
        n = self.camera.position[:2] - p[:2]
        n = n / np.linalg.norm(n) if np.linalg.norm(n) > 1e-9 else np.array([0.0, -1.0])
        u = np.array([-n[1], n[0], 0.0])  # trục ngang ảnh của camera
        up = np.array([0.0, 0.0, c.size])
        return np.array([p - r * u + up, p + r * u + up, p + r * u, p - r * u])

    def node_hull_px(self, name: str, pad: float = 0.0) -> np.ndarray:
        """Bao lồi (N, 2) chiếu lên ảnh của trụ và tag node: vùng bảng phía sau bị che."""
        node = self.nodes[name]
        h = POST_HALF + pad
        R = yaw_matrix(node.yaw_deg)
        low = -node.position[2]
        pts = [node.position + R @ [a, b, c] for a in (-h, h) for b in (-h, h) for c in (0, low)]
        px = np.array([project(p, self.camera) for p in pts], np.float32)
        return cv2.convexHull(px).reshape(-1, 2)

    # --- dựng ảnh ---------------------------------------------------------------------------

    def _rays_full(self) -> np.ndarray:
        if self._rays is None:
            self._rays = _rays(self.camera, self.size)
        return self._rays

    def _build_base(self) -> np.ndarray:
        """Cảnh tĩnh không nhiễu: nền, mặt bàn, bảng, thẻ bia, trụ và tag. Giữ tới khi đổi cảnh."""
        w, h = self.size
        camera, rays = self.camera, self._rays_full()
        canvas = np.empty((h, w, 3), np.uint8)
        canvas[:] = np.linspace(70, 45, h).astype(np.uint8)[:, None, None]
        occluder = np.zeros((h, w), np.uint8)

        tw, td = self.table["width"], self.table["depth"]
        table = np.array([[0, td, 0], [tw, td, 0], [tw, 0, 0], [0, 0, 0]])
        render_quad(canvas, self._table_tex, table, camera, rays)

        y, bw, bh = self.board["plane_y"], self.board["width"], self.board["height"]
        board = np.array([[0, y, bh], [bw, y, bh], [bw, y, 0], [0, y, 0]])
        render_quad(canvas, self._board_tex, board, camera, rays)
        for cid, c in self.cards.items():
            if c.surface == "wall":
                render_quad(canvas, self.card_images()[c.index], self.card_quad(cid), camera, rays)

        ref = self.site["tags"]["reference"]
        for tag_id, (x, yy) in ref["positions"].items():
            tex, quad = tag_quad(int(tag_id), ref["size"], [x, yy, 0.0])
            render_quad(
                canvas, cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR), quad, camera, supersample=SUPERSAMPLE
            )

        # Trụ node và thẻ đứng trên nền che nhau theo khoảng cách ngang tới camera: vẽ xa trước.
        size = self.site["tags"]["nodes"]["size"]
        cam_xy = camera.position[:2]
        items = [(np.linalg.norm(n.position[:2] - cam_xy), "node", n) for n in self.nodes.values()]
        items += [(np.linalg.norm([c.x, c.y] - cam_xy), "card", cid)
                  for cid, c in self.cards.items() if c.surface == "floor"]
        for _, kind, obj in sorted(items, key=lambda it: -it[0]):
            if kind == "card":
                c = self.cards[obj]
                render_quad(canvas, self.card_images()[c.index], self.card_quad(obj), camera,
                            supersample=2)
                continue
            node = obj
            self._draw_post(canvas, occluder, node)
            tex, quad = tag_quad(node.tag_id, size, node.position, node.yaw_deg)
            render_quad(
                canvas, cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR), quad, camera, supersample=SUPERSAMPLE
            )
            cv2.fillConvexPoly(occluder, np.round(self._project(quad)).astype(np.int32), 255)
        self._occluder = occluder
        return canvas

    def _project(self, points: np.ndarray) -> np.ndarray:
        return np.array([project(p, self.camera) for p in points])

    def _draw_post(self, canvas: np.ndarray, occluder: np.ndarray, node: Node) -> None:
        """Trụ hộp dưới tag: các mặt hông hướng về camera, vẽ từ xa tới gần."""
        top = node.position[2] - 0.002
        R = yaw_matrix(node.yaw_deg)[:2, :2]
        h = POST_HALF
        base = [node.position[:2] + R @ c for c in ((-h, h), (h, h), (h, -h), (-h, -h))]
        cam = self.camera.position
        faces = []
        for i in range(4):
            a, b = base[i], base[(i + 1) % 4]
            mid = (a + b) / 2
            normal = np.array([b[1] - a[1], a[0] - b[0]])
            normal /= np.linalg.norm(normal)
            if normal @ (cam[:2] - mid) > 0:
                quad = np.array(
                    [[a[0], a[1], top], [b[0], b[1], top], [b[0], b[1], 0], [a[0], a[1], 0]]
                )
                faces.append((np.linalg.norm(cam[:2] - mid), normal, quad))
        for _, normal, quad in sorted(faces, key=lambda f: -f[0]):
            shade = 55 + 40 * float(normal @ [0.3, -0.9])
            poly = np.round(self._project(quad)).astype(np.int32)
            cv2.fillConvexPoly(canvas, poly, (shade, shade, shade))
            cv2.fillConvexPoly(occluder, poly, 255)

    def _draw_spot(self, img: np.ndarray, pixel: tuple[float, float]) -> None:
        sigma = self.spot_sigma_px
        r = int(np.ceil(4 * sigma))
        u0, v0 = round(pixel[0]), round(pixel[1])
        x0, x1 = max(u0 - r, 0), min(u0 + r + 1, img.shape[1])
        y0, y1 = max(v0 - r, 0), min(v0 + r + 1, img.shape[0])
        if x1 <= x0 or y1 <= y0:
            return
        xs, ys = np.meshgrid(np.arange(x0, x1), np.arange(y0, y1))
        g = np.exp(-((xs - pixel[0]) ** 2 + (ys - pixel[1]) ** 2) / (2 * sigma**2))
        g *= self._occluder[y0:y1, x0:x1] == 0  # trụ và tag che mất vết
        patch = img[y0:y1, x0:x1].astype(np.float32)
        patch[..., 2] += 230 * g  # đỏ tăng mạnh, tâm chạm trần
        patch[..., 1] += 110 * g  # xanh lá và xanh dương tăng ít hơn: quầng đỏ, tâm gần trắng
        patch[..., 0] += 110 * g
        img[y0:y1, x0:x1] = np.clip(patch, 0, 255).astype(np.uint8)

    def render(self) -> np.ndarray:
        """Một khung BGR uint8. Gọi hai lần cho hai khung khác nhau (nhiễu, độ sáng)."""
        if self._base is None:
            self._base = self._build_base()
        img = self._base.copy()
        for node in self.nodes.values():
            spot = self.spot(node.name) if node.laser else None
            if spot is not None:
                self._draw_spot(img, project(spot, self.camera))
        if self.blur_sigma > 0:
            img = cv2.GaussianBlur(img, (0, 0), self.blur_sigma)
        out = img.astype(np.float32)
        out *= 1.0 + self.rng.uniform(-self.brightness_jitter, self.brightness_jitter)
        if self.noise_sigma > 0:
            out += self.rng.standard_normal(out.shape, dtype=np.float32) * self.noise_sigma
        return np.clip(out, 0, 255).astype(np.uint8)
