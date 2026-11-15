"""Measured tabletop aim plan and a bounded, manually confirmed relay transaction."""

import csv
import hashlib
import json
import math
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yaml

from nt532.config import REPO_ROOT
from nt532.net.integrated_manual import CoapTransport, IntegratedNode, PulseCalibration
from nt532.orchestrator.laser_geometry import LaserMount
from nt532.vision.calibration import load_intrinsics
from nt532.vision.camera import open_camera, read_frames, read_fresh
from nt532.vision.stream import is_stream_url
from nt532.vision.system import Vision

FRAME = "tag_2_table"


@dataclass(frozen=True)
class AimPlan:
    node: str
    tag_id: int
    target_m: tuple[float, float, float]
    tag_position_m: tuple[float, float, float]
    pivot_m: tuple[float, float, float]
    command_deg: tuple[float, float]
    pulses_us: tuple[int, int]
    predicted_hit_m: tuple[float, float, float] | None


def manual_points(path: Path, default_node: str) -> list[tuple[str, float, float]]:
    """Read real tabletop targets: x,y metres and optional node (s1 or s2)."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {"x", "y"} <= set(reader.fieldnames):
            raise ValueError(f"{path.name} requires x,y columns in metres")
        points = []
        for line, row in enumerate(reader, start=2):
            node = (row.get("node") or default_node).strip()
            if node not in ("s1", "s2"):
                raise ValueError(f"{path.name}:{line} node must be s1 or s2")
            try:
                x, y = float(row["x"]), float(row["y"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name}:{line} has invalid x,y") from exc
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError(f"{path.name}:{line} x,y must be finite")
            points.append((node, x, y))
    if not points:
        raise ValueError(f"{path.name} contains no target points")
    return points


def load_mount(path: Path, node: str, pose):
    """Require a complete measured mount; site.yaml's generic offsets are not used."""
    if not path.is_file():
        raise ValueError(f"CALIBRATION_REQUIRED: missing {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("frame") != FRAME:
        raise ValueError("calibration frame must be tag_2_table")
    row = (data.get("nodes") or {}).get(node) or {}
    fields = ("tag_to_pivot_m", "emitter_offset_m", "home_yaw_deg",
              "home_elevation_deg", "pan_sign", "tilt_sign", "pan_gain", "tilt_gain",
              "pan_limits_deg", "tilt_limits_deg", "pan_home_us", "tilt_home_us",
              "pan_us_per_deg", "tilt_us_per_deg")
    if any(row.get(key) is None for key in fields):
        raise ValueError(f"CALIBRATION_REQUIRED: measured {node} mount/pulse fields are incomplete")
    try:
        if len(row["pan_limits_deg"]) != 2 or len(row["tilt_limits_deg"]) != 2:
            raise ValueError("angle limits must have two endpoints")
        pulse = PulseCalibration(row["pan_home_us"], row["tilt_home_us"],
                                 float(row["pan_us_per_deg"]), float(row["tilt_us_per_deg"]))
        mount = LaserMount(
            pose, np.asarray(row["tag_to_pivot_m"], float),
            np.asarray(row["emitter_offset_m"], float), float(row["home_yaw_deg"]),
            float(row["home_elevation_deg"]), float(row["pan_sign"]),
            float(row["tilt_sign"]), float(row["pan_gain"]), float(row["tilt_gain"]),
            tuple(map(float, row["pan_limits_deg"])),
            tuple(map(float, row["tilt_limits_deg"])),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"CALIBRATION_REQUIRED: invalid measured {node} values: {exc}") from exc
    return mount, pulse


def plan_target(vision, node: str, target: np.ndarray, calibration: Path | None,
                frame: str = FRAME) -> AimPlan:
    if frame != FRAME:
        raise ValueError("only tag_2_table is supported; tag_22_table requires a tested transform")
    if (np.asarray(target).shape != (3,) or not np.isfinite(target).all()
            or abs(float(target[2])) > 1e-9):
        raise ValueError("target must be finite (x,y,0) metres")
    if vision.reference_mode != "auto" or vision.reference_map is None:
        raise ValueError("Dynamic Mapping auto in tag_2_table is required")
    reasons = vision.blockers(node)
    if reasons:
        raise ValueError("mapping/pose blocked: " + "; ".join(reasons))
    if not vision.in_target_area(target):
        raise ValueError("target outside Dynamic Mapping table area")
    pose = vision.nodes[node]
    if pose.tag_id != {"s1": 21, "s2": 22}.get(node):
        raise ValueError("node tag identity mismatch (s1/ID21, s2/ID22)")
    if vision.state.tag_rate.get(2, 0) < vision.cfg["min_tag_rate"] or (
            vision.state.tag_rate.get(pose.tag_id, 0) < vision.cfg["min_tag_rate"]):
        raise ValueError("anchor or node tag visibility below minimum rate")
    if calibration is None:
        raise ValueError("CALIBRATION_REQUIRED: supply measured --calibration")
    mount, pulse = load_mount(calibration, node, pose)
    result = mount.target(target)
    if result.status != "PASS" or result.command_deg is None:
        raise ValueError("target outside measured mount reach")
    pulses = pulse.pulses(*result.command_deg)
    return AimPlan(node, pose.tag_id, tuple(map(float, target)),
                   tuple(map(float, pose.position)),
                   tuple(map(float, pose.to_world(mount.tag_to_pivot))),
                   result.command_deg, pulses,
                   None if result.table_hit_m is None else tuple(map(float, result.table_hit_m)))


def run_transaction(node: IntegratedNode, pulses: tuple[int, int], hold_seconds: float,
                    confirm, capture_on, before_on=None) -> dict:
    """All network paths exit through OFF/STOP, including Ctrl+C and camera failures."""
    if not 0 < hold_seconds <= 120:
        raise ValueError("hold-seconds must be in (0,120]")
    result = None
    verified = False
    try:
        info = node.verify_identity()
        verified = True
        home = node.arm_home()
        actual = node.move(*pulses)
        if before_on:
            before_on()
        if confirm().strip() != "OK":
            raise ValueError("operator must enter OK immediately before relay ON")
        started = time.monotonic()
        node.laser_on()
        on_frame = capture_on()
        remaining = hold_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("camera capture exceeded bounded relay hold")
        shown = None

        def countdown(seconds_left):
            nonlocal shown
            current = math.ceil(seconds_left)
            if current != shown:
                shown = current
                print(f"Relay hold: {current}s left (Ctrl+C or external STOP aborts)")

        node.hold(remaining, tick=countdown)
        result = {"info": info, "home": home, "actual": actual,
                  "on_frame": on_frame, "held_s": time.monotonic() - started}
    finally:
        if verified:
            final = node.shutdown()
            if final["errors"]:
                print("OFF/STOP NOT VERIFIED; cut physical laser power: " +
                      "; ".join(final["errors"]))
    if final["errors"]:
        raise RuntimeError("OFF/STOP state not verified")
    result["final"] = final
    return result


def run_manual(args, site: dict) -> dict:
    """Acquire current 15-frame map, then optionally execute one measured target."""
    if args.surface != "floor" or args.frame != FRAME:
        raise ValueError("manual real-node mode requires --surface floor --frame tag_2_table")
    if args.x is None or args.y is None or args.z is not None or args.grid or args.points:
        raise ValueError("manual real-node mode requires one --x and --y in metres")
    if not all(math.isfinite(v) for v in (args.x, args.y)):
        raise ValueError("target coordinates must be finite")
    if not 0 < args.hold_seconds <= 120:
        raise ValueError("hold-seconds must be in (0,120]")
    if args.execute and not (is_stream_url(args.source) or
                             (isinstance(args.source, str) and args.source.isdigit())):
        raise ValueError("--execute requires an explicit live camera source")
    site = dict(site)
    site["vision"] = {**site["vision"], "reference_mode": "auto", "tabletop_anchor": "center"}
    target = np.array([args.x, args.y, 0.0], dtype=float)
    row = {"timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
           "frame": FRAME, "node": args.node, "source": args.source, "target_m": target.tolist(),
           "status": "BLOCKED", "spot_method": None, "spot_m": None, "error_cm": None,
           "physical_localization": "NOT VERIFIED"}
    row["head"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                                  capture_output=True, text=True, check=False).stdout.strip()
    row["hold_requested_s"] = args.hold_seconds
    cap = open_camera(site["camera"], args.source)
    try:
        intrinsics = load_intrinsics(args.intrinsics or REPO_ROOT / site["camera"]["intrinsics"])
        vision = Vision(site, intrinsics)
        frames = read_frames(cap, 15)
        vision.commission(frames)
        mapping = vision.reference_map
        row["mapping"] = {"frames_used": mapping.frames_used,
                          "metric_error_m": mapping.metric_error_m,
                          "reprojection_px": mapping.reprojection_px,
                          "ambiguity_m": mapping.ambiguity_m}
        try:
            plan = plan_target(vision, args.node, target, args.calibration, args.frame)
        except ValueError as exc:
            row["blocker"] = str(exc)
            print(str(exc))
            return row
        row["plan"] = asdict(plan)
        row["calibration"] = str(args.calibration)
        row["calibration_sha256"] = hashlib.sha256(args.calibration.read_bytes()).hexdigest()
        print(json.dumps({"frame": FRAME, "node": args.node, "plan": row["plan"],
                          "mapping": row["mapping"], "relay": "OFF (dry run)"}, indent=2))
        if not args.execute:
            row["status"] = "DRY RUN"
            return row
        address = (site.get("network", {}).get("nodes") or {}).get(args.node)
        if not address:
            row["blocker"] = "node address missing in network.nodes"
            return row
        row["node_address_tail"] = address[-12:]
        transport = CoapTransport(address)
        off = None
        try:
            node = IntegratedNode(args.node, transport)

            def before_on():
                nonlocal off
                vision.check(read_frames(cap, 15))
                refreshed = plan_target(vision, args.node, target, args.calibration, args.frame)
                if refreshed.pulses_us != plan.pulses_us:
                    raise ValueError("pose or mapping moved after target command")
                off = read_fresh(cap)
                row["off_timestamp"] = datetime.now().astimezone().isoformat(timespec="milliseconds")

            def capture_on():
                frame = read_fresh(cap)
                row["on_timestamp"] = datetime.now().astimezone().isoformat(
                    timespec="milliseconds")
                return frame

            try:
                result = run_transaction(node, plan.pulses_us, args.hold_seconds,
                                         lambda: input("Enter OK immediately before relay ON: "),
                                         capture_on, before_on)
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - preserve trial evidence
                row["error"] = f"{type(exc).__name__}: {exc}"
                row["final_off"] = node.last_shutdown
                return row
            on = result.pop("on_frame")
            row["node_result"] = result
            spot = vision.find_spot(on, off)
            image_dir = args.out.parent if args.out else Path("runs/measure")
            image_dir.mkdir(parents=True, exist_ok=True)
            stem = f"aim_{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}_{args.node}"
            row["off_image"] = str(image_dir / f"{stem}_off.png")
            row["on_image"] = str(image_dir / f"{stem}_on.png")
            if not cv2.imwrite(row["off_image"], off) or not cv2.imwrite(row["on_image"], on):
                raise RuntimeError("failed to save original OFF/ON frames")
            row["status"] = "NOT MEASURABLE"
            if spot is not None and abs(float(spot[2])) <= 0.01 and vision.in_target_area(spot):
                row["spot_candidate_m"] = list(map(float, spot))
                if input("Relay OFF. Confirm real tabletop spot (type SPOT): ").strip() == "SPOT":
                    row["spot_method"] = "camera"
                    row["spot_m"] = list(map(float, spot))
            if row["spot_m"] is None:
                entry = input("Relay OFF. Ruler spot X,Y in tag_2_table metres (blank if none): ").strip()
                if entry:
                    measured = [float(value.strip()) for value in entry.split(",")]
                    if len(measured) != 2 or not all(math.isfinite(value) for value in measured):
                        raise ValueError("manual spot must be finite X,Y metres")
                    row["spot_method"] = "manual"
                    row["spot_m"] = [*measured, 0.0]
            if row["spot_m"] is not None:
                row["error_cm"] = 100 * math.hypot(row["spot_m"][0] - args.x,
                                                   row["spot_m"][1] - args.y)
                row["status"] = "MEASURED"
            return row
        finally:
            try:
                transport.close()
            except Exception as exc:  # noqa: BLE001 - preserve OFF/STOP evidence
                row["transport_close_error"] = str(exc)
                row["status"] = "BLOCKED"
    finally:
        cap.release()


def save_rows(path: Path, rows: list[dict]):
    """Write all real trials as one JSON array and one CSV without replacing a trial's fields."""
    if not rows:
        raise ValueError("no trial rows to save")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, default=str) if isinstance(value, (dict, list))
                             else value for key, value in row.items()})
