"""Capture and evaluate one immutable 15-frame RTSP tabletop dataset on the Pi.

This tool reads the camera and YOLO model only. It never contacts nodes or
changes calibration, firmware, the production checkout, or actuator state.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np

from nt532.config import load_site
from nt532.vision.calibration import load_intrinsics
from nt532.vision.camera import open_camera, read_frames
from nt532.vision.localize import project
from nt532.vision.tags import detect_tags
from nt532.vision.types import Camera

FRAME_COUNT = 15
IMAGE_SIZE = (1280, 720)
DEFAULT_SOURCE = "rtsp://127.0.0.1:8554/cam/stream"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_capture(frames: list[np.ndarray], out: Path, source: str) -> dict:
    """Save exactly 15 full-resolution frames without replacing prior evidence."""
    if len(frames) != FRAME_COUNT:
        raise ValueError("capture requires exactly 15 frames")
    if any(frame is None or frame.shape[1::-1] != IMAGE_SIZE for frame in frames):
        raise ValueError("all 15 frames must be 1280x720")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    folder = out / "frames"
    folder.mkdir()
    records = []
    for index, frame in enumerate(frames, 1):
        path = folder / f"frame-{index:03d}.png"
        if not cv2.imwrite(str(path), frame):
            raise RuntimeError(f"cannot save {path}")
        tags = detect_tags(frame)
        pixel_quality = {}
        for tag_id, corners in tags.items():
            edges = np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)
            pixel_quality[str(tag_id)] = {"min_edge_px": round(float(edges.min()), 2)}
        records.append({"name": path.name, "sha256": _hash(path),
                        "detected_tags": sorted(tags),
                        "pixel_quality": pixel_quality,
                        "shape": list(frame.shape)})
    latest = set(records[-1]["detected_tags"])
    rates = {str(tag_id): sum(tag_id in row["detected_tags"] for row in records) / FRAME_COUNT
             for tag_id in (0, 1, 2, 3, 21, 22)}
    ready = ({0, 1, 2, 3, 21, 22} <= latest
             and all(rates[str(tag_id)] >= 0.6 for tag_id in (0, 1, 2, 3, 21, 22))
             and all(records[-1]["pixel_quality"][str(tag_id)]["min_edge_px"] >= 12
                     for tag_id in (0, 1, 2, 3, 21, 22)))
    manifest = {"captured_at_utc": datetime.now(UTC).isoformat(),
                "source": source, "frame_count": FRAME_COUNT,
                "image_size": list(IMAGE_SIZE), "frames": records,
                "detection_rate": rates, "capture_quality": "READY" if ready else "REVIEW"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                       encoding="utf-8")
    return manifest


def validate_capture(out: Path) -> dict:
    """Verify image names, count, dimensions and hashes before any evaluation."""
    out = Path(out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    records = manifest["frames"]
    folder = out / "frames"
    actual = sorted(folder.iterdir())
    expected = [f"frame-{index:03d}.png" for index in range(1, FRAME_COUNT + 1)]
    if (manifest["frame_count"] != FRAME_COUNT or [p.name for p in actual] != expected
            or [row["name"] for row in records] != expected):
        raise ValueError("capture must contain exactly 15 numbered frames")
    for path, record in zip(actual, records):
        if _hash(path) != record["sha256"]:
            raise ValueError(f"frame hash changed: {path.name}")
        image = cv2.imread(str(path))
        if image is None or image.shape[1::-1] != IMAGE_SIZE:
            raise ValueError(f"invalid frame size: {path.name}")
    return manifest


def _positions(data: dict) -> dict[str, np.ndarray]:
    points = {f"ref_{name}": np.asarray(xyz, float)
              for name, xyz in data["references"].items()}
    points.update({f"node_{name}": np.asarray(xyz, float)
                   for name, xyz in data["nodes"].items()})
    points["camera"] = np.asarray(data["camera"]["position"], float)
    return points


def compare_results(origin2: dict, origin22: dict, intrinsics, raw_last_count: int,
                    raw_last: list[dict] | None = None,
                    capture_quality: str = "READY") -> dict:
    """Apply separate mapping, YOLO and integration gates to the same-frame JSON."""
    summary = {"localization": "FAIL", "yolo": "FAIL", "integration": "FAIL",
               "physical_accuracy": "NOT_VERIFIED", "reasons": []}
    if origin2.get("status") != "PASS" or origin22.get("status") != "PASS":
        summary["localization"] = ("BLOCKED" if origin2.get("status") == "PASS"
                                   and origin22.get("status") == "BLOCKED"
                                   else "FAIL")
        summary["reasons"].append(origin22.get("reason") or origin2.get("reason") or
                                  "dynamic mapping failed")
        return summary
    if (origin2.get("anchor_id") != 2 or origin22.get("anchor_id") != 2
            or origin2.get("operational_origin_id") != 2
            or origin22.get("operational_origin_id") != 22
            or origin2.get("coordinate_frame") != "tag_2_table"
            or origin22.get("coordinate_frame") != "tag_22_table"
            or origin2.get("target_anchor") != "center"
            or origin22.get("target_anchor") != "center"):
        summary["reasons"].append("coordinate-frame or tabletop-anchor metadata mismatch")
        return summary
    if (not all({"0", "1", "2", "3"} <= result["references"].keys()
                and {"s1", "s2"} <= result["nodes"].keys()
                for result in (origin2, origin22))):
        summary["reasons"].append("reference ID 0-3 or node ID 21/22 missing")
        return summary
    if np.linalg.norm(origin22["nodes"]["s2"]) > 1e-5:
        summary["reasons"].append("ID 22 is not the operational origin")
        return summary
    old, new = _positions(origin2), _positions(origin22)
    common = sorted(old.keys() & new.keys())
    deviations = {f"{a}/{b}": abs(np.linalg.norm(old[a] - old[b]) -
                                   np.linalg.norm(new[a] - new[b]))
                  for a, b in combinations(common, 2)}
    summary["max_distance_delta_m"] = max(deviations.values(), default=0.)
    ref_camera = {name: value for name, value in deviations.items()
                  if "node_" not in name}
    if max(ref_camera.values(), default=0.) > 0.002 or summary["max_distance_delta_m"] > 0.025:
        summary["reasons"].append("pairwise geometry differs between output frames")
        return summary
    camera2 = Camera(intrinsics, np.asarray(origin2["camera"]["R"], float),
                     np.asarray(origin2["camera"]["t"], float))
    camera22 = Camera(intrinsics, np.asarray(origin22["camera"]["R"], float),
                      np.asarray(origin22["camera"]["t"], float))
    refs = sorted(set(origin2["references"]) & set(origin22["references"]))
    projection_delta = [np.linalg.norm(np.asarray(project(origin2["references"][name], camera2)) -
                                       project(origin22["references"][name], camera22))
                        for name in refs]
    summary["max_projection_delta_px"] = max(projection_delta, default=0.)
    if not refs or summary["max_projection_delta_px"] > 0.1:
        summary["reasons"].append("camera extrinsics do not preserve reference projections")
        return summary
    summary["localization"] = "PASS"
    targets2 = origin2.get("fire_targets", [])
    targets22 = origin22.get("fire_targets", [])
    if raw_last_count < 1:
        summary["reasons"].append("real YOLO found no fire box in the latest frame")
    elif not targets2 or not targets22:
        summary["reasons"].append("YOLO box was not localized on the mapped tabletop")
    elif len(targets2) != len(targets22):
        summary["reasons"].append("localized target counts differ between frames")
    elif raw_last is not None and (len(raw_last) != raw_last_count or not all(
            any(np.allclose(target["bbox_xywh"], detection["bbox_xywh"], atol=0.01)
                and abs(target["confidence"] - detection["confidence"]) <= 0.0001
                for detection in raw_last) for target in targets22)):
        summary["reasons"].append("localized fire target differs from raw YOLO detection")
    else:
        valid = all(
            a["surface"] == b["surface"] == "floor"
            and abs(a["xyz"][2]) < 1e-4 and abs(b["xyz"][2]) < 1e-4
            and np.allclose(a["bbox_xywh"], b["bbox_xywh"], atol=1e-6)
            and np.allclose(a["anchor_uv"], b["anchor_uv"], atol=1e-6)
            and np.allclose(a["anchor_uv"], a["bbox_xywh"][:2], atol=1e-6)
            and abs(a["confidence"] - b["confidence"]) < 1e-6
            for a, b in zip(targets2, targets22)
        )
        projection_deltas = [
            max(np.linalg.norm(np.asarray(project(target["xyz"], camera)) -
                               target["anchor_uv"])
                for target, camera in ((a, camera2), (b, camera22)))
            for a, b in zip(targets2, targets22)
        ]
        distance_deltas = [
            abs(np.linalg.norm(np.asarray(a["xyz"]) - old[name]) -
                np.linalg.norm(np.asarray(b["xyz"]) - new[name]))
            for a, b in zip(targets2, targets22) for name in common
        ]
        summary["max_target_projection_delta_px"] = max(projection_deltas, default=0.)
        summary["max_target_distance_delta_m"] = max(distance_deltas, default=0.)
        valid &= (summary["max_target_projection_delta_px"] <= 2.0
                  and summary["max_target_distance_delta_m"] <= 0.005)
        if valid:
            summary["yolo"] = "PASS"
            if capture_quality == "READY":
                summary["integration"] = "PASS"
            else:
                summary["reasons"].append("15-frame capture quality requires review")
        else:
            summary["reasons"].append(
                "target bbox, anchor, confidence, floor XYZ or projection mismatch")
    return summary


def build_commands(evidence: Path, output: Path, intrinsics: Path, weights: Path,
                   device: str | None) -> dict[int, list[str]]:
    """Run the existing mapping CLI twice on the exact same saved frames."""
    commands = {}
    for origin in (2, 22):
        command = [sys.executable, str(Path(__file__).with_name("dynamic_map.py")),
                   "--image-dir", str(evidence / "frames"),
                   "--intrinsics", str(intrinsics), "--yolo", "--weights", str(weights),
                   "--world-origin", str(origin), "--target-anchor", "center",
                   "--json", str(output / f"origin{origin}.json"),
                   "--debug", str(output / f"origin{origin}_debug.png")]
        if device is not None:
            command += ["--device", device]
        commands[origin] = command
    return commands


def yolo_overlay(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    """Mark every raw YOLO box, including boxes that mapping could not localize."""
    image = frame.copy()
    for item in detections:
        u, v, w, h = item["bbox_xywh"]
        cv2.rectangle(image, (round(u - w / 2), round(v - h / 2)),
                      (round(u + w / 2), round(v + h / 2)), (0, 0, 255), 2)
        cv2.drawMarker(image, (round(u), round(v)), (255, 0, 255),
                       cv2.MARKER_CROSS, 14, 2)
        cv2.putText(image, f"fire {item['confidence']:.3f}",
                    (round(u - w / 2), round(v - h / 2) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    return image


def capture(source: str, out: Path) -> dict:
    """Acquire one RTSP batch; callers provide a new timestamped directory."""
    site = load_site()
    camera = open_camera(site["camera"], source)
    try:
        frames = read_frames(camera, FRAME_COUNT)
    finally:
        camera.release()
    return save_capture(frames, out, source)


def evaluate(evidence: Path, intrinsics_path: Path, weights: Path,
             device: str | None = None) -> dict:
    """Run real YOLO and both output frames; preserve failures as evidence."""
    evidence = Path(evidence)
    manifest = validate_capture(evidence)
    intrinsics_path, weights = Path(intrinsics_path).expanduser(), Path(weights).expanduser()
    if not weights.is_file():
        raise FileNotFoundError(f"YOLO weights not found: {weights}")
    intrinsics = load_intrinsics(intrinsics_path)
    if intrinsics.size != IMAGE_SIZE:
        raise ValueError("camera intrinsics must be calibrated at 1280x720")
    output = evidence / "evaluation"
    output.mkdir(exist_ok=False)
    commands = build_commands(evidence, output, intrinsics_path, weights, device)
    exits = {}
    maps = {}
    for origin, command in commands.items():
        completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", check=False)
        exits[str(origin)] = completed.returncode
        (output / f"origin{origin}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
        (output / f"origin{origin}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        result_path = output / f"origin{origin}.json"
        maps[origin] = (json.loads(result_path.read_text(encoding="utf-8"))
                        if result_path.exists() else
                        {"status": "ERROR", "reason": completed.stderr.strip() or
                         f"CLI exited {completed.returncode}"})
    (output / "commands.json").write_text(json.dumps(
        {"commands": commands, "exit_codes": exits}, indent=2) + "\n", encoding="utf-8")
    from nt532.vision.detect import detect

    site = load_site()
    conf, imgsz = site["vision"]["detect_conf"], site["vision"]["imgsz"]
    yolo = {"weights": str(weights), "confidence_threshold": conf, "imgsz": imgsz,
            "device": device, "frames": [], "status": "NOT_RUN"}
    try:
        for record in manifest["frames"]:
            frame = cv2.imread(str(evidence / "frames" / record["name"]))
            found = detect(frame, weights, conf, imgsz, device)
            yolo["frames"].append({"name": record["name"], "count": len(found),
                                   "detections": [
                                       {"bbox_xywh": [d.u, d.v, d.w, d.h],
                                        "confidence": d.conf, "anchor_uv": [d.u, d.v]}
                                       for d in found]})
        latest = yolo["frames"][-1]["detections"]
        yolo["latest_count"] = len(latest)
        yolo["detection_rate"] = sum(row["count"] > 0 for row in yolo["frames"]) / FRAME_COUNT
        yolo["status"] = "PASS" if latest else "FAIL"
        last_frame = cv2.imread(str(evidence / "frames" / manifest["frames"][-1]["name"]))
        if not cv2.imwrite(str(output / "yolo_latest_debug.png"), yolo_overlay(last_frame, latest)):
            raise RuntimeError("cannot save raw YOLO debug image")
        if not latest:
            diagnostic_conf = min(0.1, conf / 2)
            try:
                candidates = detect(last_frame, weights, diagnostic_conf, imgsz, device)
                yolo["low_confidence_probe"] = {
                    "threshold": diagnostic_conf,
                    "candidates": [{"bbox_xywh": [d.u, d.v, d.w, d.h],
                                    "confidence": d.conf} for d in candidates],
                }
            except Exception as exc:  # noqa: BLE001 - diagnostic cannot erase primary result
                yolo["low_confidence_probe"] = {
                    "threshold": diagnostic_conf,
                    "error": f"{type(exc).__name__}: {exc}",
                }
    except Exception as exc:  # noqa: BLE001 - preserve arbitrary model errors as evidence
        yolo.update(status="ERROR", reason=f"{type(exc).__name__}: {exc}")
    (output / "yolo_stats.json").write_text(json.dumps(yolo, indent=2) + "\n",
                                           encoding="utf-8")
    raw_last_count = (yolo["frames"][-1]["count"] if len(yolo["frames"]) == FRAME_COUNT
                      else 0)
    raw_last = (yolo["frames"][-1]["detections"] if len(yolo["frames"]) == FRAME_COUNT
                else None)
    try:
        summary = compare_results(maps[2], maps[22], intrinsics, raw_last_count, raw_last,
                                  manifest["capture_quality"])
    except Exception as exc:  # noqa: BLE001 - malformed CLI output must still leave a report
        summary = {"localization": "FAIL", "yolo": "FAIL", "integration": "FAIL",
                   "physical_accuracy": "NOT_VERIFIED",
                   "reasons": [f"cannot compare mapping JSON: {type(exc).__name__}: {exc}"]}
    if summary["localization"] == "PASS" and any(
            exits[str(origin)] != 0 or not (output / f"origin{origin}_debug.png").is_file()
            for origin in (2, 22)):
        summary["localization"] = summary["integration"] = "FAIL"
        summary["reasons"].append("mapping CLI failed or debug image missing")
    if yolo["status"] == "ERROR":
        summary["yolo"] = summary["integration"] = "FAIL"
        summary["reasons"].append(f"YOLO inference error: {yolo['reason']}")
    summary.update({"evidence_type": "CAPTURED_FRAMES", "frame_count": FRAME_COUNT,
                    "capture_quality": manifest["capture_quality"], "exit_codes": exits,
                    "raw_yolo_status": yolo["status"],
                    "mapping_quality": {str(i): maps[i].get("quality") for i in (2, 22)},
                    "mapping_diagnostics": {str(i): maps[i].get("diagnostics")
                                            for i in (2, 22)},
                    "frame_sha256": {row["name"]: row["sha256"]
                                     for row in manifest["frames"]}})
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n",
                                         encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture", help="save one 15-frame RTSP batch")
    capture_parser.add_argument("--source", default=DEFAULT_SOURCE)
    capture_parser.add_argument("--out", type=Path, required=True,
                                help="new timestamped evidence directory")
    eval_parser = sub.add_parser("evaluate", help="run real YOLO and ID 2/22 mapping")
    eval_parser.add_argument("--evidence", type=Path, required=True)
    eval_parser.add_argument("--intrinsics", type=Path, required=True)
    eval_parser.add_argument("--weights", type=Path, required=True)
    eval_parser.add_argument("--device", help="YOLO device, e.g. cpu")
    args = parser.parse_args(argv)
    if args.command == "capture":
        result = capture(args.source, args.out)
        print(json.dumps({"capture_quality": result["capture_quality"],
                          "frame_count": result["frame_count"],
                          "detection_rate": result["detection_rate"]}, indent=2))
        return 0 if result["capture_quality"] == "READY" else 1
    summary = evaluate(args.evidence, args.intrinsics, args.weights, args.device)
    print(json.dumps({"localization": summary["localization"], "yolo": summary["yolo"],
                      "integration": summary["integration"],
                      "reasons": summary["reasons"]}, indent=2))
    return 0 if summary["integration"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
