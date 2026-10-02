import cv2
import numpy as np
import pytest
from conftest import SIZE, look_at, render_quad

from nt532.vision import (
    detect_tags,
    find_spot,
    localize,
    project,
    reprojection_error,
    solve_camera,
    tag_poses,
)
from nt532.vision.calibration import calibrate, charuco_board, load_intrinsics, save_intrinsics

BOARD_POINTS = [[0.1, 0.8, 0.05], [0.6, 0.8, 0.30], [1.1, 0.8, 0.55], [0.3, 0.8, 0.45]]


def test_localize_inverts_projection(true_camera):
    for point in BOARD_POINTS:
        found = localize(project(point, true_camera), true_camera, 0.80)
        assert np.allclose(found, point, atol=1e-6)


def test_localize_returns_none_when_plane_is_behind(true_camera):
    assert localize((640, 360), true_camera, plane_y=-1.0) is None


def test_camera_pose_from_reference_tags(scene, intrinsics, site, true_camera):
    corners = detect_tags(scene)
    assert set(corners) == {0, 1, 2, 3, 21, 22}
    camera = solve_camera(corners, intrinsics, site)
    assert np.linalg.norm(camera.position - true_camera.position) < 0.01
    assert reprojection_error(corners, camera, site) < 1.0
    for point in BOARD_POINTS:
        found = localize(project(point, true_camera), camera, site["board"]["plane_y"])
        assert np.linalg.norm(found - point) < 0.01


def test_solve_camera_needs_two_reference_tags(scene, intrinsics, site):
    corners = {i: c for i, c in detect_tags(scene).items() if i in (0, 21, 22)}
    with pytest.raises(ValueError):
        solve_camera(corners, intrinsics, site)


def test_node_tag_poses(scene, intrinsics, site):
    corners = detect_tags(scene)
    poses = tag_poses(corners, solve_camera(corners, intrinsics, site), site)
    for tag_id, center, yaw in [(21, [0.40, 0.58, 0.05], 20), (22, [0.80, 0.58, 0.05], -35)]:
        pose = poses[tag_id]
        assert np.linalg.norm(pose.position - center) < 0.01
        found_yaw = np.degrees(np.arctan2(pose.rotation[1, 0], pose.rotation[0, 0]))
        assert abs(found_yaw - yaw) < 2.0
        assert pose.rotation[2, 2] > 0.99  # tag nằm ngửa


def test_find_spot():
    rng = np.random.default_rng(0)
    off = rng.integers(60, 90, (480, 640, 3), dtype=np.uint8)
    on = off.copy()
    cv2.circle(on, (412, 205), 4, (90, 90, 255), -1)
    cv2.circle(on, (100, 400), 3, (90, 90, 150), -1)  # phản xạ yếu hơn ở chỗ khác
    u, v = find_spot(on, off)
    assert abs(u - 412) < 1 and abs(v - 205) < 1
    assert find_spot(off, off) is None


def test_charuco_calibration_recovers_focal_length(intrinsics, tmp_path):
    cfg = {"dictionary": "4X4_50", "squares_x": 6, "squares_y": 9, "square": 0.03, "marker": 0.022}
    board = charuco_board(cfg)
    w, h = 6 * 0.03, 9 * 0.03
    texture = board.generateImage((600, 900))
    corners = np.array([[0, 0, 0], [w, 0, 0], [w, h, 0], [0, h, 0]], float)
    rng = np.random.default_rng(1)
    images = []
    for _ in range(14):
        eye = [
            w / 2 + rng.uniform(-0.2, 0.2),
            h / 2 + rng.uniform(-0.2, 0.2),
            -rng.uniform(0.35, 0.6),
        ]
        target = [w / 2 + rng.uniform(-0.03, 0.03), h / 2 + rng.uniform(-0.03, 0.03), 0]
        canvas = np.full((SIZE[1], SIZE[0]), 128, np.uint8)
        render_quad(canvas, texture, corners, look_at(eye, target, intrinsics))
        images.append(canvas)
    found, rms, used = calibrate(images, board)
    assert used >= 10 and rms < 1.0
    assert abs(found.K[0, 0] - 900) / 900 < 0.03

    path = tmp_path / "camera.yaml"
    save_intrinsics(path, found, rms, used)
    assert np.allclose(load_intrinsics(path).K, found.K)
