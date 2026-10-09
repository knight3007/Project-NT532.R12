import re
import threading
import time

import numpy as np
import pytest

from nt532.decider.state_text import ACTIONS, BOTH, VERIFY, nozzles_of, render_state
from nt532.net.protocol import Aim, Fire, Stop, Telemetry
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


def test_decide_obs_has_dist3_and_both_candidate():
    tracks = aggregate([[tgt(0.62, 0.25, 0.8, w=128, h=72)]] * 3, (1280, 720))
    noz = nozzles()
    noz["s1"]["pivot"] = (0.30, 0.56, 0.25)
    noz["s2"]["pivot"] = (0.90, 0.56, 0.25)
    obs, _ = decide_obs("s1", history("s1"), tracks, noz, NODE_X, 3, {"temp": 45.0, "gas": 600.0})
    t = obs["targets"][0]
    assert t["w"] == pytest.approx(0.1) and t["h"] == pytest.approx(0.1)
    assert t["dist3"]["s1"] > t["dist3"]["s2"] > 0.3  # s2 gần hơn (0.28 so với 0.32 theo X)
    assert questions_for(obs)["nozzle"]["candidates"] == ["s1", "s2", BOTH]
    d = RuleDecider().decide(obs, questions_for(obs))
    assert d.get("nozzle") == "s2"  # nearest, không phải node báo động; không bao giờ both
    noz["s2"]["pose_s"] = 600
    obs, _ = decide_obs("s1", history("s1"), tracks, noz, NODE_X, 3, {"temp": 45.0, "gas": 600.0})
    assert questions_for(obs)["nozzle"]["candidates"] == ["s1", "s2"]
    # không có pivot thì dist3 = dist
    obs, _ = decide_obs("s1", history("s1"), tracks, nozzles(), NODE_X, 3, {"temp": 45.0, "gas": 600.0})
    assert obs["targets"][0]["dist3"] == obs["targets"][0]["dist"]
    v = verify_obs(obs, "T1", BOTH, 1, "ok", [50.0, 45.0, 40.0], [None] * 3, 1.0, history("s1"))
    assert f"with {BOTH}" in render_state(v)


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
        self.conf, self.fail, self.calls = conf, fail, 0

    def answers(self, obs, questions):
        self.calls += 1
        if self.fail:
            raise RuntimeError("không có trọng số")
        return {q: Answer("MODEL", self.conf, {"MODEL": self.conf}, "model") for q in questions}


def test_hybrid_uses_model_only_when_confident():
    obs, _ = decide_obs("s1", history("s1"), [], nozzles(), NODE_X, 4, {"temp": 45.0, "gas": 600.0})
    q = questions_for(obs)
    both = {"decide", "verify"}
    assert HybridDecider(FakeModel(0.95), tau=0.8, stages=both).decide(obs, q).get("action") == "MODEL"
    assert HybridDecider(FakeModel(0.5), tau=0.8, stages=both).decide(obs, q).get("action") == ACTIONS[2]
    h = HybridDecider(FakeModel(0.99, fail=True), stages=both)
    assert h.decide(obs, q).get("action") == ACTIONS[2] and "trọng số" in h.last_error


def test_hybrid_stages_skip_model_outside_chosen_stage():
    tracks = aggregate([[tgt(0.32, 0.25, 0.8)]] * 3, (1280, 720))
    obs, _ = decide_obs("s1", history("s1"), tracks, nozzles(), NODE_X, 3, {"temp": 45.0, "gas": 600.0})
    dq = questions_for(obs)
    vobs = verify_obs(obs, obs["targets"][0]["id"], "s1", 1, "ok", [50.0, 48.0, 46.0], [None] * 3, None, history("s1"))
    vq = questions_for(vobs)
    model = FakeModel(0.99)
    h = HybridDecider(model, stages={"verify"})  # mặc định
    d = h.decide(obs, dq)
    assert model.calls == 0 and d.get("action") == ACTIONS[0]
    assert all(a.source == "rules" for a in d.answers.values())
    assert h.decide(vobs, vq).get("after_verify") == "MODEL" and model.calls == 1
    model = FakeModel(0.99)
    assert HybridDecider(model, stages="decide").decide(vobs, vq).get("after_verify") != "MODEL"
    assert model.calls == 0
    assert "verify" in h.name


def test_aiming_matches_sim_convention_and_limits():
    pivot, target = (0.3, 0.55, 0.33), (0.1, 0.8, 0.4)
    assert aiming.angles(pivot, target) == pytest.approx(aim_angles(pivot, target))
    lim = aiming.limits(SITE, "s1")
    assert lim.ok(0, 0) and lim.ok(60, 0) and not lim.ok(80, 0) and not lim.ok(0, -40)
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


class BothDecider(RuleDecider):
    """Luật, nhưng câu `nozzle` luôn trả `both` (như Jev có thể chọn)."""

    def answers(self, obs, questions):
        ans = super().answers(obs, questions)
        if "nozzle" in ans:
            ans["nozzle"] = Answer(BOTH, 1.0, {BOTH: 1.0}, "test")
        return ans


class PumpSampler:
    """Lấy mẫu `pumping` của sa bàn mỗi 50 ms, nhớ hai bơm có chạy cùng lúc lần nào không."""

    def __init__(self, world) -> None:
        self.world, self.overlap, self.seen = world, False, set()
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._halt.wait(0.05):
            on = {n for n, v in self.world.truth()["pumping"].items() if v}
            self.seen |= on
            self.overlap |= len(on) == 2

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._halt.set()
        self._thread.join(2)


class OverlaySampler:
    """Lấy mẫu (pha, các vòi đang làm việc) từ `snapshot()` mỗi 20 ms, nhớ mọi cặp đã thấy."""

    def __init__(self, orch) -> None:
        self.orch, self.seen = orch, set()
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._halt.wait(0.02):
            snap = self.orch.snapshot()
            self.seen.add((snap["phase"], tuple(snap["overlay"].get("active", ()))))

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._halt.set()
        self._thread.join(2)


def overlay_at_run_end(orch) -> list:
    """Overlay ngay khi mỗi lượt kết thúc (lượt sau reset overlay nên không đọc muộn được)."""
    ends, real_handle = [], orch._handle

    def handle(alert):
        real_handle(alert)
        ends.append(orch.snapshot()["overlay"])

    orch._handle = handle
    return ends


def test_both_sprays_two_nozzles_together(station):
    site, pivots = station.site, {}
    for n in ("s1", "s2"):
        pivots[n] = station.vision.nodes[n].to_world(aiming.pivot_offset(site))
        assert aiming.reachable(site, n, pivots[n], 0.60, 0.30)  # cả hai vòi với tới bia
    station.orch.decider = BothDecider()
    ends = overlay_at_run_end(station.orch)
    with PumpSampler(station.world) as pumps, OverlaySampler(station.orch) as ov:
        station.world.ignite(0.60, 0.30, size="large")
        assert wait_for(lambda: station.orch.runs, 90), "không có lượt nào hoàn tất"
    run = station.orch.runs[0]
    assert run.decisions[0]["answers"]["nozzle"]["answer"] == BOTH
    assert run.nozzle == BOTH and run.outcome in ("extinguished", "human"), run.to_json()
    assert pumps.overlap, "hai bơm không chạy cùng lúc"
    assert {"s1", "s2"} <= set(station.world.marks)  # cả hai vòi đều đã phun
    ev = station.events.since(0, 10_000)
    vobs = [e["obs"]["verify"] for e in ev if e["kind"] == "decision" and e["obs"]["stage"] == "verify"]
    assert vobs and all(v["nozzle"] == BOTH and v["status"] in ("ok", "fault") for v in vobs)
    assert f"with {BOTH}" in next(d for d in run.decisions if d["stage"] == "verify")["state"]
    # AIM + CORRECT lần lượt từng vòi: có vòng của cả hai vòi, mỗi vòng ghi tên vòi
    assert {s["nozzle"] for s in run.shots} == {"s1", "s2"}
    corr = [e["text"] for e in ev if e["kind"] == "correct"]
    assert corr and all(re.match(r"vòng \d+: lệch [\d.]+ cm \(s[12]\)$", t) for t in corr)
    # overlay: AIM/CORRECT luôn đúng một vòi (lần lượt s1 rồi s2), FIRE hai vòi cùng lúc, VERIFY không vòi nào
    assert {("CORRECT", ("s1",)), ("CORRECT", ("s2",)), ("FIRE", ("s1", "s2")), ("VERIFY", ())} <= ov.seen
    for phase, active in ov.seen:
        if phase in ("AIM", "CORRECT"):
            assert active in (("s1",), ("s2",)), (phase, active)
        elif phase == "FIRE":
            assert active in ((), ("s1", "s2")), (phase, active)
        else:
            assert active == (), (phase, active)
    # mỗi vòi một điểm ngắm và một vết laser; hết lượt thì không vòi nào còn "đang làm việc"
    end = ends[0]
    assert set(end["aims"]) == {"s1", "s2"} and set(end["spots"]) == {"s1", "s2"}
    assert end["active"] == [] and end["nozzle"] == BOTH
    assert end["aims"]["s1"] != end["aims"]["s2"]
    # cả hai node cùng báo động nên có thể còn một lượt thứ hai (thường chỉ thấy thẻ cháy đen rồi bỏ qua)
    assert wait_for(lambda: station.orch.phase.value == "IDLE" and not station.orch.snapshot()["queue"], 90)
    time.sleep(0.3)
    assert not any(station.world.truth()["lasers"].values())
    assert not any(station.world.pumping.values())


def test_both_with_one_nozzle_down_sprays_with_other(station):
    station.world.set_online("s2", False)
    station.orch.decider = BothDecider()
    station.world.ignite(0.32, 0.30)
    station.world.add_spike("s1")
    assert wait_for(lambda: station.orch.runs, 60), "không có lượt nào hoàn tất"
    run = station.orch.runs[0]
    assert run.decisions[0]["answers"]["nozzle"]["answer"] == BOTH
    assert run.nozzle == "s1" and run.outcome in ("extinguished", "human"), run.to_json()
    ev = station.events.since(0, 10_000)
    assert any(e["kind"] == "safety" and "s2" in e["text"] and "chỉ phun bằng s1" in e["text"] for e in ev)
    assert "s1" in station.world.marks and "s2" not in station.world.marks
    assert not any(s["nozzle"] == "s2" for s in run.shots)


def test_both_drops_nozzle_that_fails_right_before_fire(station):
    from nt532.net.link import LinkError

    orch = station.orch
    real_aim = orch._aim

    def flaky(node, pan, tilt):  # s2 ngắm được lúc CORRECT nhưng mất liên lạc ngay lúc FIRE
        if node == "s2" and orch.phase.value == "FIRE":
            raise LinkError("s2 không trả lời lệnh ngắm")
        return real_aim(node, pan, tilt)

    orch._aim = flaky
    orch.decider = BothDecider()
    station.world.ignite(0.60, 0.30, size="large")
    assert wait_for(lambda: orch.runs, 90), "không có lượt nào hoàn tất"
    run = orch.runs[0]
    assert run.nozzle == "s1" and run.outcome in ("extinguished", "human"), run.to_json()
    ev = station.events.since(0, 10_000)
    assert any(e["kind"] == "safety" and "s2" in e["text"] and "bỏ vòi" in e["text"] for e in ev)
    vobs = [e["obs"]["verify"] for e in ev if e["kind"] == "decision" and e["obs"]["stage"] == "verify"]
    assert vobs and all(v["nozzle"] == "s1" for v in vobs)
    assert "s1" in station.world.marks and "s2" not in station.world.marks
    assert {s["nozzle"] for s in run.shots} == {"s1", "s2"}  # s2 vẫn đã qua CORRECT trước đó


def fail_aim(orch, where: dict) -> None:
    """`orch._aim` ném LinkError cho vòi n khi đang ở pha where[n] (AIM: lần ngắm đầu; CORRECT: lần ngắm
    lại sau vòng laser đầu), như một vòi mất liên lạc giữa chừng."""
    from nt532.net.link import LinkError

    real_aim = orch._aim

    def flaky(node, pan, tilt):
        if where.get(node) == orch.phase.value:
            raise LinkError(f"{node} không trả lời lệnh ngắm")
        return real_aim(node, pan, tilt)

    orch._aim = flaky


def test_both_drops_nozzle_that_fails_during_correct(station):
    orch = station.orch
    fail_aim(orch, {"s2": "CORRECT"})  # s2 ngắm được lúc AIM nhưng mất liên lạc khi ngắm lại
    orch.decider = BothDecider()
    ends = overlay_at_run_end(orch)
    with PumpSampler(station.world) as pumps:
        station.world.ignite(0.60, 0.30, size="large")
        assert wait_for(lambda: orch.runs, 90), "không có lượt nào hoàn tất"
    run = orch.runs[0]
    assert run.decisions[0]["answers"]["nozzle"]["answer"] == BOTH
    assert run.nozzle == "s1" and run.outcome in ("extinguished", "human"), run.to_json()
    ev = station.events.since(0, 10_000)
    assert any(e["kind"] == "safety" and "s2" in e["text"] and "hỏng lúc ngắm" in e["text"]
               and "bỏ vòi" in e["text"] for e in ev)
    vobs = [e["obs"]["verify"] for e in ev if e["kind"] == "decision" and e["obs"]["stage"] == "verify"]
    assert vobs and all(v["nozzle"] == "s1" for v in vobs)
    assert pumps.seen == {"s1"} and "s2" not in station.world.marks  # s2 chưa hề bơm
    assert {s["nozzle"] for s in run.shots} == {"s1", "s2"}  # s2 đã có vòng CORRECT đầu
    end = ends[0]
    assert set(end["aims"]) == {"s1"} and end["spots"].keys() == {"s1", "s2"}
    assert end["nozzle"] == "s1" and end["active"] == []


def test_both_drops_first_nozzle_that_fails_during_aim(station):
    orch = station.orch
    fail_aim(orch, {"s1": "AIM"})
    orch.decider = BothDecider()
    with PumpSampler(station.world) as pumps:
        station.world.ignite(0.60, 0.30, size="large")
        assert wait_for(lambda: orch.runs, 90), "không có lượt nào hoàn tất"
    run = orch.runs[0]
    assert run.nozzle == "s2" and run.outcome in ("extinguished", "human"), run.to_json()
    ev = station.events.since(0, 10_000)
    assert any(e["kind"] == "safety" and "s1" in e["text"] and "hỏng lúc ngắm" in e["text"] for e in ev)
    assert pumps.seen == {"s2"} and "s1" not in station.world.marks
    assert {s["nozzle"] for s in run.shots} == {"s2"}  # s1 bị bỏ trước khi kịp bắn laser


def test_both_all_nozzles_failing_while_aiming_is_fault(station):
    orch = station.orch
    fail_aim(orch, {"s1": "AIM", "s2": "CORRECT"})  # s1 bị bỏ, s2 còn lại một mình rồi cũng hỏng
    orch.decider = BothDecider()
    station.world.ignite(0.60, 0.30, size="large")
    assert wait_for(lambda: orch.runs, 90), "không có lượt nào hoàn tất"
    run = orch.runs[0]
    assert run.outcome == "fault", run.to_json()
    ev = station.events.since(0, 10_000)
    assert any(e["kind"] == "safety" and "s1" in e["text"] and "bỏ vòi" in e["text"] for e in ev)
    assert any(e["kind"] == "fault" and "s2" in e["text"] for e in ev)
    assert not station.world.marks  # chưa bơm nào chạy
    assert not any(station.world.pumping.values())


def test_single_nozzle_link_error_while_aiming_is_fault(station):
    orch = station.orch
    fail_aim(orch, {"s1": "AIM", "s2": "AIM"})
    station.world.ignite(0.32, 0.30)
    assert wait_for(lambda: orch.runs, 60), "không có lượt nào hoàn tất"
    run = orch.runs[0]
    assert run.nozzle == "s1" and run.outcome == "fault", run.to_json()
    ev = station.events.since(0, 10_000)
    assert not any(e["kind"] == "safety" and "bỏ vòi" in e["text"] for e in ev)
    assert not station.world.marks


def test_emergency_stop_during_dual_fire(station):
    station.orch.decider = BothDecider()
    station.world.ignite(0.60, 0.30, size="large")
    assert wait_for(lambda: station.orch.phase.value == "FIRE", 90)
    assert wait_for(lambda: any(station.world.pumping.values()), 3), "chưa bơm nào chạy"
    station.orch.emergency_stop("thử hai vòi")
    assert wait_for(lambda: station.orch.runs, 10)
    assert station.orch.runs[0].outcome == "stopped"
    time.sleep(0.3)
    assert not any(station.world.truth()["lasers"].values())
    assert not any(station.world.pumping.values())


def test_nozzles_of():
    assert nozzles_of("s1") == ["s1"] and nozzles_of("s2") == ["s2"]
    assert nozzles_of(BOTH) == ["s1", "s2"]


def test_emergency_stop_turns_everything_off(station):
    station.world.ignite(0.90, 0.30)
    assert wait_for(lambda: station.orch.phase.value in ("CORRECT", "FIRE"), 60)
    station.orch.emergency_stop("thử")
    assert wait_for(lambda: station.orch.runs, 10)
    assert station.orch.runs[0].outcome == "stopped"
    time.sleep(0.3)
    assert not any(station.world.truth()["lasers"].values())
    assert not any(station.world.pumping.values())


def test_fire_arriving_after_stop_is_rejected():
    # Gói /fire gửi trước /stop nhưng tới sau (luồng hoặc UDP đảo thứ tự): laser không được bật lại
    from nt532.sim.world import SimLink, SimWorld

    world = SimWorld(seed=1)
    link = SimLink(world, latency_s=0.0)
    link._handle("s1", "aim", Aim(5, 0.0, 0.0, 1500))
    link._handle("s1", "stop", Stop(6))
    link._handle("s1", "fire", Fire(5, "laser", 300))
    assert link.wait(5, ("rejected",), 0.1).err == "stopped"
    assert not world.truth()["lasers"]["s1"]
    # lệnh mới sau /stop vẫn chạy bình thường
    link._handle("s1", "aim", Aim(7, 0.0, 0.0, 1500))
    assert link.wait(7, ("reached",), 0.1) is not None


def test_lost_link_ends_in_safe_state(station):
    station.world.set_online("s2", False)
    station.world.ignite(0.90, 0.30)
    station.world.add_spike("s1")  # s2 im lặng; cần s1 báo động để có lượt xử lý
    assert wait_for(lambda: station.orch.runs, 60)
    run = station.orch.runs[0]
    # s2 mất heartbeat: không được ngắm bằng s2
    assert run.nozzle != "s2"
    assert run.outcome in ("ignored", "alarm_only", "extinguished", "human"), run.to_json()
