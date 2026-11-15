"""Offline guards for repeatable live-camera evidence; no RTSP or YOLO model needed."""

import importlib
import importlib.util
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from nt532.vision.types import Detection, Intrinsics


def _script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "origin22_live.py"
    spec = importlib.util.spec_from_file_location("origin22_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_saves_exactly_fifteen_unchanged_frames_and_refuses_overwrite(tmp_path):
    live = _script()
    frames = [np.full((720, 1280, 3), index, np.uint8) for index in range(15)]
    out = tmp_path / "capture"
    manifest = live.save_capture(frames, out, "rtsp://127.0.0.1:8554/cam/stream")
    assert len(manifest["frames"]) == 15
    assert len(list((out / "frames").glob("frame-*.png"))) == 15
    assert live.validate_capture(out)["frame_count"] == 15
    with pytest.raises(FileExistsError):
        live.save_capture(frames, out, "rtsp://127.0.0.1:8554/cam/stream")
    with pytest.raises(ValueError, match="15"):
        live.save_capture(frames[:14], tmp_path / "short", "rtsp://127.0.0.1:8554/cam/stream")


def test_capture_validation_detects_changed_frame(tmp_path):
    live = _script()
    out = tmp_path / "capture"
    live.save_capture([np.zeros((720, 1280, 3), np.uint8) for _ in range(15)], out, "test")
    (out / "frames" / "frame-001.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        live.validate_capture(out)


def test_capture_validation_rejects_extra_image_that_mapping_would_read(tmp_path):
    live = _script()
    out = tmp_path / "capture"
    live.save_capture([np.zeros((720, 1280, 3), np.uint8) for _ in range(15)], out, "test")
    (out / "frames" / "extra.jpg").write_bytes(b"extra")
    with pytest.raises(ValueError, match="exactly 15"):
        live.validate_capture(out)


def test_capture_requires_all_six_tags_clear_across_batch(tmp_path, monkeypatch):
    live = _script()
    corners = np.array([[0., 0.], [20., 0.], [20., 20.], [0., 20.]])
    monkeypatch.setattr(live, "detect_tags", lambda frame: {
        tag_id: corners for tag_id in (0, 1, 2, 3, 21, 22)})
    frames = [np.zeros((720, 1280, 3), np.uint8) for _ in range(15)]
    manifest = live.save_capture(frames, tmp_path / "ready", "test")
    assert manifest["capture_quality"] == "READY"
    assert manifest["frames"][-1]["pixel_quality"]["22"]["min_edge_px"] == 20
    monkeypatch.setattr(live, "detect_tags", lambda frame: {
        tag_id: corners for tag_id in (0, 1, 2, 3, 21)})
    assert live.save_capture(frames, tmp_path / "review", "test")["capture_quality"] == "REVIEW"


def _maps():
    camera2 = {"position": [0, 0, 1], "R": [[1, 0, 0], [0, -1, 0], [0, 0, -1]],
               "t": [0, 0, 1]}
    camera22 = {"position": [-0.4, 0, 1], "R": camera2["R"], "t": [0.4, 0, 1]}
    target = {"surface": "floor", "confidence": 0.9, "uv": [940, 260],
              "bbox_xywh": [940, 260, 20, 20], "anchor_uv": [940, 260]}
    common = {"status": "PASS", "anchor_id": 2, "target_anchor": "center", "unit": "m",
              "quality": {"metric_error_m": 0.001, "joint_reprojection_px": 0.5},
              "diagnostics": {"detection_rate": {"22": 1.0}, "rejected_tags": {}}}
    origin2 = {**common, "operational_origin_id": 2, "coordinate_frame": "tag_2_table",
               "references": {"2": [0, 0, 0], "0": [0.5, 0, 0],
                              "1": [0.1, 0, 0], "3": [0.3, 0, 0]},
               "nodes": {"s1": [0.2, 0, 0], "s2": [0.4, 0, 0]}, "camera": camera2,
               "fire_targets": [{**target, "xyz": [0.3, 0.1, 0]}]}
    origin22 = {**common, "operational_origin_id": 22, "coordinate_frame": "tag_22_table",
                "references": {"2": [-0.4, 0, 0], "0": [0.1, 0, 0],
                               "1": [-0.3, 0, 0], "3": [-0.1, 0, 0]},
                "nodes": {"s1": [-0.2, 0, 0], "s2": [0, 0, 0]}, "camera": camera22,
                "fire_targets": [{**target, "xyz": [-0.1, 0.1, 0]}]}
    return origin2, origin22


def test_compare_requires_both_frames_and_same_real_detection():
    live = _script()
    origin2, origin22 = _maps()
    intrinsics = Intrinsics(np.array([[1000., 0, 640], [0, 1000., 360], [0, 0, 1]]),
                            np.zeros(5), (1280, 720))
    summary = live.compare_results(origin2, origin22, intrinsics, raw_last_count=1)
    assert summary["localization"] == "PASS"
    assert summary["yolo"] == "PASS"
    assert summary["integration"] == "PASS"
    assert summary["max_distance_delta_m"] == pytest.approx(0)
    assert summary["max_projection_delta_px"] == pytest.approx(0)
    origin22["fire_targets"] = []
    summary = live.compare_results(origin2, origin22, intrinsics, raw_last_count=1)
    assert summary["localization"] == "PASS"
    assert summary["yolo"] == "FAIL"
    assert summary["integration"] == "FAIL"


def test_compare_rejects_fire_xyz_that_does_not_project_to_box_center():
    live = _script()
    origin2, origin22 = _maps()
    intrinsics = Intrinsics(np.array([[1000., 0, 640], [0, 1000., 360], [0, 0, 1]]),
                            np.zeros(5), (1280, 720))
    origin22["fire_targets"][0]["xyz"] = [-0.2, 0.1, 0]
    summary = live.compare_results(origin2, origin22, intrinsics, raw_last_count=1)
    assert summary["localization"] == "PASS"
    assert summary["yolo"] == "FAIL"
    assert summary["integration"] == "FAIL"


def test_compare_needs_ready_batch_and_all_reference_tags():
    live = _script()
    origin2, origin22 = _maps()
    intrinsics = Intrinsics(np.array([[1000., 0, 640], [0, 1000., 360], [0, 0, 1]]),
                            np.zeros(5), (1280, 720))
    assert live.compare_results(origin2, origin22, intrinsics, 1)["integration"] == "PASS"
    assert live.compare_results(origin2, origin22, intrinsics, 1,
                                capture_quality="REVIEW")["integration"] == "FAIL"
    del origin22["references"]["3"]
    assert live.compare_results(origin2, origin22, intrinsics, 1)["localization"] == "FAIL"


def test_compare_marks_untrusted_id22_blocked():
    live = _script()
    origin2, origin22 = _maps()
    intrinsics = Intrinsics(np.eye(3), np.zeros(5), (1280, 720))
    origin22 = {"status": "BLOCKED", "reason": "ID 22 missing", "anchor_id": 2,
                "operational_origin_id": 22, "coordinate_frame": "tag_22_table"}
    summary = live.compare_results(origin2, origin22, intrinsics, raw_last_count=0)
    assert summary["localization"] == "BLOCKED"
    assert summary["integration"] == "FAIL"


def test_compare_rejects_mismatched_raw_yolo_box():
    live = _script()
    origin2, origin22 = _maps()
    intrinsics = Intrinsics(np.eye(3), np.zeros(5), (1280, 720))
    summary = live.compare_results(origin2, origin22, intrinsics, raw_last_count=1,
                                   raw_last=[{"bbox_xywh": [100, 100, 20, 20],
                                              "confidence": 0.9}])
    assert summary["localization"] == "PASS"
    assert summary["yolo"] == "FAIL"
    assert summary["integration"] == "FAIL"


def test_both_cli_runs_use_one_frame_set_and_identical_detector_options(tmp_path):
    live = _script()
    evidence = tmp_path / "capture"
    output = evidence / "evaluation"
    commands = live.build_commands(evidence, output, Path("camera.yaml"),
                                   Path("fire-n.pt"), "cpu")
    assert set(commands) == {2, 22}
    for origin, command in commands.items():
        assert command[command.index("--image-dir") + 1] == str(evidence / "frames")
        assert command[command.index("--target-anchor") + 1] == "center"
        assert command[command.index("--world-origin") + 1] == str(origin)
        assert "--yolo" in command
        assert command[command.index("--weights") + 1] == "fire-n.pt"
        assert command[command.index("--intrinsics") + 1] == "camera.yaml"


def test_yolo_overlay_marks_raw_box_even_without_localized_target():
    live = _script()
    image = np.zeros((720, 1280, 3), np.uint8)
    debug = live.yolo_overlay(image, [{"bbox_xywh": [300, 200, 40, 20],
                                       "confidence": 0.8}])
    assert debug.shape == image.shape
    assert np.count_nonzero(debug) > 0
    assert np.array_equal(image, np.zeros_like(image))
    assert np.any(debug[200, 300] != 0)


def test_evaluate_writes_separate_gates_from_one_saved_batch(tmp_path, monkeypatch):
    live = _script()
    corners = np.array([[0., 0.], [20., 0.], [20., 20.], [0., 20.]])
    monkeypatch.setattr(live, "detect_tags", lambda frame: {
        tag_id: corners for tag_id in (0, 1, 2, 3, 21, 22)})
    evidence = tmp_path / "capture"
    live.save_capture([np.zeros((720, 1280, 3), np.uint8) for _ in range(15)],
                      evidence, "mocked-camera")
    origin2, origin22 = _maps()
    intrinsics = Intrinsics(np.array([[1000., 0, 640], [0, 1000., 360], [0, 0, 1]]),
                            np.zeros(5), (1280, 720))
    monkeypatch.setattr(live, "load_intrinsics", lambda path: intrinsics)
    detect_module = importlib.import_module("nt532.vision.detect")
    monkeypatch.setattr(detect_module, "detect", lambda *args: [
        Detection(940, 260, 20, 20, 0.9)])

    def fake_run(command, **kwargs):
        origin = int(command[command.index("--world-origin") + 1])
        result = origin2 if origin == 2 else origin22
        Path(command[command.index("--json") + 1]).write_text(json.dumps(result), encoding="utf-8")
        Path(command[command.index("--debug") + 1]).write_bytes(b"debug")
        return subprocess.CompletedProcess(command, 0, stdout="mapping done", stderr="")

    monkeypatch.setattr(live.subprocess, "run", fake_run)
    weights = tmp_path / "fire-n.pt"
    weights.write_bytes(b"mock-model")
    summary = live.evaluate(evidence, tmp_path / "camera.yaml", weights, "cpu")
    assert {key: summary[key] for key in ("localization", "yolo", "integration")} == {
        "localization": "PASS", "yolo": "PASS", "integration": "PASS"}
    output = evidence / "evaluation"
    stats = json.loads((output / "yolo_stats.json").read_text(encoding="utf-8"))
    assert len(stats["frames"]) == 15
    assert stats["latest_count"] == 1
    assert (output / "yolo_latest_debug.png").is_file()
    assert (output / "commands.json").is_file()
