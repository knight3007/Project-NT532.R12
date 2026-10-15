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


def localize_surfaces(
    pixel: tuple[float, float],
    camera: Camera,
    wall: dict | None,
    floor: dict | None,
    margin: float = 0.0,
) -> tuple[np.ndarray, str] | None:
    """Điểm đầu tiên tia nhìn qua pixel chạm vào vùng bia, kèm tên mặt ("wall" hoặc "floor").

    - wall: bảng bia đứng, mặt Y = plane_y, X trong [0, width], Z trong [0, height];
      dict có plane_y, width, height.
    - floor: nền nằm ngang trên mặt bàn, mặt Z = 0, X trong [0, width], Y trong [0, depth];
      dict có width, depth.
    Mặt nào là None thì bỏ qua. Mỗi mặt nới thêm `margin` mỗi phía. Lấy giao điểm có tham số tia
    dương nhỏ nhất (gần camera nhất); trả None nếu tia không chạm mặt nào.
    """
    origin, direction = pixel_ray(pixel, camera)
    hits: list[tuple[float, np.ndarray, str]] = []
    if wall is not None and abs(direction[1]) > 1e-9:
        s = (wall["plane_y"] - origin[1]) / direction[1]
        p = origin + s * direction
        if s > 0 and -margin <= p[0] <= wall["width"] + margin and -margin <= p[2] <= (
            wall["height"] + margin
        ):
            hits.append((s, p, "wall"))
    if floor is not None and abs(direction[2]) > 1e-9:
        s = -origin[2] / direction[2]
        p = origin + s * direction
        if s > 0 and -margin <= p[0] <= floor["width"] + margin and -margin <= p[1] <= (
            floor["depth"] + margin
        ):
            hits.append((s, p, "floor"))
    if not hits:
        return None
    _, point, name = min(hits, key=lambda h: h[0])
    return point, name


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
