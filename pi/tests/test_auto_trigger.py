"""Tự kích hoạt chữa cháy khi camera thấy lửa liên tục (không cần cảm biến), đồng hồ giả và orch giả."""

from types import SimpleNamespace

import numpy as np

from nt532.vision.live import LiveDetector
from nt532.vision.types import Detection

AUTO = {"trigger_s": 15.0, "gap_s": 0.6, "cooldown_s": 15.0, "conf": 0.35}


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


class Orch:
    def __init__(self):
        self.phase = SimpleNamespace(value="IDLE")
        self.enabled = True
        self.alerts = []

    def on_alert(self, a):
        self.alerts.append(a)


def make(auto=AUTO, point=(0.1, 0.3, 0.0)):
    box = {"seen": True, "point": point, "seq": 0}
    orch = Orch()
    nodes = {"s1": SimpleNamespace(position=np.array([0.0, 0.0, 0.0])),
             "s2": SimpleNamespace(position=np.array([1.0, 0.0, 0.0]))}

    def detect(f):
        return [Detection(80, 60, 20, 30, 0.9)] if box["seen"] else []

    vision = SimpleNamespace(state=object(), blockers=list, nodes=nodes, detect=detect,
                             localize_detection=lambda d: (np.array(box["point"]), "floor"))
    events = []
    st = SimpleNamespace(
        vision=vision, orch=orch, site={}, frames=box,
        hub=SimpleNamespace(latest=lambda: (box["seq"], np.zeros((4, 4, 3), np.uint8))),
        sensors=SimpleNamespace(samples=lambda n, k: [SimpleNamespace(temp=41.0, gas=10.0)]),
        events=SimpleNamespace(emit=lambda *a, **k: events.append((a, k))))
    clk = Clock()
    return LiveDetector(st, clock=clk, auto=auto), clk, box, orch, events


def tick(live, clk, box, dt=0.2):
    clk.t += dt
    box["seq"] += 1
    live.step()


def run(live, clk, box, secs, dt=0.2):
    for _ in range(round(secs / dt)):
        tick(live, clk, box, dt)


def test_fires_once_after_15s_on_nearest_node():
    live, clk, box, orch, events = make()
    run(live, clk, box, 14.0)
    assert orch.alerts == [] and 13.5 < live.auto_status()["seen_s"] < 14.5
    run(live, clk, box, 1.4)
    assert len(orch.alerts) == 1
    a = orch.alerts[0]
    assert (a.n, a.s, a.k, a.t) == ("s1", -1, "alert", 41.0) and a.g > 10.0
    assert events and events[0][1]["source"] == "camera"
    run(live, clk, box, 5.0)
    assert len(orch.alerts) == 1  # đang chờ


def test_nearest_node_s2():
    live, clk, box, orch, _ = make(point=(0.9, 0.2, 0.0))
    run(live, clk, box, 15.4)
    assert [a.n for a in orch.alerts] == ["s2"]


def test_missed_frame_keeps_count_but_long_gap_resets():
    live, clk, box, orch, _ = make()
    run(live, clk, box, 7.0)
    box["seen"] = False
    tick(live, clk, box, 0.2)  # lỡ một khung
    box["seen"] = True
    run(live, clk, box, 8.4)
    assert len(orch.alerts) == 1
    live, clk, box, orch, _ = make()
    run(live, clk, box, 7.0)
    box["seen"] = False
    run(live, clk, box, 1.0)  # mất quá gap_s
    box["seen"] = True
    run(live, clk, box, 8.4)
    assert orch.alerts == []


def test_moved_fire_restarts_count():
    live, clk, box, orch, _ = make()
    run(live, clk, box, 10.0)
    box["point"] = (0.5, 0.3, 0.0)
    run(live, clk, box, 8.0)
    assert orch.alerts == []


def test_cooldown_then_refires_if_fire_persists():
    live, clk, box, orch, _ = make()
    run(live, clk, box, 15.4)
    assert len(orch.alerts) == 1
    assert live.auto_status()["next_allowed_in_s"] > 14
    run(live, clk, box, 14.0)
    assert len(orch.alerts) == 1  # trong thời gian chờ
    run(live, clk, box, 3.0)
    assert len(orch.alerts) == 2  # hết chờ, lửa còn: kích lại


def test_cooldown_starts_after_run_ends():
    live, clk, box, orch, _ = make(auto={**AUTO, "cooldown_s": 30.0})
    orch.phase.value = "ENGAGE"
    run(live, clk, box, 20.0)
    orch.phase.value = "IDLE"
    run(live, clk, box, 25.0)  # đã thấy lửa > 15 s nhưng còn trong 30 s chờ sau lượt
    assert orch.alerts == []
    run(live, clk, box, 6.0)
    assert len(orch.alerts) == 1


def test_not_idle_or_disabled_or_off_does_not_fire():
    live, clk, box, orch, _ = make()
    orch.enabled = False
    run(live, clk, box, 20.0)
    assert orch.alerts == []
    live, clk, box, orch, _ = make(auto=None)
    run(live, clk, box, 20.0)
    assert orch.alerts == [] and live.auto_status() is None
