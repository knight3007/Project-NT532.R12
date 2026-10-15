"""Bia trên nền (mặt bàn) cùng với bia trên bảng đứng, camera đặt cao nhìn xuống."""

import cv2
import numpy as np
import pytest
from conftest import NODES, SIZE, look_at, make_scene

from nt532.vision import Detection, Vision, localize_surfaces, project

WALL = {"plane_y": 0.80, "width": 1.20, "height": 0.60}
FLOOR = {"width": 1.20, "depth": 0.80}
FLOOR_POINTS = [[0.30, 0.30, 0.0], [0.90, 0.60, 0.0], [0.60, 0.05, 0.0]]
WALL_POINTS = [[0.30, 0.80, 0.25], [0.90, 0.80, 0.10], [0.60, 0.80, 0.30]]


@pytest.fixture
def top_camera(intrinsics):
    """Camera cao trên trần, trước bàn, nhìn chếch xuống mặt bàn và chân bảng."""
    return look_at([0.60, -0.20, 1.50], [0.60, 0.50, 0.10], intrinsics)


@pytest.fixture
def floor_site(site) -> dict:
    return {
        **site,
        "floor": {"enabled": True, "width": 1.20, "depth": 0.80},
        "vision": {**site["vision"], "auto_repose_camera": True},
    }


@pytest.fixture
def top_vision(floor_site, intrinsics, top_camera) -> Vision:
    v = Vision(floor_site, intrinsics, detector=lambda frame: [])
    v.commission([make_scene(floor_site, top_camera)] * 3)
    return v


def _in_view(point, camera) -> bool:
    u, v = project(point, camera)
    return 0 < u < SIZE[0] and 0 < v < SIZE[1]


def test_points_are_in_view(top_camera):
    assert all(_in_view(p, top_camera) for p in FLOOR_POINTS + WALL_POINTS)


def test_floor_and_wall_localize_from_above(top_camera):
    for point in FLOOR_POINTS:
        found, surface = localize_surfaces(project(point, top_camera), top_camera, WALL, FLOOR)
        assert surface == "floor" and np.allclose(found, point, atol=1e-6)
    for point in WALL_POINTS:
        found, surface = localize_surfaces(project(point, top_camera), top_camera, WALL, FLOOR)
        assert surface == "wall" and np.allclose(found, point, atol=1e-6)


def test_first_hit_near_the_seam(top_camera):
    just_above = [0.60, 0.80, 0.02]  # trên bảng, sát chân
    just_in_front = [0.60, 0.77, 0.0]  # trên nền, sát chân bảng
    _, surface = localize_surfaces(project(just_above, top_camera), top_camera, WALL, FLOOR)
    assert surface == "wall"
    found, surface = localize_surfaces(project(just_in_front, top_camera), top_camera, WALL, FLOOR)
    assert surface == "floor" and np.allclose(found, just_in_front, atol=1e-6)


def test_floor_pixel_is_missed_when_floor_disabled(top_camera):
    pixel = project(FLOOR_POINTS[0], top_camera)
    # không có nền: tia chạm mặt phẳng bảng ở dưới Z = 0 nên bị loại
    assert localize_surfaces(pixel, top_camera, WALL, None) is None
    assert localize_surfaces(pixel, top_camera, None, FLOOR)[1] == "floor"


def test_outside_both_surfaces_is_none(top_camera):
    off_side = project([1.60, 0.30, 0.0], top_camera)  # quá mép phải của nền
    assert localize_surfaces(off_side, top_camera, WALL, FLOOR, margin=0.02) is None


def test_vision_localize_reports_surface(top_vision, top_camera):
    point, surface = top_vision.localize_surface(project(FLOOR_POINTS[1], top_camera))
    assert surface == "floor" and np.linalg.norm(point - FLOOR_POINTS[1]) < 0.01
    assert np.linalg.norm(top_vision.localize(project(WALL_POINTS[0], top_camera)) - WALL_POINTS[0]) < 0.01


def test_floor_anchor_is_box_bottom_wall_anchor_is_centre(top_vision, top_camera):
    # ngọn lửa trên nền: đáy hộp chạm nền tại P, tâm hộp nằm cao hơn trên ảnh
    u, v = project(FLOOR_POINTS[0], top_camera)
    flame = Detection(u, v - 20, 30, 40, 0.9)
    point, surface = top_vision.localize_detection(flame)
    assert surface == "floor" and np.linalg.norm(point - FLOOR_POINTS[0]) < 0.01
    # thẻ trên bảng: lấy tâm hộp
    u, v = project(WALL_POINTS[0], top_camera)
    card = Detection(u, v, 30, 40, 0.9)
    point, surface = top_vision.localize_detection(card)
    assert surface == "wall" and np.linalg.norm(point - WALL_POINTS[0]) < 0.01


def test_targets_carry_surface(top_vision, top_camera):
    boxes = []
    for conf, point in zip((0.9, 0.8), (FLOOR_POINTS[1], WALL_POINTS[1])):
        u, v = project(point, top_camera)
        boxes.append(Detection(u, v - 20 if point[2] == 0 else v, 30, 40, conf))
    scene = np.zeros((SIZE[1], SIZE[0]), np.uint8)
    x0, y0, _, _ = top_vision.target_roi(scene.shape)
    top_vision._detector = lambda crop: [
        Detection(d.u - x0, d.v - y0, d.w, d.h, d.conf) for d in boxes
    ]
    found = top_vision.targets(scene)
    assert [t.surface for t in found] == ["floor", "wall"]
    assert np.linalg.norm(found[0].position - FLOOR_POINTS[1]) < 0.01


def test_in_target_area(top_vision, site, intrinsics, top_camera):
    on_wall, on_floor = np.array([0.5, 0.8, 0.3]), np.array([0.5, 0.4, 0.0])
    assert top_vision.in_target_area(on_wall) and top_vision.in_target_area(on_floor)
    assert top_vision.on_board(on_floor)  # tên cũ giữ nguyên, nay gồm cả nền
    assert not top_vision.in_target_area(np.array([0.5, 0.4, 0.3]))  # lơ lửng giữa không trung
    assert not top_vision.in_target_area(np.array([1.4, 0.4, 0.0]))
    wall_only = Vision({k: v for k, v in top_vision.site.items() if k != "floor"}, intrinsics)
    assert wall_only.in_target_area(on_wall) and not wall_only.in_target_area(on_floor)


def test_roi_covers_floor_and_wall(top_vision, top_camera, site, intrinsics):
    x0, y0, x1, y1 = top_vision.target_roi((SIZE[1], SIZE[0], 3))
    assert top_vision.board_roi((SIZE[1], SIZE[0], 3)) == (x0, y0, x1, y1)
    for point in FLOOR_POINTS + WALL_POINTS:
        u, v = project(point, top_camera)
        assert x0 <= u <= x1 and y0 <= v <= y1
    wall_only = Vision({k: v for k, v in top_vision.site.items() if k != "floor"}, intrinsics)
    wall_only.state = top_vision.state
    wx0, wy0, wx1, wy1 = wall_only.target_roi((SIZE[1], SIZE[0], 3))
    assert (x1 - x0) * (y1 - y0) > (wx1 - wx0) * (wy1 - wy0)
    assert len(top_vision.floor_polygon()) > 0 and len(wall_only.floor_polygon()) == 0


def test_auto_repose_after_camera_shift(top_vision, floor_site, intrinsics):
    bumped = look_at([0.63, -0.22, 1.50], [0.60, 0.50, 0.10], intrinsics)
    health = top_vision.check(make_scene(floor_site, bumped))
    assert health.camera_shift_px > 3.0
    assert health.repose is not None and health.repose["moved_m"] > 0.02
    assert sorted(health.repose["nodes"]) == ["s1", "s2"]
    assert health.stale == [] and top_vision.blockers("s1") == []
    assert np.linalg.norm(top_vision.camera.position - bumped.position) < 0.01
    for name, tag_id in (("s1", 21), ("s2", 22)):
        # camera nhìn dốc xuống thì Z của tag nhỏ kém chính xác hơn, chỉ so X, Y
        assert np.linalg.norm(top_vision.nodes[name].position[:2] - NODES[tag_id][0][:2]) < 0.02
    # sau khi tính lại, kiểm tra lần nữa thì khỏe
    assert top_vision.check(make_scene(floor_site, bumped)).repose is None


def test_auto_repose_keeps_node_moved_flag(top_vision, floor_site, intrinsics):
    bumped = look_at([0.63, -0.22, 1.50], [0.60, 0.50, 0.10], intrinsics)
    moved = dict(NODES)
    moved[21] = ([0.45, 0.58, 0.05], 20)
    health = top_vision.check(make_scene(floor_site, bumped, moved))
    assert health.repose is not None and health.stale == ["s1"]
    assert top_vision.blockers("s1") and top_vision.blockers("s2") == []


def test_camera_shift_still_blocks_when_reference_tags_not_steady(
    top_vision, floor_site, intrinsics
):
    bumped = look_at([0.63, -0.22, 1.50], [0.60, 0.50, 0.10], intrinsics)
    full = make_scene(floor_site, bumped)
    hidden = full.copy()
    for x, y in floor_site["tags"]["reference"]["positions"].values():
        u, v = project([x, y, 0.0], bumped)
        cv2.circle(hidden, (int(u), int(v)), 40, 180, -1)
    # tag tham chiếu chỉ hiện ở 2/5 khung, dưới min_tag_rate: không đủ tin để tính lại pose
    health = top_vision.check([full, full, hidden, hidden, hidden])
    assert health.repose is None and "camera" in health.stale
    assert top_vision.blockers("s1") != []


def test_flag_off_keeps_old_behaviour(top_vision, floor_site, intrinsics):
    top_vision.cfg = {**top_vision.cfg, "auto_repose_camera": False}
    bumped = look_at([0.63, -0.22, 1.50], [0.60, 0.50, 0.10], intrinsics)
    health = top_vision.check(make_scene(floor_site, bumped))
    assert health.repose is None and "camera" in health.stale
