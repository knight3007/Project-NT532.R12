"""StreamCapture với nguồn giả: không cần mediamtx hay điện thoại."""

import threading
import time

import numpy as np
import pytest

from nt532.orchestrator.frames import FrameHub
from nt532.vision import read_fresh
from nt532.vision.stream import StreamCapture, is_stream_url


class FakeCap:
    """Phát khung 20 fps; giá trị điểm ảnh là số thứ tự khung. Hết `n` khung thì giả mất stream."""

    def __init__(self, n=10_000, period=0.05, start=0):
        self.n, self.period, self.i = n, period, start
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        if self.i >= self.n:
            return False, None
        time.sleep(self.period)
        self.i += 1
        return True, np.full((4, 6, 3), self.i % 256, np.uint8)

    def release(self):
        self.released = True


def opener_of(*caps):
    """Lần mở thứ k trả caps[k]; hết danh sách thì mở hỏng (None)."""
    it = iter(caps)
    opened = []

    def open_(url):
        cap = next(it, None)
        opened.append(cap)
        return cap

    open_.opened = opened
    return open_


def test_url_detection():
    assert is_stream_url("rtsp://127.0.0.1:8554/cam")
    assert is_stream_url("RTMP://pi.local/cam")
    assert not is_stream_url("clip.mp4")
    assert not is_stream_url("0")
    assert not is_stream_url(0)


def test_stamps_subtract_latency_and_read_after_waits():
    cap = StreamCapture("rtsp://x/cam", latency_s=0.2, opener=opener_of(FakeCap()))
    try:
        assert cap.wait_connected(2)
        frame, t = cap.read_stamped()
        assert frame.shape == (4, 6, 3)
        assert t < time.monotonic() - 0.15  # chụp trước lúc nhận khoảng latency
        t_call = time.monotonic()
        cap.read_after()
        # phải chờ ít nhất cỡ độ trễ để có khung chụp sau lúc gọi
        assert time.monotonic() - t_call >= 0.15
        with cap._cv:
            assert cap._t > t_call
    finally:
        cap.release()


def test_read_returns_next_frame_without_waiting_for_latency():
    cap = StreamCapture("rtsp://x/cam", latency_s=1.0, opener=opener_of(FakeCap()))
    try:
        assert cap.wait_connected(2)
        t0 = time.monotonic()
        seqs = []
        for _ in range(3):
            ok, frame = cap.read()
            assert ok
            seqs.append(int(frame[0, 0, 0]))
        assert time.monotonic() - t0 < 0.6  # vòng lặp xem trực tiếp không bị kéo về 1 khung/giây
        assert seqs == sorted(set(seqs))  # mỗi lần một khung mới
    finally:
        cap.release()


def test_read_fresh_uses_capture_time_for_streams():
    cap = StreamCapture("rtsp://x/cam", latency_s=0.3, opener=opener_of(FakeCap()))
    try:
        assert cap.wait_connected(2)
        t_call = time.monotonic()
        read_fresh(cap)
        assert time.monotonic() - t_call >= 0.25
    finally:
        cap.release()


def test_reconnects_after_stream_drops():
    first, second = FakeCap(n=3), FakeCap(start=100)
    open_ = opener_of(None, first, second)  # lần đầu mở hỏng, rồi stream rớt sau 3 khung
    cap = StreamCapture("rtsp://x/cam", reconnect_s=0.05, opener=open_)
    try:
        deadline = time.monotonic() + 3
        value = 0
        while time.monotonic() < deadline and value <= 100:
            value = int(cap.read_stamped(timeout=2)[0][0, 0, 0])
        assert value > 100, "không chuyển sang stream mới"
        assert cap.reconnects >= 2 and first.released
        assert cap.connected
    finally:
        cap.release()


def test_read_stamped_times_out_with_reason():
    cap = StreamCapture("rtsp://x/cam", reconnect_s=0.05, opener=opener_of())
    try:
        with pytest.raises(RuntimeError, match="không mở được"):
            cap.read_stamped(timeout=0.3)
    finally:
        cap.release()


def test_framehub_fresh_honours_stream_stamps():
    cap = StreamCapture("rtsp://x/cam", latency_s=0.3, opener=opener_of(FakeCap()))
    hub = FrameHub(cap.read_stamped, fps=30).start()
    try:
        assert cap.wait_connected(2)
        hub.fresh(2)
        t_call = time.monotonic()
        hub.fresh(2)
        # khung "mới" theo thời điểm chụp chỉ tới sau khoảng độ trễ, không phải khung kế tiếp nhận được
        assert time.monotonic() - t_call >= 0.25
        seq, frame = hub.latest()
        assert seq > 0 and frame is not None
    finally:
        hub.stop()
        cap.release()


def test_framehub_plain_reader_unchanged():
    n = {"i": 0}
    lock = threading.Lock()

    def read():
        with lock:
            n["i"] += 1
        return np.zeros((2, 2), np.uint8)

    hub = FrameHub(read, fps=50).start()
    try:
        assert hub.fresh(1) is not None
    finally:
        hub.stop()
