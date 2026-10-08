import time

import numpy as np
import pytest

from nt532.decider.state_text import ACTIONS, VERIFY, render_state
from nt532.net.protocol import Telemetry
from nt532.orchestrator import aiming
from nt532.orchestrator.decide import Answer, HybridDecider, RuleDecider
from nt532.orchestrator.fusion import (
    SensorHistory,
    aggregate,
    candidate_id,
    decide_obs,
    questions_for,
    verify_obs,
)
from nt532.sim import aim_angles, sim_site
from nt532.vision.types import Detection, Target

SITE = sim_site(write=False)
NODE_X = {"s1": 0.30, "s2": 0.90}


def tgt(x, z, conf, w=40, h=40):
    return Target(Detection(0, 0, w, h, conf), np.array([x, 0.8, z]))


def history(hot: str | None = None) -> SensorHistory:
    h = SensorHistory()
    for i in range(6):
        for n in ("s1", "s2"):
            t = 55.0 if n == hot and i >= 3 else 27.0
            h.add(Telemetry(n, i, i, t, 820 if n == hot and i >= 3 else 180, 50))
    return h


def nozzles(hb=200, pose=10, reach=True):
    return {n: {"hb_ms": hb, "pose_s": pose, "reach": lambda x, z: reach} for n in ("s1", "s2")}


def test_aggregate_joins_frames_and_marks_misses():
    frames = [[tgt(0.30, 0.20, 0.8), tgt(0.85, 0.40, 0.4)],
              [tgt(0.31, 0.21, 0.7)],
              [tgt(0.84, 0.41, 0.5), tgt(0.29, 0.20, 0.9)]]
    tracks = sorted(aggregate(frames, (1280, 720)), key=lambda t: t.position[0])
    assert len(tracks) == 2
    assert tracks[0].conf == [0.8, 0.7, 0.9]
    assert tracks[1].conf == [0.4, None, 0.5]
    assert abs(tracks[0].position[0] - 0.30) < 0.01
    assert tracks[0].w == pytest.approx(40 / 1280)


def test_two_targets_in_one_frame_never_merge():
    tracks = aggregate([[tgt(0.30, 0.20, 0.8), tgt(0.32, 0.21, 0.6)]], (1280, 720))
    assert len(tracks) == 2


def test_decide_obs_matches_scenario_schema_and_rules_run():
    tracks = aggregate([[tgt(0.32, 0.25, 0.8)]] * 3, (1280, 720))
    obs, by_id = decide_obs("s1", history("s1"), tracks, nozzles(), NODE_X, 3,
                            {"temp": 45.0, "gas": 600.0})
    assert set(obs) == {"stage", "alarm", "limits", "sensors", "frames", "targets", "nozzles"}
    assert obs["targets"][0]["id"] == "T1" and "T1" in by_id
    assert obs["targets"][0]["dist"] == {"s1": 0.02, "s2": 0.58}
    assert len(obs["sensors"]["s1"]["temp"]) == 5
    text = render_state(obs)
    assert "T1: x 0.32 z 0.25" in text and "nozzle s1: heartbeat_age 200ms" in text
    q = questions_for(obs)
    assert set(q) == {"real_fire", "action", "target", "nozzle"}
    d = RuleDecider().decide(obs, q)
    assert d.get("real_fire") is True and d.get("action") == ACTIONS[0]
    assert candidate_id(d.get("target")) == "T1" and d.get("nozzle") == "s1"


def test_rules_ignore_when_nothing_seen_and_avoid_dead_nozzle():
    obs, _ = decide_obs("s2", history("s2"), [], nozzles(), NODE_X, 4, {"temp": 45.0, "gas": 600.0})
    q = questions_for(obs)
    assert set(q) == {"real_fire", "action"}
    assert RuleDecider().decide(obs, q).get("action") == ACTIONS[2]

    tracks = aggregate([[tgt(0.32, 0.25, 0.8)]], (1280, 720))
    noz = nozzles()
    noz["s1"]["hb_ms"] = 5000  # vòi s1 mất heartbeat
    obs, _ = decide_obs("s1", history("s1"), tracks, noz, NODE_X, 1, {"temp": 45.0, "gas": 600.0})
    assert RuleDecider().decide(obs, questions_for(obs)).get("nozzle") == "s2"


def test_verify_obs_and_rule():
    tracks = aggregate([[tgt(0.32, 0.25, 0.8)]], (1280, 720))
    obs, _ = decide_obs("s1", history("s1"), tracks, nozzles(), NODE_X, 1, {"temp": 45.0, "gas": 600.0})
    v = verify_obs(obs, "T1", "s1", 1, "ok", [50.0, 45.0, 40.0], [None, None, None], 1.24,
                   history("s1"))
    assert "water_mark offset: 1.2cm" in render_state(v)
    assert RuleDecider().decide(v, questions_for(v)).get("after_verify") == VERIFY[0]
    v["verify"]["status"] = "fault"
    assert RuleDecider().decide(v, questions_for(v)).get("after_verify") == VERIFY[3]


def test_sensor_window_pads_and_handles_missing_node():
    h = SensorHistory()
    h.add(Telemetry("s1", 1, 1, 30.0, 200))
    w = h.window("s1")
    assert w["temp"] == [30.0] * 5 and w["hum"] == [None] * 5
    assert h.window("s2")["gas"] == [0] * 5


class FakeModel:
    def __init__(self, conf, fail=False):
        self.conf, self.fail = conf, fail

    def answers(self, obs, questions):
        if self.fail:
            raise RuntimeError("không có trọng số")
        return {q: Answer("MODEL", self.conf, {"MODEL": self.conf}, "model") for q in questions}


def test_hybrid_uses_model_only_when_confident():
    obs, _ = decide_obs("s1", history("s1"), [], nozzles(), NODE_X, 4, {"temp": 45.0, "gas": 600.0})
    q = questions_for(obs)
    assert HybridDecider(FakeModel(0.95), tau=0.8).decide(obs, q).get("action") == "MODEL"
    assert HybridDecider(FakeModel(0.5), tau=0.8).decide(obs, q).get("action") == ACTIONS[2]
    h = HybridDecider(FakeModel(0.99, fail=True))
    assert h.decide(obs, q).get("action") == ACTIONS[2] and "trọng số" in h.last_error


def test_aiming_matches_sim_convention_and_limits():
    pivot, target = (0.3, 0.55, 0.33), (0.1, 0.8, 0.4)
    assert aiming.angles(pivot, target) == pytest.approx(aim_angles(pivot, target))
    lim = aiming.limits(SITE, "s1")
    assert lim.ok(0, 0) and not lim.ok(60, 0) and not lim.ok(0, -40)
    assert aiming.reachable(SITE, "s1", pivot, 0.35, 0.3)


# --- chạy cả trạm trên sa bàn ảo (thời gian thực, khoảng 10-30 s) ---------------------------

def wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def station():
    from nt532.orchestrator.machine import Settings
    from nt532.station import build_sim

    st = build_sim(settings=Settings(frame_gap_s=0.05, verify_wait_s=1.0), fps=12)
    st.start()
    yield st
    st.stop()


def test_station_puts_out_fire_end_to_end(station):
    station.world.ignite(0.32, 0.30)
    assert wait_for(lambda: station.orch.runs, 60), "không có lượt nào hoàn tất"
    run = station.orch.runs[0]
    assert run.node == "s1" and run.nozzle == "s1"
    assert run.outcome in ("extinguished", "human"), run.to_json()
    assert run.decisions[0]["answers"]["action"]["answer"] == "spray"
    assert run.shots and run.shots[-1]["miss_cm"] is not None and run.shots[-1]["miss_cm"] < 3
    assert "s1" in station.world.marks  # bơm đã phun thật
    assert not any(station.world.truth()["lasers"].values())
    assert not any(station.world.pumping.values())


def test_emergency_stop_turns_everything_off(station):
    station.world.ignite(0.90, 0.30)
    assert wait_for(lambda: station.orch.phase.value in ("CORRECT", "FIRE"), 60)
    station.orch.emergency_stop("thử")
    assert wait_for(lambda: station.orch.runs, 10)
    assert station.orch.runs[0].outcome == "stopped"
    time.sleep(0.3)
    assert not any(station.world.truth()["lasers"].values())
    assert not any(station.world.pumping.values())


def test_lost_link_ends_in_safe_state(station):
    station.world.set_online("s2", False)
    station.world.ignite(0.90, 0.30)
    station.world.add_spike("s1")  # s2 im lặng; cần s1 báo động để có lượt xử lý
    assert wait_for(lambda: station.orch.runs, 60)
    run = station.orch.runs[0]
    # s2 mất heartbeat: không được ngắm bằng s2
    assert run.nozzle != "s2"
    assert run.outcome in ("ignored", "alarm_only", "extinguished", "human"), run.to_json()
