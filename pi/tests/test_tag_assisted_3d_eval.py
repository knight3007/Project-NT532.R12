"""Offline contract tests for the tag-assisted screen-point evaluator."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tag_assisted_3d_eval.py"
spec = importlib.util.spec_from_file_location("tag_assisted_3d_eval", SCRIPT)
eval3d = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = eval3d
spec.loader.exec_module(eval3d)


@pytest.mark.parametrize("z", [0, 0.05, 0.10, 0.15, 0.20, 0.30])
@pytest.mark.parametrize("yaw,lean", [(0, 0), (20, 12), (-25, -10)])
def test_clean_screen_point_recovers_xyz_at_every_height(z, yaw, lean):
    case = eval3d.make_case((0.30, 0.23, z), yaw, lean)
    row = eval3d.run_case(case, "B", "s1", "clean")
    assert row["status"] in ("PASS", "OUT_OF_RANGE", "UNKNOWN"), row
    if row["status"] == "UNKNOWN":
        assert row["reason"] == "tag_pose_ambiguous_or_poor"
        return
    assert row["xyz_error_cm"] < 0.5, row
    if row["status"] == "PASS":
        assert row["ray_miss_cm"] < 3, row


def test_floor_projection_is_distinct_from_screen_xyz():
    case = eval3d.make_case((0.30, 0.23, 0.20), 15, 10)
    floor = eval3d.run_case(case, "A", "s1", "clean")
    screen = eval3d.run_case(case, "B", "s1", "clean")
    assert floor["estimated_z_m"] == 0
    assert screen["estimated_z_m"] > 0.15
    assert floor["xyz_error_cm"] > 10


@pytest.mark.parametrize("z", [0, 0.05, 0.10, 0.15, 0.20, 0.30])
def test_each_height_has_at_least_one_accepted_screen_pose(z):
    rows = [
        eval3d.run_case(eval3d.make_case((0.30, 0.23, z), yaw, lean), "B", "s1")
        for yaw, lean in ((0, 0), (20, 12), (-25, -10))
    ]
    assert any(np.isfinite(row["xyz_error_cm"]) and row["xyz_error_cm"] < 0.5 for row in rows), rows


@pytest.mark.parametrize(
    "scenario,reason",
    [
        ("tag_missing", "tag_23_missing"),
        ("tag_occluded", "tag_23_missing"),
        ("offset_unknown", "screen_offset_unknown"),
        ("outside_screen", "pixel_outside_screen"),
        ("near_parallel", "ray_near_parallel"),
    ],
)
def test_invalid_screen_geometry_fails_closed(scenario, reason):
    case = eval3d.make_case((0.30, 0.23, 0.15), 0, 0)
    row = eval3d.run_case(case, "B", "s1", scenario)
    assert row["status"] in ("UNKNOWN", "BLOCKED")
    assert row["reason"] == reason
    assert np.isnan(row["estimated_z_m"])


def test_oracle_is_used_only_in_evaluation():
    case = eval3d.make_case((0.30, 0.23, 0.20), 0, 0)
    observed = eval3d.observe(case, "clean")
    estimated = eval3d.tag_assisted_xyz(observed)
    assert estimated.status == "PASS"
    assert np.linalg.norm(estimated.xyz - case.target) < 0.005
    assert not hasattr(observed, "target")


def test_real_flame_on_same_ray_is_not_claimed_as_true_xyz():
    case = eval3d.make_case((0.30, 0.23, 0.20), 0, 0)
    row = eval3d.run_case(case, "B", "s1", "free_flame_off_plane")
    assert row["status"] == "UNKNOWN"
    assert row["reason"] == "real_flame_depth_unobservable"


def test_unreachable_target_is_reported_for_each_node():
    case = eval3d.make_case((0.30, 0.23, 0.30), 0, 0)
    for node in ("s1", "s2"):
        row = eval3d.run_case(case, "B", node, "tight_limits")
        assert row["status"] == "OUT_OF_RANGE"
        assert row["reason"] == "command_or_pwm_limit"


def test_camera_pose_comes_from_dynamic_mapping():
    observed = eval3d.observe(eval3d.make_case((0.30, 0.23, 0.15), 0, 0), "clean")
    assert observed.mapping.frames_used == 1
    assert set(observed.mapping.references) == {0, 1, 2, 3}
    assert observed.mapping.metric_error_m < 0.012


def test_rendered_tag_23_is_detectable_beside_screen():
    case = eval3d.make_case((0.30, 0.23, 0.15), 0, 0)
    result = eval3d.render_tag_trial(case)
    assert result["detected"]
    assert result["tag_edge_px"] > 20


@pytest.mark.parametrize("noise", ["tag_noise_0p5px", "tag_noise_1p5px"])
def test_noisy_tag_pose_never_claims_a_large_error_as_valid(noise):
    for z in eval3d.HEIGHTS_M:
        for name, (x, y) in eval3d.POSITIONS_XY.items():
            case = eval3d.make_case((x, y, z), 20, 12)
            row = eval3d.run_case(case, "B", "s1", noise, seed=int(1000 * z) + len(name))
            if np.isfinite(row["xyz_error_cm"]):
                assert row["xyz_error_cm"] < 3, row
