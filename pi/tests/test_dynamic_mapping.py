"""Independent pinhole ground truth; the estimator receives image corners only."""

import copy
import importlib.util
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from conftest import render_quad

from nt532.dashboard.server import Dashboard
from nt532.orchestrator.events import EventLog
from nt532.orchestrator.machine import Orchestrator
from nt532.sim.render import tag_quad
from nt532.vision import Vision
from nt532.vision.calibration import save_intrinsics
from nt532.vision.dynamic_mapping import estimate_reference_map, estimate_reference_maps
from nt532.vision.tags import detect_tags
from nt532.vision.types import Camera, Detection, Intrinsics

SIZE = 0.08
LAYOUTS = {
    "60x40": {2: (0, 0), 0: (0.60, 0), 1: (0.60, 0.40), 3: (0, 0.40)},
    "80x50": {2: (0, 0), 0: (0.80, 0), 1: (0.80, 0.50), 3: (0, 0.50)},
    "asymmetric": {2: (0, 0), 0: (0.63, -0.08), 1: (0.74, 0.43), 3: (-0.09, 0.34)},
}


@pytest.fixture
def intrinsics():
    return Intrinsics(np.array([[1050., 0, 640.], [0, 1030., 360.], [0, 0, 1.]]),
                      np.array([0.01, -0.005, 0.001, 0.0, 0.0]), (1280, 720))


def camera(intrinsics, eye=(0.32, -0.35, 1.0), target=(0.30, 0.20, 0)):
    eye, target = np.array(eye, float), np.array(target, float)
    forward = (target - eye) / np.linalg.norm(target - eye)
    right = np.cross(forward, [0, 0, 1.])
    right /= np.linalg.norm(right)
    R = np.stack((right, np.cross(forward, right), forward))
    return Camera(intrinsics, R, -R @ eye)


def corners_for(layout, cam, noise=0.0, seed=4):
    rng = np.random.default_rng(seed)
    h = SIZE / 2
    local = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], float)
    result = {}
    for tag_id, (x, y) in layout.items():
        yaw = np.deg2rad({2: 0, 0: 13, 1: -19, 3: 27}.get(tag_id, 0))
        rot = np.array([[np.cos(yaw), -np.sin(yaw), 0],
                        [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
        xyz = local @ rot.T + [x, y, 0]
        uv, _ = cv2.projectPoints(xyz, cv2.Rodrigues(cam.R)[0], cam.t,
                                  cam.intrinsics.K, cam.intrinsics.dist)
        result[tag_id] = uv.reshape(4, 2) + rng.normal(0, noise, (4, 2))
    return result


@pytest.mark.parametrize("name", LAYOUTS)
def test_recovers_layouts_without_surveyed_coordinates(name, intrinsics):
    layout = LAYOUTS[name]
    cam = camera(intrinsics)
    mapping = estimate_reference_map(corners_for(layout, cam), intrinsics, SIZE)
    assert mapping.references.keys() == layout.keys()
    for tag_id, xy in layout.items():
        assert np.linalg.norm(mapping.references[tag_id][:2] - xy) < 0.005
    assert np.linalg.norm(mapping.camera.position - cam.position) < 0.01
    assert mapping.metric_error_m < 0.005


def test_camera_move_and_arbitrary_table_pixel(intrinsics):
    layout = LAYOUTS["asymmetric"]
    point = np.array([0.36, 0.19, 0.])
    for eye, target in [((0.32, -0.35, 1), (0.3, 0.2, 0)),
                        ((0.75, 0.10, 0.78), (0.25, 0.15, 0))]:
        cam = camera(intrinsics, eye, target)
        mapping = estimate_reference_map(corners_for(layout, cam), intrinsics, SIZE)
        pixel, _ = cv2.projectPoints(point.reshape(1, 3), cv2.Rodrigues(cam.R)[0],
                                     cam.t, intrinsics.K, intrinsics.dist)
        assert np.linalg.norm(mapping.table_point(tuple(pixel.reshape(2))) - point) < 0.01


@pytest.mark.parametrize("tag_id,xy", [(0, (0.46, 0.15)), (1, (0.54, 0.31))])
def test_moved_reference_is_remeasured(intrinsics, tag_id, xy):
    cam = camera(intrinsics)
    first = estimate_reference_map(corners_for(LAYOUTS["60x40"], cam), intrinsics, SIZE)
    moved = dict(LAYOUTS["60x40"])
    moved[tag_id] = xy
    second = estimate_reference_map(corners_for(moved, cam), intrinsics, SIZE)
    assert np.linalg.norm(second.references[tag_id] - first.references[tag_id]) > 0.10
    assert np.linalg.norm(second.references[tag_id][:2] - moved[tag_id]) < 0.01


def test_low_angle_real_corners_keep_moved_id_0_in_the_solution():
    """Detector output from low-angle-fail/frame-024.jpg; no surveyed coordinates."""
    calibrated = Intrinsics(
        np.array([[993.765464334269, 0, 643.1603481283688],
                  [0, 992.2958974909341, 360.62700180078434], [0, 0, 1]]),
        np.array([0.020177991555003598, 0.1681489831696114,
                  0.0008123582073143769, 0.00042226930927851607,
                  -0.4472832951591382]), (1280, 720),
    )
    corners = {
        2: np.array([[186.851, 555.691], [342.170, 553.328],
                     [298.706, 646.625], [114.280, 647.492]]),
        3: np.array([[948.047, 567.455], [1091.088, 567.555],
                     [1180.318, 658.378], [1013.773, 657.631]]),
        1: np.array([[879.557, 360.608], [968.725, 362.735],
                     [1000.558, 395.501], [902.641, 390.448]]),
        0: np.array([[404.426, 308.865], [483.435, 310.746],
                     [474.917, 332.485], [387.911, 330.526]]),
    }
    mapping = estimate_reference_map(corners, calibrated, SIZE)
    assert set(mapping.references) == {0, 1, 2, 3}
    assert mapping.camera.position[2] > 0.15
    assert all(q["max_corner_px"] < 5 for q in mapping.tag_quality.values())
    assert mapping.metric_error_m < 0.012


def test_low_angle_synthetic_ground_truth_and_table_ray(intrinsics):
    layout = {2: (0, 0), 0: (0.02, 0.45), 1: (0.44, 0.31), 3: (0.40, -0.02)}
    cam = camera(intrinsics, (0.17, -0.43, 0.27), (0.20, 0.32, 0))
    observed = corners_for(layout, cam, noise=0.25, seed=12)
    mapping = estimate_reference_map(observed, intrinsics, SIZE)
    errors = {i: float(np.linalg.norm(mapping.references[i][:2] - xy))
              for i, xy in layout.items()}
    assert max(errors.values()) < 0.02, errors
    assert np.linalg.norm(mapping.camera.position - cam.position) < 0.02
    point = np.array([0.25, 0.19, 0.])
    uv, _ = cv2.projectPoints(point.reshape(1, 3), cv2.Rodrigues(cam.R)[0], cam.t,
                              intrinsics.K, intrinsics.dist)
    assert np.linalg.norm(mapping.table_point(tuple(uv.reshape(2))) - point) < 0.02


def test_bad_id_0_corner_rejects_entire_mapping(intrinsics):
    corners = corners_for(LAYOUTS["asymmetric"], camera(intrinsics))
    corners[0][0] += [15, -9]
    with pytest.raises(ValueError, match="ID 0"):
        estimate_reference_map(corners, intrinsics, SIZE)


def test_full_external_low_angle_dataset_when_available():
    root = os.environ.get("NT532_LOW_ANGLE_DATASET")
    if not root:
        pytest.skip("set NT532_LOW_ANGLE_DATASET to run real-image regression")
    from nt532.vision.calibration import load_intrinsics

    images = sorted(Path(root).glob("frame-*.jpg"))
    assert len(images) == 24
    calibrated = load_intrinsics(Path(root) / "camera.yaml")
    observations = [detect_tags(cv2.imread(str(path))) for path in images]
    assert sum(1 in frame for frame in observations) == 21
    assert all({0, 2, 3} <= frame.keys() for frame in observations)
    maps = [estimate_reference_map(frame, calibrated, SIZE) for frame in observations]
    assert all(0 in mapping.references for mapping in maps)
    batch = estimate_reference_maps(observations, calibrated, SIZE)
    assert batch.frames_used == 24
    assert set(batch.references) == {0, 1, 2, 3}
    assert batch.holdout_m is not None and batch.holdout_m < 0.025


def test_missing_anchor_and_degenerate_geometry_fail_closed(intrinsics):
    corners = corners_for(LAYOUTS["60x40"], camera(intrinsics))
    del corners[2]
    with pytest.raises(ValueError, match="ID 2"):
        estimate_reference_map(corners, intrinsics, SIZE)
    only_two = corners_for(LAYOUTS["60x40"], camera(intrinsics))
    del only_two[0]
    del only_two[3]
    with pytest.raises(ValueError, match="reference"):
        estimate_reference_map(only_two, intrinsics, SIZE)


def test_missing_id_1_still_maps_id_0(intrinsics):
    corners = corners_for(LAYOUTS["asymmetric"], camera(intrinsics))
    del corners[1]
    mapping = estimate_reference_map(corners, intrinsics, SIZE)
    assert set(mapping.references) == {0, 2, 3}
    assert np.linalg.norm(mapping.references[0][:2] - LAYOUTS["asymmetric"][0]) < 0.01


def test_unstable_anchor_corners_fail_quality_gate(intrinsics):
    corners = corners_for(LAYOUTS["60x40"], camera(intrinsics))
    corners[2][0] += [10, -7]
    with pytest.raises(ValueError):
        estimate_reference_map(corners, intrinsics, SIZE)


def test_near_frontal_planar_pose_ambiguity_is_rejected(intrinsics):
    close_layout = {2: (0, 0), 0: (0.12, 0), 1: (0, 0.12)}
    near_frontal = camera(intrinsics, (0, 0, 1), (0.001, 0, 0))
    corners = corners_for(close_layout, near_frontal, noise=0.15, seed=0)
    with pytest.raises(ValueError, match="ambiguous"):
        estimate_reference_map(corners, intrinsics, SIZE)


def test_multiframe_filter_rejects_geometry_change_during_batch(intrinsics):
    cam = camera(intrinsics)
    first = corners_for(LAYOUTS["60x40"], cam, noise=0.15)
    moved = dict(LAYOUTS["60x40"])
    moved[1] = (0.54, 0.31)
    second = corners_for(moved, cam, noise=0.15)
    with pytest.raises(ValueError, match="inconsistent"):
        estimate_reference_maps([first, second], intrinsics, SIZE)


def test_multiframe_does_not_reuse_pose_when_latest_frame_loses_anchor(intrinsics):
    observations = [corners_for(LAYOUTS["60x40"], camera(intrinsics)) for _ in range(3)]
    del observations[-1][2]
    with pytest.raises(ValueError, match="latest"):
        estimate_reference_maps(observations, intrinsics, SIZE)


def test_multiframe_accepts_camera_movement_with_static_references(intrinsics):
    layout = LAYOUTS["80x50"]
    views = [corners_for(layout, camera(intrinsics, eye, (0.4, 0.25, 0)), 0.15, i)
             for i, eye in enumerate(((0.4, -0.35, 1), (0.65, 0.1, 0.9), (0.2, -0.1, 0.8)))]
    mapping = estimate_reference_maps(views, intrinsics, SIZE)
    assert mapping.frames_used == 3
    assert max(np.linalg.norm(mapping.references[i][:2] - xy)
               for i, xy in layout.items()) < 0.02


def test_legacy_mode_still_uses_surveyed_coordinates(site, intrinsics, scene):
    legacy = Vision(site, intrinsics)
    state = legacy.commission([scene] * 3)
    assert state.reprojection_px < 1
    assert legacy.blockers("s1") == []


def test_auto_mode_does_not_use_old_positions_or_load_old_commissioning(site, intrinsics, tmp_path):
    auto_site = copy.deepcopy(site)
    auto_site["vision"]["reference_mode"] = "auto"
    auto_site["vision"]["commissioning"] = str(tmp_path / "old.yaml")
    auto_site["tags"]["reference"]["positions"] = {i: [99, 99] for i in range(4)}
    v = Vision(auto_site, intrinsics)
    assert v.state is None
    assert v.blockers()
    cam = camera(intrinsics)
    # Integration test passes image corners through the same estimator as the CLI.
    state = v.commission_corners([corners_for(LAYOUTS["60x40"], cam)] * 3)
    assert state.reprojection_px < 2
    assert np.linalg.norm(v.reference_map.references[1][:2] - [0.6, 0.4]) < 0.01
    assert v.localize_table((640, 360)) is not None
    with pytest.raises(RuntimeError, match="auto"):
        v.save()
    assert not (tmp_path / "old.yaml").exists()


def rendered_scene(layout, cam, *, anchor=True, nodes=True):
    image = np.full((720, 1280), 180, np.uint8)
    for tag_id, xy in layout.items():
        if tag_id == 2 and not anchor:
            continue
        texture, quad = tag_quad(tag_id, SIZE, [*xy, 0])
        render_quad(image, texture, quad, cam)
    if nodes:
        for tag_id, center in ((21, [0.20, 0.22, 0.05]), (22, [0.47, 0.27, 0.05])):
            texture, quad = tag_quad(tag_id, SIZE, center)
            render_quad(image, texture, quad, cam)
    return image


def test_auto_vision_from_rendered_images_tracks_nodes_and_rejects_missing_anchor(
    site, intrinsics,
):
    auto_site = copy.deepcopy(site)
    auto_site["vision"]["reference_mode"] = "auto"
    auto_site["tags"]["reference"]["positions"] = {i: None for i in range(4)}
    cam = camera(intrinsics)
    layout = LAYOUTS["60x40"]
    vision = Vision(auto_site, intrinsics, detector=lambda frame: [])
    frame = rendered_scene(layout, cam)
    vision.commission([frame] * 3)
    assert vision.blockers("s1") == []
    assert vision.blockers("s2") == []
    assert np.linalg.norm(vision.nodes["s1"].position - [0.20, 0.22, 0.05]) < 0.02
    assert np.linalg.norm(vision.nodes["s2"].position - [0.47, 0.27, 0.05]) < 0.02
    moved = dict(layout)
    moved[1] = (0.50, 0.43)
    health = vision.check([rendered_scene(moved, cam)] * 3)
    assert health.stale == [] and health.repose is None
    assert np.linalg.norm(vision.reference_map.references[1][:2] - moved[1]) < 0.02
    health = vision.check([rendered_scene(moved, cam, anchor=False)] * 3)
    assert "camera" in health.stale
    assert vision.localize_table((640, 360)) is None
    assert vision.targets(frame) == []
    assert vision.check([rendered_scene(moved, cam)] * 3).stale == []
    assert vision.localize_table((640, 360)) is not None


def test_auto_vision_rejects_image_size_mismatch(site, intrinsics):
    auto_site = copy.deepcopy(site)
    auto_site["vision"]["reference_mode"] = "auto"
    vision = Vision(auto_site, intrinsics)
    frame = rendered_scene(LAYOUTS["60x40"], camera(intrinsics))
    with pytest.raises(ValueError, match="image size"):
        vision.commission([cv2.resize(frame, (640, 360))])


def test_failed_recommission_invalidates_old_auto_pose_and_dashboard(site, intrinsics):
    auto_site = copy.deepcopy(site)
    auto_site["vision"]["reference_mode"] = "auto"
    vision = Vision(auto_site, intrinsics)
    cam = camera(intrinsics)
    frame = rendered_scene(LAYOUTS["60x40"], cam)
    vision.commission([frame])
    with pytest.raises(ValueError):
        vision.commission([rendered_scene(LAYOUTS["60x40"], cam, anchor=False)])
    assert vision.blockers()
    assert vision.localize_table((640, 360)) is None
    dash = Dashboard.__new__(Dashboard)
    dash.st = SimpleNamespace(vision=vision, site=auto_site)
    annotated = dash.annotate(np.zeros((720, 1280, 3), np.uint8))
    assert annotated.any()


def test_auto_targets_use_table_contact_point_and_ignore_fixed_board(site, intrinsics):
    auto_site = copy.deepcopy(site)
    auto_site["vision"]["reference_mode"] = "auto"
    auto_site["tags"]["reference"]["positions"] = {i: [99, 99] for i in range(4)}
    cam = camera(intrinsics)
    point = np.array([[0.31, 0.18, 0.]])
    uv, _ = cv2.projectPoints(point, cv2.Rodrigues(cam.R)[0], cam.t,
                              intrinsics.K, intrinsics.dist)
    u, v = uv.reshape(2)
    vision = Vision(auto_site, intrinsics, detector=lambda frame: [Detection(u, v, 0, 0, 0.9)])
    vision.commission_corners([corners_for(LAYOUTS["60x40"], cam)])
    found = vision.targets(np.zeros((720, 1280, 3), np.uint8))
    assert len(found) == 1 and found[0].surface == "floor"
    assert np.linalg.norm(found[0].position - point[0]) < 0.01
    assert vision.localize_table((float("nan"), 360)) is None
    assert vision.localize_table((-1, 360)) is None


def test_headless_cli_writes_quality_json_and_debug_image(intrinsics, tmp_path, capsys):
    script = Path(__file__).resolve().parents[1] / "scripts" / "dynamic_map.py"
    spec = importlib.util.spec_from_file_location("dynamic_map_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    cam = camera(intrinsics)
    point = np.array([[0.32, 0.20, 0.]])
    uv, _ = cv2.projectPoints(point, cv2.Rodrigues(cam.R)[0], cam.t,
                              intrinsics.K, intrinsics.dist)
    u, v = uv.reshape(2)
    image = tmp_path / "scene.png"
    calibration = tmp_path / "camera.yaml"
    report = tmp_path / "result.json"
    debug = tmp_path / "debug.png"
    cv2.imwrite(str(image), rendered_scene(LAYOUTS["60x40"], cam))
    save_intrinsics(calibration, intrinsics, 0.1, 10)
    code = cli.main(["--image", str(image), "--intrinsics", str(calibration),
                     "--pixel", f"{u},{v}", "--json", str(report), "--debug", str(debug)])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["status"] == "PASS"
    assert set(data["references"]) == {"0", "1", "2", "3"}
    assert np.linalg.norm(data["pixel"]["xyz"] - point[0]) < 0.02
    assert data["quality"]["metric_error_m"] < 0.012
    assert data["quality"]["joint_reprojection_px"] >= 0
    assert data["quality"]["reference_hull_area_m2"] > 0.005
    assert set(data["diagnostics"]["used_tags"]) == {0, 1, 2, 3}
    assert data["diagnostics"]["rejected_tags"] == {}
    assert set(data["diagnostics"]["per_tag_quality"]) == {"0", "1", "2", "3"}
    assert data["diagnostics"]["frame_results"][0]["status"] == "PASS"
    assert len(data["diagnostics"]["frame_results"][0]["anchor_pose_candidates"]) == 2
    assert json.loads(report.read_text(encoding="utf-8")) == data
    assert debug.exists() and debug.stat().st_size > 0


def test_cli_image_directory_works_without_node_tags(intrinsics, tmp_path, capsys):
    script = Path(__file__).resolve().parents[1] / "scripts" / "dynamic_map.py"
    spec = importlib.util.spec_from_file_location("dynamic_map_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    cam = camera(intrinsics)
    images = tmp_path / "images"
    images.mkdir()
    frame = rendered_scene(LAYOUTS["asymmetric"], cam, nodes=False)
    for index in range(3):
        cv2.imwrite(str(images / f"frame-{index:03d}.png"), frame)
    calibration = tmp_path / "camera.yaml"
    save_intrinsics(calibration, intrinsics, 0.1, 10)
    code = cli.main(["--image-dir", str(images), "--intrinsics", str(calibration)])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["status"] == "PASS"
    assert data["nodes"] == {}
    assert data["quality"]["frames_used"] == 3
    assert data["quality"]["holdout_m"] == 0
    assert len(data["diagnostics"]["frame_results"]) == 3


def test_cli_failure_json_explains_missing_anchor(intrinsics, tmp_path, capsys):
    script = Path(__file__).resolve().parents[1] / "scripts" / "dynamic_map.py"
    spec = importlib.util.spec_from_file_location("dynamic_map_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    image = tmp_path / "missing-anchor.png"
    calibration = tmp_path / "camera.yaml"
    cv2.imwrite(str(image), rendered_scene(LAYOUTS["60x40"], camera(intrinsics),
                                           anchor=False, nodes=False))
    save_intrinsics(calibration, intrinsics, 0.1, 10)
    code = cli.main(["--image", str(image), "--intrinsics", str(calibration)])
    data = json.loads(capsys.readouterr().out)
    assert code == 1 and data["status"] == "FAIL"
    assert "ID 2" in data["reason"]
    assert data["diagnostics"]["used_tags"] == []
    assert "2" in data["diagnostics"]["rejected_tags"]
    assert data["diagnostics"]["frame_results"][0]["status"] == "FAIL"


def test_periodic_health_initializes_auto_mode_without_saved_commissioning(site, intrinsics):
    auto_site = copy.deepcopy(site)
    auto_site["vision"]["reference_mode"] = "auto"
    cam = camera(intrinsics)
    frame = rendered_scene(LAYOUTS["60x40"], cam)
    vision = Vision(auto_site, intrinsics, detector=lambda image: [])
    events = EventLog()
    orch = Orchestrator.__new__(Orchestrator)
    orch.vision = vision
    orch.hub = SimpleNamespace(frames=lambda count: [frame] * count)
    orch.events = events
    orch.clock = lambda: 42.0
    orch.pose_time = {}
    orch._health_lock = threading.Lock()
    orch._health_check()
    assert vision.blockers("s1") == []
    assert orch.pose_time == {"s1": 42.0, "s2": 42.0}
