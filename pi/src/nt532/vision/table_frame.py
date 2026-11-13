"""Rigid conversion from the solver's ID 2 table frame to tabletop ID 22.

Both frames have Z normal to the measured table. ID 22's printed +X edge sets
the operational +X direction; +Y completes a right-handed frame. Only yaw is
retained from its estimated pose so image noise cannot tilt the table plane.
"""

from dataclasses import dataclass

import numpy as np

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
