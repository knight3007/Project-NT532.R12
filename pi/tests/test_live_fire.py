"""Nhận diện lửa nền lúc IDLE: luồng nền, vẽ hộp lên khung, và push_camera nhường nguồn hình."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from nt532.dashboard.server import ORANGE, Dashboard
from nt532.vision.live import LiveDetector
from nt532.vision.types import Detection


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


HIT = (np.array([0.3, 0.2, 0.0]), "floor")
READY = object()


def _st(phase="IDLE", blockers=(), state=READY, hit=HIT):
    frames = {"seq": 1, "img": np.zeros((120, 160, 3), np.uint8)}
    vision = SimpleNamespace(state=state, blockers=lambda: list(blockers),
                             detect=lambda f: [Detection(80, 60, 20, 30, 0.9)],
                             localize_detection=lambda d: hit)
    return SimpleNamespace(vision=vision, orch=SimpleNamespace(phase=SimpleNamespace(value=phase)),
                           hub=SimpleNamespace(latest=lambda: (frames["seq"], frames["img"])), frames=frames)


def test_step_produces_live_fire_with_point():
    st, clk = _st(), Clock()
    live = LiveDetector(st, clock=clk)
    assert live.step() is True
    (d,) = live.latest()
    assert d["conf"] == 0.9 and d["box"] == [70.0, 45.0, 90.0, 75.0] and d["anchor"] == [80.0, 75.0]
    assert d["surface"] == "floor" and d["point"][:2] == [0.3, 0.2] and d["age_s"] == 0.0


def test_step_skipped_when_busy_or_not_ready_or_same_frame():
    for kw in ({"phase": "LOCALIZE"}, {"phase": "AIM"}, {"blockers": ["camera xê dịch"]}, {"state": None}):
        st = _st(**kw)
        live = LiveDetector(st, clock=Clock())
        assert live.step() is False and live.latest() == []
    st = _st()
    live = LiveDetector(st, clock=Clock())
    assert live.step() is True and live.step() is False  # cùng seq: không chạy lại


def test_old_result_is_not_returned():
    st, clk = _st(), Clock()
    live = LiveDetector(st, clock=clk)
    live.step()
    clk.t += 1.4
    assert len(live.latest()) == 1
    clk.t += 0.2
    assert live.latest() == []


def test_run_loop_swallows_exceptions():
    st = _st()
    st.vision.detect = lambda f: 1 / 0
    live = LiveDetector(st, hz=200).start()
    import time
    time.sleep(0.1)
    live.stop()
    assert live.latest() == []


def _dash(live, state=None):
    d = Dashboard.__new__(Dashboard)
    d.st = SimpleNamespace(live=live)
    return d


def test_dashboard_draws_box_and_exposes_live_fire():
    st, clk = _st(), Clock()
    live = LiveDetector(st, clock=clk)
    live.step()
    d = _dash(live)
    assert d._live_fire()[0]["surface"] == "floor"
    img = np.zeros((120, 160, 3), np.uint8)
    d._draw_live(img)
    assert (img[45, 70] == ORANGE).all()  # góc khung
    clk.t += 5
    img2 = np.zeros((120, 160, 3), np.uint8)
    d._draw_live(img2)
    assert not img2.any()  # kết quả cũ: không vẽ


def test_draw_out_of_area_label_and_no_live():
    st, clk = _st(hit=None), Clock()
    live = LiveDetector(st, clock=clk)
    live.step()
    img = np.zeros((120, 160, 3), np.uint8)
    _dash(live)._draw_live(img)
    assert img.any()
    assert _dash(None)._live_fire() == []


def _push_camera():
    path = Path(__file__).resolve().parents[1] / "scripts" / "push_camera.py"
    spec = importlib.util.spec_from_file_location("push_camera", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_push_camera_yields_when_alive_file_fresh():
    f = _push_camera().station_is_pushing
    assert f(98.0, 100.0) is True
    assert f(96.0, 100.0) is False
    assert f(None, 100.0) is False
    assert f(101.0, 100.0) is False  # đồng hồ lệch: không tin


def test_live_detector_default_hz_is_5():
    assert LiveDetector(_st()).hz == 5.0


def test_run_loop_delay_subtracts_inference_time():
    st, waits = _st(), []
    clk = Clock()
    live = LiveDetector(st, hz=5, clock=clk)

    def slow_step():
        clk.t += 0.12  # suy luận tốn 0,12 s
        return True

    live.step = slow_step

    class Stop:
        def wait(self, d):
            waits.append(d)
            return len(waits) > 2

    live._stop = Stop()
    live._run()
    assert waits[0] == 0.2 and abs(waits[1] - 0.08) < 1e-9  # chu kỳ 0,2 s trừ 0,12 s suy luận


def test_push_camera_camera_action():
    f = _push_camera().camera_action
    assert f(True, True) == "release" and f(True, False) == "wait"
    assert f(False, False) == "reopen" and f(False, True) == "run"
