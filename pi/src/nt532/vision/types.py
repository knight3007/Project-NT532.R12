from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Detection:
    """Một bia do YOLO tìm thấy, tọa độ pixel trên ảnh gốc."""

    u: float  # tâm hộp
    v: float
    w: float
    h: float
    conf: float


@dataclass(frozen=True)
class Intrinsics:
    """Thông số nội tại của webcam, lấy từ bước hiệu chuẩn."""

    K: np.ndarray  # (3, 3)
    dist: np.ndarray  # hệ số méo
    size: tuple[int, int]  # (rộng, cao) của ảnh lúc hiệu chuẩn


@dataclass(frozen=True)
class Camera:
    """Camera đã biết pose trong hệ sa bàn: x_cam = R @ x_world + t."""

    intrinsics: Intrinsics
    R: np.ndarray  # (3, 3)
    t: np.ndarray  # (3,)

    @property
    def position(self) -> np.ndarray:
        return -self.R.T @ self.t


@dataclass(frozen=True)
class TagPose:
    """Pose của một tag trong hệ tọa độ sa bàn."""

    tag_id: int
    position: np.ndarray  # (3,), mét
    rotation: np.ndarray  # (3, 3), từ hệ tag sang hệ sa bàn
