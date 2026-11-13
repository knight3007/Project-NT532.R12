"""Ground-truth checks for the optional tabletop frame and laser geometry."""

import numpy as np
import pytest

from nt532.vision.localize import project
from nt532.vision.table_frame import TableFrame
from nt532.vision.types import Camera, Intrinsics, TagPose


def _pose(tag_id=22, xy=(0.4, 0.2), yaw_deg=90, tilt_deg=0):
    yaw, tilt = np.deg2rad([yaw_deg, tilt_deg])
    cz, sz, cx, sx = np.cos(yaw), np.sin(yaw), np.cos(tilt), np.sin(tilt)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    return TagPose(tag_id, np.array([*xy, 0.004]), rz @ rx)


def test_id22_origin_rotation_inverse_and_distances():
    frame = TableFrame.from_tag(_pose())
    a = np.array([0.4, 0.2, 0.0])
    b = np.array([0.5, 0.2, 0.0])
    assert frame.point(a) == pytest.approx([0, 0, 0], abs=1e-12)
    assert frame.point(b) == pytest.approx([0, -0.1, 0], abs=1e-12)
    assert frame.inverse(frame.point(b)) == pytest.approx(b, abs=1e-12)
    assert np.linalg.norm(frame.point(a) - frame.point(b)) == pytest.approx(
        np.linalg.norm(a - b))


def test_pose_noise_does_not_tilt_table_and_tag_orientation_is_transformed():
    frame = TableFrame.from_tag(_pose(tilt_deg=3))
    assert frame.rotation[:, 2] == pytest.approx([0, 0, 1])
    assert frame.point([0.7, 0.3, 0])[2] == pytest.approx(0)
    tag = frame.tag_pose(_pose(21, (0.5, 0.3), 15))
    assert tag.position == pytest.approx(frame.point([0.5, 0.3, 0.004]))
    assert tag.rotation == pytest.approx(frame.rotation.T @ _pose(21, (0.5, 0.3), 15).rotation)


def test_camera_projected_pixels_are_invariant_after_frame_conversion():
    intr = Intrinsics(np.array([[1000., 0, 640], [0, 1000., 360], [0, 0, 1]]),
                      np.zeros(5), (1280, 720))
    R = np.diag([1., -1., -1.])
    camera = Camera(intr, R, np.array([0., 0., 0.8]))
    frame = TableFrame.from_tag(_pose())
    point = np.array([0.6, 0.3, 0.])
    converted = frame.camera(camera)
    assert converted.position == pytest.approx(frame.point(camera.position))
    assert project(frame.point(point), converted) == pytest.approx(project(point, camera))
    assert converted.R @ frame.point(point) + converted.t == pytest.approx(camera.R @ point + camera.t)


@pytest.mark.parametrize("pose", [
    _pose(21), _pose(22, tilt_deg=25),
    TagPose(22, np.array([0.4, 0.2, 0.08]), np.eye(3)),
    TagPose(22, np.array([np.nan, 0, 0]), np.eye(3)),
])
def test_untrusted_origin_pose_is_blocked(pose):
    with pytest.raises(ValueError):
        TableFrame.from_tag(pose)
