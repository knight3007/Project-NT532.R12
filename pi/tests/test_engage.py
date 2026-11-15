"""ENGAGE: một pha liên tục (phun, bám tia, bám lửa, xác nhận dập) với link và vision giả."""

import numpy as np
import pytest
from test_correct_servo import FLOOR_TARGET, build, pivot_of, true_error_cm

from nt532.decider.state_text import VERIFY
from nt532.orchestrator.machine import Phase, Run
from nt532.vision.types import Detection, Target

FAST = {"engage_hz": 40, "engage_clear_s": 0.3, "engage_min_hit_s": 0.1, "engage_max_hit_s": 0.6,
        "engage_timeout_s": 6.0, "engage_lost_s": 0.5, "engage_verify_s": 0.1, "tolerance_m": 0.03}


class Dec:
    decider = "fake"

    def __init__(self, ans):
        self.ans = ans

    def get(self, key, default=None):
        return self.ans if key == "after_verify" else default

    def to_json(self):
        return {"decider": "fake", "answers": {"after_verify": self.ans}}


class FakeDecider:
    def __init__(self, ans=VERIFY[2]):
        self.ans, self.calls = ans, 0

    def decide(self, obs, questions):
        self.calls += 1
        return Dec(self.ans)


def rig(fire_visible, **tcfg):
    """Orchestrator giả; `fire_visible(n)` -> vị trí 3D của lửa ở lần gọi `targets` thứ n, hoặc None."""
    o = build(**{**FAST, **tcfg})
    o.cfg.spot_pair_gap_s = 0.0
    o.decider = FakeDecider()
    calls = {"n": 0}
    o.vision.state = True

    def targets(frame):
        calls["n"] += 1
        pos = fire_visible(calls["n"])
        if pos is None:
            return []
        return [Target(Detection(900.0, 500.0, 60.0, 80.0, 0.9), np.asarray(pos, float), "floor")]

    o.vision.targets = targets
    o.pivots = {"s1": pivot_of(o, "s1")}
    return o


def engage(o, target=FLOOR_TARGET):
    run = Run(1, "s1")
    obs = {"alarm": "s1", "limits": {"temp": 50.0, "gas": 300.0},
           "targets": [{"id": "T1", "x": float(target[0]), "y": float(target[1]), "z": float(target[2]),
                        "surface": "floor"}]}
    out = o._engage(run, obs, "T1", target, ["s1"], o.pivots)
    return run, out


def test_fire_vanishes_after_hit_is_extinguished_and_laser_stays_on():
    o = rig(lambda n: FLOOR_TARGET if n <= 45 else None, engage_max_hit_s=10.0)
    _, out = engage(o)
    assert out == "extinguished"
    assert Phase.ENGAGE in o.phases and Phase.HUMAN not in o.phases
    assert o.link.laser_off_cmds() == []  # không có lệnh tắt laser trước khi kết thúc
    assert any(e[0] == "fire" and e[2] == "laser" for e in o.link.log)


def test_fire_never_goes_out_calls_human_after_max_hit():
    o = rig(lambda n: FLOOR_TARGET, engage_max_hit_s=0.4)
    _, out = engage(o)
    assert out == "human" and Phase.HUMAN in o.phases
    assert o.link.laser_off_cmds() == []


def test_long_lost_spot_calls_human():
    o = rig(lambda n: FLOOR_TARGET)
    o.vision.find_spot = lambda on, off, **kw: None
    run, out = engage(o)
    assert out == "human"
    assert run.shots and all(s["miss_cm"] is None for s in run.shots)


def test_target_follows_moving_fire():
    moved = FLOOR_TARGET + np.array([0.06, 0.0, 0.0])
    o = rig(lambda n: FLOOR_TARGET if n <= 8 else moved, engage_max_hit_s=2.0)
    o.vision.target = moved
    run, _ = engage(o)
    tgt = o.overlay["target"]
    assert abs(tgt["x"] - moved[0]) < 0.01  # đích EMA đã bám theo lửa
    last = [s["miss_cm"] for s in run.shots if s["miss_cm"] is not None][-3:]
    assert min(last) < 3.0


def test_decider_give_up_calls_human():
    o = rig(lambda n: FLOOR_TARGET, engage_max_hit_s=30.0)
    o.decider = FakeDecider(VERIFY[3])
    run, out = engage(o)
    assert out == "human" and o.decider.calls >= 1
    assert run.decisions and run.decisions[0]["stage"] == "verify"


def test_decider_done_ignored_while_camera_still_sees_fire():
    o = rig(lambda n: FLOOR_TARGET, engage_max_hit_s=0.5)
    o.decider = FakeDecider(VERIFY[0])
    _, out = engage(o)
    assert out == "human"  # "done" bị bỏ qua vì camera vẫn thấy lửa


def test_badly_wrong_model_still_converges_under_3cm():
    o = rig(lambda n: FLOOR_TARGET, engage_max_hit_s=1.0)
    first = true_error_cm(o, "s1", FLOOR_TARGET, o._cmd("s1", pivot_of(o, "s1"), FLOOR_TARGET))
    assert first > 10
    o.link.log.clear()
    run, _ = engage(o)
    assert true_error_cm(o, "s1", FLOOR_TARGET, o.link.current["s1"]) < 3.0
    assert any(s["miss_cm"] is not None and s["miss_cm"] < 3.0 for s in run.shots)


@pytest.mark.parametrize("has_pump", [True, False])
def test_pump_node_fires_pump_and_laser(has_pump):
    o = rig(lambda n: FLOOR_TARGET if n <= 30 else None, engage_max_hit_s=10.0)
    o.link.has_pump = has_pump
    engage(o)
    devs = {e[2] for e in o.link.log if e[0] == "fire"}
    assert ("pump" in devs) == has_pump and "laser" in devs
