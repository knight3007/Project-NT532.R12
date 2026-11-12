"""Headless Dynamic Reference Mapping probe; reads calibration, never writes it.

    uv run python scripts/dynamic_map.py --image frame.jpg --pixel 640,360 --json map.json --debug map.jpg
    uv run python scripts/dynamic_map.py --source rtsp://127.0.0.1:8554/cam/stream --frames 15
    uv run python scripts/dynamic_map.py --image frame.jpg --yolo --weights models/fire-n.pt
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from nt532.config import REPO_ROOT, load_site
from nt532.vision.calibration import load_intrinsics
from nt532.vision.camera import open_camera, read_frames
from nt532.vision.system import Vision


def _pair(value: str) -> tuple[float, float]:
    try:
        u, v = (float(x) for x in value.split(","))
        return u, v
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("pixel must be U,V") from exc


def _xyz(point: np.ndarray) -> list[float]:
    return [round(float(v), 5) for v in point]


def _debug_image(frame: np.ndarray, vision: Vision, pixel, targets, status: str) -> np.ndarray:
    image = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
    cv2.putText(image, status, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (0, 180, 0) if status == "PASS" else (0, 0, 255), 2)
    if vision.reference_map is None:
        return image
    from nt532.vision.localize import project

    for tag_id, position in vision.reference_map.references.items():
        u, v = project(position, vision.camera)
        cv2.drawMarker(image, (round(u), round(v)), (255, 150, 0), cv2.MARKER_CROSS, 20, 2)
        cv2.putText(image, f"ID {tag_id}: {position[0]:.3f},{position[1]:.3f}",
                    (round(u) + 8, round(v) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (255, 150, 0), 1)
    if pixel is not None:
        cv2.drawMarker(image, tuple(map(round, pixel)), (0, 255, 255), cv2.MARKER_CROSS, 24, 2)
    for target in targets:
        d = target.detection
        cv2.rectangle(image, (round(d.u - d.w / 2), round(d.v - d.h / 2)),
                      (round(d.u + d.w / 2), round(d.v + d.h / 2)), (0, 0, 255), 2)
        cv2.putText(image, f"{target.position[0]:.3f},{target.position[1]:.3f},0",
                    (round(d.u), round(d.v)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="single image file")
    source.add_argument("--source", help="RTSP URL or camera source accepted by open_camera")
    parser.add_argument("--site", type=Path, help="site YAML; default config/site.yaml")
    parser.add_argument("--intrinsics", type=Path, help="camera YAML; default site camera.intrinsics")
    parser.add_argument("--frames", type=int, default=15, help="frames from source (default 15)")
    parser.add_argument("--pixel", type=_pair, help="manual table pixel U,V")
    parser.add_argument("--yolo", action="store_true", help="optional YOLO fire detections")
    parser.add_argument("--weights", type=Path, help="YOLO weights; default site vision.weights")
    parser.add_argument("--device", help="YOLO device, e.g. cpu")
    parser.add_argument("--json", type=Path, help="write JSON result")
    parser.add_argument("--debug", type=Path, help="write annotated image")
    args = parser.parse_args(argv)
    if args.frames < 1:
        parser.error("--frames must be positive")
    site = load_site(args.site)
    site["vision"] = {**site["vision"], "reference_mode": "auto"}
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
    else:
        cap = open_camera(site["camera"], args.source)
        try:
            images = read_frames(cap, args.frames)
        finally:
            cap.release()
        frame = images[-1]
    if frame.shape[1::-1] != intrinsics.size:
        raise SystemExit(f"image size {frame.shape[1::-1]} differs from calibration {intrinsics.size}")
    result = {"status": "FAIL", "mode": "auto", "anchor_id": 2, "unit": "m"}
    targets = []
    try:
        state = vision.commission(images)
        mapping = vision.reference_map
        result["references"] = {str(i): _xyz(p) for i, p in sorted(mapping.references.items())}
        result["missing_references"] = sorted(set(range(4)) - set(mapping.references))
        result["camera"] = {"position": _xyz(state.camera.position),
                            "R": state.camera.R.tolist(), "t": _xyz(state.camera.t)}
        result["nodes"] = {name: _xyz(pose.position) for name, pose in state.nodes.items()}
        result["quality"] = {"anchor_reprojection_px": round(mapping.reprojection_px, 3),
                             "metric_error_m": round(mapping.metric_error_m, 5),
                             "pose_ambiguity_m": round(mapping.ambiguity_m, 5),
                             "frames_used": mapping.frames_used}
        if args.pixel is not None:
            point = vision.localize_table(args.pixel)
            if point is None:
                raise ValueError("manual pixel does not reliably intersect mapped table area")
            result["pixel"] = {"uv": list(args.pixel), "xyz": _xyz(point)}
        if args.yolo:
            targets = vision.targets(frame)
            result["fire_targets"] = [
                {"xyz": _xyz(t.position), "surface": t.surface,
                 "confidence": round(t.detection.conf, 4),
                 "uv": [t.detection.u, t.detection.v]} for t in targets
            ]
        result["status"] = "PASS"
    except (ValueError, RuntimeError) as exc:
        result["reason"] = str(exc)
    if args.debug is not None:
        args.debug.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.debug), _debug_image(frame, vision, args.pixel, targets,
                                                         result["status"])):
            raise SystemExit(f"cannot write debug image {args.debug}")
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(output + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
