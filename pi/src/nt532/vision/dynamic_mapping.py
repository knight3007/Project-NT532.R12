"""Metric table map from current coplanar AprilTag observations, anchored at ID 2."""

from dataclasses import dataclass

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


def estimate_reference_map(
    corners: dict[int, np.ndarray], intrinsics: Intrinsics, size: float = 0.08,
) -> DynamicMap:
    """Solve two IPPE anchor poses and choose by other tags' metric square consistency.

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

    count, rvecs, tvecs, errors = cv2.solvePnPGeneric(
        _square(size), seen[2], intrinsics.K, intrinsics.dist,
        flags=cv2.SOLVEPNP_IPPE_SQUARE,
    )
    if count < 1:
        raise ValueError("anchor pose estimation failed")
    candidates = []
    for rvec, tvec, error in zip(rvecs, tvecs, errors):
        camera = Camera(intrinsics, cv2.Rodrigues(rvec)[0], tvec.reshape(3))
        if camera.position[2] < 0.15 or float(error[0]) > 2.0:
            continue
        try:
            points = {i: _plane_corners(c, camera) for i, c in seen.items() if i != 2}
        except ValueError:
            continue
        metric = max(_square_error(p, size) for p in points.values())
        centers = {2: np.zeros(3)}
        centers.update({i: p.mean(axis=0) for i, p in points.items()})
        xy = np.array([p[:2] for p in centers.values()], np.float32)
        hull_area = cv2.contourArea(cv2.convexHull(xy))
        if hull_area < 0.005:
            continue
        candidates.append((metric, float(error[0]), camera, centers))
    if not candidates:
        raise ValueError("reference geometry or anchor pose is invalid")
    candidates.sort(key=lambda x: (x[0], x[1]))
    best = candidates[0]
    if best[0] > 0.012:
        raise ValueError(f"reference metric error {best[0]:.4f} m exceeds 0.012 m")
    ambiguity = 0.0
    if len(candidates) > 1:
        other = candidates[1]
        ambiguity = float(np.linalg.norm(best[2].position - other[2].position))
        if ambiguity > 0.02 and other[0] - best[0] < 0.002:
            raise ValueError("ambiguous planar anchor pose")
    return DynamicMap(best[2], best[3], best[1], best[0], ambiguity)


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
    common = set.intersection(*(set(m.references) for m in maps))
    if len(common) < 3:
        raise ValueError("insufficient common reference tags")
    references = {}
    for tag_id in common:
        positions = np.stack([m.references[tag_id] for m in maps])
        median = np.median(positions, axis=0)
        if np.linalg.norm(positions - median, axis=1).max() > 0.025:
            raise ValueError("inconsistent reference geometry across frames")
        references[tag_id] = median
    latest = maps[-1]
    return DynamicMap(latest.camera, references, max(m.reprojection_px for m in maps),
                      max(m.metric_error_m for m in maps), max(m.ambiguity_m for m in maps),
                      len(maps))
