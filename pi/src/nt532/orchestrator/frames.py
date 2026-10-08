"""Một luồng duy nhất đọc camera; orchestrator và dashboard cùng lấy khung từ đây.

Đọc liên tục nên khung lấy ra luôn mới (không dính bộ đệm của webcam), và `fresh()` trả đúng khung
bắt đầu chụp sau thời điểm gọi, thay cho `read_fresh` khi cần ảnh ngay sau khi bật laser.
"""

import threading
import time
from collections.abc import Callable

import numpy as np


class FrameHub:
    def __init__(self, read: Callable[[], np.ndarray], fps: float = 15.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.read, self.period, self.clock = read, 1.0 / fps, clock
        self._cv = threading.Condition()
        self._frame: np.ndarray | None = None
        self._t = -1.0  # thời điểm bắt đầu chụp khung hiện có
        self.seq = 0
        self.error: str | None = None
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "FrameHub":
        self._thread = threading.Thread(target=self._run, daemon=True, name="frames")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._halt.set()

    def _run(self) -> None:
        while not self._halt.is_set():
            t0 = self.clock()
            try:
                frame = self.read()
                self.error = None
            except Exception as e:  # noqa: BLE001 - camera lỗi thì báo lên dashboard, thử lại
                self.error = f"{type(e).__name__}: {e}"
                self._halt.wait(0.5)
                continue
            with self._cv:
                self._frame, self._t = frame, t0
                self.seq += 1
                self._cv.notify_all()
            self._halt.wait(max(0.0, self.period - (self.clock() - t0)))

    def latest(self) -> tuple[int, np.ndarray | None]:
        with self._cv:
            return self.seq, self._frame

    def fresh(self, timeout: float = 3.0) -> np.ndarray:
        """Khung đầu tiên bắt đầu chụp sau lúc gọi hàm."""
        t_call = self.clock()
        deadline = t_call + timeout
        with self._cv:
            while self._t <= t_call:
                left = deadline - self.clock()
                if left <= 0:
                    raise TimeoutError(f"không có khung hình mới sau {timeout:g} s ({self.error})")
                self._cv.wait(left)
            return self._frame

    def frames(self, n: int, timeout: float = 3.0) -> list[np.ndarray]:
        return [self.fresh(timeout) for _ in range(n)]
