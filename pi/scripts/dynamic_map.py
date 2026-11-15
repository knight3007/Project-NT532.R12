"""Headless Dynamic Reference Mapping probe; reads calibration, never writes it.

    uv run python scripts/dynamic_map.py --image frame.jpg --pixel 640,360 --json map.json --debug map.jpg
    uv run python scripts/dynamic_map.py --source rtsp://127.0.0.1:8554/cam/stream --frames 15
    uv run python scripts/dynamic_map.py --image frame.jpg --yolo --weights models/fire-n.pt
"""

import argparse
import json
import re
from pathlib import Path

import cv2
import numpy as np

from nt532.config import REPO_ROOT, load_site
from nt532.vision.calibration import load_intrinsics
from nt532.vision.camera import open_camera, read_frames
from nt532.vision.dynamic_mapping import estimate_reference_map
from nt532.vision.system import Vision
from nt532.vision.table_frame import analyze_table_frame
from nt532.vision.tags import _square, detect_tags
from nt532.vision.types import Target


def _pair(value: str) -> tuple[float, float]:
    try:
        u, v = (float(x) for x in value.split(","))
        return u, v
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("pixel must be U,V") from exc


def _xyz(point: np.ndarray) -> list[float]:
    return [round(float(v), 5) for v in point]


def _quality(quality: dict[int, dict[str, float]]) -> dict[str, dict[str, float]]:
    return {str(i): {name: round(value, 5) for name, value in q.items()}
            for i, q in sorted(quality.items())}


def _diagnose(observations, intrinsics, size):
    results = []
    rejected = {}
    for index, corners in enumerate(observations, 1):
        reference_ids = sorted(set(corners) & set(range(4)))
        pixel_quality = {}
        for tag_id in reference_ids:
            edges = np.linalg.norm(np.roll(corners[tag_id], -1, axis=0) - corners[tag_id],
                                   axis=1)
            pixel_quality[str(tag_id)] = {"min_edge_px": round(float(edges.min()), 2),
                                          "max_edge_px": round(float(edges.max()), 2)}
        entry = {"frame": index, "detected_tags": sorted(corners),
                 "reference_tags": reference_ids, "pixel_quality": pixel_quality}
        if 2 in corners:
            _, rotations, translations, errors = cv2.solvePnPGeneric(
                _square(size), corners[2], intrinsics.K, intrinsics.dist,
                flags=cv2.SOLVEPNP_IPPE_SQUARE,
            )
            entry["anchor_pose_candidates"] = [
                {"reprojection_px": round(float(error[0]), 3),
                 "camera_height_m": round(float((
                     -cv2.Rodrigues(rotation)[0].T @ translation.reshape(3))[2]), 5)}
                for rotation, translation, error in zip(rotations, translations, errors)
            ]
        try:
            mapping = estimate_reference_map(corners, intrinsics, size)
            entry.update(status="PASS", metric_error_m=round(mapping.metric_error_m, 5),
                         reprojection_px=round(mapping.reprojection_px, 3),
                         per_tag_quality=_quality(mapping.tag_quality))
        except ValueError as exc:
            entry.update(status="FAIL", reason=str(exc))
            match = re.search(r"\bID (\d+)\b", str(exc))
            if match:
                rejected[match.group(1)] = str(exc)
        results.append(entry)
    all_ids = sorted(set().union(*(set(c) for c in observations)))
    rates = {str(i): round(sum(i in frame for frame in observations) / len(observations), 3)
             for i in all_ids}
    return {"detection_rate": rates, "frame_results": results, "used_tags": [],
            "rejected_tags": rejected, "per_tag_quality": {}}


def _debug_image(frame: np.ndarray, pixel, targets, result: dict, camera) -> np.ndarray:
    image = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
    status = result["status"]
    cv2.putText(image, status, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (0, 180, 0) if status == "PASS" else (0, 0, 255), 2)
    cv2.putText(image, result["coordinate_frame"], (16, 60), cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (0, 180, 0) if status == "PASS" else (0, 0, 255), 2)
    if status != "PASS":
        return image
    from nt532.vision.localize import project

    for tag_id, xyz in result["references"].items():
        position = np.asarray(xyz)
        u, v = project(position, camera)
        cv2.drawMarker(image, (round(u), round(v)), (255, 150, 0), cv2.MARKER_CROSS, 20, 2)
        cv2.putText(image, f"ID {tag_id}: {position[0]:.3f},{position[1]:.3f}",
                    (round(u) + 8, round(v) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (255, 150, 0), 1)
    for name, xyz in result["nodes"].items():
        u, v = project(np.asarray(xyz), camera)
        cv2.putText(image, f"{name}: {xyz[0]:.3f},{xyz[1]:.3f},{xyz[2]:.3f}",
                    (round(u) + 8, round(v) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (0, 120, 200), 1)
    if pixel is not None:
        cv2.drawMarker(image, tuple(map(round, pixel)), (0, 255, 255), cv2.MARKER_CROSS, 24, 2)
    for target in targets:
        d = target.detection
        cv2.rectangle(image, (round(d.u - d.w / 2), round(d.v - d.h / 2)),
                      (round(d.u + d.w / 2), round(d.v + d.h / 2)), (0, 0, 255), 2)
        cv2.putText(image, f"{target.position[0]:.3f},{target.position[1]:.3f},0",
                    (round(d.u), round(d.v)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        anchor_v = d.v if result["target_anchor"] == "center" else d.v + d.h / 2
        cv2.drawMarker(image, (round(d.u), round(anchor_v)), (255, 0, 255),
                       cv2.MARKER_CROSS, 14, 2)
    return image


def _origin_overlay(image: np.ndarray, corners: dict, result: dict, camera, mapping) -> np.ndarray:
    """Explain a BLOCKED ID 22 origin on the latest frame; details stay in the JSON."""
    from nt532.vision.localize import project

    image = image.copy()
    origin = result["diagnostics"].get("operational_origin", {})
    cv2.putText(image, f"{origin.get('reason_code') or 'ID22 BLOCKED'}", (16, 88),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    if camera is not None and mapping is not None and len(mapping.references) >= 3:
        pixels = np.array([project(p, camera) for p in mapping.references.values()], np.float32)
        hull = cv2.convexHull(pixels).reshape(-1, 2)
        cv2.polylines(image, [np.round(hull).astype(np.int32)], True, (255, 150, 0), 2)
    if 22 not in corners:
        return image
    quad = np.asarray(corners[22], float)
    for name, point in zip(("TL", "TR", "BR", "BL"), quad):
        pixel = np.round(point).astype(int)
        cv2.circle(image, tuple(pixel), 4, (0, 255, 0), -1)
        cv2.putText(image, name, tuple(pixel + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (0, 255, 0), 1)
    latest = origin.get("frames", [{}])[-1] if origin.get("frames") else {}
    for point in latest.get("table_pose", {}).get("projected_corners_px", []):
        cv2.drawMarker(image, tuple(np.round(point).astype(int)), (0, 0, 255),
                       cv2.MARKER_TILTED_CROSS, 12, 2)
    center = np.round(quad.mean(axis=0)).astype(int)
    cv2.drawMarker(image, tuple(center), (0, 255, 255), cv2.MARKER_CROSS, 16, 2)
    lines = []
    if "max_edge_error_mm" in latest:
        lines.append(f"edge {latest['max_edge_error_mm']:.1f}/{origin['thresholds']['edge_mm']:.0f}"
                     f" mm ({latest['worst_edge']}) diag {latest['max_diagonal_error_mm']:.1f}/"
                     f"{origin['thresholds']['diagonal_mm']:.0f} mm")
    if "table_reprojection_px" in latest:
        lines.append(f"table reproj max {latest['table_reprojection_px']['max']:.1f} px")
    hull_info = origin.get("reference_hull")
    if hull_info is not None:
        lines.append(f"{'inside' if hull_info['inside'] else 'OUTSIDE'} reference hull "
                     f"{hull_info['signed_distance_m'] * 100:+.1f} cm, "
                     f"r/rmax {origin.get('image_radius_ratio', float('nan')):.2f}")
    if "pose_disagreement_mm" in origin:
        lines.append(f"PnP vs table {origin['pose_disagreement_mm']['xy']:.1f} mm XY, "
                     f"PnP z {origin['pnp_pose']['position_m'][2] * 1000:+.1f} mm")
    x = int(np.clip(center[0] - 60, 8, image.shape[1] - 420))
    y = int(np.clip(center[1] - 80 - 18 * len(lines), 110, image.shape[0] - 18 * len(lines)))
    for offset, text in enumerate(lines):
        cv2.putText(image, text, (x, y + 18 * offset), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (0, 0, 255), 1)
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="single image file")
    source.add_argument("--image-dir", type=Path, help="all JPG/PNG frames in a directory")
    source.add_argument("--source", help="RTSP URL or camera source accepted by open_camera")
    parser.add_argument("--site", type=Path, help="site YAML; default config/site.yaml")
    parser.add_argument("--intrinsics", type=Path, help="camera YAML; default site camera.intrinsics")
    parser.add_argument("--frames", type=int, default=15, help="frames from source (default 15)")
    parser.add_argument("--pixel", type=_pair, help="manual table pixel U,V")
    parser.add_argument("--yolo", action="store_true", help="optional YOLO fire detections")
    parser.add_argument("--weights", type=Path, help="YOLO weights; default site vision.weights")
    parser.add_argument("--device", help="YOLO device, e.g. cpu")
    parser.add_argument("--world-origin", type=int, choices=(2, 22), default=2,
                        help="output frame: legacy solver ID 2 or tabletop ID 22")
    parser.add_argument("--target-anchor", choices=("bottom_center", "center"),
                        help="YOLO table contact pixel; default center for ID 22, legacy otherwise")
    parser.add_argument("--json", type=Path, help="write JSON result")
    parser.add_argument("--debug", type=Path, help="write annotated image")
    args = parser.parse_args(argv)
    if args.frames < 1:
        parser.error("--frames must be positive")
    site = load_site(args.site)
    site["vision"] = {**site["vision"], "reference_mode": "auto",
                      "tabletop_anchor": args.target_anchor or (
                          "center" if args.world_origin == 22 else "bottom_center")}
    intrinsics_path = args.intrinsics or REPO_ROOT / site["camera"]["intrinsics"]
    intrinsics = load_intrinsics(intrinsics_path)
    detector = None
    if args.yolo:
        from nt532.vision.detect import detect

        weights = args.weights or REPO_ROOT / site["vision"]["weights"]
        detector = lambda frame: detect(frame, weights, site["vision"]["detect_conf"],
                                        site["vision"]["imgsz"], args.device)
    vision = Vision(site, intrinsics, detector)
    if args.image is not None:
        frame = cv2.imread(str(args.image))
        if frame is None:
            raise SystemExit(f"cannot read image {args.image}")
        images = [frame]
    elif args.image_dir is not None:
        paths = sorted(p for p in args.image_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        if not paths:
            raise SystemExit(f"no images in {args.image_dir}")
        images = [cv2.imread(str(path)) for path in paths]
        if any(image is None for image in images):
            raise SystemExit("cannot read one or more images in directory")
        frame = images[-1]
    else:
        cap = open_camera(site["camera"], args.source)
        try:
            images = read_frames(cap, args.frames)
        finally:
            cap.release()
        frame = images[-1]
    if any(image.shape[1::-1] != intrinsics.size for image in images):
        raise SystemExit(f"image size differs from calibration {intrinsics.size}")
    frame_image = frame
    observations = [detect_tags(image) for image in images]
    size = site["tags"]["reference"]["size"]
    result = {"status": "FAIL", "mode": "auto", "anchor_id": 2,
              "operational_origin_id": args.world_origin,
              "coordinate_frame": f"tag_{args.world_origin}_table",
              "target_anchor": site["vision"]["tabletop_anchor"], "unit": "m",
              "diagnostics": _diagnose(observations, intrinsics, size)}
    targets = []
    display_camera = None
    solver_camera = None
    origin_blocked = False
    try:
        state = vision.commission_corners(observations)
        mapping = vision.reference_map
        table_frame = None
        if args.world_origin == 22:
            solver_camera = state.camera
            table_frame, origin_diagnostics = analyze_table_frame(
                observations, intrinsics, site, size, state.camera, mapping.references)
            result["diagnostics"]["operational_origin"] = origin_diagnostics
            if table_frame is None:
                origin_blocked = True
                result["reason_code"] = origin_diagnostics["reason_code"]
                raise ValueError(origin_diagnostics["reason"])
        convert = table_frame.point if table_frame is not None else np.asarray
        display_camera = (table_frame.camera(state.camera) if table_frame is not None
                          else state.camera)
        result["diagnostics"]["used_tags"] = sorted(mapping.references)
        result["diagnostics"]["per_tag_quality"] = _quality(mapping.tag_quality)
        result["references"] = {str(i): _xyz(convert(p))
                                for i, p in sorted(mapping.references.items())}
        result["missing_references"] = sorted(set(range(4)) - set(mapping.references))
        result["camera"] = {"position": _xyz(display_camera.position),
                            "R": display_camera.R.tolist(), "t": _xyz(display_camera.t)}
        result["nodes"] = {name: _xyz(convert(pose.position))
                           for name, pose in state.nodes.items()}
        if table_frame is not None and "s2" in result["nodes"]:
            # The tabletop origin deliberately discards PnP height noise.
            result["nodes"]["s2"] = [0.0, 0.0, 0.0]
        result["quality"] = {"anchor_reprojection_px": round(mapping.anchor_reprojection_px, 3),
                             "joint_reprojection_px": round(mapping.reprojection_px, 3),
                             "metric_error_m": round(mapping.metric_error_m, 5),
                             "initial_metric_error_m": round(mapping.initial_metric_error_m, 5),
                             "pose_ambiguity_m": round(mapping.ambiguity_m, 5),
                             "reference_hull_area_m2": round(mapping.hull_area_m2, 5),
                             "stability_m": round(mapping.stability_m, 5),
                             "holdout_m": (None if mapping.holdout_m is None else
                                           round(mapping.holdout_m, 5)),
                             "frames_used": mapping.frames_used}
        if args.pixel is not None:
            point = vision.localize_table(args.pixel)
            if point is None:
                raise ValueError("manual pixel does not reliably intersect mapped table area")
            result["pixel"] = {"uv": list(args.pixel), "xyz": _xyz(convert(point))}
        if args.yolo:
            targets = vision.targets(frame_image)
            if table_frame is not None:
                targets = [Target(t.detection, table_frame.point(t.position), t.surface)
                           for t in targets]
            result["fire_targets"] = [
                {"xyz": _xyz(t.position), "surface": t.surface,
                 "confidence": round(t.detection.conf, 4),
                 "uv": [t.detection.u, t.detection.v],
                 "bbox_xywh": [t.detection.u, t.detection.v,
                               t.detection.w, t.detection.h],
                 "anchor_uv": [t.detection.u, t.detection.v if
                               site["vision"]["tabletop_anchor"] == "center"
                               else t.detection.v + t.detection.h / 2]} for t in targets
            ]
        result["status"] = "PASS"
    except (ValueError, RuntimeError) as exc:
        if origin_blocked:
            result["status"] = "BLOCKED"
        result["reason"] = str(exc)
    if args.debug is not None:
        args.debug.parent.mkdir(parents=True, exist_ok=True)
        debug_image = _debug_image(frame_image, args.pixel, targets, result, display_camera)
        if origin_blocked:
            debug_image = _origin_overlay(debug_image, observations[-1], result,
                                          solver_camera, vision.reference_map)
        if not cv2.imwrite(str(args.debug), debug_image):
            raise SystemExit(f"cannot write debug image {args.debug}")
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(output + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
