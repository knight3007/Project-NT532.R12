import cv2
import numpy as np
import pytest
from conftest import NODES, SIZE, look_at, make_scene

from nt532.vision import Detection, TagPose, Vision, project

ON_BOARD = [[0.30, 0.80, 0.10], [0.85, 0.80, 0.20], [0.60, 0.80, 0.05]]


@pytest.fixture
def vision(site, intrinsics, scene) -> Vision:
    v = Vision(site, intrinsics, detector=lambda frame: [])
    v.commission([scene] * 3)
    return v


def test_board_points_are_in_view(true_camera):
    for point in ON_BOARD:
        u, v = project(point, true_camera)
        assert 0 < u < SIZE[0] and 0 < v < SIZE[1]


def test_commission_recovers_camera_and_nodes(vision, true_camera):
    state = vision.state
    assert np.linalg.norm(state.camera.position - true_camera.position) < 0.01
    assert state.reprojection_px < 1.0 and state.missing == []
    for name, tag_id in (("s1", 21), ("s2", 22)):
        assert np.linalg.norm(state.nodes[name].position - NODES[tag_id][0]) < 0.01
    assert vision.blockers("s1") == []


def test_blockers_before_commissioning(site, intrinsics):
    assert Vision(site, intrinsics).blockers("s1") == ["chưa commissioning"]


def test_check_same_scene_is_healthy(vision, scene):
    health = vision.check(scene)
    assert health.camera_shift_px < 1.0
    assert max(health.node_moved_m.values()) < 0.005
    assert health.stale == []


def test_check_flags_moved_node(vision, site, true_camera):
    moved = dict(NODES)
    moved[21] = ([0.45, 0.58, 0.05], 20)
    health = vision.check(make_scene(site, true_camera, moved))
    assert health.stale == ["s1"]
    assert abs(health.node_moved_m["s1"] - 0.05) < 0.01
    assert vision.blockers("s1") and vision.blockers("s2") == []


def test_check_flags_moved_camera(vision, site, intrinsics):
    bumped = look_at([0.62, 0.05, 0.80], [0.60, 0.70, 0.10], intrinsics)
    health = vision.check(make_scene(site, bumped))
    assert health.camera_shift_px > 3.0
    assert "camera" in health.stale
    assert vision.blockers() != []


def test_check_with_hidden_references_draws_no_conclusion(vision, site, true_camera):
    frame = make_scene(site, true_camera)
    for tag_id in (1, 2, 3):  # che ba tag tham chiếu
        x, y = site["tags"]["reference"]["positions"][tag_id]
        u, v = project([x, y, 0.0], true_camera)
        cv2.circle(frame, (int(u), int(v)), 60, 180, -1)
    health = vision.check(frame)
    assert health.camera_shift_px is None and "camera" not in health.stale


def test_commission_needs_reference_tags(site, intrinsics):
    frame = np.full((SIZE[1], SIZE[0]), 180, np.uint8)
    with pytest.raises(ValueError):
        Vision(site, intrinsics).commission([frame])


def test_targets_filters_by_board_and_sensor(vision, true_camera, scene):
    off_board = [0.60, 0.50, 0.0]  # trên mặt bàn: tia nhìn cắt mặt phẳng bảng dưới Z = 0
    world = [ON_BOARD[0], ON_BOARD[1], off_board]
    x0, y0, _, _ = vision.board_roi(scene.shape)
    crops = []

    def fake_detector(crop):
        crops.append(crop.shape)
        out = []
        for conf, point in zip((0.9, 0.8, 0.7), world):
            u, v = project(point, true_camera)
            out.append(Detection(u - x0, v - y0, 20, 20, conf))
        return out

    vision._detector = fake_detector
    found = vision.targets(scene)
    assert len(found) == 2 and crops[0][:2] != scene.shape[:2]
    for target, point in zip(found, ON_BOARD):
        assert np.linalg.norm(target.position - point) < 0.01
    near_s1 = vision.targets(scene, sensor="s1")
    assert [t.detection.conf for t in near_s1] == [0.9]
    with pytest.raises(ValueError):
        vision.targets(scene, sensor="s9")


def test_find_spot_on_board_only(vision, true_camera, scene):
    off = cv2.cvtColor(scene, cv2.COLOR_GRAY2BGR)
    on = off.copy()
    u, v = project(ON_BOARD[2], true_camera)
    cv2.circle(on, (round(u), round(v)), 4, (120, 120, 255), -1)
    ut, vt = project([0.60, 0.30, 0.0], true_camera)  # vệt sáng hơn trên mặt bàn, ngoài bảng
    cv2.circle(on, (round(ut), round(vt)), 6, (255, 255, 255), -1)
    spot = vision.find_spot(on, off)
    assert np.linalg.norm(spot - ON_BOARD[2]) < 0.01
    assert vision.find_spot(off, off) is None


def test_save_and_load(vision, site, intrinsics, tmp_path):
    path = vision.save(tmp_path / "commissioning.yaml")
    loaded = Vision(site, intrinsics)
    loaded.load(path)
    assert np.allclose(loaded.camera.R, vision.camera.R)
    assert np.allclose(loaded.nodes["s2"].position, vision.nodes["s2"].position)
    assert loaded.blockers("s2") == []


def test_tag_pose_to_world():
    yaw = np.radians(90)
    R = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    pose = TagPose(21, np.array([0.4, 0.5, 0.05]), R)
    assert np.allclose(pose.to_world([0.1, 0, 0.02]), [0.4, 0.6, 0.07])
