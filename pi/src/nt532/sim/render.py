"""Dựng ảnh từ hình học: tia nhìn qua từng pixel và dán ảnh phẳng vào khung hình.

Mọi mặt đều là hình chữ nhật trong không gian; ảnh được lấy mẫu bằng cách giao tia nhìn của từng
pixel (đã tính méo ống kính) với mặt phẳng đó, nên đúng cả khi ống kính có méo.
"""

import cv2
import numpy as np

from ..vision.localize import project
from ..vision.types import Camera, Intrinsics


def look_at(position, target, intrinsics: Intrinsics) -> Camera:
    """Camera đặt tại `position`, trục nhìn hướng về `target`, cạnh ngang ảnh song song mặt bàn."""
    position, target = np.asarray(position, float), np.asarray(target, float)
    z = target - position
    z /= np.linalg.norm(z)
    x = np.cross(z, [0.0, 0.0, 1.0])
    x /= np.linalg.norm(x)
    R = np.stack([x, np.cross(z, x), z])
    return Camera(intrinsics, R, -R @ position)


def camera_rays(camera: Camera, shape: tuple[int, int]) -> np.ndarray:
    """Hướng đơn vị (H, W, 3), trong hệ sa bàn, của tia nhìn qua mọi pixel. Gốc tia là camera."""
    h, w = shape
    u, v = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    pts = np.stack([u, v], axis=-1).reshape(-1, 1, 2)
    k = camera.intrinsics
    xy = cv2.undistortPoints(pts, k.K, k.dist).reshape(h, w, 2)
    cam = np.concatenate([xy, np.ones((h, w, 1))], axis=-1)
    world = cam @ camera.R  # (R.T @ c) cho từng pixel
    return world / np.linalg.norm(world, axis=-1, keepdims=True)


def project_points(points: np.ndarray, camera: Camera) -> np.ndarray:
    """Chiếu (N, 3) điểm trong hệ sa bàn lên ảnh, trả (N, 2) pixel."""
    return np.array([project(p, camera) for p in np.asarray(points, float)])


def in_frame(points: np.ndarray, camera: Camera, size: tuple[int, int], margin: float = 0.0):
    """Các điểm 3D có rơi trong khung (rộng, cao) cách mép ít nhất `margin` pixel, và nằm trước máy."""
    pts = np.asarray(points, float).reshape(-1, 3)
    px = project_points(pts, camera)
    depth = (pts @ camera.R.T + camera.t)[:, 2]
    return (
        (depth > 0)
        & (px[:, 0] >= margin)
        & (px[:, 0] <= size[0] - 1 - margin)
        & (px[:, 1] >= margin)
        & (px[:, 1] <= size[1] - 1 - margin)
    )


def render_quad(
    canvas: np.ndarray,
    texture: np.ndarray,
    world: np.ndarray,
    camera: Camera,
    rays: np.ndarray | None = None,
    supersample: int = 1,
) -> None:
    """Dán ảnh phẳng có bốn góc `world` (TL, TR, BR, BL, hình chữ nhật) vào khung của camera.

    `rays` là kết quả `camera_rays` của cả ảnh nếu đã có (dựng nhiều mặt cùng một camera thì tính
    một lần). `supersample` > 1 lấy mẫu mịn hơn rồi lấy trung bình trong từng pixel, cho cạnh
    mượt và góc tag không lệch theo hướng răng cưa (bỏ qua `rays`). Canvas và texture cùng số
    kênh (xám hoặc BGR).
    """
    world = np.asarray(world, float)
    height, width = canvas.shape[:2]
    depth = (world @ camera.R.T + camera.t)[:, 2]
    if np.any(depth <= 1e-3):
        return  # mặt chạm hoặc vượt mặt phẳng máy ảnh; sa bàn không có trường hợp này
    px = project_points(world, camera)
    x0, y0 = np.maximum(np.floor(px.min(axis=0)).astype(int) - 2, 0)
    x1, y1 = np.minimum(np.ceil(px.max(axis=0)).astype(int) + 3, [width, height])
    if x1 <= x0 or y1 <= y0:
        return

    ss = max(int(supersample), 1)
    h, w = (y1 - y0) * ss, (x1 - x0) * ss
    origin = camera.position
    e1, e2 = world[1] - world[0], world[3] - world[0]
    normal = np.cross(e1, e2)
    if rays is None or ss > 1:
        xs = x0 - 0.5 + (np.arange(w) + 0.5) / ss
        ys = y0 - 0.5 + (np.arange(h) + 0.5) / ss
        grid = np.stack(np.meshgrid(xs, ys), axis=-1).reshape(-1, 1, 2)
        k = camera.intrinsics
        xy = cv2.undistortPoints(grid, k.K, k.dist).reshape(h, w, 2)
        d = np.concatenate([xy, np.ones((h, w, 1))], axis=-1) @ camera.R
    else:
        d = rays[y0:y1, x0:x1]
    denom = d @ normal
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (world[0] - origin) @ normal / denom
        rel = origin + s[..., None] * d - world[0]
        a = rel @ e1 / (e1 @ e1)
        b = rel @ e2 / (e2 @ e2)
    valid = (s > 0) & (a >= 0) & (a <= 1) & (b >= 0) & (b <= 1)
    if not valid.any():
        return

    # Thu nhỏ trước để lấy mẫu tuyến tính không bị răng cưa khi mặt nằm xa.
    th, tw = texture.shape[:2]
    ratio = ss * max(np.ptp(px[:, 0]), np.ptp(px[:, 1])) / max(th, tw)
    if ratio < 0.7:
        scale = max(ratio * 1.5, 1e-3)
        size = (max(int(tw * scale), 2), max(int(th * scale), 2))
        texture = cv2.resize(texture, size, interpolation=cv2.INTER_AREA)
        th, tw = texture.shape[:2]
    map_x = np.nan_to_num(a * tw - 0.5).astype(np.float32)
    map_y = np.nan_to_num(b * th - 0.5).astype(np.float32)
    sampled = cv2.remap(texture, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    region = canvas[y0:y1, x0:x1]
    if ss == 1:
        region[valid] = sampled[valid]
        return
    # Trung bình ss x ss mẫu trong mỗi pixel, pha với nền theo phần diện tích mặt phủ.
    channels = 1 if sampled.ndim == 2 else sampled.shape[2]
    sampled = sampled.reshape(h, w, channels).astype(np.float32)
    mask = valid[..., None].astype(np.float32)
    count = mask.reshape(y1 - y0, ss, x1 - x0, ss, 1).sum(axis=(1, 3))
    color = (sampled * mask).reshape(y1 - y0, ss, x1 - x0, ss, channels).sum(axis=(1, 3))
    color /= np.maximum(count, 1)
    cover = count / (ss * ss)
    blended = region.reshape(y1 - y0, x1 - x0, channels) * (1 - cover) + color * cover
    region[...] = np.clip(blended + 0.5, 0, 255).astype(canvas.dtype).reshape(region.shape)


def tag_quad(tag_id: int, size: float, center, yaw_deg: float = 0.0):
    """Ảnh tag có viền trắng một ô và bốn góc của cả tấm (kể cả viền) trong hệ sa bàn.

    Tag nằm ngửa tại độ cao center[2], mép trên hướng +Y khi yaw = 0, xoay ngược chiều kim đồng hồ
    nhìn từ trên xuống `yaw_deg` độ.
    """
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    marker = cv2.aruco.generateImageMarker(dictionary, tag_id, 400)
    texture = cv2.copyMakeBorder(marker, 50, 50, 50, 50, cv2.BORDER_CONSTANT, value=255)
    h = size / 2 * 1.25
    square = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]])
    return texture, square @ yaw_matrix(yaw_deg).T + np.asarray(center, float)


def yaw_matrix(yaw_deg: float) -> np.ndarray:
    a = np.radians(yaw_deg)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
