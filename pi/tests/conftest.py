import numpy as np
import pytest

from nt532.sim.render import look_at, render_quad, tag_quad
from nt532.vision.types import Camera, Intrinsics

SIZE = (1280, 720)


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
