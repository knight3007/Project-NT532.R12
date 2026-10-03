"""Sa bàn ảo: dựng ảnh như webcam thật nhìn sa bàn chữ L, kèm ground truth.

    scene = Scene()                       # camera, bảng, 4 tag tham chiếu, hai node s1 và s2
    scene.add_card(0.40, 0.25)            # thẻ bia 7 cm tại (x, z) trên bảng
    scene.aim("s1", pan, tilt); scene.set_laser("s1", True)
    frame = scene.render()                # BGR uint8, mỗi lần gọi nhiễu khác nhau

Hệ tọa độ theo kế hoạch: gốc ở góc trước bên trái mặt bàn, X sang phải, Y về phía bảng bia, Z lên
trên, đơn vị mét. Bảng bia là mặt Y = board.plane_y, X từ 0 tới width, Z từ 0 tới height.

Cái khác với thật (đọc trước khi tin con số): ánh sáng phẳng, không bóng, không phản xạ, không
nước; vết laser là một đốm gauss đặt đúng nơi tia cắt bảng; servo chỉ có sai lệch hệ thống, không
có rơ hay rung; mặt bàn và bảng là ảnh sinh ra chứ không phải vật liệu thật.
"""

import copy
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import REPO_ROOT, read_site
from ..vision.calibration import load_intrinsics, save_intrinsics
from ..vision.localize import project
from ..vision.types import Camera, Intrinsics, TagPose
from .render import camera_rays, in_frame, look_at, render_quad, tag_quad, yaw_matrix

CARDS_DIR = REPO_ROOT / "data" / "sim" / "cards"
SIM_DIR = REPO_ROOT / "data" / "sim"
CARD_SIZE = 0.07

# 4 tag tham chiếu trên mặt bàn, tâm (x, y). Rộng hơn "giữa bàn" một chút cho pose camera chính
# xác hơn, nhưng vẫn nằm trước và hai bên node nên không bị trụ node che.
DEFAULT_REFERENCE = {0: (0.30, 0.28), 1: (0.90, 0.28), 2: (0.15, 0.62), 3: (1.05, 0.62)}
# Tâm tag trên đế (x, y, z) và góc xoay (độ). Cách bảng 0,25 m, lệch trái và phải.
DEFAULT_NODES = {"s1": ((0.30, 0.55, 0.25), 15.0), "s2": ((0.90, 0.55, 0.25), -25.0)}
# Tâm quay pan–tilt trong hệ tag (đo từ tâm tag). Hướng 0 của pan–tilt được giả định song song
# trục Y sa bàn, không theo yaw của tag.
DEFAULT_PIVOT_OFFSET = (0.0, 0.0, 0.08)
# Camera theo kế hoạch: chính giữa X, cách mép trước 0,40 m, cao 0,70 m, nhìn chéo xuống bảng.
PLAN_CAMERA = (0.60, 0.40, 0.70)
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
    return site


def aim_angles(pivot, target) -> tuple[float, float]:
    """Góc (pan, tilt), độ, để tia từ `pivot` đi qua `target`. Hai điểm trong hệ sa bàn.

    CHỈ LÀ BẢN THAM CHIẾU CHO MÔ PHỎNG, khớp quy ước của `Node.direction`: pan = 0 và tilt = 0 là
    hướng +Y; pan dương quay về phía -X; tilt dương ngẩng lên +Z. Công thức chính thức (có bảng
    hiệu chỉnh servo) do orchestrator viết (Hậu).
    """
    v = np.asarray(target, float) - np.asarray(pivot, float)
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
        return np.array([-np.sin(pan) * np.cos(tilt), np.cos(pan) * np.cos(tilt), np.sin(tilt)])


@dataclass
class Card:
    x: float
    z: float
    index: int
    size: float = CARD_SIZE


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

        self.nodes: dict[str, Node] = {}
        for name, tag_id in self.site["tags"]["nodes"]["ids"].items():
            pos, yaw = DEFAULT_NODES[name]
            pos = np.array(pos, float)
            if node_height is not None:
                pos[2] = node_height
            self.nodes[name] = Node(name, tag_id, pos, yaw)
        self.cards: dict[int, Card] = {}
        self._next_card = 0
        self._images: list[np.ndarray] | None = None

        self.camera = camera or self.default_camera()
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

    def tag_corner_points(self) -> np.ndarray:
        """Bốn góc bảng và bốn góc ngoài (kể cả viền trắng) của mọi tag."""
        board = self.board
        pts = [[x, board["plane_y"], z] for x in (0, board["width"]) for z in (0, board["height"])]
        ref = self.site["tags"]["reference"]
        h = ref["size"] / 2 * 1.25
        for center in ref["positions"].values():
            pts += [[center[0] + a, center[1] + b, 0.0] for a in (-h, h) for b in (-h, h)]
        h = self.site["tags"]["nodes"]["size"] / 2 * 1.25
        for node in self.nodes.values():
            R = node.pose.rotation
            pts += [node.position + R @ [a, b, 0] for a in (-h, h) for b in (-h, h)]
        return np.array(pts)

    def default_camera(self) -> Camera:
        """Camera theo kế hoạch (giữa X, cách mép trước 0,40 m, cao 0,70 m, nhìn xuống bảng); nếu
        với intrinsics này không thấy hết bốn góc bảng và mọi tag thì lùi ra phía trước bàn.

        Với f ~ 900 px (HFOV 70 độ) vị trí theo kế hoạch KHÔNG thấy hết bảng rộng 1,2 m từ cách
        bảng 0,4 m; cần webcam góc rộng cỡ f ~ 350 px. Hàm lùi camera từng 5 cm cho tới khi vừa.
        """
        pts = self.tag_corner_points()
        x, y0, z = PLAN_CAMERA
        plane_y = self.board["plane_y"]
        for y in np.arange(y0, -2.0, -0.05):
            for aim_z in (0.30, 0.20, 0.10):
                camera = look_at([x, y, z], [x, plane_y, aim_z], self.intrinsics)
                if in_frame(pts, camera, self.size, FRAME_MARGIN_PX).all():
                    return camera
        raise RuntimeError("không đặt được camera thấy hết bảng và tag với intrinsics này")

    def frame_check(self, margin: float = 0.0) -> bool:
        """Bốn góc bảng và mọi tag đều nằm trong khung."""
        return bool(in_frame(self.tag_corner_points(), self.camera, self.size, margin).all())

    def set_camera(self, camera: Camera) -> None:
        self.camera = camera
        self._rays = None
        self._dirty()

    def shift_camera(self, delta=(0.0, 0.0, 0.0), yaw_deg: float = 0.0, pitch_deg: float = 0.0):
        """Xê dịch camera: tịnh tiến `delta` (hệ sa bàn) rồi xoay thêm quanh trục dọc/ngang máy."""
        cam = self.camera
        position = cam.position + np.asarray(delta, float)
        a, b = np.radians([yaw_deg, pitch_deg])
        Ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
        Rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
        R = Ry @ Rx @ cam.R
        self.set_camera(Camera(cam.intrinsics, R, -R @ position))

    # --- thao tác trên cảnh -----------------------------------------------------------------

    def _dirty(self) -> None:
        self._base = None

    def add_card(self, x: float, z: float, index: int | None = None, size: float = CARD_SIZE):
        """Dán thẻ bia tâm (x, z) lên bảng, trả id thẻ. `index` chọn ảnh thẻ (mặc định lần lượt)."""
        n = len(self.card_images())
        card_id = self._next_card
        self._next_card += 1
        self.cards[card_id] = Card(
            float(x), float(z), card_id % n if index is None else index % n, size
        )
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

    def spot(self, name: str) -> np.ndarray | None:
        """Điểm tia laser của node cắt mặt bảng (bất kể laser bật hay tắt); None nếu ra ngoài."""
        node = self.nodes[name]
        d, p = node.direction, node.pivot
        if d[1] <= 1e-9:
            return None
        point = p + (self.board["plane_y"] - p[1]) / d[1] * d
        ok = 0 <= point[0] <= self.board["width"] and 0 <= point[2] <= self.board["height"]
        return point if ok else None

    def card_position(self, card_id: int) -> np.ndarray:
        c = self.cards[card_id]
        return np.array([c.x, self.board["plane_y"], c.z])

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
        for c in self.cards.values():
            r = c.size / 2
            quad = np.array(
                [
                    [c.x - r, y, c.z + r],
                    [c.x + r, y, c.z + r],
                    [c.x + r, y, c.z - r],
                    [c.x - r, y, c.z - r],
                ]
            )
            render_quad(canvas, self.card_images()[c.index], quad, camera, rays)

        ref = self.site["tags"]["reference"]
        for tag_id, (x, yy) in ref["positions"].items():
            tex, quad = tag_quad(int(tag_id), ref["size"], [x, yy, 0.0])
            render_quad(
                canvas, cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR), quad, camera, supersample=SUPERSAMPLE
            )

        size = self.site["tags"]["nodes"]["size"]
        for node in self.nodes.values():
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
        u0, v0 = int(round(pixel[0])), int(round(pixel[1]))
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
