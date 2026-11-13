"""Metric table map from current coplanar AprilTag observations, anchored at ID 2."""

from dataclasses import dataclass, field

import cv2
import numpy as np

from .localize import pixel_ray
from .tags import _square
from .types import Camera, Intrinsics

REFERENCE_IDS = frozenset((0, 1, 2, 3))


@dataclass(frozen=True)
class DynamicMap:
    camera: Camera
    references: dict[int, np.ndarray]
    reprojection_px: float
    metric_error_m: float
    ambiguity_m: float
    frames_used: int = 1
    tag_quality: dict[int, dict[str, float]] = field(default_factory=dict)
    stability_m: float = 0.0
    initial_metric_error_m: float = 0.0
    holdout_m: float | None = None
    anchor_reprojection_px: float = 0.0
    hull_area_m2: float = 0.0

    def table_point(self, pixel: tuple[float, float]) -> np.ndarray | None:
        """Intersect a calibrated pixel ray with the ID 2 plane (Z=0)."""
        origin, direction = pixel_ray(pixel, self.camera)
        if direction[2] >= -0.1:
            return None
        distance = -origin[2] / direction[2]
        if distance <= 0:
            return None
        point = origin + distance * direction
        point[2] = 0.0
        return point


def _plane_corners(corners: np.ndarray, camera: Camera) -> np.ndarray:
    xy = cv2.undistortPoints(corners.reshape(-1, 1, 2), camera.intrinsics.K,
                             camera.intrinsics.dist).reshape(-1, 2)
    rays = np.column_stack((xy, np.ones(len(xy)))) @ camera.R
    if np.any(rays[:, 2] >= -0.1):
        raise ValueError("reference ray nearly parallel to table")
    origin = camera.position
    distances = -origin[2] / rays[:, 2]
    if np.any(distances <= 0):
        raise ValueError("reference plane behind camera")
    points = origin + distances[:, None] * rays
    points[:, 2] = 0.0
    return points


def _square_error(points: np.ndarray, size: float) -> float:
    edges = np.roll(points[:, :2], -1, axis=0) - points[:, :2]
    lengths = np.linalg.norm(edges, axis=1)
    diag = np.linalg.norm(points[:2, :2] - points[2:, :2], axis=1)
    return float(max(np.max(np.abs(lengths - size)),
                     np.max(np.abs(diag - size * np.sqrt(2)))))


def _model_points(parameters: np.ndarray, ids: list[int], size: float) -> np.ndarray:
    """Fixed squares on Z=0; ID 2 fixes the world origin and heading."""
    local = _square(size)
    points = []
    for index, tag_id in enumerate(ids):
        if tag_id == 2:
            points.append(local)
            continue
        offset = 6 + 3 * (index - 1)
        x, y, yaw = parameters[offset:offset + 3]
        co, si = np.cos(yaw), np.sin(yaw)
        xy = local[:, :2] @ np.array([[co, si], [-si, co]])
        points.append(np.column_stack((xy + [x, y], np.zeros(4))))
    return np.concatenate(points)


def _residual(parameters: np.ndarray, ids: list[int], seen: dict[int, np.ndarray],
              intrinsics: Intrinsics, size: float) -> tuple[np.ndarray, np.ndarray]:
    world = _model_points(parameters, ids, size)
    # OpenCV projection includes calibrated distortion: https://docs.opencv.org/5.0/main_modules/d_projection.html
    image, _ = cv2.projectPoints(world, parameters[:3], parameters[3:6],
                                 intrinsics.K, intrinsics.dist)
    error = image.reshape(-1, 2) - np.concatenate([seen[i] for i in ids])
    depth = (cv2.Rodrigues(parameters[:3])[0] @ world.T + parameters[3:6, None])[2]
    return error, depth


def _huber(error: np.ndarray, delta: float = 1.5) -> tuple[float, np.ndarray]:
    norm = np.linalg.norm(error, axis=1)
    weight = np.minimum(1.0, delta / np.maximum(norm, 1e-12))
    cost = np.where(norm <= delta, 0.5 * norm**2, delta * (norm - 0.5 * delta)).sum()
    return float(cost), weight


def _fit_candidate(initial: np.ndarray, ids: list[int], seen: dict[int, np.ndarray],
                   intrinsics: Intrinsics, size: float) -> tuple[np.ndarray, float]:
    """Small robust LM solve for 6 camera and 3 parameters per free square."""
    parameters = initial.copy()
    damping = 1e-3
    for _ in range(40):
        error, depth = _residual(parameters, ids, seen, intrinsics, size)
        if np.min(depth) <= 0:
            break
        cost, weight = _huber(error)
        jacobian = np.empty((error.size, len(parameters)))
        for col in range(len(parameters)):
            step = 1e-5 if col < 3 or (col >= 6 and (col - 6) % 3 == 2) else 1e-6
            shifted = parameters.copy()
            shifted[col] += step
            changed, _ = _residual(shifted, ids, seen, intrinsics, size)
            jacobian[:, col] = ((changed - error) / step).ravel()
        root_weight = np.repeat(np.sqrt(weight), 2)
        weighted = jacobian * root_weight[:, None]
        hessian = weighted.T @ weighted
        gradient = weighted.T @ (error.ravel() * root_weight)
        try:
            update = np.linalg.solve(hessian + damping * np.diag(np.maximum(
                np.diag(hessian), 1.0)), -gradient)
        except np.linalg.LinAlgError:
            break
        trial = parameters + update
        trial_error, trial_depth = _residual(trial, ids, seen, intrinsics, size)
        trial_camera = Camera(intrinsics, cv2.Rodrigues(trial[:3])[0], trial[3:6])
        trial_cost, _ = _huber(trial_error)
        if np.min(trial_depth) > 0 and trial_camera.position[2] >= 0.15 and trial_cost < cost:
            parameters = trial
            damping = max(damping / 3, 1e-8)
            if np.linalg.norm(update) < 1e-8:
                break
        else:
            damping *= 5
            if damping > 1e8:
                break
    error, _ = _residual(parameters, ids, seen, intrinsics, size)
    return parameters, _huber(error)[0]


def estimate_reference_map(
    corners: dict[int, np.ndarray], intrinsics: Intrinsics, size: float = 0.08,
) -> DynamicMap:
    """Jointly fit camera and coplanar fixed-size squares from both IPPE anchor seeds.

    No surveyed reference positions are read. Ambiguous or weak observations raise ValueError.
    """
    if size <= 0 or not np.isfinite(size):
        raise ValueError("reference size must be positive")
    seen = {i: np.asarray(corners[i], np.float64) for i in REFERENCE_IDS & corners.keys()}
    if 2 not in seen:
        raise ValueError("reference anchor ID 2 is missing")
    if len(seen) < 3:
        raise ValueError("need ID 2 and two other reference tags")
    if any(c.shape != (4, 2) or not np.isfinite(c).all() for c in seen.values()):
        raise ValueError("invalid reference corners")
    if min(np.linalg.norm(np.roll(c, -1, axis=0) - c, axis=1).min()
           for c in seen.values()) < 12:
        raise ValueError("reference marker too small in image")

    # IPPE_SQUARE returns two poses for this corner order: https://docs.opencv.org/5.0/main_modules/d_projection.html
    count, rvecs, tvecs, errors = cv2.solvePnPGeneric(
        _square(size), seen[2], intrinsics.K, intrinsics.dist,
        flags=cv2.SOLVEPNP_IPPE_SQUARE,
    )
    if count < 1:
        raise ValueError("anchor pose estimation failed")
    if count > 1 and abs(float(errors[0][0]) - float(errors[1][0])) < 0.3:
        first = Camera(intrinsics, cv2.Rodrigues(rvecs[0])[0], tvecs[0].reshape(3))
        second = Camera(intrinsics, cv2.Rodrigues(rvecs[1])[0], tvecs[1].reshape(3))
        if np.linalg.norm(first.position - second.position) > 0.02:
            raise ValueError("ambiguous planar anchor pose")
    ids = [2, *sorted(i for i in seen if i != 2)]
    candidates = []
    for rvec, tvec, error in zip(rvecs, tvecs, errors):
        camera = Camera(intrinsics, cv2.Rodrigues(rvec)[0], tvec.reshape(3))
        if camera.position[2] < 0.15 or float(error[0]) > 2.0:
            continue
        try:
            points = {i: _plane_corners(c, camera) for i, c in seen.items() if i != 2}
        except ValueError:
            continue
        initial_metric = max(_square_error(p, size) for p in points.values())
        seed = [rvec.reshape(3), tvec.reshape(3)]
        for tag_id in ids[1:]:
            edge = points[tag_id][1, :2] - points[tag_id][0, :2]
            seed.append(np.array([*points[tag_id].mean(axis=0)[:2],
                                  np.arctan2(edge[1], edge[0])]))
        initial = np.concatenate(seed)
        fitted, cost = _fit_candidate(initial, ids, seen, intrinsics, size)
        fitted_camera = Camera(intrinsics, cv2.Rodrigues(fitted[:3])[0], fitted[3:6])
        try:
            fitted_points = {i: _plane_corners(c, fitted_camera) for i, c in seen.items()
                             if i != 2}
        except ValueError:
            continue
        metric = max(_square_error(p, size) for p in fitted_points.values())
        world = _model_points(fitted, ids, size).reshape(-1, 4, 3)
        centers = {tag_id: world[index].mean(axis=0)
                   for index, tag_id in enumerate(ids)}
        xy = np.array([p[:2] for p in centers.values()], np.float32)
        hull_area = cv2.contourArea(cv2.convexHull(xy))
        if hull_area < 0.005:
            continue
        residual, depth = _residual(fitted, ids, seen, intrinsics, size)
        if np.min(depth) <= 0 or fitted_camera.position[2] < 0.15:
            continue
        quality = {}
        for tag_id, errors_for_tag in zip(ids, residual.reshape(-1, 4, 2)):
            norms = np.linalg.norm(errors_for_tag, axis=1)
            quality[tag_id] = {"rms_corner_px": float(np.sqrt(np.mean(norms**2))),
                               "max_corner_px": float(np.max(norms)),
                               "downweighted_corners": float(np.count_nonzero(norms > 1.5)),
                               "square_error_m": (0.0 if tag_id == 2 else
                                                  _square_error(fitted_points[tag_id], size))}
        candidates.append((cost, metric, fitted_camera, centers, quality, initial_metric,
                           float(error[0]), float(hull_area)))
    if not candidates:
        raise ValueError("reference geometry or anchor pose is invalid")
    candidates.sort(key=lambda x: x[0])
    best = candidates[0]
    if best[1] > 0.012:
        worst_id = max(best[4], key=lambda i: best[4][i]["square_error_m"])
        raise ValueError(f"ID {worst_id} reference metric error "
                         f"{best[1]:.4f} m exceeds 0.012 m")
    bad_id = max(best[4], key=lambda i: best[4][i]["max_corner_px"])
    if best[4][bad_id]["max_corner_px"] > 5.0:
        raise ValueError(f"ID {bad_id} corner reprojection "
                         f"{best[4][bad_id]['max_corner_px']:.2f} px exceeds 5 px")
    ambiguity = 0.0
    if len(candidates) > 1:
        other = candidates[1]
        ambiguity = float(np.linalg.norm(best[2].position - other[2].position))
        if ambiguity > 0.02 and other[0] - best[0] < 2.0:
            raise ValueError("ambiguous planar anchor pose")
    rms = float(np.sqrt(np.mean([q["rms_corner_px"]**2 for q in best[4].values()])))
    return DynamicMap(best[2], best[3], rms, best[1], ambiguity,
                      tag_quality=best[4], initial_metric_error_m=best[5],
                      anchor_reprojection_px=best[6], hull_area_m2=best[7])


def estimate_reference_maps(
    observations: list[dict[int, np.ndarray]], intrinsics: Intrinsics, size: float = 0.08,
) -> DynamicMap:
    """Estimate every frame independently; reject changing geometry within a batch."""
    if not observations:
        raise ValueError("no frames")
    maps = []
    for index, corners in enumerate(observations):
        try:
            maps.append(estimate_reference_map(corners, intrinsics, size))
        except ValueError as exc:
            if index == len(observations) - 1:
                raise ValueError(f"latest frame is unreliable: {exc}") from exc
            continue
    if len(maps) < max(1, int(np.ceil(0.6 * len(observations)))):
        raise ValueError("insufficient reliable reference frames")
    required = int(np.ceil(0.6 * len(maps)))
    visible = {i for i in maps[-1].references
               if sum(i in m.references for m in maps) >= required}
    if len(visible) < 3:
        raise ValueError("insufficient stable reference tags in latest frame")
    references = {}
    stability = 0.0
    for tag_id in visible:
        positions = np.stack([m.references[tag_id] for m in maps if tag_id in m.references])
        median = np.median(positions, axis=0)
        deviation = float(np.linalg.norm(positions - median, axis=1).max())
        stability = max(stability, deviation)
        if deviation > 0.025:
            raise ValueError("inconsistent reference geometry across frames")
        references[tag_id] = median
    holdout = None
    if len(maps) >= 3:
        split = min(len(maps) - 1, max(2, int(np.ceil(0.7 * len(maps)))))
        deviations = []
        for tag_id in visible:
            training = [m.references[tag_id] for m in maps[:split] if tag_id in m.references]
            if len(training) < 2:
                raise ValueError(f"insufficient training frames for ID {tag_id}")
            center = np.median(training, axis=0)
            deviations.extend(float(np.linalg.norm(m.references[tag_id] - center))
                              for m in maps[split:] if tag_id in m.references)
        if not deviations:
            raise ValueError("no holdout reference observations")
        holdout = max(deviations)
        if holdout > 0.025:
            raise ValueError(f"holdout reference drift {holdout:.4f} m exceeds 0.025 m")
    latest = maps[-1]
    return DynamicMap(latest.camera, references, max(m.reprojection_px for m in maps),
                      max(m.metric_error_m for m in maps), max(m.ambiguity_m for m in maps),
                      len(maps), tag_quality=latest.tag_quality,
                      stability_m=stability,
                      initial_metric_error_m=max(m.initial_metric_error_m for m in maps),
                      holdout_m=holdout,
                      anchor_reprojection_px=max(m.anchor_reprojection_px for m in maps),
                      hull_area_m2=latest.hull_area_m2)
