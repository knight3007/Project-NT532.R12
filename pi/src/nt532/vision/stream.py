"""Camera qua mạng: điện thoại đẩy RTMP lên mediamtx trên Pi, Pi đọc lại bằng RTSP/TCP.

Stream chậm hơn thực tế cỡ 0,5–2 s. Khung nhận lúc `t` thật ra được chụp lúc `t - latency_s`, nên mọi
chỗ cần "ảnh chụp sau thời điểm X" (ảnh bật laser ở bước CORRECT) phải so với thời điểm chụp ước tính,
không phải lúc nhận. Đo `latency_s` bằng `scripts/stream_latency.py`. Xem docs/camera-dien-thoai.md.
"""

import os
import threading
import time
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

STREAM_SCHEMES = ("rtsp://", "rtsps://", "rtmp://", "rtmps://", "srt://", "http://", "https://", "udp://")

# Bỏ bộ đệm của FFmpeg; RTSP qua TCP để không mất gói trên Wi-Fi. Phải đặt trước khi mở VideoCapture.
FFMPEG_OPTIONS = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;0"


def is_stream_url(source: Any) -> bool:
    return isinstance(source, str) and source.lower().startswith(STREAM_SCHEMES)


def _open_ffmpeg(url: str, timeout_s: float) -> cv2.VideoCapture:
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", FFMPEG_OPTIONS)
    ms = int(timeout_s * 1000)
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                            [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, ms, cv2.CAP_PROP_READ_TIMEOUT_MSEC, ms])


class StreamCapture:
    """Đọc stream ở một luồng riêng, giữ khung mới nhất kèm thời điểm chụp ước tính, tự kết nối lại.

    Dùng thay `cv2.VideoCapture` được (`read`, `grab`, `isOpened`, `get`, `set`, `release`), nên các
    script cũ chạy nguyên. `read()` trả khung mới kế tiếp; `read_stamped()` trả thêm thời điểm chụp ước
    tính (cho `FrameHub`); `read_after(t)` chờ khung chụp sau `t` (ảnh bật laser, dùng qua `read_fresh`).
    """

    def __init__(self, url: str, latency_s: float = 0.0, reconnect_s: float = 2.0, timeout_s: float = 5.0,
                 opener: Callable[[str], Any] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.url, self.latency_s, self.reconnect_s, self.timeout_s = url, float(latency_s), reconnect_s, timeout_s
        self.clock = clock
        self._open = opener or (lambda u: _open_ffmpeg(u, timeout_s))
        self._cv = threading.Condition()
        self._frame: np.ndarray | None = None
        self._t = -1.0  # thời điểm chụp ước tính của khung hiện có
        self.seq = 0
        self.connected = False
        self.reconnects = 0
        self.error: str | None = None
        self._size = (0, 0)
        self._last_seq = 0  # khung cuối read_stamped đã trả
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="stream")
        self._thread.start()

    # --- luồng đọc --------------------------------------------------------------------------

    def _run(self) -> None:
        while not self._halt.is_set():
            cap = self._open(self.url)
            if cap is None or not cap.isOpened():
                self._fail(cap, f"không mở được {self.url}")
                continue
            self.connected, self.error = True, None
            while not self._halt.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                t = self.clock() - self.latency_s
                with self._cv:
                    self._frame, self._t = frame, t
                    self._size = (frame.shape[1], frame.shape[0])
                    self.seq += 1
                    self._cv.notify_all()
            self._fail(cap, "mất stream")

    def _fail(self, cap, msg: str) -> None:
        if cap is not None:
            cap.release()
        with self._cv:
            self.connected, self.error = False, msg
            self._cv.notify_all()
        if not self._halt.is_set():
            self.reconnects += 1
            self._halt.wait(self.reconnect_s)

    # --- đọc khung --------------------------------------------------------------------------

    def wait_connected(self, timeout: float) -> bool:
        deadline = self.clock() + timeout
        with self._cv:
            while self._frame is None:
                left = deadline - self.clock()
                if left <= 0:
                    return False
                self._cv.wait(left)
        return True

    def read_stamped(self, timeout: float | None = None) -> tuple[np.ndarray, float]:
        """Khung mới kế tiếp (chưa trả lần nào) và thời điểm chụp ước tính của nó."""
        timeout = self.timeout_s if timeout is None else timeout
        deadline = self.clock() + timeout
        with self._cv:
            while self.seq == self._last_seq:
                left = deadline - self.clock()
                if left <= 0:
                    raise RuntimeError(f"không có khung mới từ stream sau {timeout:g} s ({self.error})")
                self._cv.wait(left)
            self._last_seq = self.seq
            return self._frame, self._t

    def read_after(self, t: float | None = None) -> np.ndarray:
        """Khung chụp sau thời điểm `t` (mặc định: lúc gọi), tính cả độ trễ stream; chờ nếu cần."""
        t = self.clock() if t is None else t
        deadline = self.clock() + self.latency_s + self.timeout_s
        with self._cv:
            while self._t <= t:
                left = deadline - self.clock()
                if left <= 0:
                    raise RuntimeError(f"không có khung chụp sau mốc yêu cầu ({self.error})")
                self._cv.wait(left)
            self._last_seq = self.seq
            return self._frame

    def read(self) -> tuple[bool, np.ndarray | None]:
        """Như VideoCapture.read: khung mới kế tiếp. Cần ảnh chụp sau một thời điểm thì dùng read_after."""
        try:
            return True, self.read_stamped()[0]
        except RuntimeError:
            return False, None

    def grab(self) -> bool:
        return True  # luồng nền đã bỏ khung cũ; read() luôn trả khung mới

    def isOpened(self) -> bool:  # giữ tên của cv2.VideoCapture
        return not self._halt.is_set()

    def get(self, prop: int) -> float:
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._size[0])
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._size[1])
        return 0.0

    def set(self, prop: int, value: float) -> bool:
        return False  # độ phân giải, lấy nét, phơi sáng chỉnh trên ứng dụng điện thoại

    def release(self) -> None:
        self._halt.set()
        with self._cv:
            self._cv.notify_all()
