"""Offline A/B laser geometry. All offsets and angular limits require measurement.

Command angles are degrees relative to HOME. This module never converts PWM or
sends a command. A PASS is a geometric result for the supplied parameters only.
"""

from dataclasses import dataclass

import numpy as np

from nt532.vision.table_frame import TableFrame
from nt532.vision.types import TagPose


def table_hit(emitter, direction) -> np.ndarray | None:
    """First forward intersection of a laser ray with tabletop Z=0."""
    p = np.asarray(emitter, float)
    d = np.asarray(direction, float)
    if p.shape != (3,) or d.shape != (3,) or not np.isfinite(p).all() or not np.isfinite(d).all():
        return None
    if d[2] >= -1e-9 or p[2] <= 0:
        return None
    hit = p - p[2] / d[2] * d
    hit[2] = 0.
    return hit


@dataclass(frozen=True)
class LaserResult:
    status: str
    command_deg: tuple[float, float] | None = None
    table_hit_m: np.ndarray | None = None


@dataclass(frozen=True)
class LaserMount:
    """A measured rig model; emitter_offset is in the laser's local HOME axes."""

    tag_pose: TagPose
    tag_to_pivot: np.ndarray
    emitter_offset: np.ndarray
    home_yaw_deg: float
    home_elevation_deg: float
    pan_sign: float
    tilt_sign: float
    pan_gain: float
    tilt_gain: float
    pan_limits_deg: tuple[float, float]
    tilt_limits_deg: tuple[float, float]

    def __post_init__(self):
        numbers = [self.home_yaw_deg, self.home_elevation_deg, self.pan_sign,
                   self.tilt_sign, self.pan_gain, self.tilt_gain,
                   *self.pan_limits_deg, *self.tilt_limits_deg]
        if (not np.isfinite(numbers).all() or self.pan_sign not in (-1, 1)
                or self.tilt_sign not in (-1, 1) or self.pan_gain <= 0 or self.tilt_gain <= 0
                or self.pan_limits_deg[0] > self.pan_limits_deg[1]
                or self.tilt_limits_deg[0] > self.tilt_limits_deg[1]):
            raise ValueError("invalid measured laser geometry")
        for offset in (self.tag_to_pivot, self.emitter_offset):
            if np.asarray(offset).shape != (3,) or not np.isfinite(offset).all():
                raise ValueError("invalid laser offset")

    def _allowed(self, pan: float, tilt: float) -> bool:
        return (self.pan_limits_deg[0] <= pan <= self.pan_limits_deg[1]
                and self.tilt_limits_deg[0] <= tilt <= self.tilt_limits_deg[1])

    def ray(self, pan_deg: float, tilt_deg: float) -> tuple[np.ndarray, np.ndarray]:
        """Physical emitter and unit ray for two relative HOME commands."""
        yaw = np.deg2rad(self.home_yaw_deg + self.pan_sign * self.pan_gain * pan_deg)
        elev = np.deg2rad(self.home_elevation_deg + self.tilt_sign * self.tilt_gain * tilt_deg)
        cy, sy, ce, se = np.cos(yaw), np.sin(yaw), np.cos(elev), np.sin(elev)
        rz = np.array([[cy, -sy, 0.], [sy, cy, 0.], [0., 0., 1.]])
        rx = np.array([[1., 0., 0.], [0., ce, -se], [0., se, ce]])
        orientation = rz @ rx
        pivot = self.tag_pose.to_world(self.tag_to_pivot)
        return pivot + orientation @ self.emitter_offset, orientation @ [0., 1., 0.]

    def vertical_calibration(self, tag_center, pan_deg: float, tilt_deg: float) -> LaserResult:
        """PASS only for an allowed downward 90-degree ray through one tag centre."""
        if not self._allowed(pan_deg, tilt_deg):
            return LaserResult("UNREACHABLE")
        emitter, direction = self.ray(pan_deg, tilt_deg)
        tag = np.asarray(tag_center, float)
        hit = table_hit(emitter, direction)
        if (tag.shape != (3,) or not np.isfinite(tag).all() or abs(tag[2]) > 1e-3
                or hit is None or direction[2] > -np.cos(np.deg2rad(0.5))
                or np.linalg.norm(hit[:2] - tag[:2]) > 1e-3):
            return LaserResult("UNREACHABLE")
        return LaserResult("PASS", (pan_deg, tilt_deg), hit)

    def target(self, point) -> LaserResult:
        """Iteratively aim from the moving emitter; reject unreachable commands."""
        target = np.asarray(point, float)
        if target.shape != (3,) or not np.isfinite(target).all():
            return LaserResult("UNREACHABLE")
        emitter = self.tag_pose.to_world(self.tag_to_pivot)
        command = (0., 0.)
        for _ in range(20):
            delta = target - emitter
            horizontal = np.hypot(delta[0], delta[1])
            if np.linalg.norm(delta) < 1e-6 or horizontal < 1e-9:
                return LaserResult("UNREACHABLE")
            yaw = np.rad2deg(np.arctan2(-delta[0], delta[1]))
            elev = np.rad2deg(np.arctan2(delta[2], horizontal))
            pan = ((yaw - self.home_yaw_deg + 180) % 360 - 180) / (self.pan_sign * self.pan_gain)
            tilt = (elev - self.home_elevation_deg) / (self.tilt_sign * self.tilt_gain)
            command = (float(pan), float(tilt))
            new_emitter, direction = self.ray(*command)
            if np.linalg.norm(new_emitter - emitter) < 1e-10:
                emitter = new_emitter
                break
            emitter = new_emitter
        delta = target - emitter
        miss = np.linalg.norm(np.cross(delta, direction))
        if (not self._allowed(*command) or np.dot(delta, direction) <= 0 or miss > 1e-4):
            return LaserResult("UNREACHABLE")
        return LaserResult("PASS", command, table_hit(emitter, direction))

    def in_frame(self, frame: TableFrame) -> "LaserMount":
        """Express the same physical mount in ID 22 coordinates."""
        yaw = np.deg2rad(self.home_yaw_deg)
        forward = frame.rotation.T @ [-np.sin(yaw), np.cos(yaw), 0.]
        new_yaw = float(np.rad2deg(np.arctan2(-forward[0], forward[1])))
        return LaserMount(frame.tag_pose(self.tag_pose), self.tag_to_pivot,
                          self.emitter_offset, new_yaw, self.home_elevation_deg,
                          self.pan_sign, self.tilt_sign, self.pan_gain, self.tilt_gain,
                          self.pan_limits_deg, self.tilt_limits_deg)
