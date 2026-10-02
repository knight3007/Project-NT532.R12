from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from .types import Intrinsics


def charuco_board(cfg: dict[str, Any]) -> cv2.aruco.CharucoBoard:
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, f"DICT_{cfg['dictionary']}"))
    return cv2.aruco.CharucoBoard(
        (cfg["squares_x"], cfg["squares_y"]), cfg["square"], cfg["marker"], dictionary
    )


def calibrate(
    images: list[np.ndarray], board: cv2.aruco.CharucoBoard, min_corners: int = 12
) -> tuple[Intrinsics, float, int]:
    """Hiệu chuẩn từ các ảnh chụp bảng ChArUco.

    Trả (thông số nội tại, sai số chiếu lại RMS tính bằng pixel, số ảnh dùng được).
    """
    detector = cv2.aruco.CharucoDetector(board)
    obj_points, img_points = [], []
    size = None
    for image in images:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        size = gray.shape[::-1]
        corners, ids, _, _ = detector.detectBoard(gray)
        if ids is None or len(ids) < min_corners:
            continue
        obj, img = board.matchImagePoints(corners, ids)
        obj_points.append(obj)
        img_points.append(img)
    if len(obj_points) < 5:
        raise ValueError(f"chỉ có {len(obj_points)} ảnh thấy đủ góc bảng, cần ít nhất 5")
    rms, K, dist, _, _ = cv2.calibrateCamera(obj_points, img_points, size, None, None)
    return Intrinsics(K, dist.reshape(-1), size), float(rms), len(obj_points)


def save_intrinsics(path: str | Path, intrinsics: Intrinsics, rms: float, n_images: int) -> None:
    data = {
        "date": datetime.now().astimezone().date().isoformat(),
        "image_size": list(intrinsics.size),
        "rms_px": round(rms, 4),
        "n_images": n_images,
        "camera_matrix": intrinsics.K.tolist(),
        "dist_coeffs": intrinsics.dist.tolist(),
    }
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def load_intrinsics(path: str | Path) -> Intrinsics:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Intrinsics(
        np.array(data["camera_matrix"], dtype=np.float64),
        np.array(data["dist_coeffs"], dtype=np.float64),
        tuple(data["image_size"]),
    )
