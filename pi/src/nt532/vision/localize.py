import cv2
import numpy as np

from .types import Camera


def pixel_ray(pixel: tuple[float, float], camera: Camera) -> tuple[np.ndarray, np.ndarray]:
    """Tia nhìn qua pixel (u, v) trong hệ sa bàn: (gốc tia, hướng đơn vị)."""
    pts = np.array([[pixel]], dtype=np.float64)
    x, y = cv2.undistortPoints(pts, camera.intrinsics.K, camera.intrinsics.dist).reshape(2)
    direction = camera.R.T @ np.array([x, y, 1.0])
    return camera.position, direction / np.linalg.norm(direction)


def localize(pixel: tuple[float, float], camera: Camera, plane_y: float) -> np.ndarray | None:
    """Giao tia nhìn qua pixel với mặt phẳng bảng bia Y = plane_y.

    Trả tọa độ (x, y, z) trong hệ sa bàn, hoặc None nếu tia không cắt bảng ở phía trước camera.
    """
    origin, direction = pixel_ray(pixel, camera)
    if abs(direction[1]) < 1e-9:
        return None
    s = (plane_y - origin[1]) / direction[1]
    if s <= 0:
        return None
    return origin + s * direction


def project(point: np.ndarray, camera: Camera) -> tuple[float, float]:
    """Chiếu một điểm trong hệ sa bàn lên ảnh, trả pixel (u, v)."""
    proj, _ = cv2.projectPoints(
        np.asarray(point, dtype=np.float64).reshape(1, 3),
        cv2.Rodrigues(camera.R)[0],
        camera.t,
        camera.intrinsics.K,
        camera.intrinsics.dist,
    )
    u, v = proj.reshape(2)
    return float(u), float(v)
