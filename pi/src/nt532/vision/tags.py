from collections import defaultdict
from typing import Any

import cv2
import numpy as np

from .types import Camera, Intrinsics, TagPose

_DICTIONARY = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
_PARAMS = cv2.aruco.DetectorParameters()
_PARAMS.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
_DETECTOR = cv2.aruco.ArucoDetector(_DICTIONARY, _PARAMS)


def _square(size: float) -> np.ndarray:
    """Bốn góc của tag trong hệ của chính nó, theo thứ tự OpenCV trả về: TL, TR, BR, BL."""
    h = size / 2
    return np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], dtype=np.float64)


def detect_tags(frame: np.ndarray) -> dict[int, np.ndarray]:
    """Phát hiện AprilTag 36h11, trả bốn góc pixel (4, 2) theo ID."""
    corners, ids, _ = _DETECTOR.detectMarkers(frame)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2).astype(np.float64) for i, c in zip(ids.flatten(), corners)}


def median_corners(
    frames: list[np.ndarray],
) -> tuple[dict[int, np.ndarray], dict[int, float]]:
    """Phát hiện tag trên nhiều khung của một cảnh đứng yên, lấy trung vị góc để bớt nhiễu.

    Trả (góc trung vị theo ID, tỷ lệ số khung thấy từng ID).
    """
    seen: dict[int, list[np.ndarray]] = defaultdict(list)
    for frame in frames:
        for tag_id, corners in detect_tags(frame).items():
            seen[tag_id].append(corners)
    corners = {i: np.median(np.stack(c), axis=0) for i, c in seen.items()}
    rate = {i: len(c) / len(frames) for i, c in seen.items()}
    return corners, rate


def reference_corners(site: dict[str, Any]) -> dict[int, np.ndarray]:
    """Tọa độ sa bàn của bốn góc từng tag tham chiếu.

    Quy ước dán: tag nằm ngửa trên mặt bàn (z = 0), mép trên của tag hướng về bảng bia (+Y).
    """
    ref = site["tags"]["reference"]
    out = {}
    for tag_id, center in ref["positions"].items():
        if center is None:
            continue
        out[int(tag_id)] = _square(ref["size"]) + np.array([center[0], center[1], 0.0])
    return out


def solve_camera(
    corners: dict[int, np.ndarray], intrinsics: Intrinsics, site: dict[str, Any]
) -> Camera:
    """Tính pose camera trong hệ sa bàn từ các tag tham chiếu nhìn thấy."""
    world = reference_corners(site)
    seen = [i for i in world if i in corners]
    if len(seen) < 2:
        raise ValueError(f"cần ít nhất 2 tag tham chiếu, chỉ thấy {seen}")
    obj = np.concatenate([world[i] for i in seen])
    img = np.concatenate([corners[i] for i in seen])
    ok, rvec, tvec = cv2.solvePnP(obj, img, intrinsics.K, intrinsics.dist)
    if not ok:
        raise ValueError("solvePnP thất bại")
    return Camera(intrinsics, cv2.Rodrigues(rvec)[0], tvec.reshape(3))


def reprojection_error(
    corners: dict[int, np.ndarray], camera: Camera, site: dict[str, Any]
) -> float:
    """Sai số chiếu lại lớn nhất (pixel) của các tag tham chiếu; tăng vọt khi camera bị xê dịch."""
    worst = 0.0
    for tag_id, world in reference_corners(site).items():
        if tag_id not in corners:
            continue
        proj, _ = cv2.projectPoints(
            world, cv2.Rodrigues(camera.R)[0], camera.t, camera.intrinsics.K, camera.intrinsics.dist
        )
        worst = max(
            worst, float(np.linalg.norm(proj.reshape(4, 2) - corners[tag_id], axis=1).max())
        )
    return worst


def tag_poses(
    corners: dict[int, np.ndarray], camera: Camera, site: dict[str, Any]
) -> dict[int, TagPose]:
    """Pose của các tag node trong hệ sa bàn, từ kết quả `detect_tags`."""
    nodes = site["tags"]["nodes"]
    obj = _square(nodes["size"])
    out = {}
    for tag_id in nodes["ids"].values():
        if tag_id not in corners:
            continue
        ok, rvec, tvec = cv2.solvePnP(
            obj,
            corners[tag_id],
            camera.intrinsics.K,
            camera.intrinsics.dist,
            flags=cv2.SOLVEPNP_IPPE_SQUARE,
        )
        if not ok:
            continue
        rotation = camera.R.T @ cv2.Rodrigues(rvec)[0]
        position = camera.R.T @ (tvec.reshape(3) - camera.t)
        out[tag_id] = TagPose(tag_id, position, rotation)
    return out
