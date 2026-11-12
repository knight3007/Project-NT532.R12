"""Offline tag-assisted screen localization experiment; never commands hardware.

From pi/: python scripts/tag_assisted_3d_eval.py --out ../reports/tag_assisted_3d
The estimator receives image observations and measured mounting assumptions only.
Ground truth is held by the synthetic scene and the evaluator.
"""

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from nt532.orchestrator import aiming
from nt532.sim.render import look_at, render_quad, yaw_matrix
from nt532.sim.scene import Node
from nt532.vision.dynamic_mapping import DynamicMap, estimate_reference_map
from nt532.vision.localize import pixel_ray, project
from nt532.vision.tags import _square, detect_tags
from nt532.vision.types import Camera, Intrinsics, TagPose

# Simulation variables. None of these values is a measured production servo setting.
INTRINSICS = Intrinsics(
    np.array(
        [
            [993.765464334269, 0, 643.1603481283688],
            [0, 992.2958974909341, 360.62700180078434],
            [0, 0, 1.0],
        ]
    ),
    np.array(
        [
            0.020177991555003598,
            0.1681489831696114,
            0.0008123582073143769,
            0.00042226930927851607,
            -0.4472832951591382,
        ]
    ),
    (1280, 720),
)
REFERENCE_XY = {2: (0.0, 0.0), 0: (0.60, 0.0), 1: (0.60, 0.40), 3: (0.0, 0.40)}
REFERENCE_SIZE_M = 0.08
TAG_23_SIZE_M = 0.04
SCREEN_WIDTH_M, SCREEN_HEIGHT_M = 0.075, 0.145
TAG_FROM_SCREEN_M = np.array([0.065, 0.0, 0.0])  # screen center -> tag center, screen axes
HEIGHTS_M = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30)
POSITIONS_XY = {
    "left": (0.18, 0.19),
    "center": (0.30, 0.23),
    "right": (0.43, 0.30),
    "far": (0.30, 0.36),
}
ORIENTATIONS = {
    "upright": (0.0, 0.0, 0.0),
    "yaw20_lean12": (20.0, 12.0, 0.0),
    "yaw_minus25_lean_minus10": (-25.0, -10.0, 0.0),
    "marker_roll20": (10.0, 8.0, 20.0),
}
NODES = {
    "s1": {"id": 21, "tag": (0.05, -0.10, 0.20), "home_yaw": -35.0},
    "s2": {"id": 22, "tag": (0.55, -0.10, 0.20), "home_yaw": 35.0},
}
PIVOT_OFFSET_M = np.array([0.0, 0.0, 0.10])  # only the magnitude 10 cm is planned
PAN_LIMITS_DEG, TILT_LIMITS_DEG = (-70.0, 70.0), (-35.0, 45.0)
HOME_PWM_US, PWM_US_PER_DEG = 1500.0, 2000.0 / 180.0
PAN_PWM_US, TILT_PWM_US = (500.0, 2500.0), (600.0, 2500.0)
HIT_RADIUS_CM = 3.0


@dataclass(frozen=True)
class ScreenCase:
    target: np.ndarray  # evaluator only: displayed fire point, not a free-space flame
    screen_center: np.ndarray
    screen_R: np.ndarray  # columns: right, up, outward normal
    tag_pose: TagPose
    camera: Camera
    local_target: np.ndarray
    yaw_deg: float
    lean_deg: float
    roll_deg: float


@dataclass(frozen=True)
class Observation:
    mapping: DynamicMap | None
    tag_corners: np.ndarray | None
    pixel: tuple[float, float]
    screen_from_tag: np.ndarray | None  # tag-local vector: tag center -> screen center
    screen_R_from_tag: np.ndarray
    reason: str = ""
    corner_uncertainty_px: float = 0.0


@dataclass(frozen=True)
class Estimate:
    status: str
    reason: str
    xyz: np.ndarray | None = None
    incidence: float = math.nan
    tag_reprojection_px: float = math.nan


def _rx(deg):
    a = math.radians(deg)
    return np.array(
        [[1.0, 0.0, 0.0], [0.0, math.cos(a), -math.sin(a)], [0.0, math.sin(a), math.cos(a)]]
    )


def _rz(deg):
    return yaw_matrix(deg)


def make_case(xyz, yaw=0.0, lean=0.0, roll=0.0, *, low_angle=False) -> ScreenCase:
    target = np.asarray(xyz, float)
    # Local +Y points up, +Z faces the camera. A screen point at Z=0 is its
    # lower edge; at higher Z the point moves toward the screen center.
    vertical = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
    R = _rz(yaw) @ vertical @ _rx(lean) @ _rz(roll)
    local = np.array([0.0, -max(SCREEN_HEIGHT_M / 2 - target[2], 0.0), 0.0])
    center = target - R @ local
    tag = TagPose(23, center + R @ TAG_FROM_SCREEN_M, R)
    eye = (0.10, -0.57, 0.19 if low_angle else 0.45)
    camera = look_at(eye, (0.30, 0.24, 0.06), INTRINSICS)
    return ScreenCase(target, center, R, tag, camera, local, float(yaw), float(lean), float(roll))


def _corners(pose: TagPose, size: float, camera: Camera) -> np.ndarray:
    return np.array([project(p, camera) for p in pose.position + _square(size) @ pose.rotation.T])


def _reference_corners(camera: Camera, sigma: float, rng) -> dict[int, np.ndarray]:
    return {
        i: _corners(TagPose(i, np.array([*xy, 0.0]), np.eye(3)), REFERENCE_SIZE_M, camera)
        + rng.normal(0, sigma, (4, 2))
        for i, xy in REFERENCE_XY.items()
    }


def observe(case: ScreenCase, scenario="clean", seed=0) -> Observation:
    """Render sensor observations; the returned value has no target XYZ."""
    rng = np.random.default_rng(seed)
    true_camera = case.camera
    if scenario == "low_angle_pose":
        true_camera = make_case(
            case.target, case.yaw_deg, case.lean_deg, case.roll_deg, low_angle=True
        ).camera
    map_sigma = (
        0.5 if scenario == "low_angle_pose" else (1.0 if scenario == "map_noise_1px" else 0.0)
    )
    try:
        mapping = estimate_reference_map(
            _reference_corners(true_camera, map_sigma, rng), INTRINSICS, REFERENCE_SIZE_M
        )
    except ValueError:
        mapping = None
    tag_pose = case.tag_pose
    if scenario == "near_parallel":
        edge = make_case(case.target, 76.0, 0.0, 0.0, low_angle=False)
        tag_pose = edge.tag_pose
        true_camera = edge.camera
    corners = _corners(tag_pose, TAG_23_SIZE_M, true_camera)
    corner_uncertainty = 0.0
    if scenario in ("tag_missing", "tag_occluded"):
        corners = None
    elif scenario in ("tag_noise_0p5px", "tag_noise_1p5px"):
        sigma = 0.5 if scenario == "tag_noise_0p5px" else 1.5
        samples = corners + rng.normal(0, sigma, (5, 4, 2))
        corners = np.median(samples, axis=0)
        # Five-frame corner stability is an observable, not target truth.
        corner_uncertainty = float(1.3 * np.std(samples, axis=0, ddof=1).mean() / np.sqrt(5))
    pixel = np.array(project(case.target, true_camera))
    if scenario == "bbox_bias_3px":
        pixel += (3.0, -3.0)
    elif scenario == "bbox_bias_6px":
        pixel += (6.0, -6.0)
    elif scenario == "outside_screen":
        pixel += (160.0, 0.0)
    offset = -TAG_FROM_SCREEN_M.copy()
    if scenario == "offset_error_5mm":
        offset += (0.005, 0.0, 0.005)
    elif scenario == "offset_error_10mm":
        offset += (0.01, 0.0, 0.01)
    elif scenario == "offset_unknown":
        offset = None
    return Observation(
        mapping, corners, tuple(pixel), offset, np.eye(3), corner_uncertainty_px=corner_uncertainty
    )


def estimate_tag_pose(corners: np.ndarray, camera: Camera) -> tuple[TagPose | None, float]:
    """Single-marker IPPE; report ambiguous planar poses instead of guessing."""
    obj = _square(TAG_23_SIZE_M)
    if not np.isfinite(corners).all() or corners.shape != (4, 2):
        return None, math.nan
    # Marker must have enough image support for a useful pose, including under
    # foreshortening. Check both dimensions rather than only perimeter length.
    edges = np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)
    if min(edges) < 12.0:
        return None, math.nan
    count, rvecs, tvecs, reproj = cv2.solvePnPGeneric(
        obj,
        np.ascontiguousarray(corners),
        camera.intrinsics.K,
        camera.intrinsics.dist,
        flags=cv2.SOLVEPNP_IPPE_SQUARE,
    )
    if count < 1:
        return None, math.nan
    options = []
    for rvec, tvec, err in zip(rvecs, tvecs, reproj.reshape(-1)):
        R_cam_tag = cv2.Rodrigues(rvec)[0]
        position = camera.R.T @ (tvec.reshape(3) - camera.t)
        rotation = camera.R.T @ R_cam_tag
        # A physical screen placed on the table faces the camera and is not
        # behind it. This cannot disambiguate every planar two-solution case.
        normal = rotation[:, 2]
        if (camera.position - position) @ normal <= 0:
            continue
        # Configured fixture is an upright phone facing the camera. Reject
        # the mirrored IPPE solution when it implies a side-facing screen or
        # an upside-down display. These bounds must be measured for a real rig.
        if normal[1] > -0.60 or rotation[2, 1] < 0.70:
            continue
        options.append((float(err), TagPose(23, position, rotation)))
    if not options:
        return None, math.nan
    options.sort(key=lambda o: o[0])
    best_err, best = options[0]
    if best_err > 2.0:
        return None, best_err
    if len(options) > 1 and options[1][0] - best_err < 0.15:
        # Similar reprojection can correspond to distinct 3D screen positions.
        other = options[1][1]
        if (
            np.linalg.norm(other.position - best.position) > 0.01
            or np.linalg.norm(other.rotation[:, 2] - best.rotation[:, 2]) > 0.05
        ):
            return None, best_err
    return best, best_err


def tag_assisted_xyz(obs: Observation) -> Estimate:
    if obs.mapping is None:
        return Estimate("BLOCKED", "dynamic_map_untrusted")
    if obs.tag_corners is None:
        return Estimate("BLOCKED", "tag_23_missing")
    if obs.screen_from_tag is None:
        return Estimate("BLOCKED", "screen_offset_unknown")
    edges = np.linalg.norm(np.roll(obs.tag_corners, -1, axis=0) - obs.tag_corners, axis=1)
    if min(edges) / max(edges) < 0.40:
        return Estimate("UNKNOWN", "ray_near_parallel")
    camera = obs.mapping.camera
    pose, error = estimate_tag_pose(obs.tag_corners, camera)
    if pose is None:
        return Estimate("UNKNOWN", "tag_pose_ambiguous_or_poor", tag_reprojection_px=error)
    range_uncertainty = (
        3 * np.linalg.norm(pose.position - camera.position) * obs.corner_uncertainty_px / min(edges)
    )
    if range_uncertainty > HIT_RADIUS_CM / 100:
        return Estimate("UNKNOWN", "tag_uncertainty_exceeds_hit_radius", tag_reprojection_px=error)
    center = pose.to_world(obs.screen_from_tag)
    R = pose.rotation @ obs.screen_R_from_tag
    normal = R[:, 2]
    origin, direction = pixel_ray(obs.pixel, camera)
    incidence = abs(float(direction @ normal))
    if incidence < 0.10:
        return Estimate(
            "UNKNOWN", "ray_near_parallel", incidence=incidence, tag_reprojection_px=error
        )
    distance = float((center - origin) @ normal / (direction @ normal))
    if distance <= 0:
        return Estimate(
            "UNKNOWN", "screen_behind_camera", incidence=incidence, tag_reprojection_px=error
        )
    point = origin + distance * direction
    local = R.T @ (point - center)
    if abs(local[0]) > SCREEN_WIDTH_M / 2 + 0.001 or abs(local[1]) > SCREEN_HEIGHT_M / 2 + 0.001:
        return Estimate(
            "UNKNOWN", "pixel_outside_screen", incidence=incidence, tag_reprojection_px=error
        )
    return Estimate("PASS", "screen_point", point, incidence, error)


def _rig(node: str, scenario: str):
    cfg = NODES[node]
    tag = np.array(cfg["tag"])
    true_pivot = tag + PIVOT_OFFSET_M
    assumed = PIVOT_OFFSET_M.copy()
    if scenario == "pivot_offset_1cm":
        assumed += (0.0, 0.0, 0.01)
    return true_pivot, tag + assumed, cfg["home_yaw"]


def _command_ok(pan: float, tilt: float, scenario: str) -> bool:
    pan_limits = (-1.0, 1.0) if scenario == "tight_limits" else PAN_LIMITS_DEG
    tilt_limits = (-1.0, 1.0) if scenario == "tight_limits" else TILT_LIMITS_DEG
    return (
        pan_limits[0] <= pan <= pan_limits[1]
        and tilt_limits[0] <= tilt <= tilt_limits[1]
        and PAN_PWM_US[0] <= HOME_PWM_US + PWM_US_PER_DEG * pan <= PAN_PWM_US[1]
        and TILT_PWM_US[0] <= HOME_PWM_US + PWM_US_PER_DEG * tilt <= TILT_PWM_US[1]
    )


def _physical_ray(node: str, pivot, yaw, pan, tilt, scenario):
    rig = Node(node, NODES[node]["id"], pivot, 0.0, pivot_offset=np.zeros(3), facing_deg=yaw)
    gain = 1.10 if scenario == "servo_gain_10pct" else 1.0
    rig.pan = gain * pan * (-1.0 if scenario == "pan_sign_reversed" else 1.0)
    rig.tilt = gain * tilt + (5.0 if scenario == "tilt_home_5deg" else 0.0)
    return rig.direction, rig.pan, rig.tilt


def run_case(
    case: ScreenCase, method: str, node: str, scenario="clean", seed=0, *, _observation=None
) -> dict:
    """Keep truth in this evaluator; B receives only `Observation`."""
    if method not in ("A", "B", "C") or node not in NODES:
        raise ValueError("method must be A/B/C and node s1/s2")
    obs = _observation if _observation is not None else observe(case, scenario, seed)
    true_pivot, assumed_pivot, yaw = _rig(node, scenario)
    ideal_pan, ideal_tilt = aiming.angles(true_pivot, case.target, yaw)
    row = {
        "method": method,
        "node": node,
        "scenario": scenario,
        "x_m": case.target[0],
        "y_m": case.target[1],
        "z_m": case.target[2],
        "screen_yaw_deg": case.yaw_deg,
        "screen_lean_deg": case.lean_deg,
        "marker_roll_deg": case.roll_deg,
        "estimated_x_m": math.nan,
        "estimated_y_m": math.nan,
        "estimated_z_m": math.nan,
        "xyz_error_cm": math.nan,
        "pan_error_deg": math.nan,
        "tilt_error_deg": math.nan,
        "command_pan_error_deg": math.nan,
        "command_tilt_error_deg": math.nan,
        "ray_miss_cm": math.nan,
        "hit": False,
        "reachable": False,
        "ground_truth_reachable": _command_ok(ideal_pan, ideal_tilt, "clean"),
        "status": "BLOCKED",
        "reason": "",
        "incidence": math.nan,
        "tag_reprojection_px": math.nan,
    }
    if scenario == "free_flame_off_plane" and method == "B":
        row.update(status="UNKNOWN", reason="real_flame_depth_unobservable")
        return row
    if method == "C":
        estimate = Estimate("PASS", "oracle_only", case.target)
    elif method == "A":
        xyz = obs.mapping.table_point(obs.pixel) if obs.mapping else None
        estimate = (
            Estimate("PASS", "floor_z0_assumption", xyz)
            if xyz is not None
            else Estimate("BLOCKED", "floor_intersection_unavailable")
        )
    else:
        estimate = tag_assisted_xyz(obs)
    row.update(
        status=estimate.status,
        reason=estimate.reason,
        incidence=estimate.incidence,
        tag_reprojection_px=estimate.tag_reprojection_px,
    )
    if estimate.xyz is None:
        return row
    xyz = estimate.xyz
    row.update(
        estimated_x_m=xyz[0],
        estimated_y_m=xyz[1],
        estimated_z_m=xyz[2],
        xyz_error_cm=float(np.linalg.norm(xyz - case.target) * 100),
    )
    pan, tilt = aiming.angles(assumed_pivot, xyz, yaw)
    row.update(command_pan_error_deg=pan - ideal_pan, command_tilt_error_deg=tilt - ideal_tilt)
    if not _command_ok(pan, tilt, scenario):
        row.update(status="OUT_OF_RANGE", reason="command_or_pwm_limit")
        return row
    row["reachable"] = True
    ray, physical_pan, physical_tilt = _physical_ray(node, true_pivot, yaw, pan, tilt, scenario)
    v = case.target - true_pivot
    miss = np.linalg.norm(v - max(float(v @ ray), 0.0) * ray) * 100
    row.update(
        pan_error_deg=physical_pan - ideal_pan,
        tilt_error_deg=physical_tilt - ideal_tilt,
        ray_miss_cm=float(miss),
        hit=bool(miss <= HIT_RADIUS_CM),
        status="PASS" if miss <= HIT_RADIUS_CM else "FAIL",
        reason="geometric_hit" if miss <= HIT_RADIUS_CM else "geometric_miss",
    )
    return row


def render_tag_trial(case: ScreenCase, *, occluded=False, shrink=False) -> dict:
    """Real detector on a synthetic raster; never reads an RTSP stream."""
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    marker = cv2.aruco.generateImageMarker(dictionary, 23, 400)
    texture = cv2.copyMakeBorder(marker, 50, 50, 50, 50, cv2.BORDER_CONSTANT, value=255)
    h = TAG_23_SIZE_M * 1.25 / 2
    quad = (
        case.tag_pose.position
        + np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]]) @ case.tag_pose.rotation.T
    )
    image = np.full((720, 1280), 180, np.uint8)
    render_quad(image, texture, quad, case.camera, supersample=2)
    corners = _corners(case.tag_pose, TAG_23_SIZE_M, case.camera)
    edge = float(np.min(np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)))
    if occluded:
        cv2.fillConvexPoly(image, np.round(corners).astype(np.int32), 180)
    if shrink:
        image = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)
    detected = 23 in detect_tags(image)
    return {
        "detected": detected,
        "tag_edge_px": edge / (2 if shrink else 1),
        "resolution": "640x360" if shrink else "1280x720",
    }


def evaluate() -> list[dict]:
    rows = []
    for z in HEIGHTS_M:
        for position, (x, y) in POSITIONS_XY.items():
            for orientation, (yaw, lean, roll) in ORIENTATIONS.items():
                case = make_case((x, y, z), yaw, lean, roll)
                obs = observe(case)
                for method in ("A", "B", "C"):
                    for node in NODES:
                        row = run_case(case, method, node, _observation=obs)
                        row.update(position=position, orientation=orientation)
                        rows.append(row)
    special = (
        "bbox_bias_3px",
        "bbox_bias_6px",
        "tag_noise_0p5px",
        "tag_noise_1p5px",
        "map_noise_1px",
        "offset_error_5mm",
        "offset_error_10mm",
        "low_angle_pose",
        "tag_missing",
        "tag_occluded",
        "offset_unknown",
        "outside_screen",
        "near_parallel",
        "tight_limits",
        "pivot_offset_1cm",
        "tilt_home_5deg",
        "servo_gain_10pct",
        "pan_sign_reversed",
        "free_flame_off_plane",
    )
    for scenario in special:
        for z in HEIGHTS_M:
            for position, (x, y) in POSITIONS_XY.items():
                case = make_case((x, y, z), 20.0, 12.0)
                seed = int(1000 * z) + len(position)
                obs = observe(case, scenario, seed)
                for method in ("A", "B", "C"):
                    for node in NODES:
                        row = run_case(case, method, node, scenario, seed, _observation=obs)
                        row.update(position=position, orientation="yaw20_lean12")
                        rows.append(row)
    return rows


def _stats(values):
    a = np.asarray([x for x in values if np.isfinite(x)], float)
    return (
        (float(np.median(a)), float(np.percentile(a, 95)), float(np.max(a)))
        if len(a)
        else (math.nan,) * 3
    )


def summarize(rows):
    out = []
    for scenario in sorted({r["scenario"] for r in rows}):
        for method in ("A", "B", "C"):
            for node in NODES:
                group = [
                    r
                    for r in rows
                    if r["scenario"] == scenario and r["method"] == method and r["node"] == node
                ]
                result = {
                    "scenario": scenario,
                    "method": method,
                    "node": node,
                    "n": len(group),
                    "estimated": sum(np.isfinite(r["xyz_error_cm"]) for r in group),
                    "reachable": sum(r["reachable"] for r in group),
                    "hits": sum(r["hit"] for r in group),
                    "hit_rate": sum(r["hit"] for r in group) / len(group),
                    "reasons": "; ".join(
                        f"{k}:{sum(r['reason'] == k for r in group)}"
                        for k in sorted({r["reason"] for r in group})
                    ),
                }
                for key in ("xyz_error_cm", "pan_error_deg", "tilt_error_deg", "ray_miss_cm"):
                    for label, value in zip(
                        ("median", "p95", "worst"), _stats(abs(r[key]) for r in group)
                    ):
                        result[f"{key}_{label}"] = value
                out.append(result)
    return out


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _plot(path, rows, metric, title):
    series = {}
    for method in ("A", "B", "C"):
        yy = []
        for z in HEIGHTS_M:
            values = [
                abs(r[metric])
                for r in rows
                if r["scenario"] == "clean"
                and r["method"] == method
                and abs(r["z_m"] - z) < 1e-6
                and np.isfinite(r[metric])
            ]
            yy.append(float(np.median(values)) if values else math.nan)
        series[method] = yy
    ymax = max(v for values in series.values() for v in values if np.isfinite(v)) * 1.1
    ymax = max(ymax, 1.0)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="760" height="430" viewBox="0 0 760 430">',
        '<rect width="760" height="430" fill="white"/>',
        f'<text x="55" y="32" font-size="19" font-family="sans-serif">{title}</text>',
        '<path d="M75 355 H720 M75 355 V75" stroke="#333" fill="none"/>',
        '<text x="300" y="410" font-size="13">Displayed target Z (cm)</text>',
    ]
    for fraction in (0.0, 0.5, 1.0):
        y = 355 - 265 * fraction
        parts.append(f'<path d="M75 {y:.1f} H720" stroke="#ddd"/>')
        parts.append(f'<text x="20" y="{y + 5:.1f}" font-size="12">{ymax * fraction:.1f}</text>')
    for z in HEIGHTS_M:
        x = 75 + z / 0.30 * 630
        parts.append(f'<text x="{x - 9:.1f}" y="378" font-size="12">{z * 100:.0f}</text>')
    for method, color in (("A", "#c14b42"), ("B", "#2372b5"), ("C", "#33866b")):
        points = [
            (75 + z / 0.30 * 630, 355 - v / ymax * 265)
            for z, v in zip(HEIGHTS_M, series[method])
            if np.isfinite(v)
        ]
        polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        parts.append(
            f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="2.5"/>'
        )
        parts += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>' for x, y in points]
        parts.append(
            f'<text x="{585 + 42 * (ord(method) - ord("A"))}" y="65" fill="{color}" font-size="15">{method}</text>'
        )
    path.write_text("\n".join(parts + ["</svg>"]), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("../reports/tag_assisted_3d"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    rows = evaluate()
    _write_csv(args.out / "cases.csv", rows)
    _write_csv(args.out / "summary.csv", summarize(rows))
    trials = []
    for name, case in (
        ("nominal", make_case((0.30, 0.23, 0.15))),
        ("yaw20_lean12", make_case((0.30, 0.23, 0.15), 20, 12)),
        ("marker_roll20", make_case((0.30, 0.23, 0.15), 10, 8, 20)),
        ("low_angle", make_case((0.30, 0.23, 0.15), 0, 0, low_angle=True)),
    ):
        for shrink in (False, True):
            for occluded in (False, True):
                trials.append(
                    dict(
                        scenario=name,
                        occluded=occluded,
                        **render_tag_trial(case, occluded=occluded, shrink=shrink),
                    )
                )
    _write_csv(args.out / "detector_trials.csv", trials)
    _plot(args.out / "xyz_error_by_height.svg", rows, "xyz_error_cm", "Median XYZ error (cm)")
    _plot(args.out / "ray_miss_by_height.svg", rows, "ray_miss_cm", "Median ray miss (cm)")
    print(f"wrote {len(rows)} cases, {len(trials)} detector trials to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
