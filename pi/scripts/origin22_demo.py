"""Reproducible synthetic tabletop study; never reads a camera or controls hardware.

From pi/: uv run python scripts/origin22_demo.py --out ../reports/origin22_demo
"""

import argparse
import contextlib
import csv
import importlib
import io
import json
from pathlib import Path

import cv2
import dynamic_map
import numpy as np
import yaml

from nt532.orchestrator.laser_geometry import LaserMount
from nt532.sim.render import in_frame, look_at, render_quad, tag_quad, yaw_matrix
from nt532.vision.calibration import save_intrinsics
from nt532.vision.localize import project
from nt532.vision.types import Detection, Intrinsics, TagPose

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "pi/simulations/origin22_tabletop.yaml"


def _camera(profile, intrinsics, height, angle):
    focus = np.asarray(profile["camera_focus_xy_m"], float)
    distance = height / np.tan(np.deg2rad(angle))
    return look_at([focus[0], focus[1] - distance, height], [*focus, 0], intrinsics)


def _tag_world(profile, tag_id):
    if tag_id in profile["reference_xy_m"]:
        xy = profile["reference_xy_m"][tag_id]
        yaw = 0
    else:
        xy = profile["node_xy_m"][tag_id]
        yaw = profile["node_yaw_deg"][tag_id]
    return tag_quad(tag_id, profile["reference_size_m"], [*xy, 0], yaw_deg=yaw)


def _study(profile, intrinsics):
    rows = []
    tags = [*profile["reference_xy_m"], *profile["node_xy_m"]]
    for height in profile["camera_heights_m"]:
        for angle in profile["camera_down_angles_deg"]:
            cam = _camera(profile, intrinsics, height, angle)
            visible = []
            min_edge = float("inf")
            for tag_id in tags:
                _, world = _tag_world(profile, tag_id)
                pixels = np.array([project(p, cam) for p in world])
                edges = np.linalg.norm(np.roll(pixels, -1, axis=0) - pixels, axis=1)
                min_edge = min(min_edge, float(edges.min()))
                if in_frame(world, cam, intrinsics.size, margin=4).all():
                    visible.append(tag_id)
            rows.append({"height_m": height, "down_angle_deg": angle,
                         "visible_tags": ",".join(map(str, sorted(visible))),
                         "all_six_visible": len(visible) == len(tags),
                         "min_tag_edge_px": round(min_edge, 1)})
    return rows


def _render(profile, cam, path):
    width, height = profile["image_size"]
    image = np.full((height, width), 180, np.uint8)
    for tag_id in [*profile["reference_xy_m"], *profile["node_xy_m"]]:
        render_quad(image, *_tag_world(profile, tag_id), cam)
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"cannot save {path}")


def _cli(image, calibration, out, origin, pixel, detection):
    # The callback supplies a known synthetic bbox; no YOLO weights are loaded.
    module = importlib.import_module("nt532.vision.detect")
    original = module.detect
    module.detect = lambda *_args, **_kwargs: [detection]
    try:
        args = ["--image", str(image), "--intrinsics", str(calibration),
                "--world-origin", str(origin), "--pixel", f"{pixel[0]},{pixel[1]}",
                "--yolo", "--json", str(out / f"mapping_id{origin}.json")]
        if origin == 22:
            args += ["--debug", str(out / "debug_id22.png")]
        with contextlib.redirect_stdout(io.StringIO()):
            code = dynamic_map.main(args)
    finally:
        module.detect = original
    data = json.loads((out / f"mapping_id{origin}.json").read_text(encoding="utf-8"))
    if code != 0:
        raise RuntimeError(f"synthetic ID {origin} CLI failed: {data['reason']}")
    return data


def _coverage(profile):
    results = {}
    grid = [(x, y, 0.) for x in np.linspace(0, 0.6, 9)
            for y in np.linspace(0, 0.4, 7)]
    for tag_id, xy in profile["node_xy_m"].items():
        node = f"s{1 if tag_id == 21 else 2}"
        config = profile["synthetic_mounts"][node]
        tag = TagPose(tag_id, np.array([*xy, 0.]), yaw_matrix(profile["node_yaw_deg"][tag_id]))
        rig = LaserMount(tag, np.array(config["tag_to_pivot_m"]),
                         np.array(config["emitter_offset_m"]),
                         config["home_yaw_deg"], config["home_elevation_deg"],
                         config["pan_sign"], config["tilt_sign"], config["pan_gain"],
                         config["tilt_gain"], tuple(config["pan_limits_deg"]),
                         tuple(config["tilt_limits_deg"]))
        results[node] = sum(
            rig.target(p).status == "PASS" for p in grid)
    return {"grid_points": len(grid), "reachable_points": results,
            "status": "SYNTHETIC_ONLY"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/origin22_demo")
    args = parser.parse_args(argv)
    profile = yaml.safe_load(args.profile.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    intrinsics = Intrinsics(np.array(profile["camera_K"], float),
                            np.zeros(5), tuple(profile["image_size"]))
    rows = _study(profile, intrinsics)
    with (args.out / "camera_layout_grid.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    selected = profile["example_camera"]
    cam = _camera(profile, intrinsics, selected["height_m"], selected["down_angle_deg"])
    image = args.out / "scene_synthetic.png"
    calibration = args.out / "camera_synthetic.yaml"
    _render(profile, cam, image)
    save_intrinsics(calibration, intrinsics, 0.0, 1)
    pixel = project(np.array([*profile["target_xy_m"], 0.]), cam)
    width, height = profile["synthetic_bbox_wh_px"]
    detection = Detection(*pixel, width, height, 0.9)
    legacy = _cli(image, calibration, args.out, 2, pixel, detection)
    tabletop = _cli(image, calibration, args.out, 22, pixel, detection)
    summary = {"evidence": "SYNTHETIC_ONLY", "example_camera": selected,
               "origin22_xyz_m": tabletop["nodes"]["s2"],
               "node21_xyz_m": tabletop["nodes"]["s1"],
               "target_xyz_m": tabletop["fire_targets"][0]["xyz"],
               "legacy_bbox_bottom_error_cm": round(float(np.linalg.norm(
                   np.array(legacy["fire_targets"][0]["xyz"]) -
                   [*profile["target_xy_m"], 0.])) * 100, 3),
               "tabletop_bbox_center_error_cm": round(float(np.linalg.norm(
                   np.array(tabletop["fire_targets"][0]["xyz"]) -
                   np.array(tabletop["pixel"]["xyz"]))) * 100, 3),
               "camera_layouts_all_six_visible": sum(row["all_six_visible"] for row in rows),
               "camera_layouts_tested": len(rows), "aim_coverage": _coverage(profile),
               "occlusion": "NOT_VERIFIED: enclosure dimensions and final mounts unknown"}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
