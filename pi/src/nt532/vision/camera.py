import sys
from typing import Any

import cv2
import numpy as np


def open_camera(cfg: dict[str, Any], source: int | str | None = None) -> cv2.VideoCapture:
    """Mở webcam theo mục `camera` của site.yaml, hoặc một file video/ảnh nếu `source` là đường dẫn.

    Đặt độ phân giải, khóa lấy nét và phơi sáng nếu cấu hình có giá trị. `source == "sim"` trả
    camera ảo nhìn sa bàn ảo mặc định (nt532.sim), khung nào cũng dựng mới từ hình học.
    """
    if source == "sim":
        from ..sim import SimCamera  # import lười: không kéo theo khi không dùng sim

        return SimCamera.demo()
    if isinstance(source, str) and not source.isdigit():
        cap = cv2.VideoCapture(source)
    else:
        index = cfg["index"] if source is None else int(source)
        # Trên Windows backend mặc định (MSMF) hay không lấy được khung hình; DirectShow ổn định hơn.
        if sys.platform == "win32":
            backend = cv2.CAP_DSHOW
        elif sys.platform.startswith("linux"):
            backend = cv2.CAP_V4L2
        else:
            backend = cv2.CAP_ANY
        cap = cv2.VideoCapture(index, backend)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["height"])
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if cfg.get("focus") is not None:
            cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
            cap.set(cv2.CAP_PROP_FOCUS, cfg["focus"])
        if cfg.get("exposure") is not None:
            # Giá trị "tắt tự động" khác nhau theo backend: DirectShow 0.25, V4L2 1 (manual).
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if sys.platform == "win32" else 1)
            cap.set(cv2.CAP_PROP_EXPOSURE, cfg["exposure"])
    if not cap.isOpened():
        raise RuntimeError(f"không mở được camera {source if source is not None else cfg['index']}")
    return cap


def read_fresh(cap: cv2.VideoCapture, skip: int = 4) -> np.ndarray:
    """Đọc một khung chụp sau thời điểm gọi hàm.

    Webcam giữ sẵn vài khung trong bộ đệm; bỏ chúng đi để ảnh bật laser không phải ảnh cũ.
    """
    for _ in range(skip):
        cap.grab()
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError("không đọc được khung hình")
    return frame


def read_frames(cap: cv2.VideoCapture, n: int) -> list[np.ndarray]:
    """Đọc n khung liên tiếp, khung đầu là khung mới."""
    frames = [read_fresh(cap)]
    for _ in range(n - 1):
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("không đọc được khung hình")
        frames.append(frame)
    return frames
