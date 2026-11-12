"""Offline A/B aiming geometry evaluation. No hardware, firmware, or runtime entry point.

Run from pi/: python scripts/aiming_geometry_audit.py --out ../reports/aiming_evaluation
The evaluator owns all 3D truth; strategy B receives only noisy image observations.
"""

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nt532.orchestrator import aiming
from nt532.sim.render import in_frame, look_at, yaw_matrix
from nt532.sim.scene import Node, default_intrinsics
from nt532.vision.dynamic_mapping import DynamicMap
from nt532.vision.localize import pixel_ray, project
from nt532.vision.system import Commissioning, Vision
from nt532.vision.types import Detection, TagPose

# Every number here is a simulation ASSUMPTION, not a measured actuator setting.
ASSUMPTIONS = {
    "target_radius_m": 0.03,
    "pixel_noise_sigma_px": 0.6,
    "spot_noise_sigma_px": 0.6,
    "spot_loss_probability": 0.05,
    "one_step_delay_probability": 0.10,
    "servo_backlash_deg": 0.3,
    "servo_pwm_us_per_degree": 2000.0 / 180.0,  # firmware HAL nominal -90..+90
    "pan_pwm_range_us": (500, 2500),
    "tilt_pwm_range_us": (600, 2500),
    "home_pwm_us": 1500,
    "pan_command_limits_deg": (-70.0, 70.0),
    "tilt_command_limits_deg": (-35.0, 45.0),
    "camera_position_m": (0.60, -0.75, 0.45),
    "camera_look_at_m": (0.60, 0.45, 0.0),
    "bbox_height_px": 0.0,  # point-target geometry: no invented fire-box shape
}
REFERENCE_XY = {2: (0.18, 0.22), 0: (1.02, 0.22), 1: (1.02, 0.68), 3: (0.18, 0.68)}
HEIGHTS_M = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30)
POSITIONS_XY = {
    "near_A": (0.32, 0.34), "near_B": (0.88, 0.34),
    "overlap": (0.60, 0.48), "far_camera": (0.60, 0.70),
    "near_camera": (0.60, 0.12), "edge": (0.10, 0.74),
}
SCENARIOS = (
    "nominal", "pivot_plus_1cm", "pivot_minus_1cm", "tilt_home_plus_5",
    "servo_bias_2deg", "servo_gain_plus_10pct", "camera_pose_1deg", "wrong_vertical_offset",
    "tag_axis_yaw", "pan_sign_reversed", "bbox_bottom_12px",
)


@dataclass(frozen=True)
class Rig:
    node: str
    mount: str
    pose: TagPose
    true_offset: np.ndarray
    true_pivot: np.ndarray
    estimated_pivot: np.ndarray
    true_yaw_deg: float
    assumed_yaw_deg: float
    home_tilt_deg: float
    pan_sign: float
    servo_gain: float
    servo_bias_deg: tuple[float, float]


def make_camera(scenario: str = "nominal"):
    eye = ASSUMPTIONS["camera_position_m"]
    look = ASSUMPTIONS["camera_look_at_m"]
    if scenario == "camera_pose_1deg":
        # Approximate one-degree pose error at 1.2 m range, estimator only.
        look = (look[0] + 0.021, look[1], look[2])
    return look_at(eye, look, default_intrinsics())


def make_rig(mount: str, node: str, scenario: str = "nominal") -> Rig:
    if mount not in ("horizontal", "vertical") or node not in ("s1", "s2"):
        raise ValueError("mount must be horizontal/vertical and node must be s1/s2")
    x = 0.24 if node == "s1" else 0.96
    yaw = -10.0 if node == "s1" else 10.0  # tag top edge, not PAN HOME
    home = -35.0 if node == "s1" else 35.0  # measured only in this synthetic world
    # Vertical tag normal points toward the camera (-Y); its top edge points +Z.
    rx = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])
    rotation = yaw_matrix(yaw) @ (np.eye(3) if mount == "horizontal" else rx)
    pose = TagPose(21 if node == "s1" else 22, np.array([x, 0.10, 0.20]), rotation)
    true_offset = np.array([0.0, 0.0, 0.10] if mount == "horizontal" else [0.0, 0.10, 0.0])
    estimated_offset = true_offset.copy()
    if scenario == "pivot_plus_1cm":
        estimated_offset *= 1.1
    elif scenario == "pivot_minus_1cm":
        estimated_offset *= 0.9
    elif scenario == "wrong_vertical_offset" and mount == "vertical":
        estimated_offset = np.array([0.0, 0.0, 0.10])
    # Use the exact production tag-to-world transform and explicit 10 cm input.
    site = {"actuator": {"pivot_offset": estimated_offset.tolist()}}
    estimated_pivot = pose.to_world(aiming.pivot_offset(site))
    assumed_yaw = aiming.heading_yaw_deg(pose) if scenario == "tag_axis_yaw" else home
    return Rig(node, mount, pose, true_offset, pose.to_world(true_offset), estimated_pivot,
               home, assumed_yaw, 5.0 if scenario == "tilt_home_plus_5" else 0.0,
               -1.0 if scenario == "pan_sign_reversed" else 1.0,
               1.1 if scenario == "servo_gain_plus_10pct" else 1.0,
               (2.0, -2.0) if scenario == "servo_bias_2deg" else (0.0, 0.0))


def ray_for_command(rig: Rig, pan: float, tilt: float) -> np.ndarray:
    """Physical ray after the assumed PWM-to-angle mapping and HOME offsets."""
    node = Node(rig.node, rig.pose.tag_id, rig.true_pivot, 0.0,
                pivot_offset=np.zeros(3), facing_deg=rig.true_yaw_deg)
    node.pan = rig.pan_sign * rig.servo_gain * pan + rig.servo_bias_deg[0]
    node.tilt = rig.servo_gain * tilt + rig.home_tilt_deg + rig.servo_bias_deg[1]
    return node.direction  # reuse nt532.sim's ray convention


def site_for(rig: Rig) -> dict:
    return {
        "board": {"plane_y": 0.80, "width": 1.20, "height": 0.60},
        "floor": {"enabled": True, "width": 1.20, "depth": 0.80},
        "tags": {"reference": {"size": 0.08, "positions": REFERENCE_XY},
                 "nodes": {"size": 0.08, "ids": {"s1": 21, "s2": 22}}},
        "vision": {"reference_mode": "auto", "board_margin_m": 0.02,
                   "auto_extrapolation_m": 0.20, "laser_min_rise": 40},
        "actuator": {"pivot_offset": rig.true_offset.tolist(), "nodes": {
            "s1": {"pan_limits": [-70, 70], "tilt_limits": [-35, 45]},
            "s2": {"pan_limits": [-70, 70], "tilt_limits": [-35, 45]},
        }},
        "targeting": {"sensor_match_radius_x": 0.35},
    }


def _vision(camera, rig: Rig, scenario: str) -> Vision:
    site = site_for(rig)
    vision = Vision(site, camera.intrinsics)
    refs = {k: np.array([*v, 0.0]) for k, v in REFERENCE_XY.items()}
    vision.reference_map = DynamicMap(camera, refs, 0, 0, 0)
    vision.state = Commissioning(camera, {rig.node: rig.pose}, 0, {k: 1.0 for k in refs})
    if scenario == "untrusted_reference":
        vision.stale.add("camera")
    return vision


def _inside_view(point, camera) -> bool:
    return bool(in_frame(np.asarray(point), camera, camera.intrinsics.size)[0])


def _line_metrics(rig: Rig, point, ray) -> tuple[float, float]:
    v = np.asarray(point, float) - rig.true_pivot
    length = float(np.linalg.norm(v))
    along = max(float(np.dot(v, ray)), 0.0)
    return float(np.linalg.norm(v - length * ray) * 100), float(np.linalg.norm(v - along * ray) * 100)


def _angles_ok(site: dict, node: str, pan: float, tilt: float) -> bool:
    if not aiming.limits(site, node).ok(pan, tilt):
        return False
    home = ASSUMPTIONS["home_pwm_us"]
    scale = ASSUMPTIONS["servo_pwm_us_per_degree"]
    return (ASSUMPTIONS["pan_pwm_range_us"][0] <= home + scale * pan <= ASSUMPTIONS["pan_pwm_range_us"][1]
            and ASSUMPTIONS["tilt_pwm_range_us"][0] <= home + scale * tilt <= ASSUMPTIONS["tilt_pwm_range_us"][1])


def _background_hit(rig: Rig, ray) -> np.ndarray | None:
    p, hits = rig.true_pivot, []
    if ray[2] < -1e-9:
        s = -p[2] / ray[2]
        q = p + s * ray
        if s > 0 and 0 <= q[0] <= 1.2 and 0 <= q[1] <= 0.8:
            hits.append((s, q))
    if ray[1] > 1e-9:
        s = (0.8 - p[1]) / ray[1]
        q = p + s * ray
        if s > 0 and 0 <= q[0] <= 1.2 and 0 <= q[2] <= 0.6:
            hits.append((s, q))
    return min(hits, key=lambda hit: hit[0])[1] if hits else None


def _visible_spot(rig: Rig, target, camera, pan: float, tilt: float):
    """Evaluator's optical scene: a camera-facing circular patch occludes the background."""
    ray = ray_for_command(rig, pan, tilt)
    normal = camera.position - target
    normal /= np.linalg.norm(normal)
    denominator = float(np.dot(ray, normal))
    background = _background_hit(rig, ray)
    background_range = (float(np.linalg.norm(background - rig.true_pivot))
                        if background is not None else math.inf)
    if abs(denominator) > 1e-9:
        s = float(np.dot(target - rig.true_pivot, normal) / denominator)
        q = rig.true_pivot + s * ray
        if (0 < s <= background_range + 1e-6
                and np.linalg.norm(q - target) <= ASSUMPTIONS["target_radius_m"]
                and _inside_view(q, camera)):
            return project(q, camera), "target"
    if background is not None and _inside_view(background, camera):
        return project(background, camera), "background"
    return None, "none"


def _feedback(node: str, target_px, observe, site, start, rng):
    """Image-only finite-difference servo; XYZ exists only inside the observation callback."""
    cmd = np.array(start, float)
    last_delta = np.zeros(2)
    previous_observation = None
    last_surface = "none"
    for iteration in range(1, 9):
        probes = [cmd, cmd + [1.0, 0], cmd + [0, 1.0]]
        readings = []
        for probe in probes:
            if not _angles_ok(site, node, *probe):
                return cmd, "OUT_OF_RANGE", "probe_limit", iteration, last_surface, math.nan
            pixel, surface = observe(*probe)
            if pixel is None or rng.random() < ASSUMPTIONS["spot_loss_probability"]:
                return cmd, "LOST_SPOT", "spot_not_observed", iteration, surface, math.nan
            measured = np.asarray(pixel) + rng.normal(0, ASSUMPTIONS["spot_noise_sigma_px"], 2)
            if previous_observation is not None and rng.random() < ASSUMPTIONS["one_step_delay_probability"]:
                measured = previous_observation.copy()
            previous_observation = measured.copy()
            readings.append((measured, surface))
        (spot_px, surface), (pan_px, pan_surface), (tilt_px, tilt_surface) = readings
        last_surface = surface
        residual = float(np.linalg.norm(target_px - spot_px))
        if residual <= 2.0:
            if surface != "target":
                return cmd, "UNKNOWN", "pixel_aligned_on_background", iteration, surface, residual
            return cmd, "CONVERGED_TARGET", "target_surface_observed", iteration, surface, residual
        if surface != pan_surface or surface != tilt_surface:
            return cmd, "UNKNOWN", "spot_surface_changed_during_probe", iteration, surface, residual
        jacobian = np.column_stack((pan_px - spot_px, tilt_px - spot_px))
        if abs(float(np.linalg.det(jacobian))) < 0.1:
            return cmd, "NOT_CONVERGED", "image_jacobian_singular", iteration, surface, residual
        delta = np.clip(np.linalg.solve(jacobian, target_px - spot_px), -5.0, 5.0)
        # Backlash on a small reversal: the physical angle remains at the previous command.
        delta[(delta * last_delta < 0) & (np.abs(delta) < ASSUMPTIONS["servo_backlash_deg"])] = 0
        if np.linalg.norm(delta) < 0.05:
            return cmd, "NOT_CONVERGED", "stalled_by_backlash", iteration, surface, residual
        if not _angles_ok(site, node, *(cmd + delta)):
            return cmd, "OUT_OF_RANGE", "next_command_limit", iteration, surface, residual
        cmd += delta
        last_delta = delta
    return cmd, "NOT_CONVERGED", "iteration_limit", 8, last_surface, residual


def run_case(mount: str, node: str, xyz, scenario: str = "nominal", strategy: str = "A", *,
             seed: int = 0, depth_error_m: float = 0.0) -> dict:
    if strategy not in ("A", "B", "C", "D"):
        raise ValueError("strategy must be A/B/C/D")
    target = np.asarray(xyz, float)
    rig = make_rig(mount, node, scenario)
    camera, estimated_camera = make_camera(), make_camera(scenario)
    ideal_pan, ideal_tilt = aiming.angles(rig.true_pivot, target, rig.true_yaw_deg)
    row = {"mount": mount, "node": node, "scenario": scenario, "strategy": strategy,
           "x_m": target[0], "y_m": target[1], "z_m": target[2],
           "estimated_x_m": math.nan, "estimated_y_m": math.nan, "estimated_z_m": math.nan,
           "pivot_x_m": rig.true_pivot[0], "pivot_y_m": rig.true_pivot[1], "pivot_z_m": rig.true_pivot[2],
           "ideal_pan_deg": ideal_pan, "ideal_tilt_deg": ideal_tilt,
           "command_pan_deg": math.nan, "command_tilt_deg": math.nan,
           "pan_error_deg": math.nan, "tilt_error_deg": math.nan,
           "aim_position_error_cm": math.nan, "ray_miss_cm": math.nan,
           "hit": False, "verified_3d_hit": False, "reachable": False,
           "converged": False, "status": "BLOCKED", "reason": "", "spot_surface": "none",
           "pixel_residual_px": math.nan, "iterations": 0, "requires_depth": strategy == "C"}
    row["requires_depth"] = strategy in ("C", "D")
    row["depth_error_m"] = depth_error_m if strategy == "D" else math.nan
    if scenario == f"missing_{node}":
        row["reason"] = "node_pose_missing"
        return row
    if scenario == "untrusted_reference":
        row["reason"] = "reference_untrusted"
        return row
    if not _inside_view(target, camera):
        row.update(status="OUT_OF_VIEW", reason="target_outside_camera_frame")
        return row
    if strategy == "C":
        estimated = target
    elif strategy == "D":
        # Synthetic metric-depth predictor: evaluator perturbs the true camera range.
        # No AI model or truth is available to the actual A/B controllers.
        origin, direction = pixel_ray(project(target, camera), camera)
        estimated = origin + (np.linalg.norm(target - origin) + depth_error_m) * direction
    else:
        pixel = np.asarray(project(target, camera))
        rng = np.random.default_rng(seed)
        # Zero-noise geometry baseline; noise is injected in feedback observations below.
        pixel += rng.normal(0, 0.0, 2)
        box_height = 12.0 if scenario == "bbox_bottom_12px" else ASSUMPTIONS["bbox_height_px"]
        detection = Detection(*pixel, 0.0, box_height, 0.8)
        vision = _vision(estimated_camera, rig, scenario)
        hit = vision.localize_detection(detection)
        if hit is None:
            row["reason"] = "floor_intersection_rejected_by_auto_area"
            return row
        estimated = hit[0]
    row.update(estimated_x_m=estimated[0], estimated_y_m=estimated[1], estimated_z_m=estimated[2])
    pan, tilt = aiming.angles(rig.estimated_pivot, estimated, rig.assumed_yaw_deg)
    row.update(command_pan_deg=pan, command_tilt_deg=tilt)
    site = site_for(rig)
    if not _angles_ok(site, rig.node, pan, tilt):
        row.update(status="OUT_OF_RANGE", reason="command_or_pwm_limit")
        return row
    row["reachable"] = True
    if strategy == "B":
        rng = np.random.default_rng(seed)
        target_px = np.asarray(project(target, camera)) + rng.normal(
            0, ASSUMPTIONS["pixel_noise_sigma_px"], 2)
        # This sensor callback alone owns the 3D scene; _feedback sees only its output.
        def observe(probe_pan, probe_tilt):
            if scenario == "spot_unobservable":
                return None, "none"
            return _visible_spot(rig, target, camera, probe_pan, probe_tilt)
        (pan, tilt), status, reason, iterations, surface, residual = _feedback(
            rig.node, target_px, observe, site, (pan, tilt), rng)
        row.update(command_pan_deg=pan, command_tilt_deg=tilt, status=status, reason=reason,
                   iterations=iterations, spot_surface=surface, pixel_residual_px=residual,
                   converged=status in ("CONVERGED_TARGET", "UNKNOWN") and residual <= 2.0)
    physical_pan = rig.pan_sign * rig.servo_gain * pan + rig.servo_bias_deg[0]
    physical_tilt = rig.servo_gain * tilt + rig.home_tilt_deg + rig.servo_bias_deg[1]
    position_error, miss = _line_metrics(rig, target, ray_for_command(rig, pan, tilt))
    hit = miss <= ASSUMPTIONS["target_radius_m"] * 100
    row.update(pan_error_deg=physical_pan + rig.true_yaw_deg - (ideal_pan + rig.true_yaw_deg),
               tilt_error_deg=physical_tilt - ideal_tilt,
               aim_position_error_cm=position_error, ray_miss_cm=miss, hit=hit)
    if strategy == "B" and row["status"] == "CONVERGED_TARGET":
        row["status"] = "PASS" if hit else "FAIL"
    if strategy != "B":
        row.update(status="PASS" if hit else "FAIL", reason="geometric_hit" if hit else "geometric_miss",
                   converged=True)
    row["verified_3d_hit"] = bool(strategy == "C" and hit or strategy == "B" and row["status"] == "PASS")
    return row


def evaluate() -> list[dict]:
    rows = []
    for scenario in SCENARIOS:
        for mount in ("horizontal", "vertical"):
            if scenario == "wrong_vertical_offset" and mount != "vertical":
                continue
            for node in ("s1", "s2"):
                for z in HEIGHTS_M:
                    for name, (x, y) in POSITIONS_XY.items():
                        for strategy in ("A", "B", "C"):
                            row = run_case(mount, node, (x, y, z), scenario, strategy,
                                           seed=int(z * 1000) + len(name) + (node == "s2"))
                            row["position"] = name
                            rows.append(row)
    for scenario, node in (("missing_s1", "s1"), ("missing_s2", "s2"),
                           ("untrusted_reference", "s1")):
        for strategy in ("A", "B", "C"):
            row = run_case("horizontal", node, (0.60, 0.48, 0.10), scenario, strategy)
            row["position"] = "overlap"
            rows.append(row)
    for scenario, strategy, point in (("spot_unobservable", "B", (0.72, 0.12, 0.0)),
                                      ("outside_view", "A", (2.0, 0.50, 0.10))):
        row = run_case("horizontal", "s1", point, scenario, strategy)
        row["position"] = "special"
        rows.append(row)
    return rows


def depth_sweep() -> list[dict]:
    rows = []
    for error in (0.0, -0.01, 0.01, -0.02, 0.02, -0.05, 0.05, -0.10, 0.10):
        for mount in ("horizontal", "vertical"):
            for node in ("s1", "s2"):
                for z in HEIGHTS_M:
                    for name, (x, y) in POSITIONS_XY.items():
                        row = run_case(mount, node, (x, y, z), "nominal", "D", depth_error_m=error)
                        row["position"] = name
                        rows.append(row)
    return rows


def _svg_line(path: Path, title: str, series: dict[str, list[tuple[float, float]]], ylabel: str):
    colors = ("#2665a8", "#d06033", "#32956c", "#8e58a8", "#af8625", "#538d9d")
    all_values = [v for pts in series.values() for _, v in pts if np.isfinite(v)]
    ymax = max(all_values, default=1) * 1.15 or 1
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="760" height="420" viewBox="0 0 760 420">',
             '<rect width="760" height="420" fill="white"/>',
             f'<text x="50" y="28" font-size="19" font-family="sans-serif">{title}</text>',
             '<path d="M70 355 H710 M70 355 V95" stroke="#333" fill="none"/>',
             f'<text x="8" y="87" font-size="12" font-family="sans-serif">{ylabel}</text>']
    for fraction in (0, 0.5, 1):
        y = 355 - 250 * fraction
        parts.append(f'<path d="M70 {y:.0f} H710" stroke="#dddddd"/>')
        parts.append(f'<text x="35" y="{y + 4:.0f}" font-size="11">{ymax * fraction:.1f}</text>')
    for z in HEIGHTS_M:
        x = 70 + z / 0.30 * 640
        parts.append(f'<text x="{x-10:.0f}" y="378" font-size="12">{z*100:.0f}</text>')
    parts.append('<text x="330" y="407" font-size="13">Target Z (cm)</text>')
    for index, (label, points) in enumerate(series.items()):
        color = colors[index % len(colors)]
        coords = [(70 + z / 0.30 * 640, 355 - v / ymax * 250) for z, v in points if np.isfinite(v)]
        if coords:
            point_text = ' '.join(f'{x:.1f},{y:.1f}' for x, y in coords)
            parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{point_text}"/>')
        parts.append(f'<text x="{85 + index % 3 * 210}" y="{49 + index // 3 * 19}" '
                     f'fill="{color}" font-size="13">{label}</text>')
    parts.append('</svg>')
    path.write_text('\n'.join(parts), encoding="utf-8")


def write_outputs(rows: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    groups = {}
    for row in rows:
        key = (row["scenario"], row["mount"], row["node"], row["strategy"], row["z_m"])
        groups.setdefault(key, []).append(row)
    summary_fields = ("scenario", "mount", "node", "strategy", "z_m", "cases", "reachable",
                      "hit", "pass", "unknown", "blocked_or_unreachable", "measured",
                      "ray_miss_median_cm", "ray_miss_p95_cm", "ray_miss_worst_cm",
                      "aim_position_median_cm", "pan_abs_median_deg", "tilt_abs_median_deg")
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        for key, group in sorted(groups.items()):
            measured = [r for r in group if np.isfinite(r["ray_miss_cm"])]
            misses = [r["ray_miss_cm"] for r in measured]
            writer.writerow(dict(zip(summary_fields[:5], key)) | {
                "cases": len(group), "reachable": sum(r["reachable"] for r in group),
                "hit": sum(r["hit"] for r in group),
                "pass": sum(r["status"] == "PASS" for r in group),
                "unknown": sum(r["status"] == "UNKNOWN" for r in group),
                "blocked_or_unreachable": sum(r["status"] in ("BLOCKED", "OUT_OF_RANGE", "OUT_OF_VIEW")
                                              for r in group),
                "measured": len(measured),
                "ray_miss_median_cm": np.median(misses) if misses else "",
                "ray_miss_p95_cm": np.percentile(misses, 95) if misses else "",
                "ray_miss_worst_cm": max(misses) if misses else "",
                "aim_position_median_cm": np.median([r["aim_position_error_cm"] for r in measured])
                                          if measured else "",
                "pan_abs_median_deg": np.median([abs(r["pan_error_deg"]) for r in measured])
                                      if measured else "",
                "tilt_abs_median_deg": np.median([abs(r["tilt_error_deg"]) for r in measured])
                                       if measured else "",
            })
    nominal = [r for r in rows if r["scenario"] == "nominal"]
    def med(values):
        return float(np.median(values)) if values else math.nan
    for field, filename, label in (("ray_miss_cm", "error_by_z.svg", "Median ray miss (cm)"),
                                   ("pan_error_deg", "pan_error_by_z.svg", "Median absolute pan error (deg)"),
                                   ("tilt_error_deg", "tilt_error_by_z.svg", "Median absolute tilt error (deg)")):
        series = {}
        for strategy in ("A", "B", "C"):
            for node in ("s1", "s2"):
                points = []
                for z in HEIGHTS_M:
                    vals = [abs(float(r[field])) for r in nominal if r["strategy"] == strategy
                            and r["node"] == node and r["z_m"] == z and np.isfinite(r[field])]
                    points.append((z, med(vals)))
                series[f"{strategy} {node}"] = points
        _svg_line(out / filename, label, series, label)
    # Two-node coverage: same mount/position/height, evaluator-known hit for each node.
    coverage = {}
    for strategy in ("A", "B", "C"):
        points = []
        for z in HEIGHTS_M:
            pairs = []
            for mount in ("horizontal", "vertical"):
                for position in POSITIONS_XY:
                    a, b = (next(r for r in nominal if r["strategy"] == strategy and r["mount"] == mount
                                 and r["position"] == position and r["z_m"] == z and r["node"] == n)
                            for n in ("s1", "s2"))
                    pairs.append((a["hit"], b["hit"]))
            points.append((z, 100 * sum(a and b for a, b in pairs) / len(pairs)))
        coverage[strategy] = points
    _svg_line(out / "dual_coverage_by_z.svg", "Both-node geometric hit coverage", coverage, "Coverage (%)")


def write_depth(rows: list[dict], out: Path) -> None:
    with (out / "depth_sensitivity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (out / "depth_sensitivity_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("abs_depth_error_cm", "cases", "reachable", "hit", "ray_miss_median_cm",
                         "ray_miss_p95_cm", "ray_miss_worst_cm"))
        for cm in (0, 1, 2, 5, 10):
            group = [r for r in rows if round(abs(r["depth_error_m"]) * 100) == cm]
            values = [r["ray_miss_cm"] for r in group if np.isfinite(r["ray_miss_cm"])]
            writer.writerow((cm, len(group), sum(r["reachable"] for r in group),
                             sum(r["hit"] for r in group), np.median(values),
                             np.percentile(values, 95), max(values)))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("../reports/aiming_evaluation"))
    args = parser.parse_args()
    rows = evaluate()
    write_outputs(rows, args.out)
    depth_rows = depth_sweep()
    write_depth(depth_rows, args.out)
    print(f"Wrote {len(rows)} A/B/C and {len(depth_rows)} synthetic-depth rows to {args.out}")


if __name__ == "__main__":
    main()
