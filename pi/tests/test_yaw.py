"""Yaw của node: hướng pan = 0 theo tag đế hoặc theo site.yaml, góc ngắm và tầm với tương đối theo yaw."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from nt532.orchestrator import aiming
from nt532.vision.types import TagPose

HEAD = (0.6, 0.8)  # s1 ở góc trước nhìn chéo vào trong
YAW = math.degrees(math.atan2(-HEAD[0], HEAD[1]))  # -36,87


def rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def pose(deg: float, pos=(0, 0, 0.0)) -> TagPose:
    return TagPose(1, np.array(pos, float), rot_z(deg))


def site(**node) -> dict:
    return {"board": {"plane_y": 1.0}, "actuator": {"nodes": {"s1": node}}}


def test_yaw_zero_reproduces_old_angles():
    pivot, target = (0.3, 0.55, 0.33), (0.1, 0.8, 0.4)
    old = (math.degrees(math.atan2(-(target[0] - pivot[0]), target[1] - pivot[1])),
           math.degrees(math.atan2(0.07, math.hypot(-0.2, 0.25))))
    assert aiming.angles(pivot, target) == pytest.approx(old, abs=0)
    assert aiming.angles(pivot, target, 0.0) == aiming.angles(pivot, target)
    assert aiming.heading_yaw_deg(pose(0)) == pytest.approx(0, abs=1e-9)  # tag không xoay: hướng +Y


def test_yaw_read_from_rotated_tag():
    assert aiming.heading_yaw_deg(pose(30)) == pytest.approx(30)
    assert aiming.heading_yaw_deg(pose(-36.87)) == pytest.approx(-36.87)
    assert aiming.heading_yaw_deg(pose(170)) == pytest.approx(170)
    assert aiming.node_yaw(site(), "s1", pose(-36.87)) == pytest.approx(-36.87)
    assert aiming.node_yaw(site(yaw_deg=None), "s1", pose(20)) == pytest.approx(20)
    assert aiming.node_yaw(site(), "s1") == 0.0 and aiming.node_yaw({}, "s9") == 0.0


def test_config_override_wins():
    assert aiming.node_yaw(site(yaw_deg=-36.87), "s1", pose(10)) == pytest.approx(-36.87)
    assert aiming.node_yaw(site(yaw_deg=0), "s1", pose(10)) == 0.0  # 0 là giá trị đặt, không phải null


def test_pan_is_relative_to_heading_and_keeps_sign():
    pivot = (0, 0, 0.08)
    ahead = (0.6, 0.8, 0.08)
    assert aiming.angles(pivot, ahead, YAW)[0] == pytest.approx(0, abs=1e-9)
    # bên trái node (ngược chiều kim đồng hồ) là pan dương
    left = (0.6 * math.cos(0.3) - 0.8 * math.sin(0.3), 0.6 * math.sin(0.3) + 0.8 * math.cos(0.3), 0.08)
    assert aiming.angles(pivot, left, YAW)[0] == pytest.approx(math.degrees(0.3))
    # yaw 0: bên trái là -X, pan dương
    assert aiming.angles(pivot, (-1, 1, 0.08))[0] == pytest.approx(45)
    # quấn về (-180, 180]
    assert -180 < aiming.angles(pivot, (0, -1, 0.08), 170)[0] <= 180


def test_front_corner_node_reaches_floor_and_far_wall_at_default_limits():
    s = {"board": {"plane_y": 1.2}, "actuator": {"nodes": {"s1": {}, "s2": {}}}}
    p1, p2 = np.array([0, 0, 0.3]), np.array([1.2, 0, 0.3])
    y1, y2 = YAW, -YAW
    floor = (0.35, 0.50, 0.0)  # trước mặt, trên nền
    wall = (0.6, 1.2, 0.5)  # bảng đứng phía xa
    assert aiming.reachable_point(s, "s1", p1, floor, y1)
    assert aiming.reachable_point(s, "s1", p1, wall, y1)
    assert aiming.reachable_point(s, "s2", p2, (0.85, 0.50, 0.0), y2)
    assert aiming.reachable_point(s, "s2", p2, wall, y2)
    assert aiming.reachable(s, "s1", p1, 0.6, 0.5, None, y1)
    # nhìn chéo vào trong thì với được bia sát cạnh bên mà nhìn thẳng +Y (pan > 70) thì không
    side = (1.0, 0.15, 0.0)
    assert not aiming.reachable_point(s, "s1", p1, side, 0.0)
    assert aiming.reachable_point(s, "s1", p1, side, y1)


def test_machine_reach_uses_node_yaw():
    from nt532.orchestrator.machine import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o.site, o.vision = site(), SimpleNamespace(nodes={"s1": pose(YAW, (0, 0, 0.22))})
    assert o._yaw("s1") == pytest.approx(YAW)
    assert o._yaw("s2") == 0.0
    o.site = site(yaw_deg=5.0)
    assert o._yaw("s1") == 5.0
