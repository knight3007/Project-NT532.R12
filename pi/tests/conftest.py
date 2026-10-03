import cv2
import numpy as np
import pytest

from nt532.vision.types import Camera, Intrinsics

SIZE = (1280, 720)


def look_at(position, target, intrinsics: Intrinsics) -> Camera:
    position, target = np.asarray(position, float), np.asarray(target, float)
    z = target - position
    z /= np.linalg.norm(z)
    x = np.cross(z, [0.0, 0.0, 1.0])
    x /= np.linalg.norm(x)
    R = np.stack([x, np.cross(z, x), z])
    return Camera(intrinsics, R, -R @ position)


def render_quad(canvas: np.ndarray, texture: np.ndarray, world: np.ndarray, camera: Camera) -> None:
    """Dán một ảnh phẳng có bốn góc `world` (TL, TR, BR, BL) vào khung hình của camera."""
    k = camera.intrinsics
    px, _ = cv2.projectPoints(world, cv2.Rodrigues(camera.R)[0], camera.t, k.K, k.dist)
    h, w = texture.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    H = cv2.getPerspectiveTransform(src, px.reshape(4, 2).astype(np.float32))
    size = (canvas.shape[1], canvas.shape[0])
    warped = cv2.warpPerspective(texture, H, size, flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), H, size)
    canvas[mask > 0] = warped[mask > 0]


def tag_quad(tag_id: int, size: float, center, yaw_deg: float = 0.0):
    """Ảnh tag có viền trắng một ô và bốn góc của cả tấm (kể cả viền) trong hệ sa bàn."""
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    marker = cv2.aruco.generateImageMarker(dictionary, tag_id, 400)
    texture = cv2.copyMakeBorder(marker, 50, 50, 50, 50, cv2.BORDER_CONSTANT, value=255)
    h = size / 2 * 1.25
    square = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]])
    a = np.radians(yaw_deg)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    return texture, square @ Rz.T + np.asarray(center, float)


@pytest.fixture
def intrinsics() -> Intrinsics:
    K = np.array([[900.0, 0, 640], [0, 900.0, 360], [0, 0, 1]])
    return Intrinsics(K, np.zeros(5), SIZE)


NODES = {21: ([0.40, 0.58, 0.05], 20), 22: ([0.80, 0.58, 0.05], -35)}


def make_scene(site: dict, camera: Camera, nodes: dict = NODES) -> np.ndarray:
    """Ảnh xám tổng hợp: bốn tag tham chiếu trên mặt bàn và các tag node {id: (tâm, góc xoay)}."""
    canvas = np.full((SIZE[1], SIZE[0]), 180, np.uint8)
    ref = site["tags"]["reference"]
    for tag_id, (x, y) in ref["positions"].items():
        render_quad(canvas, *tag_quad(tag_id, ref["size"], [x, y, 0.0]), camera)
    for tag_id, (center, yaw) in nodes.items():
        render_quad(canvas, *tag_quad(tag_id, 0.08, center, yaw_deg=yaw), camera)
    return canvas


@pytest.fixture
def site() -> dict:
    return {
        "board": {"plane_y": 0.80, "width": 1.20, "height": 0.60},
        "tags": {
            "reference": {
                "size": 0.08,
                "positions": {0: [0.25, 0.45], 1: [0.95, 0.45], 2: [0.25, 0.70], 3: [0.95, 0.70]},
            },
            "nodes": {"size": 0.08, "ids": {"s1": 21, "s2": 22}},
        },
        "vision": {
            "board_margin_m": 0.02,
            "min_tag_rate": 0.6,
            "camera_shift_px": 3.0,
            "node_moved_m": 0.015,
            "laser_min_rise": 40,
        },
        "targeting": {"sensor_match_radius_x": 0.35},
    }


@pytest.fixture
def true_camera(intrinsics) -> Camera:
    return look_at([0.60, 0.05, 0.80], [0.60, 0.70, 0.10], intrinsics)


@pytest.fixture
def scene(site, true_camera) -> np.ndarray:
    """Bốn tag tham chiếu trên mặt bàn và hai tag node đặt cao 5 cm, xoay lệch."""
    return make_scene(site, true_camera)
