"""Regression checks for the isolated A/B aiming evaluation harness."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from nt532.orchestrator import aiming
from nt532.vision.dynamic_mapping import DynamicMap
from nt532.vision.localize import pixel_ray, project
from nt532.vision.tags import _square, tag_poses

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "aiming_geometry_audit.py"
SPEC = importlib.util.spec_from_file_location("aiming_geometry_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


@pytest.mark.parametrize("mount", ["horizontal", "vertical"])
@pytest.mark.parametrize("node", ["s1", "s2"])
def test_explicit_tag_offset_places_pivot_ten_centimetres_above_tag(mount, node):
    rig = audit.make_rig(mount, node)
    assert np.linalg.norm(rig.true_offset) == pytest.approx(0.10)
    assert rig.true_pivot == pytest.approx(rig.pose.position + [0, 0, 0.10])
    assert rig.estimated_pivot == pytest.approx(rig.true_pivot)


def test_vertical_tag_wrong_axis_moves_pivot_and_its_y_axis_has_no_heading():
    rig = audit.make_rig("vertical", "s1", "wrong_vertical_offset")
    assert np.linalg.norm(rig.estimated_pivot - rig.true_pivot) > 0.14
    assert np.linalg.norm(rig.pose.rotation[:2, 1]) < 1e-12
    assert audit.make_rig("vertical", "s1").assumed_yaw_deg == pytest.approx(-35)
    assert aiming.heading_yaw_deg(rig.pose) != pytest.approx(rig.true_yaw_deg)


def test_vertical_tag_pnp_pose_still_transforms_the_offset():
    rig, camera = audit.make_rig("vertical", "s1"), audit.make_camera()
    corners = np.array([project(rig.pose.to_world(p), camera) for p in _square(0.08)])
    solved = tag_poses({21: corners}, camera, audit.site_for(rig))[21]
    assert solved.position == pytest.approx(rig.pose.position, abs=1e-6)
    assert solved.to_world(rig.true_offset) == pytest.approx(rig.true_pivot, abs=1e-6)


def test_symmetric_home_headings_and_tilt_home_offset_are_explicit():
    left = audit.make_rig("horizontal", "s1")
    right = audit.make_rig("horizontal", "s2")
    assert left.true_yaw_deg == pytest.approx(-right.true_yaw_deg)
    assert audit.ray_for_command(left, 0, 0)[:2] == pytest.approx(
        -np.array([np.sin(np.radians(left.true_yaw_deg)),
                   -np.cos(np.radians(left.true_yaw_deg))])
    )
    elevated = audit.make_rig("horizontal", "s1", "tilt_home_plus_5")
    assert audit.ray_for_command(elevated, 0, 0)[2] == pytest.approx(np.sin(np.radians(5)))


def test_same_pixel_has_many_depths_but_one_floor_intersection():
    cam = audit.make_camera()
    origin, direction = pixel_ray((640, 410), cam)
    near, far = origin + 0.8 * direction, origin + 1.1 * direction
    assert near[2] != pytest.approx(far[2])
    assert project(near, cam) == pytest.approx(project(far, cam), abs=1e-7)
    floor = DynamicMap(cam, {}, 0, 0, 0).table_point((640, 410))
    assert floor[2] == pytest.approx(0)
    assert floor != pytest.approx(near)
    assert floor != pytest.approx(far)


def test_floor_projection_hits_floor_but_misses_elevated_target():
    floor = audit.run_case("horizontal", "s1", (0.72, 0.12, 0), "nominal", "A", seed=1)
    high = audit.run_case("horizontal", "s1", (0.72, 0.12, 0.20), "nominal", "A", seed=1)
    assert floor["status"] == "PASS"
    assert floor["ray_miss_cm"] < 0.01
    assert high["status"] == "FAIL"
    assert high["ray_miss_cm"] > 3.0
    assert high["estimated_z_m"] == pytest.approx(0)


def test_oracle_uses_true_xyz_but_still_obeys_reach_limits():
    hit = audit.run_case("horizontal", "s1", (0.60, 0.60, 0.20), "nominal", "C", seed=1)
    assert hit["status"] == "PASS"
    assert hit["ray_miss_cm"] < 0.01
    blocked = audit.run_case("horizontal", "s1", (0.60, 0.12, 0), "nominal", "C", seed=1)
    assert blocked["status"] in ("OUT_OF_RANGE", "OUT_OF_VIEW")


def test_background_pixel_alignment_cannot_certify_3d_hit():
    row = audit.run_case("horizontal", "s1", (0.72, 0.12, 0.05), "nominal", "B", seed=2)
    assert row["status"] == "UNKNOWN"
    assert row["pixel_residual_px"] < 2.0
    assert row["ray_miss_cm"] > 3.0
    assert row["verified_3d_hit"] is False


def test_target_surface_feedback_can_certify_a_floor_hit():
    row = audit.run_case("horizontal", "s1", (0.72, 0.12, 0), "nominal", "B", seed=2)
    assert row["status"] == "PASS"
    assert row["spot_surface"] == "target"
    assert row["verified_3d_hit"] is True


def test_lost_spot_stops_feedback_without_claiming_convergence():
    row = audit.run_case("horizontal", "s1", (0.72, 0.12, 0), "spot_unobservable", "B")
    assert row["status"] == "LOST_SPOT"
    assert row["converged"] is False


def test_missing_node_and_reference_fail_closed():
    node = audit.run_case("horizontal", "s1", (0.60, 0.60, 0.10), "missing_s1", "A")
    refs = audit.run_case("horizontal", "s2", (0.60, 0.60, 0.10), "untrusted_reference", "A")
    assert node["status"] == "BLOCKED"
    assert refs["status"] == "BLOCKED"


def test_servo_sign_reversal_is_not_assumed_from_pwm_range():
    nominal = audit.make_rig("horizontal", "s1")
    reversed_pan = audit.make_rig("horizontal", "s1", "pan_sign_reversed")
    assert audit.ray_for_command(nominal, 15, 0) != pytest.approx(
        audit.ray_for_command(reversed_pan, 15, 0)
    )


def test_wrong_vertical_offset_degrades_even_oracle_3d_targeting():
    nominal = audit.run_case("vertical", "s1", (0.60, 0.48, 0.20), "nominal", "C")
    wrong = audit.run_case("vertical", "s1", (0.60, 0.48, 0.20), "wrong_vertical_offset", "C")
    assert nominal["status"] == "PASS"
    assert wrong["status"] == "FAIL"
    assert wrong["ray_miss_cm"] > 3


def test_uncalibrated_servo_gain_degrades_oracle_targeting():
    nominal = audit.run_case("horizontal", "s1", (0.60, 0.48, 0.20), "nominal", "C")
    gain = audit.run_case("horizontal", "s1", (0.60, 0.48, 0.20), "servo_gain_plus_10pct", "C")
    assert nominal["ray_miss_cm"] < 0.01
    assert gain["ray_miss_cm"] > 0.5


def test_synthetic_metric_depth_error_is_separate_from_oracle():
    point = (0.72, 0.12, 0.20)
    oracle = audit.run_case("horizontal", "s1", point, "nominal", "C")
    exact = audit.run_case("horizontal", "s1", point, "nominal", "D", depth_error_m=0)
    biased = audit.run_case("horizontal", "s1", point, "nominal", "D", depth_error_m=0.10)
    assert exact["ray_miss_cm"] == pytest.approx(oracle["ray_miss_cm"], abs=1e-8)
    assert biased["ray_miss_cm"] > exact["ray_miss_cm"] + 0.5
    assert biased["requires_depth"] is True
