"""Offline laser calibration and targeting with a separate emitter origin."""

import numpy as np
import pytest

from nt532.orchestrator.laser_geometry import LaserMount, table_hit
from nt532.vision.table_frame import TableFrame
from nt532.vision.types import TagPose


def _mount(tag_id=22, xy=(0.4, 0.1), emitter_offset=(0, 0, 0)):
    tag = TagPose(tag_id, np.array([*xy, 0.]), np.eye(3))
    return LaserMount(tag, np.array([0., 0., 0.1]), np.array(emitter_offset, float),
                      home_yaw_deg=0, home_elevation_deg=0,
                      pan_sign=1, tilt_sign=1, pan_gain=1, tilt_gain=1,
                      pan_limits_deg=(-70, 70), tilt_limits_deg=(-90, 40))


def test_vertical_ray_hits_only_directly_below_emitter():
    emitter = np.array([0.4, 0.1, 0.15])
    hit = table_hit(emitter, [0, 0, -1])
    assert hit == pytest.approx([0.4, 0.1, 0])
    assert np.linalg.norm(hit[:2] - [0.5, 0.1]) == pytest.approx(0.1)
    assert table_hit(emitter, [0, 1, 0]) is None


def test_vertical_calibration_respects_emitter_offset_and_cannot_hit_two_tags():
    mount = _mount(emitter_offset=(0.03, 0, 0))
    assert mount.vertical_calibration([0.4, 0.1, 0], pan_deg=0, tilt_deg=-90).status == "UNREACHABLE"
    assert mount.vertical_calibration([0.43, 0.1, 0], pan_deg=0, tilt_deg=-90).status == "PASS"
    assert mount.vertical_calibration([0.5, 0.1, 0], pan_deg=0, tilt_deg=-90).status == "UNREACHABLE"


def test_targeting_uses_emitter_position_and_rejects_limits():
    mount = _mount(emitter_offset=(0.03, 0, 0))
    target = np.array([0.5, 0.4, 0.])
    result = mount.target(target)
    assert result.status == "PASS"
    emitter, ray = mount.ray(*result.command_deg)
    assert np.linalg.norm(np.cross(target - emitter, ray)) < 1e-4
    assert mount.target([0.4, -0.5, 0]).status == "UNREACHABLE"


def test_commands_are_invariant_under_id22_frame_change():
    mount = _mount(21, (0.2, 0.1), emitter_offset=(0.02, 0, 0))
    target = np.array([0.5, 0.4, 0.])
    before = mount.target(target)
    origin = TagPose(22, np.array([0.4, 0.1, 0]),
                     np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]]))
    frame = TableFrame.from_tag(origin)
    after = mount.in_frame(frame).target(frame.point(target))
    assert before.status == after.status == "PASS"
    assert after.command_deg == pytest.approx(before.command_deg, abs=1e-6)


def test_vertical_request_outside_mechanical_tilt_is_unreachable():
    mount = _mount()
    mount = LaserMount(mount.tag_pose, mount.tag_to_pivot, mount.emitter_offset,
                       0, 0, 1, 1, 1, 1, (-70, 70), (-35, 40))
    assert mount.vertical_calibration([0.4, 0.1, 0], 0, -90).status == "UNREACHABLE"
