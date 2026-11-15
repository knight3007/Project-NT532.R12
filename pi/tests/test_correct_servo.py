"""CORRECT bằng visual servoing (Jacobian + Broyden) và laser sáng liên tục từ CORRECT tới hết FIRE.

Môi trường giả: node "thật" có mô hình góc khác hẳn cal của orchestrator (pan_gain 0,4, yaw lệch 8 độ), link ghi
lại mọi lệnh, vision giả trả vết laser tính từ mô hình thật tại góc lệnh đang giữ."""

import copy
import threading
import time
from itertools import pairwise
from types import SimpleNamespace

import numpy as np
import pytest

from nt532.decider.state_text import VERIFY
from nt532.net.fakenode import free_port
from nt532.net.integrated_link import IntegratedLink
from nt532.net.integrated_node import IntegratedCore, IntegratedFakeNode
from nt532.net.link import HeartbeatSender, NodeLink
from nt532.net.protocol import Status
from nt532.orchestrator import selfcal
from nt532.orchestrator.events import EventLog
from nt532.orchestrator.fusion import SensorHistory
from nt532.orchestrator.machine import Abort, Orchestrator, Run, Settings
from nt532.orchestrator.selfcal import NodeCal
from nt532.sim import sim_site
from nt532.vision.types import TagPose

SURF_SITE = sim_site(write=False)
FLOOR_TARGET = np.array([0.50, 0.45, 0.0])
WALL_TARGET = np.array([0.55, SURF_SITE["board"]["plane_y"], 0.30])


def yaw_rot(deg):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def make_site(**tcfg) -> dict:
    site = copy.deepcopy(SURF_SITE)
    site["actuator"]["nodes"] = {n: {"pan_limits": [-90, 90], "tilt_limits": [-80, 45]} for n in ("s1", "s2")}
    site["targeting"].update({"correct_max_iters": 6, "correct_done_m": 0.01, **tcfg})
    return site


class RigLink:
    """Link giả: servo tới ngay góc lệnh, ghi mọi lệnh theo thứ tự vào `log`."""

    def __init__(self, has_pump=None):
        if has_pump is not None:
            self.has_pump = has_pump
        self.log: list[tuple] = []
        self.current: dict[str, tuple[float, float]] = {}
        self._ids = 0

    def aim(self, node, pan, tilt, ttl):
        self._ids += 1
        self.current[node] = (pan, tilt)
        self.log.append(("aim", node, round(pan, 2), round(tilt, 2)))
        return self._ids

    def fire(self, node, cmd, dev, ms):
        self.log.append(("fire", node, dev, ms))

    def stop(self, node=None):
        self.log.append(("stop", node))

    def wait(self, cmd, states=("reached",), timeout=1.0):
        return Status(cmd, "done" if "done" in states else "reached")

    def hb_age_ms(self, node):
        return 0.0

    def laser_off_cmds(self):
        """Lệnh làm laser tắt: laser 1 ms (cách `_laser_off` tắt không nhả servo) hoặc /stop."""
        return [i for i, e in enumerate(self.log)
                if (e[0] == "fire" and e[2] == "laser" and e[3] <= 1) or e[0] == "stop"]


class RigVision:
    """Vision giả. Vết thật = giao tia của mô hình THẬT với mặt phẳng; `bad_at`: số thứ tự lần đo trả vết bậy."""

    def __init__(self, link, tags, truth, bad_at=(), bad_offset=(0.6, 0.0, 0.0)):
        self.link, self.nodes, self.truth = link, tags, truth
        self.bad_at, self.bad_offset = set(bad_at), np.array(bad_offset)
        self.calls: list[dict] = []
        self.state = True

    def true_spot(self, node, target):
        pan, tilt = self.link.current[node]
        cal = self.truth[node]
        surf = "floor" if target[2] == 0 else "wall"
        return selfcal.forward_spot(cal, self.nodes[node], pan, tilt, surf, selfcal.surfaces_of(self._site))

    def find_spot(self, on, off, **kw):
        node = self.node_of_laser
        self.calls.append(kw)
        spot = self.true_spot(node, self.target)
        if len(self.calls) - 1 in self.bad_at:
            spot = spot + self.bad_offset
        return spot

    def targets(self, frame):
        return []

    def blockers(self, node):
        return []

    def on_board(self, p):
        return True


def build(truth_cal=None, has_pump=None, bad_at=(), target=FLOOR_TARGET, nodes=("s1",), **tcfg):
    """Orchestrator dựng tối giản với link/vision giả. `truth_cal`: {node: NodeCal} của node thật."""
    site = make_site(**tcfg)
    link = RigLink(has_pump)
    tags = {"s1": TagPose(21, np.array([0.30, 0.0, 0.0]), yaw_rot(0)),
            "s2": TagPose(22, np.array([0.90, 0.0, 0.0]), yaw_rot(0))}
    truth = truth_cal or {n: NodeCal((0.0, 0.0, 0.35), yaw0_deg=8.0, pan_gain=0.4, tilt_gain=1.0)
                          for n in ("s1", "s2")}
    vis = RigVision(link, tags, truth, bad_at)
    vis._site, vis.target = site, np.asarray(target, float)
    o = Orchestrator.__new__(Orchestrator)
    o.site, o.tcfg, o.acfg = site, site["targeting"], {"fire_ms": 300, "aim_ttl_ms": 1500}
    o.cfg = Settings(laser_settle_s=0.0, verify_wait_s=0.0, status_timeout_s=1.0, laser_hold_ms=4000)
    o.link, o.vision, o.events = link, vis, EventLog()
    o.hub = SimpleNamespace(fresh=lambda: np.zeros((2, 2, 3), np.uint8))
    o.sensors = SensorHistory()
    o.overlay, o._lock, o._abort = {}, threading.Lock(), threading.Event()
    o.clock = time.monotonic
    o.cals = {n: NodeCal((0.0, 0.0, 0.35)) for n in ("s1", "s2")}  # cal của orchestrator: không biết gain, yaw0
    o.pose_age = lambda n: 0.0
    o.phases = []
    o._set = lambda phase, note="", active=None: (o.phases.append(phase), link.log.append(("phase", phase.value)))

    def find_spot(on, off, **kw):  # vision biết vòi nào đang CORRECT qua `current` của lệnh gần nhất
        vis.node_of_laser = [e for e in link.log if e[0] == "aim"][-1][1]
        return RigVision.find_spot(vis, on, off, **kw)
    vis.find_spot = find_spot
    return o


def pivot_of(o, node):
    return o.cal(node).pivot_world(o.vision.nodes[node])


def true_error_cm(o, node, target, c):
    o.link.current[node] = c
    o.vision.node_of_laser = node
    return float(np.linalg.norm(RigVision.true_spot(o.vision, node, np.asarray(target)) - target) * 100)


# --- 1. hội tụ khi cal sai nặng ----------------------------------------------------------------------


def old_method_error_cm(o, node, target, iters):
    """Cách cũ: dời điểm ngắm 3D đúng bằng độ lệch đo, đổi sang lệnh bằng cal (sai). Trả độ lệch cm mỗi vòng."""
    target = np.asarray(target)
    mask = o._plane_mask(target)
    aim, errs = target.copy(), []
    for _ in range(iters):
        c = o._cmd(node, pivot_of(o, node), aim)
        errs.append(true_error_cm(o, node, target, c))
        spot = RigVision.true_spot(o.vision, node, target)
        aim = aim + (target - spot) * mask
    return errs


@pytest.mark.parametrize("target", [FLOOR_TARGET, WALL_TARGET], ids=["floor", "wall"])
def test_correct_converges_with_badly_wrong_model(target):
    o = build(target=target)
    run = Run(1, "s1")
    first = true_error_cm(o, "s1", target, o._cmd("s1", pivot_of(o, "s1"), target))
    assert first > 10  # mô hình sai nặng: trượt hơn 10 cm khi ngắm thẳng
    o.link.log.clear()
    _, miss_cm, ang = o._aim_and_correct(run, "s1", pivot_of(o, "s1"), target, target.copy())
    assert len(run.shots) <= 6 and miss_cm < 3.0, run.shots
    assert true_error_cm(o, "s1", target, ang) < 3.0  # góc trả về đúng là góc đang giữ laser
    assert ang == tuple(o.link.current["s1"])
    assert o.overlay["spots"]["s1"]["surface"] == ("floor" if target[2] == 0 else "wall")


def test_old_method_does_not_reach_3cm_on_same_case():
    o = build()
    errs = old_method_error_cm(o, "s1", FLOOR_TARGET, 3)  # ngân sách cũ: correct_max_iters = 3
    assert errs[0] > 10 and errs[-1] > 3.0, errs
    run = Run(1, "s1")
    o.link.log.clear()
    _, miss_cm, _ = o._aim_and_correct(run, "s1", pivot_of(o, "s1"), FLOOR_TARGET, FLOOR_TARGET.copy())
    assert len(run.shots) <= 6 and miss_cm < errs[-1]


def test_correct_uses_model_jacobian_then_broyden_in_few_rounds():
    o = build()
    run = Run(1, "s1")
    o._aim_and_correct(run, "s1", pivot_of(o, "s1"), FLOOR_TARGET, FLOOR_TARGET.copy())
    misses = [s["miss_cm"] for s in run.shots]
    assert misses[0] > 10 and misses[-1] < 3 and len(misses) <= 5, misses
    assert o.events.since(0) and any(e["kind"] == "correct" for e in o.events.since(0))


def test_search_region_is_passed_to_vision_and_widens_when_missing():
    o = build()
    run = Run(1, "s1")
    o._aim_and_correct(run, "s1", pivot_of(o, "s1"), FLOOR_TARGET, FLOOR_TARGET.copy())
    calls = o.vision.calls
    assert all("near" in kw and kw["near_m"] > 0 for kw in calls)
    assert calls[0]["near_m"] == pytest.approx(0.30)  # vòng đầu: bán kính gấp đôi
    assert all(kw["near_m"] == pytest.approx(0.15) for kw in calls[1:])


def test_missing_spot_widens_radius_then_gives_up():
    o = build()
    seen = []

    def blind(on, off, **kw):
        seen.append(kw["near_m"])
    o.vision.find_spot = blind
    run = Run(1, "s1")
    _, miss_cm, _ = o._aim_and_correct(run, "s1", pivot_of(o, "s1"), FLOOR_TARGET, FLOOR_TARGET.copy())
    assert seen == pytest.approx([0.30, 0.60, 1.20]) and miss_cm is None
    assert run.shots == [{"round": 1, "miss_cm": None, "nozzle": "s1"}]


def test_outlier_spot_is_remeasured_once():
    # lần đo thứ 3 (chỉ số 2) trả vết bậy cách 60 cm: phải đo lại trước khi tin, rồi vẫn hội tụ
    o = build(bad_at=(2,))
    run = Run(1, "s1")
    _, miss_cm, _ = o._aim_and_correct(run, "s1", pivot_of(o, "s1"), FLOOR_TARGET, FLOOR_TARGET.copy())
    assert any("đo lại" in e["text"] for e in o.events.since(0))
    assert miss_cm < 3.0 and all(s["miss_cm"] < 40 for s in run.shots), run.shots  # vết bậy không vào shots
    assert len(o.vision.calls) == len(run.shots) + 1


def test_step_is_clamped():
    o = build(correct_step_max_deg=2.0)
    run = Run(1, "s1")
    o._aim_and_correct(run, "s1", pivot_of(o, "s1"), FLOOR_TARGET, FLOOR_TARGET.copy())
    aims = [e for e in o.link.log if e[0] == "aim"]
    assert len(aims) >= 3
    for a, b in pairwise(aims):
        assert np.hypot(b[2] - a[2], b[3] - a[3]) <= 2.0 + 0.02


# --- 2. laser sáng liên tục --------------------------------------------------------------------------


class StubDecider:
    def __init__(self, nxt=VERIFY[0]):
        self.nxt, self.verify = nxt, []
        self.decider = "stub"

    def decide(self, obs, q):
        self.verify.append(obs["verify"])
        return SimpleNamespace(get=lambda k: self.nxt, to_json=dict, decider="stub")


def engage(o, nozzles=("s1",), target=FLOOR_TARGET):
    o.decider = StubDecider()
    obs = {"alarm": "s1", "limits": {}, "targets": [{"id": "T1", "x": float(target[0]), "z": float(target[2]),
                                                    "y": float(target[1]), "surface": "floor"}]}
    run = Run(1, "s1")
    pivots = {n: pivot_of(o, n) for n in nozzles}
    out = o._engage(run, obs, "T1", target, list(nozzles), pivots)
    return run, out


def first_index(log, pred):
    return next(i for i, e in enumerate(log) if pred(e))


ENGAGE_FAST = {"engage_hz": 40, "engage_clear_s": 0.3, "engage_min_hit_s": 0.1, "engage_max_hit_s": 5.0,
               "engage_verify_s": 0.1, "engage_lost_s": 3.0, "engage_timeout_s": 6.0}


def test_no_laser_off_during_engage_for_node_without_pump():
    o = build(has_pump=False, **ENGAGE_FAST)
    o.cfg.spot_pair_gap_s = 0.0
    run, out = engage(o)
    log = o.link.log
    assert out == "extinguished"
    assert o.link.laser_off_cmds() == [], log  # không một lệnh tắt laser nào trước khi lượt kết thúc
    assert not any(e[:3] == ("fire", "s1", "pump") for e in log)  # firmware không có bơm: laser thay tia nước
    # mỗi chu kỳ ENGAGE gia hạn laser (không bật tắt): số lệnh laser >= số lần đo
    assert sum(e[:3] == ("fire", "s1", "laser") for e in log) >= len(run.shots)


def test_node_with_pump_fires_pump_and_keeps_aiming_laser():
    o = build(has_pump=True, **ENGAGE_FAST)
    o.cfg.spot_pair_gap_s = 0.0
    _, out = engage(o)
    log = o.link.log
    pump = first_index(log, lambda e: e[:3] == ("fire", "s1", "pump"))
    assert out == "extinguished"
    assert log[pump][3] == o.acfg["fire_ms"]
    assert o.link.laser_off_cmds() == []


def test_pump_water_tilt_is_divided_by_tilt_gain():
    o = build(has_pump=True)
    o.site["actuator"]["nodes"]["s1"]["water_tilt_deg"] = 3.0
    o.cals["s1"] = NodeCal((0.0, 0.0, 0.35), tilt_gain=1.5)
    pivots = {"s1": pivot_of(o, "s1")}
    held = {"s1": (-10.0, -20.0)}
    o._final_aim(["s1"], FLOOR_TARGET, pivots, {"s1": FLOOR_TARGET}, held)
    assert o.link.log[-1] == ("aim", "s1", -10.0, round(-20.0 + 3.0 / 1.5, 2))
    o2 = build(has_pump=False)  # không bơm: laser thay tia nước, không bù
    o2.site["actuator"]["nodes"]["s1"]["water_tilt_deg"] = 3.0
    o2._final_aim(["s1"], FLOOR_TARGET, {"s1": pivot_of(o2, "s1")}, {"s1": FLOOR_TARGET}, held)
    assert o2.link.log[-1] == ("aim", "s1", -10.0, -20.0)


def test_both_nozzles_only_first_tracks_laser_and_both_are_lit():
    o = build(has_pump=False, **ENGAGE_FAST)
    o.cfg.spot_pair_gap_s = 0.0
    o.vision.find_spot = lambda on, off, **kw: (setattr(o.vision, "node_of_laser", "s1"),
                                                RigVision.find_spot(o.vision, on, off, **kw))[1]  # vết của s1
    run, out = engage(o, nozzles=("s1", "s2"))
    log = o.link.log
    assert out == "extinguished"
    assert {s["nozzle"] for s in run.shots} == {"s1"}  # hai chấm không phân biệt được: chỉ bám tia vòi đầu
    lit = {e[1] for e in log if e[0] == "fire" and e[2] == "laser"}
    assert lit == {"s1", "s2"} and o.link.laser_off_cmds() == []


def test_emergency_stop_and_fault_still_use_link_stop():
    o = build(has_pump=False)
    o._abort.set()
    with pytest.raises(Abort):
        o._aim_and_correct(Run(1, "s1"), "s1", pivot_of(o, "s1"), FLOOR_TARGET, FLOOR_TARGET.copy())
    # _handle luôn gọi link.stop() ở finally; xác nhận đúng chỗ đó còn trong mã
    import inspect
    assert "self.link.stop()" in inspect.getsource(Orchestrator._handle)
    assert "self.link.stop()" in inspect.getsource(Orchestrator.emergency_stop)


def test_link_has_pump_flags():
    assert NodeLink.has_pump is True and IntegratedLink.has_pump is False


# --- IntegratedLink: fire mới thay fire cũ thì không có OFF chen giữa -------------------------------------


@pytest.fixture(scope="module")
def core():
    try:
        return IntegratedCore.get()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"không có lõi C node B tích hợp: {e}")


def _wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def node_rig(core):
    pi, port = free_port(), free_port()
    tr = ["simplesocketserver"]
    link = IntegratedLink({"s1": f"127.0.0.1:{port}"}, ("127.0.0.1", pi), timeout_s=1.0, transports=tr).start()
    node = IntegratedFakeNode("s1", ("127.0.0.1", port), f"coap://127.0.0.1:{pi}", transports=tr).start()
    hb = HeartbeatSender(link.heartbeat, ["s1"], 200)
    hb.start()
    assert _wait_for(lambda: node.snapshot()["hb_seen"], 5)
    yield link, node
    hb.halt()
    node.stop()
    link.close()


def laser_log(node):
    return [m for _, m in node.hw.log if m.startswith("laser")]


def test_new_laser_fire_replaces_old_without_off_in_between(node_rig):
    link, node = node_rig
    cid = link.aim("s1", 10.0, 5.0, 4000)
    assert link.wait(cid, ("reached", "rejected", "fault"), 5).st == "reached"
    link.fire("s1", cid, "laser", 300)  # hẹn tắt sau 0,3 s
    assert _wait_for(lambda: node.hw.on["laser"], 2)
    time.sleep(0.1)
    cid2 = link.aim("s1", 12.0, 5.0, 4000)  # ngắm lúc laser đang bật không được tắt laser
    assert link.wait(cid2, ("reached", "rejected", "fault"), 5).st == "reached"
    assert node.hw.on["laser"]
    link.fire("s1", cid2, "laser", 1500)  # thay lệnh cũ trước khi nó hết giờ
    time.sleep(0.6)  # quá hạn của lệnh cũ (0,3 s): vẫn phải sáng
    assert node.hw.on["laser"] and laser_log(node)[-1:] == ["laser on"]
    st = link.wait(cid2, ("done", "fault"), 4)
    assert st is not None and st.st == "done"  # lệnh hiện hành hết giờ thì tắt và báo done
    assert not node.hw.on["laser"]
    assert laser_log(node) == ["laser on", "laser off"]  # đúng một lần bật và một lần tắt


def test_stop_still_turns_laser_off_immediately(node_rig):
    link, node = node_rig
    cid = link.aim("s1", 10.0, 5.0, 4000)
    link.wait(cid, ("reached", "rejected", "fault"), 5)
    link.fire("s1", cid, "laser", 5000)
    assert _wait_for(lambda: node.hw.on["laser"], 2)
    link.stop("s1")
    assert _wait_for(lambda: not node.hw.on["laser"], 2)
    st = link.wait(cid, ("done", "fault"), 3)
    assert st is not None and st.st == "fault" and st.err == "stopped"
