"""Ground-truth checks for the optional tabletop frame and laser geometry."""

import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from test_dynamic_mapping import LAYOUTS
from test_dynamic_mapping import camera as scene_camera

from nt532.sim.render import render_quad, tag_quad
from nt532.vision.calibration import save_intrinsics
from nt532.vision.localize import project
from nt532.vision.table_frame import TableFrame, estimate_table_frame
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


def _scene(intrinsics, *, tag22=(0.45, 0.08), include22=True):
    cam = scene_camera(intrinsics)
    image = np.full((720, 1280), 180, np.uint8)
    for tag_id, xy in LAYOUTS["60x40"].items():
        render_quad(image, *tag_quad(tag_id, 0.08, [*xy, 0]), cam)
    render_quad(image, *tag_quad(21, 0.08, [0.18, 0.08, 0], yaw_deg=15), cam)
    if include22:
        render_quad(image, *tag_quad(22, 0.08, [*tag22, 0], yaw_deg=90), cam)
    return image


def _cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "dynamic_map.py"
    spec = importlib.util.spec_from_file_location("origin22_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_id22_converts_every_coordinate_and_debug(tmp_path, capsys):
    intr = Intrinsics(np.array([[1050., 0, 640], [0, 1030., 360], [0, 0, 1.]]),
                      np.zeros(5), (1280, 720))
    image = tmp_path / "scene.png"
    calibration = tmp_path / "camera.yaml"
    debug = tmp_path / "debug.png"
    cv2.imwrite(str(image), _scene(intr))
    save_intrinsics(calibration, intr, 0.1, 10)
    uv = project(np.array([0.3, 0.2, 0.]), scene_camera(intr))
    args = ["--image", str(image), "--intrinsics", str(calibration),
            "--world-origin", "22", "--pixel", f"{uv[0]},{uv[1]}", "--debug", str(debug)]
    assert _cli().main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "PASS"
    assert data["anchor_id"] == 2 and data["operational_origin_id"] == 22
    assert data["coordinate_frame"] == "tag_22_table"
    assert data["nodes"]["s2"] == pytest.approx([0, 0, 0], abs=0.01)
    assert data["nodes"]["s1"] == pytest.approx([0, 0.27, 0], abs=0.015)
    assert data["pixel"]["xyz"] == pytest.approx([0.12, 0.15, 0], abs=0.015)
    converted = Camera(intr, np.array(data["camera"]["R"]), np.array(data["camera"]["t"]))
    assert project(np.array(data["pixel"]["xyz"]), converted) == pytest.approx(uv, abs=0.05)
    assert debug.exists() and debug.stat().st_size > 0


def test_cli_id22_missing_tag_blocks_without_coordinates(tmp_path, capsys):
    intr = Intrinsics(np.array([[1050., 0, 640], [0, 1030., 360], [0, 0, 1.]]),
                      np.zeros(5), (1280, 720))
    image = tmp_path / "missing.png"
    calibration = tmp_path / "camera.yaml"
    cv2.imwrite(str(image), _scene(intr, include22=False))
    save_intrinsics(calibration, intr, 0.1, 10)
    assert _cli().main(["--image", str(image), "--intrinsics", str(calibration),
                        "--world-origin", "22"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "BLOCKED" and "22" in data["reason"]
    assert "camera" not in data and "references" not in data and "nodes" not in data


def test_operational_frame_rejects_batch_drift():
    intr = Intrinsics(np.array([[1050., 0, 640], [0, 1030., 360], [0, 0, 1.]]),
                      np.zeros(5), (1280, 720))
    from nt532.vision.tags import detect_tags
    frames = [detect_tags(_scene(intr, tag22=xy)) for xy in
              ((0.45, 0.08), (0.45, 0.08), (0.53, 0.08))]
    with pytest.raises(ValueError, match="unstable"):
        estimate_table_frame(frames, intr, {"tags": {"nodes": {"size": 0.08,
            "ids": {"s1": 21, "s2": 22}}}}, 0.08, scene_camera(intr))
