"""Rigid conversion from the solver's ID 2 table frame to tabletop ID 22.

Both frames have Z normal to the measured table. ID 22's printed +X edge sets
the operational +X direction; +Y completes a right-handed frame. Only yaw is
retained from its estimated pose so image noise cannot tilt the table plane.
"""

from dataclasses import dataclass

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


def estimate_table_frame(
    observations: list[dict[int, np.ndarray]], intrinsics, site: dict,
    reference_size: float, latest_camera: Camera,
) -> TableFrame:
    """Require a reliable, stationary ID 22 observation in a mapped batch.

    Earlier frames get their own camera pose so a moving camera cannot appear
    to move a fixed tag. The caller must first pass the ID 2 solver gate.
    """
    if not observations or 22 not in observations[-1]:
        raise ValueError("ID 22 missing from latest frame")
    frames = []
    size = site["tags"]["nodes"]["size"]
    for index, corners in enumerate(observations):
        if 22 not in corners:
            continue
        quad = np.asarray(corners[22], dtype=float)
        if quad.shape != (4, 2) or not np.isfinite(quad).all():
            raise ValueError("ID 22 corners are invalid")
        edges = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        if edges.min() < 12:
            raise ValueError("ID 22 is too small in image")
        camera = (latest_camera if index == len(observations) - 1 else
                  estimate_reference_map(corners, intrinsics, reference_size).camera)
        table = DynamicMap(camera, {}, 0., 0., 0.)
        points = [table.table_point(tuple(pixel)) for pixel in quad]
        if any(point is None for point in points):
            raise ValueError("ID 22 does not intersect the table plane")
        points = np.stack(points)
        edges_m = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
        diagonals_m = np.linalg.norm(points[:2] - points[2:], axis=1)
        if (np.max(np.abs(edges_m - size)) > 0.005
                or np.max(np.abs(diagonals_m - size * np.sqrt(2))) > 0.007):
            raise ValueError("ID 22 square geometry is unreliable")
        x = (points[1] - points[0] + points[2] - points[3]) / 2
        x /= np.linalg.norm(x)
        y = np.cross([0., 0., 1.], x)
        pose = TagPose(22, points.mean(axis=0), np.column_stack((x, y, [0., 0., 1.])))
        frame = TableFrame.from_tag(pose)
        projected = np.array([project(pose.to_world(p), camera) for p in _square(size)])
        if np.linalg.norm(projected - quad, axis=1).max() > 3:
            raise ValueError("ID 22 reprojection is unreliable")
        frames.append(frame)
    minimum = max(1, int(np.ceil(site.get("vision", {}).get("min_tag_rate", 0.6)
                                 * len(observations))))
    if len(frames) < minimum:
        raise ValueError("ID 22 detection rate is unreliable")
    latest = frames[-1]
    for frame in frames[:-1]:
        shift = np.linalg.norm(frame.origin - latest.origin)
        heading = np.rad2deg(np.arccos(np.clip(
            np.dot(frame.rotation[:, 0], latest.rotation[:, 0]), -1., 1.)))
        if shift > 0.025 or heading > 5:
            raise ValueError("ID 22 pose is unstable across frames")
    return latest
