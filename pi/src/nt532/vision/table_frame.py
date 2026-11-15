"""Rigid conversion from the solver's ID 2 table frame to tabletop ID 22.

Both frames have Z normal to the measured table. ID 22's printed +X edge sets
the operational +X direction; +Y completes a right-handed frame. Only yaw is
retained from its estimated pose so image noise cannot tilt the table plane.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .dynamic_mapping import DynamicMap, estimate_reference_map
from .localize import project
from .tags import _square
from .types import Camera, TagPose


@dataclass(frozen=True)
class TableFrame:
    """ID 22 axes and origin expressed in ID 2 coordinates, in metres."""

    origin: np.ndarray
    rotation: np.ndarray  # columns are ID 22 +X, +Y, +Z in ID 2

    @classmethod
    def from_tag(cls, tag: TagPose) -> "TableFrame":
        """Flatten a trusted tabletop tag pose; reject a moved or tilted tag."""
        p = np.asarray(tag.position, dtype=float)
        r = np.asarray(tag.rotation, dtype=float)
        if tag.tag_id != 22 or p.shape != (3,) or r.shape != (3, 3):
            raise ValueError("operational origin requires ID 22 pose")
        if not np.isfinite(p).all() or not np.isfinite(r).all():
            raise ValueError("ID 22 pose is non-finite")
        if abs(p[2]) > 0.02:
            raise ValueError("ID 22 is not on the table plane")
        if np.linalg.norm(r.T @ r - np.eye(3)) > 0.05 or np.linalg.det(r) < 0.95:
            raise ValueError("ID 22 orientation is degenerate")
        if r[2, 2] < np.cos(np.deg2rad(10)):
            raise ValueError("ID 22 is not lying face-up on the table")
        x = np.array([r[0, 0], r[1, 0], 0.0])
        norm = np.linalg.norm(x)
        if norm < 0.1:
            raise ValueError("ID 22 heading is degenerate")
        x /= norm
        y = np.cross([0., 0., 1.], x)
        if np.dot(y, r[:, 1]) < 0.9:
            raise ValueError("ID 22 printed axes are inconsistent")
        return cls(np.array([p[0], p[1], 0.0]), np.column_stack((x, y, [0., 0., 1.])))

    def point(self, point: np.ndarray) -> np.ndarray:
        """Convert XYZ in ID 2 to XYZ in ID 22."""
        return self.rotation.T @ (np.asarray(point, dtype=float) - self.origin)

    def inverse(self, point: np.ndarray) -> np.ndarray:
        """Convert XYZ in ID 22 back to XYZ in ID 2."""
        return self.rotation @ np.asarray(point, dtype=float) + self.origin

    def camera(self, camera: Camera) -> Camera:
        """Preserve the camera projection while changing its world coordinates."""
        return Camera(camera.intrinsics, camera.R @ self.rotation,
                      camera.R @ self.origin + camera.t)

    def tag_pose(self, pose: TagPose) -> TagPose:
        """Convert a node tag's centre and full orientation together."""
        return TagPose(pose.tag_id, self.point(pose.position),
                       self.rotation.T @ pose.rotation)


# Gate values of the tabletop origin. They are reported in diagnostics, never relaxed here.
EDGE_TOL_M = 0.005
DIAGONAL_TOL_M = 0.007
REPROJECTION_TOL_PX = 3.0
MIN_EDGE_PX = 12.0
MAX_SHIFT_M = 0.025
MAX_HEADING_DEG = 5.0
EDGE_NAMES = ("TL-TR", "TR-BR", "BR-BL", "BL-TL")
# Frame-to-frame spread below this (mm) means a failure is systematic, not detection noise.
SYSTEMATIC_STD_MM = 0.5


class TableFrameError(ValueError):
    """ID 22 cannot be trusted as the operational origin; message kept for old callers."""

    def __init__(self, message: str, code: str, diagnostics: dict):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics


def _round(value, digits: int = 3):
    return np.round(np.asarray(value, dtype=float), digits).tolist()


def _image_radius_ratio(quad: np.ndarray, intrinsics) -> float:
    """Tag-centre distance from the principal point, as a fraction of the farthest image corner."""
    cx, cy = intrinsics.K[0, 2], intrinsics.K[1, 2]
    width, height = intrinsics.size
    radius = max(np.hypot(u - cx, v - cy) for u in (0, width) for v in (0, height))
    center = np.asarray(quad, dtype=float).mean(axis=0)
    return float(np.hypot(center[0] - cx, center[1] - cy) / radius)


def _pnp_pose(quad: np.ndarray, camera: Camera, size: float) -> dict | None:
    """Unconstrained IPPE pose of ID 22, kept only as a diagnostic beside the table estimate."""
    intrinsics = camera.intrinsics
    count, rvecs, tvecs, _ = cv2.solvePnPGeneric(
        _square(size), quad, intrinsics.K, intrinsics.dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    best = None
    for rvec, tvec in zip(rvecs[:count], tvecs[:count]):
        rotation = camera.R.T @ cv2.Rodrigues(rvec)[0]
        position = camera.R.T @ (tvec.reshape(3) - camera.t)
        pose = TagPose(22, position, rotation)
        error = np.linalg.norm(np.array([project(pose.to_world(p), camera)
                                         for p in _square(size)]) - quad, axis=1)
        if best is None or np.sqrt(np.mean(error**2)) < best[0]:
            best = (float(np.sqrt(np.mean(error**2))), float(error.max()), pose)
    if best is None:
        return None
    rms, worst, pose = best
    return {"coordinate_frame": "tag_2_table", "estimator": "solvePnP IPPE_SQUARE",
            "position_m": _round(pose.position, 5),
            "tilt_from_table_deg": round(float(np.rad2deg(np.arccos(
                np.clip(pose.rotation[2, 2], -1., 1.)))), 3),
            "reprojection_px": {"rms": round(rms, 3), "max": round(worst, 3)}}


def _frame_metrics(quad: np.ndarray, camera: Camera, size: float) -> tuple[dict, str | None,
                                                                            TableFrame | None]:
    """Measure one ID 22 observation against the flat-square-on-Z=0 assumption."""
    entry: dict = {"checks": {}}
    table = DynamicMap(camera, {}, 0., 0., 0.)
    points = [table.table_point(tuple(pixel)) for pixel in quad]
    if any(point is None for point in points):
        entry["checks"]["plane_intersection"] = False
        return entry, "ID22_NO_PLANE_INTERSECTION", None
    entry["checks"]["plane_intersection"] = True
    points = np.stack(points)
    edges_m = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    diagonals_m = np.linalg.norm(points[:2] - points[2:], axis=1)
    edge_error = edges_m - size
    diagonal_error = diagonals_m - size * np.sqrt(2)
    entry.update(
        edges_mm=_round(edges_m * 1000, 2), diagonals_mm=_round(diagonals_m * 1000, 2),
        edge_errors_mm=_round(edge_error * 1000, 2),
        diagonal_errors_mm=_round(diagonal_error * 1000, 2),
        worst_edge=EDGE_NAMES[int(np.argmax(np.abs(edge_error)))],
        max_edge_error_mm=round(float(np.max(np.abs(edge_error))) * 1000, 3),
        max_diagonal_error_mm=round(float(np.max(np.abs(diagonal_error))) * 1000, 3))
    x = (points[1] - points[0] + points[2] - points[3]) / 2
    x /= np.linalg.norm(x)
    y = np.cross([0., 0., 1.], x)
    pose = TagPose(22, points.mean(axis=0), np.column_stack((x, y, [0., 0., 1.])))
    projected = np.array([project(pose.to_world(p), camera) for p in _square(size)])
    residual = np.linalg.norm(projected - quad, axis=1)
    entry["table_pose"] = {"coordinate_frame": "tag_2_table",
                           "estimator": "corner rays intersected with Z=0",
                           "position_m": _round(pose.position, 5),
                           "projected_corners_px": _round(projected, 2)}
    entry["table_reprojection_px"] = {"rms": round(float(np.sqrt(np.mean(residual**2))), 3),
                                      "max": round(float(residual.max()), 3)}
    square_ok = (np.max(np.abs(edge_error)) <= EDGE_TOL_M
                 and np.max(np.abs(diagonal_error)) <= DIAGONAL_TOL_M)
    entry["checks"]["square_geometry"] = bool(square_ok)
    if not square_ok:
        return entry, "ID22_SQUARE_GEOMETRY", None
    try:
        frame = TableFrame.from_tag(pose)
    except ValueError as exc:
        entry["checks"]["pose"] = False
        entry["pose_error"] = str(exc)
        return entry, "ID22_POSE_UNTRUSTED", None
    entry["checks"]["pose"] = True
    reprojection_ok = residual.max() <= REPROJECTION_TOL_PX
    entry["checks"]["table_reprojection"] = bool(reprojection_ok)
    if not reprojection_ok:
        return entry, "ID22_REPROJECTION", None
    return entry, None, frame


_MESSAGES = {
    "ID22_MISSING": "ID 22 missing from latest frame",
    "ID22_INVALID_CORNERS": "ID 22 corners are invalid",
    "ID22_TOO_SMALL": "ID 22 is too small in image",
    "ID22_NO_PLANE_INTERSECTION": "ID 22 does not intersect the table plane",
    "ID22_SQUARE_GEOMETRY": "ID 22 square geometry is unreliable",
    "ID22_REPROJECTION": "ID 22 reprojection is unreliable",
    "ID22_DETECTION_RATE": "ID 22 detection rate is unreliable",
    "ID22_UNSTABLE": "ID 22 pose is unstable across frames",
}


def _spread(values: list[float]) -> dict | None:
    if not values:
        return None
    array = np.asarray(values, dtype=float)
    return {"min": round(float(array.min()), 3), "max": round(float(array.max()), 3),
            "mean": round(float(array.mean()), 3), "std": round(float(array.std()), 3)}


def analyze_table_frame(
    observations: list[dict[int, np.ndarray]], intrinsics, site: dict,
    reference_size: float, latest_camera: Camera,
    references: dict[int, np.ndarray] | None = None,
) -> tuple[TableFrame | None, dict]:
    """Evaluate every frame of the ID 22 origin gate and explain the decision.

    The accept/reject decision and its first reason are identical to the original
    single-pass gate; the extra work only records metrics for all frames, the PnP
    pose beside the table-plane pose, image position and reference-hull position.
    """
    size = site["tags"]["nodes"]["size"]
    diagnostics: dict = {
        "status": "BLOCKED", "tag_id": 22, "reason": None, "reason_code": None,
        "failing_frame": None, "failing_frames": [],
        "thresholds": {"edge_mm": EDGE_TOL_M * 1000, "diagonal_mm": DIAGONAL_TOL_M * 1000,
                       "table_reprojection_px": REPROJECTION_TOL_PX,
                       "min_edge_px": MIN_EDGE_PX, "max_shift_m": MAX_SHIFT_M,
                       "max_heading_deg": MAX_HEADING_DEG,
                       "min_tag_rate": site.get("vision", {}).get("min_tag_rate", 0.6)},
        "units": {"edge_error": "mm", "diagonal_error": "mm", "reprojection": "px",
                  "position": "m", "angle": "deg", "image_radius_ratio": "fraction"},
        "frames": [],
    }

    def blocked(code: str, frame_number: int | None = None, message: str | None = None):
        diagnostics.update(reason=message or _MESSAGES[code], reason_code=code)
        if frame_number is not None and diagnostics["failing_frame"] is None:
            diagnostics["failing_frame"] = frame_number
        return None, diagnostics

    if not observations or 22 not in observations[-1]:
        return blocked("ID22_MISSING")
    latest_quad = np.asarray(observations[-1][22], dtype=float)
    reference_ratios = [_image_radius_ratio(observations[-1][i], intrinsics)
                        for i in (0, 1, 2, 3) if i in observations[-1]]
    if latest_quad.shape == (4, 2) and np.isfinite(latest_quad).all():
        diagnostics["image_radius_ratio"] = round(_image_radius_ratio(latest_quad, intrinsics), 3)
    diagnostics["reference_image_radius_ratio_max"] = (round(max(reference_ratios), 3)
                                                       if reference_ratios else None)

    frames: list[TableFrame] = []
    first_failure: tuple[str, int, str | None] | None = None
    for index, corners in enumerate(observations):
        if 22 not in corners:
            continue
        number = index + 1
        quad = np.asarray(corners[22], dtype=float)
        entry: dict = {"frame": number}
        code, message, frame = None, None, None
        if quad.shape != (4, 2) or not np.isfinite(quad).all():
            code = "ID22_INVALID_CORNERS"
        else:
            edges_px = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
            entry["edges_px"] = _round(edges_px, 2)
            entry["detected_corners_px"] = _round(quad, 2)
            if edges_px.min() < MIN_EDGE_PX:
                code = "ID22_TOO_SMALL"
            else:
                try:
                    camera = (latest_camera if index == len(observations) - 1 else
                              estimate_reference_map(corners, intrinsics, reference_size).camera)
                except ValueError as exc:
                    code, message = "ID22_FRAME_CAMERA", str(exc)
                else:
                    metrics, code, frame = _frame_metrics(quad, camera, size)
                    entry.update(metrics)
                    if code == "ID22_POSE_UNTRUSTED":
                        message = metrics.get("pose_error")
                    pnp = _pnp_pose(quad, camera, size)
                    if pnp is not None:
                        entry["pnp_pose"] = pnp
        entry["reason_code"] = code
        diagnostics["frames"].append(entry)
        if code is not None:
            diagnostics["failing_frames"].append(number)
            if first_failure is None:
                first_failure = (code, number, message)
        elif frame is not None:
            frames.append(frame)

    measured = [f for f in diagnostics["frames"] if "max_edge_error_mm" in f]
    edge_values = [f["max_edge_error_mm"] for f in measured]
    diagonal_values = [f["max_diagonal_error_mm"] for f in measured]
    reprojection_values = [f["table_reprojection_px"]["max"] for f in measured]
    evaluated = len(diagnostics["frames"])
    systematic = bool(
        first_failure is not None and evaluated > 1
        and len(diagnostics["failing_frames"]) == evaluated
        and len({f["reason_code"] for f in diagnostics["frames"]}) == 1
        and len(measured) == evaluated
        and np.std(edge_values) < SYSTEMATIC_STD_MM
        and np.std(diagonal_values) < SYSTEMATIC_STD_MM)
    diagnostics["summary"] = {
        "frames_evaluated": evaluated,
        "max_edge_error_mm": _spread(edge_values),
        "max_diagonal_error_mm": _spread(diagonal_values),
        "table_reprojection_max_px": _spread(reprojection_values),
        "systematic": systematic,
        "classification": ("SYSTEMATIC_GEOMETRY_INCONSISTENCY" if systematic else
                           "INTERMITTENT" if first_failure is not None else "NONE"),
    }
    latest_entry = diagnostics["frames"][-1] if diagnostics["frames"] else {}
    if "table_pose" in latest_entry:
        diagnostics["table_pose"] = {k: v for k, v in latest_entry["table_pose"].items()
                                     if k != "projected_corners_px"}
    if "pnp_pose" in latest_entry:
        diagnostics["pnp_pose"] = latest_entry["pnp_pose"]
    if "table_pose" in diagnostics and "pnp_pose" in diagnostics:
        delta = (np.asarray(diagnostics["pnp_pose"]["position_m"])
                 - np.asarray(diagnostics["table_pose"]["position_m"])) * 1000
        diagnostics["pose_disagreement_mm"] = {
            "xy": round(float(np.linalg.norm(delta[:2])), 2),
            "z": round(float(delta[2]), 2), "3d": round(float(np.linalg.norm(delta)), 2)}
    if references and "table_pose" in diagnostics:
        centers = np.array([np.asarray(references[i])[:2] for i in sorted(references)],
                           np.float32)
        if len(centers) >= 3:
            point = diagnostics["table_pose"]["position_m"]
            distance = cv2.pointPolygonTest(cv2.convexHull(centers),
                                            (float(point[0]), float(point[1])), True)
            diagnostics["reference_hull"] = {"inside": bool(distance >= 0),
                                             "signed_distance_m": round(float(distance), 4)}
    # Non-blocking context: these regions are not constrained by the reference solver.
    warnings = []
    if diagnostics.get("reference_hull", {}).get("inside") is False:
        warnings.append("ID22_OUTSIDE_REFERENCE_HULL")
    ratio = diagnostics.get("image_radius_ratio")
    reference_max = diagnostics.get("reference_image_radius_ratio_max")
    if ratio is not None and reference_max is not None and ratio > reference_max + 0.1:
        warnings.append("ID22_IMAGE_PERIPHERY_BEYOND_REFERENCES")
    diagnostics["warnings"] = warnings
    if first_failure is not None:
        code, number, message = first_failure
        return blocked(code, number, message)

    minimum = max(1, int(np.ceil(diagnostics["thresholds"]["min_tag_rate"]
                                 * len(observations))))
    if len(frames) < minimum:
        return blocked("ID22_DETECTION_RATE")
    latest = frames[-1]
    for frame in frames[:-1]:
        shift = np.linalg.norm(frame.origin - latest.origin)
        heading = np.rad2deg(np.arccos(np.clip(
            np.dot(frame.rotation[:, 0], latest.rotation[:, 0]), -1., 1.)))
        if shift > MAX_SHIFT_M or heading > MAX_HEADING_DEG:
            return blocked("ID22_UNSTABLE")
    diagnostics["status"] = "PASS"
    return latest, diagnostics


def estimate_table_frame(
    observations: list[dict[int, np.ndarray]], intrinsics, site: dict,
    reference_size: float, latest_camera: Camera,
    references: dict[int, np.ndarray] | None = None,
) -> TableFrame:
    """Require a reliable, stationary ID 22 observation in a mapped batch.

    Earlier frames get their own camera pose so a moving camera cannot appear
    to move a fixed tag. The caller must first pass the ID 2 solver gate.
    Raises `TableFrameError` (a `ValueError`) with structured diagnostics.
    """
    frame, diagnostics = analyze_table_frame(observations, intrinsics, site, reference_size,
                                             latest_camera, references)
    if frame is None:
        raise TableFrameError(diagnostics["reason"], diagnostics["reason_code"], diagnostics)
    return frame
