"""Regression on real pos01 evidence: ID 22 square-geometry gate and its diagnostics.

The fixture holds the AprilTag corners detected from the 15 real frames of
build-output/origin22-accuracy-evidence/pos01-20261114T053535Z (hashes inside) and the
intrinsics used on the Pi. Thresholds are never changed here; a corrected tag only passes
when its observed geometry is actually a flat 80 mm square on the mapped table.
"""

import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from nt532.vision.calibration import save_intrinsics
from nt532.vision.dynamic_mapping import estimate_reference_map, estimate_reference_maps
from nt532.vision.localize import project
from nt532.vision.table_frame import TableFrameError, analyze_table_frame, estimate_table_frame
from nt532.vision.tags import _square
from nt532.vision.types import Intrinsics, TagPose

FIXTURE = Path(__file__).with_name("data") / "origin22_pos01_corners.json"
SITE = {"tags": {"nodes": {"size": 0.08, "ids": {"s1": 21, "s2": 22}}},
        "vision": {"min_tag_rate": 0.6}}


@pytest.fixture(scope="module")
def evidence():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cal = data["intrinsics"]
    intr = Intrinsics(np.array(cal["camera_matrix"], float), np.array(cal["dist_coeffs"], float),
                      tuple(cal["image_size"]))
    observations = [{int(k): np.array(v, float) for k, v in frame["corners"].items()}
                    for frame in data["frames"]]
    mapping = estimate_reference_maps(observations, intr, 0.08)
    return intr, observations, mapping


def _copy(observations):
    return [{k: v.copy() for k, v in frame.items()} for frame in observations]


def test_evidence_reference_id2_mapping_passes(evidence):
    _, observations, mapping = evidence
    assert len(observations) == 15
    assert mapping.frames_used == 15
    assert mapping.metric_error_m < 0.012
    assert mapping.camera.position == pytest.approx([0.16917, -0.52037, 0.73571], abs=2e-5)
    assert sorted(mapping.references) == [0, 1, 2, 3]


def test_evidence_id22_detected_in_every_frame(evidence):
    _, observations, _ = evidence
    assert all({0, 1, 2, 3, 21, 22} <= frame.keys() for frame in observations)


def test_evidence_id22_square_geometry_blocked_with_structured_diagnostics(evidence):
    intr, observations, mapping = evidence
    with pytest.raises(TableFrameError, match="^ID 22 square geometry is unreliable$") as info:
        estimate_table_frame(observations, intr, SITE, 0.08, mapping.camera,
                             references=mapping.references)
    error = info.value
    assert isinstance(error, ValueError)  # existing callers catch ValueError
    d = error.diagnostics
    assert error.code == d["reason_code"] == "ID22_SQUARE_GEOMETRY"
    assert d["status"] == "BLOCKED" and d["tag_id"] == 22
    assert d["failing_frame"] == 1
    assert d["failing_frames"] == list(range(1, 16))
    assert d["thresholds"]["edge_mm"] == 5.0 and d["thresholds"]["diagonal_mm"] == 7.0
    assert d["thresholds"]["table_reprojection_px"] == 3.0
    assert d["units"]["edge_error"] == "mm" and d["units"]["reprojection"] == "px"
    first = d["frames"][0]
    assert first["frame"] == 1 and first["checks"]["square_geometry"] is False
    assert first["worst_edge"] == "BR-BL"
    assert first["max_edge_error_mm"] == pytest.approx(5.84, abs=0.05)
    assert first["max_diagonal_error_mm"] == pytest.approx(7.35, abs=0.05)
    assert first["table_reprojection_px"]["max"] > 3.0
    summary = d["summary"]
    assert summary["systematic"] is True
    assert summary["max_edge_error_mm"]["std"] < 0.1
    # ID 22 lies in the image periphery, far beyond every reference tag, and outside the hull.
    assert d["image_radius_ratio"] > 0.8
    assert d["reference_image_radius_ratio_max"] < 0.6
    assert d["reference_hull"]["inside"] is False
    assert d["reference_hull"]["signed_distance_m"] < -0.25
    # PnP and the table-plane estimate are reported separately and disagree by centimetres.
    assert d["pnp_pose"]["position_m"][2] == pytest.approx(0.0444, abs=0.001)
    assert d["pose_disagreement_mm"]["xy"] == pytest.approx(30.7, abs=0.5)
    assert d["pnp_pose"]["coordinate_frame"] == d["table_pose"]["coordinate_frame"] == "tag_2_table"


def test_evidence_frame_with_true_flat_square_passes_unchanged_thresholds(evidence):
    """Same cameras, same thresholds: a geometrically valid ID 22 must still be accepted."""
    intr, observations, mapping = evidence
    fixed = _copy(observations)
    pose = TagPose(22, np.array([-0.22, -0.20, 0.0]), np.eye(3))
    for index, frame in enumerate(fixed):
        camera = (mapping.camera if index == len(fixed) - 1 else
                  estimate_reference_map(frame, intr, 0.08).camera)
        frame[22] = np.array([project(pose.to_world(p), camera) for p in _square(0.08)])
    result, d = analyze_table_frame(fixed, intr, SITE, 0.08, mapping.camera,
                                    references=mapping.references)
    assert d["status"] == "PASS" and d["reason_code"] is None
    assert result.origin == pytest.approx([-0.22, -0.20, 0.0], abs=0.001)
    assert d["frames"][-1]["max_edge_error_mm"] < 1.0


def test_evidence_missing_id22_stays_blocked(evidence):
    intr, observations, mapping = evidence
    missing = _copy(observations)
    del missing[-1][22]
    with pytest.raises(TableFrameError) as info:
        estimate_table_frame(missing, intr, SITE, 0.08, mapping.camera)
    assert info.value.code == "ID22_MISSING"
    assert str(info.value) == "ID 22 missing from latest frame"


def test_evidence_tiny_id22_stays_blocked(evidence):
    intr, observations, mapping = evidence
    tiny = _copy(observations)
    for frame in tiny:
        center = frame[22].mean(axis=0)
        frame[22] = center + (frame[22] - center) * 0.1
    with pytest.raises(TableFrameError) as info:
        estimate_table_frame(tiny, intr, SITE, 0.08, mapping.camera)
    assert info.value.code == "ID22_TOO_SMALL"


def test_tilted_tag_keeps_untrusted_reason_code():
    from test_dynamic_mapping import LAYOUTS, corners_for
    from test_dynamic_mapping import camera as scene_camera

    intr = Intrinsics(np.array([[1050., 0, 640], [0, 1030., 360], [0, 0, 1.]]),
                      np.zeros(5), (1280, 720))
    cam = scene_camera(intr)
    corners = corners_for(LAYOUTS["60x40"], cam)
    angle = np.deg2rad(25)
    tilted = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)],
                       [0, np.sin(angle), np.cos(angle)]])
    corners[22] = np.array([project(p, cam) for p in
                            (_square(0.08) @ tilted.T + [0.45, 0.08, 0])])
    _, d = analyze_table_frame([corners], intr, SITE, 0.08, cam)
    assert d["status"] == "BLOCKED"
    assert d["reason_code"] in {"ID22_SQUARE_GEOMETRY", "ID22_REPROJECTION"}


def _cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "dynamic_map.py"
    spec = importlib.util.spec_from_file_location("origin22_cli_v2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_blocked_origin_writes_diagnostics_and_overlay(tmp_path, capsys):
    """Old consumers keep `status`/`reason`; new keys are additive."""
    from test_origin22 import _scene

    intr = Intrinsics(np.array([[1050., 0, 640], [0, 1030., 360], [0, 0, 1.]]),
                      np.zeros(5), (1280, 720))
    image = tmp_path / "missing.png"
    calibration = tmp_path / "camera.yaml"
    debug = tmp_path / "debug.png"
    cv2.imwrite(str(image), _scene(intr, include22=False))
    save_intrinsics(calibration, intr, 0.1, 10)
    assert _cli().main(["--image", str(image), "--intrinsics", str(calibration),
                        "--world-origin", "22", "--debug", str(debug)]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "BLOCKED"
    assert data["reason"] == "ID 22 missing from latest frame"
    assert data["reason_code"] == "ID22_MISSING"
    assert data["diagnostics"]["operational_origin"]["reason_code"] == "ID22_MISSING"
    assert "nodes" not in data and "camera" not in data
    assert debug.exists() and debug.stat().st_size > 0


def test_cli_pass_reports_both_id22_estimators(tmp_path, capsys):
    from test_origin22 import _scene

    intr = Intrinsics(np.array([[1050., 0, 640], [0, 1030., 360], [0, 0, 1.]]),
                      np.zeros(5), (1280, 720))
    image = tmp_path / "scene.png"
    calibration = tmp_path / "camera.yaml"
    cv2.imwrite(str(image), _scene(intr))
    save_intrinsics(calibration, intr, 0.1, 10)
    assert _cli().main(["--image", str(image), "--intrinsics", str(calibration),
                        "--world-origin", "22"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "PASS" and data["nodes"]["s2"] == [0.0, 0.0, 0.0]
    origin = data["diagnostics"]["operational_origin"]
    assert origin["status"] == "PASS" and origin["reason_code"] is None
    assert set(origin["pose_disagreement_mm"]) == {"xy", "z", "3d"}
    assert origin["table_pose"]["position_m"][2] == 0.0
