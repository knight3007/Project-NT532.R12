"""Tự hiệu chuẩn vòi: mô hình hình học, khớp, lưu file; và chạy trên sa bàn ảo có servo lệch."""

import threading
import time
from dataclasses import replace

import numpy as np
import pytest

from nt532.dashboard.server import Dashboard
from nt532.orchestrator import aiming, selfcal
from nt532.orchestrator.machine import Phase, Run
from nt532.orchestrator.selfcal import NodeCal
from nt532.sim import sim_site
from nt532.sim.demo import fast_settings
from nt532.station import build_sim
from nt532.vision.types import TagPose

SITE = sim_site(write=False)
SURF = selfcal.surfaces_of(SITE)


def yaw_rot(deg):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


TAG = TagPose(21, np.array([0.0, 0.0, 0.0]), yaw_rot(-36.87))
TRUE = NodeCal((0.02, -0.015, 0.09), yaw0_deg=2.5, tilt0_deg=-3.0, pan_gain=1.02, tilt_gain=0.98, heading_deg=None)
PRIOR = NodeCal((0.0, 0.0, 0.08))


def synth(cal, n=12, noise=0.005, seed=1, tag=TAG, limits=None):
    rng = np.random.default_rng(seed)
    lim = limits or aiming.Limits((-70, 70), (-35, 45))
    out = []
    for pan, tilt in selfcal.sweep_commands(PRIOR, tag, SITE, lim, n=n, margin_m=0.05):
        spot = selfcal.predict_spot(cal, tag, pan, tilt, SURF)
        if spot is not None:
            out.append((tag, pan, tilt, spot + rng.normal(0, noise, 3)))
    return out


def test_command_for_round_trip():
    for target in ([0.6, 0.8, 0.3], [0.5, 0.4, 0.0], [0.2, 0.3, 0.0]):
        pan, tilt = selfcal.command_for(TRUE, TAG, target)
        spot = selfcal.predict_spot(TRUE, TAG, pan, tilt, SURF)
        assert spot is not None and np.linalg.norm(spot - target) < 1e-6
    pan, tilt = selfcal.command_for(TRUE, TAG, [0.5, 0.4, 0.0], extra_tilt_deg=2.0)
    pan2, tilt2 = selfcal.command_for(TRUE, TAG, [0.5, 0.4, 0.0])
    assert tilt - tilt2 == pytest.approx(2.0 / TRUE.tilt_gain) and pan == pytest.approx(pan2)


def test_prior_matches_aiming_angles():
    p = TAG.to_world(PRIOR.pivot)
    assert selfcal.command_for(PRIOR, TAG, [0.5, 0.8, 0.3]) == pytest.approx(
        aiming.angles(p, [0.5, 0.8, 0.3], aiming.heading_yaw_deg(TAG)))


def test_fit_recovers_params_from_noisy_spots():
    samples = synth(TRUE, n=12)
    assert len(samples) >= 10
    cal = selfcal.fit(samples, PRIOR, SITE)
    assert cal.fitted, cal.note
    assert np.linalg.norm(np.array(cal.pivot) - TRUE.pivot) < 0.01
    assert abs(cal.yaw0_deg - TRUE.yaw0_deg) < 0.5 and abs(cal.tilt0_deg - TRUE.tilt0_deg) < 0.5
    assert abs(cal.pan_gain - TRUE.pan_gain) < 0.02 and abs(cal.tilt_gain - TRUE.tilt_gain) < 0.02
    assert cal.rms_cm < 1.5 and cal.rms_before_cm > 2 * cal.rms_cm


def test_fit_recovers_pivot_far_from_prior():
    """Vòi trên trụ cao: tag nằm cạnh trụ, pivot thật cách tâm tag [0,10; 0; 0,35] còn prior là [0; 0; 0,08]."""
    true = NodeCal((0.10, 0.0, 0.35), yaw0_deg=1.5, tilt0_deg=-2.0)
    tag = TagPose(22, np.array([0.0, 0.0, 0.0]), yaw_rot(-36.87))
    samples = []
    rng = np.random.default_rng(3)
    for pan in np.linspace(-30, 30, 5):
        for tilt in (-35, -22, -10, 5):
            spot = selfcal.predict_spot(true, tag, pan, tilt, SURF)
            if spot is not None:
                samples.append((tag, float(pan), float(tilt), spot + rng.normal(0, 0.005, 3)))
    assert len(samples) >= 10
    wide = {**SITE, "selfcal": {**(SITE.get("selfcal") or {}), "max_pivot_shift_m": 0.5}}  # pivot thật cách mồi 27 cm
    cal = selfcal.fit(samples, PRIOR, wide)
    assert cal.fitted, cal.note
    assert np.linalg.norm(np.array(cal.pivot) - true.pivot) < 0.015, cal.pivot


def test_degenerate_set_fits_only_yaw_and_tilt():
    true = NodeCal((0.0, 0.0, 0.08), yaw0_deg=3.0, tilt0_deg=-2.0)
    samples = []
    for pan, tilt in ((0, -20), (3, -21), (-3, -19), (2, -22)):
        spot = selfcal.predict_spot(true, TAG, pan, tilt, SURF)
        assert spot is not None
        samples.append((TAG, pan, tilt, spot))
    cal = selfcal.fit(samples, PRIOR, SITE)
    assert cal.fitted and "chỉ khớp yaw0, tilt0" in cal.note
    assert cal.pivot == PRIOR.pivot and cal.pan_gain == 1.0 and cal.tilt_gain == 1.0
    assert abs(cal.yaw0_deg - 3.0) < 0.3 and abs(cal.tilt0_deg + 2.0) < 0.3


def test_fit_rejects_inconsistent_spots():
    rng = np.random.default_rng(0)
    samples = [(TAG, p, t, np.array([rng.uniform(0, 1.2), rng.uniform(0, 0.8), 0.0]))
               for p, t in selfcal.sweep_commands(PRIOR, TAG, SITE, aiming.Limits((-70, 70), (-35, 45)), 8)]
    cal = selfcal.fit(samples, PRIOR, SITE)
    assert not cal.fitted and "bác" in cal.note and cal.pivot == PRIOR.pivot


def test_sweep_commands_spread_and_visible():
    lim = aiming.Limits((-70, 70), (-35, 45))
    cmds = selfcal.sweep_commands(PRIOR, TAG, SITE, lim, n=8)
    assert len(cmds) == 8
    assert max(c[0] for c in cmds) - min(c[0] for c in cmds) >= 15
    assert max(c[1] for c in cmds) - min(c[1] for c in cmds) >= 8
    assert all(lim.ok(*c) and selfcal.predict_spot(PRIOR, TAG, *c, SURF, -0.10) is not None for c in cmds)


def test_yaml_round_trip(tmp_path):
    site = {**SITE, "selfcal": {"file": str(tmp_path / "nodes.yaml")}}
    cal = NodeCal((0.05, 0.0, 0.12), 1.2, -0.7, 1.01, 0.99, 9, 0.8, True, 123.0)
    selfcal.save_cals(site, {"s1": cal, "s2": NodeCal((0, 0, 0.08))})
    got = selfcal.load_cals(site)
    assert set(got) == {"s1"}  # chỉ cal đã khớp được lưu
    g = got["s1"]
    assert g.pivot == pytest.approx(cal.pivot) and g.yaw0_deg == pytest.approx(1.2)
    assert g.tilt0_deg == pytest.approx(-0.7) and g.n_samples == 9 and g.fitted
    assert selfcal.load_cals({**SITE, "selfcal": {"file": str(tmp_path / "none.yaml")}}) == {}


def test_per_node_pivot_prior():
    site = {"actuator": {"pivot_offset": [0, 0, 0.08], "nodes": {"s1": {"pivot_offset": [0.1, 0, 0.35]}, "s2": {}}}}
    assert NodeCal.from_site(site, "s1").pivot == (0.1, 0.0, 0.35)
    assert NodeCal.from_site(site, "s2").pivot == (0.0, 0.0, 0.08)
    assert aiming.pivot_offset(site) == (0, 0, 0.08)


# --- sa bàn ảo: servo lệch, pivot giả định sai ------------------------------------------------------



def calib_events(st, stage=None):
    return [e for e in st.events.since(0, 10_000)
            if e["kind"] == "calib" and (stage is None or e.get("stage") == stage)]


def first_round_miss(st, node, target):
    """Độ lệch cm của vòng CORRECT đầu tiên khi ngắm thẳng tới `target` bằng cal hiện tại."""
    orch = st.orch
    run = Run(1, node)
    orch.overlay = {"active": [], "aims": {}, "spots": {}}
    pivot = orch.cal(node).pivot_world(orch.vision.nodes[node])
    t = np.asarray(target, float)
    orch._aim_and_correct(run, node, pivot, t, t.copy())
    orch.phase = Phase.IDLE
    orch.link.stop(node)  # như cuối một lượt thật: CORRECT để laser sáng, không để nó lẫn vào khung nền của lần quét sau
    time.sleep(0.5)
    return run.shots[0]["miss_cm"]


@pytest.fixture(scope="module")
def rig():
    st = build_sim(settings=fast_settings(), fps=12, pivot_error_m=0.02, seed=0)
    st.start()
    dash = Dashboard(st, "127.0.0.1", 0).start()
    time.sleep(1.0)
    yield st, dash
    dash.stop()
    st.stop()


def test_calibrate_recovers_servo_bias_and_pivot_then_aims_true(rig):
    st, _ = rig
    truth = st.world.calib_truth()["s1"]
    target = [0.5, 0.5, 0.0]
    before = first_round_miss(st, "s1", target)
    assert before > 2.0  # chưa hiệu chuẩn: servo lệch và pivot sai làm trượt vài cm

    res = st.orch.calibrate("s1")
    assert res["ok"], res
    cal = st.orch.cals["s1"]
    print("s1 truth", truth, "fit", cal.to_json())
    # hướng thật = lệnh + sai lệch servo, nên yaw0, tilt0 bằng sai lệch (lệnh phải bù ngược dấu)
    assert abs(cal.yaw0_deg - truth["yaw0_deg"]) < 0.5 and abs(cal.tilt0_deg - truth["tilt0_deg"]) < 0.5
    assert np.linalg.norm(np.array(cal.pivot) - truth["pivot"]) < 0.01
    assert cal.fitted and cal.rms_cm < 1.0 and res["rms_before_cm"] > 2 * res["rms_cm"]
    assert first_round_miss(st, "s1", target) < 1.5

    result = calib_events(st, "result")[-1]
    assert result["node"] == "s1" and len(result["pivot_delta_cm"]) == 3 and "cm" in result["text"]
    assert len(calib_events(st, "shot")) >= 4
    assert set(selfcal.load_cals(st.site)) == {"s1"}  # đã lưu file


def test_snapshot_has_calib_state(rig):
    st, _ = rig
    calib = st.orch.snapshot()["calib"]
    assert set(calib) == {"s1", "s2"}
    assert calib["s1"]["fitted"] and calib["s1"]["state"] == "ok" and calib["s1"]["rms_cm"] < 1.0
    assert calib["s2"]["state"] in ("idle", "ok") and {"pivot", "yaw0_deg", "tilt0_deg", "n_samples"} <= set(calib["s1"])


def test_calibrate_aborts_on_emergency_stop(rig):
    st, _ = rig
    before = st.orch.cals["s2"]
    out = {}
    th = threading.Thread(target=lambda: out.update(st.orch.calibrate("s2")))
    n0 = len(calib_events(st, "shot"))
    th.start()
    end = time.time() + 30
    while len(calib_events(st, "shot")) < n0 + 2 and time.time() < end:
        time.sleep(0.05)
    st.orch.emergency_stop("thử")
    th.join(20)
    assert not th.is_alive() and out["ok"] is False and "dừng" in out["reason"]
    assert st.orch.cals["s2"] is before and st.orch.snapshot()["calib"]["s2"]["state"] != "sweeping"
    time.sleep(0.5)
    assert not any(st.world.truth()["lasers"].values())


def test_calibrate_yields_to_alert_and_refuses_when_busy(rig):
    st, _ = rig
    st.orch.enabled = False
    assert "tắt" in st.orch.calibrate("s2")["reason"]
    st.orch.enabled = True
    st.orch.phase = Phase.FIRE
    try:
        assert "bận" in st.orch.calibrate("s2")["reason"]
    finally:
        st.orch.phase = Phase.IDLE
    assert st.orch.calibrate("s9")["ok"] is False


def test_dashboard_calibrate_command(rig):
    st, dash = rig
    assert not dash.command({"cmd": "calibrate", "node": "x"})["ok"]
    assert dash.command({"cmd": "calibrate", "node": "s2"})["ok"]
    end = time.time() + 60
    while time.time() < end and st.orch.snapshot()["calib"]["s2"]["state"] != "ok":
        time.sleep(0.2)
    c = st.orch.snapshot()["calib"]["s2"]
    assert c["fitted"] and c["state"] == "ok"
    truth = st.world.calib_truth()["s2"]
    assert abs(c["yaw0_deg"] - truth["yaw0_deg"]) < 0.5 and abs(c["tilt0_deg"] - truth["tilt0_deg"]) < 0.5
    assert set(selfcal.load_cals(st.site)) == {"s1", "s2"}


# --- bác kết quả khớp vô lý, bỏ cal vô lý khi nạp, quét lùi về số mồi -----------------------------------

# hai kết quả vô lý có thật đã lưu trên Pi: tilt_gain gần 0; tâm quay cách số mồi gần 1 m và dưới sàn
BAD_S1 = NodeCal((-0.0075, -0.3268, 0.1123), tilt_gain=0.0003, fitted=True, rms_cm=5.31)
BAD_S2 = NodeCal((-0.3755, 0.7611, -0.0191), yaw0_deg=-15.3, fitted=True, rms_cm=7.89)
PI_PRIOR = NodeCal((0.02, -0.29, 0.36))


def test_cal_violations_each_limit():
    assert selfcal.cal_violations(PI_PRIOR, PI_PRIOR, SITE) == []
    assert any("tâm quay" in v for v in selfcal.cal_violations(BAD_S2, PI_PRIOR, SITE))
    assert any("tilt_gain" in v for v in selfcal.cal_violations(BAD_S1, PI_PRIOR, SITE))
    assert any("pan_gain" in v for v in selfcal.cal_violations(replace(PI_PRIOR, pan_gain=1.4), PI_PRIOR, SITE))
    assert any("yaw0" in v for v in selfcal.cal_violations(replace(PI_PRIOR, yaw0_deg=-26.0), PI_PRIOR, SITE))
    assert any("tilt0" in v for v in selfcal.cal_violations(replace(PI_PRIOR, tilt0_deg=30.0), PI_PRIOR, SITE))
    assert selfcal.cal_violations(replace(PI_PRIOR, pan_gain=1.25, yaw0_deg=-20.0, tilt0_deg=24.0), PI_PRIOR, SITE) == []
    # ngưỡng đọc từ site.yaml mục selfcal
    loose = {**SITE, "selfcal": {**SITE["selfcal"], "max_pivot_shift_m": 2.0, "min_gain": 0.0, "max_angle_offset_deg": 90}}
    assert selfcal.cal_violations(BAD_S2, PI_PRIOR, loose) == []


def test_fit_rejects_absurd_but_low_rms_solution():
    """Mẫu sinh từ pivot thật cách mồi 27 cm: rms thấp nhưng vô lý theo ngưỡng mặc định, nên bị bác và ghi lý do."""
    true = NodeCal((0.10, 0.0, 0.35), yaw0_deg=1.5, tilt0_deg=-2.0)
    tag = TagPose(22, np.array([0.0, 0.0, 0.0]), yaw_rot(-36.87))
    rng = np.random.default_rng(3)
    samples = []
    for pan in np.linspace(-30, 30, 5):
        for tilt in (-35, -22, -10, 5):
            spot = selfcal.predict_spot(true, tag, pan, tilt, SURF)
            if spot is not None:
                samples.append((tag, float(pan), float(tilt), spot + rng.normal(0, 0.005, 3)))
    cal = selfcal.fit(samples, PRIOR, SITE)
    assert not cal.fitted and "vô lý" in cal.note and "tâm quay" in cal.note
    assert cal.pivot == PRIOR.pivot  # trả baseline


def test_load_cals_drops_absurd_saved_cal_and_reports(tmp_path):
    site = {**SITE, "selfcal": {**SITE["selfcal"], "file": str(tmp_path / "nodes.yaml")},
            "actuator": {**SITE["actuator"], "nodes": {"s1": {"pivot_offset": [0.02, -0.29, 0.36]},
                                                        "s2": {"pivot_offset": [0.0, -0.28, 0.31]}}}}
    good = NodeCal((0.03, -0.30, 0.35), 1.0, -1.0, 1.02, 0.98, 9, 0.8, True, 1.0)
    selfcal.save_cals(site, {"s1": BAD_S1, "s2": good})
    out: list = []
    got = selfcal.load_cals(site, out)
    assert set(got) == {"s2"} and len(out) == 1 and out[0].startswith("s1:") and "tilt_gain" in out[0]
    selfcal.save_cals(site, {"s1": good, "s2": BAD_S2})
    out = []
    assert set(selfcal.load_cals(site, out)) == {"s1"} and "tâm quay" in out[0]


def test_orchestrator_startup_warns_and_uses_prior_for_absurd_saved_cal(tmp_path):
    from types import SimpleNamespace

    from nt532.orchestrator.events import EventLog
    from nt532.orchestrator.machine import Orchestrator
    site = {**SITE, "selfcal": {**SITE["selfcal"], "file": str(tmp_path / "nodes.yaml")},
            "actuator": {**SITE["actuator"], "nodes": {"s1": {"pivot_offset": [0.02, -0.29, 0.36]},
                                                        "s2": {"pivot_offset": [0.0, -0.28, 0.31]}}}}
    selfcal.save_cals(site, {"s1": BAD_S1, "s2": BAD_S2})
    ev = EventLog()
    orch = Orchestrator(site, SimpleNamespace(nodes={}), None, None, None, None, ev)
    assert not orch.cals["s1"].fitted and orch.cals["s1"].pivot == (0.02, -0.29, 0.36)
    assert not orch.cals["s2"].fitted and orch.cals["s2"].pivot == (0.0, -0.28, 0.31)
    warns = [e for e in ev.since(0) if e["kind"] == "calib" and e["level"] == "warn"]
    assert len(warns) == 2 and "bỏ cal đã lưu" in warns[0]["text"]


def test_sweep_falls_back_to_prior_when_saved_cal_is_absurd():
    lim = aiming.Limits((-70, 70), (-35, 45))
    # cal hỏng (tilt_gain ~ 0) dự đoán mọi vết ra ngoài vùng: không có đường lui thì "0 góc quét"
    assert selfcal.sweep_commands(BAD_S1, TAG, SITE, lim, 8) == []
    cmds = selfcal.sweep_commands(BAD_S1, TAG, SITE, lim, 8, prior=PRIOR)
    assert len(cmds) == 8
    assert all(selfcal.predict_spot(PRIOR, TAG, *c, SURF, -0.10) is not None for c in cmds)  # chọn theo số mồi


def test_sweep_relaxes_margin_to_zero_before_giving_up():
    lim = aiming.Limits((-70, 70), (-35, 45))
    assert selfcal.sweep_commands(PRIOR, TAG, SITE, lim, 8, margin_m=0.35) != []  # lề 0,35 m không chọn nổi: nới về 0
    assert len(selfcal.sweep_commands(PRIOR, TAG, SITE, lim, 8, margin_m=0.35)) == 8
    assert len(selfcal.sweep_commands(PRIOR, TAG, SITE, lim, 8, margin_m=0.10)) == 8  # đủ góc thì giữ nguyên lề
    # cả cal hỏng lẫn lề: thử đủ cả bốn tổ hợp
    assert len(selfcal.sweep_commands(BAD_S1, TAG, SITE, lim, 8, margin_m=0.35, prior=PRIOR)) == 8


# --- quét dẫn đường tới tâm tag, laser chỉ trong khung tag tham chiếu -------------------------------------


class _GuidedRig:
    """Orchestrator tối giản: link/laser giả, thế giới thật có cal `truth`; ghi mọi lệnh và vết."""

    def __init__(self, truth, believed, lose_after=None, outside=False):
        from types import SimpleNamespace

        from nt532.orchestrator.events import EventLog
        from nt532.orchestrator.machine import Orchestrator
        self.cmds, self.spots = [], []
        self.truth, self.lose_after, self.outside = truth, lose_after, outside
        vision = SimpleNamespace(nodes={"s1": TAG}, reference_mode="fixed", reference_map=None)
        orch = Orchestrator(SITE, vision, None, None, None, None, EventLog())
        orch.cals = {**orch.cals, "s1": believed}
        orch._aim = self._aim
        orch._observe = self._observe
        orch.hub = SimpleNamespace(fresh=lambda: None)
        self.fires, self.stops = [], []
        orch.link = SimpleNamespace(fire=lambda n, c, kind, ms: self.fires.append((kind, ms)),
                                    stop=lambda *a: self.stops.append(a))
        orch._sleep = lambda s: None
        self.orch, self.cmd = orch, None

    def _aim(self, node, pan, tilt):
        assert aiming.limits(SITE, node).ok(pan, tilt)
        self.cmds.append((pan, tilt))
        return len(self.cmds)

    def _observe(self, node, center, radius_m, off, on=None):
        pan, tilt = self.cmds[-1]
        if self.lose_after is not None and len(self.spots) >= self.lose_after:
            self.spots.append(None)
            return None
        s = selfcal.forward_spot(self.truth, TAG, pan, tilt, "floor", SURF)
        if s is not None and self.outside:
            s = s + np.array([0.0, -2.0, 0.0])  # luôn rơi xa ngoài khung
        self.spots.append(s)
        return None if s is None else np.asarray(s, float)


def _hull_of(orch):
    return orch._sweep_hull()


def test_guided_sweep_stays_in_frame_and_fits():
    truth = replace(TRUE, tilt0_deg=-3.0)
    believed = replace(PRIOR, tilt0_deg=30.0, pan_gain=0.6)  # cal sai nặng
    rig = _GuidedRig(truth, believed)
    orch = rig.orch
    hull = _hull_of(orch)
    targets = orch._sweep_targets("s1", hull)
    assert len(targets) == 4 and targets[0][0] in ("tag 0", "tag 1", "tag 2", "tag 3")
    samples = orch._guided_sweep("s1", hull, targets)
    assert len(samples) >= 6
    for _t, _p, _tl, s in samples:
        assert selfcal.hull_margin(s[:2], hull) >= -0.05 and abs(s[2]) < 0.05  # mọi vết trong khung nới lề
    # không lệnh nào ngóc quá 6 độ so với vết tốt trước đó (và bắt đầu không ngóc quá cal + 6)
    for (_, t0), (_, t1) in zip(rig.cmds, rig.cmds[1:], strict=False):
        assert t1 <= t0 + 6.0 + 1e-6 or rig.spots[len(rig.cmds) - 1] is None
    # mọi tag đều được dắt tới sát tâm
    ends = {}
    for name, tgt in targets:
        ends[name] = min(np.linalg.norm(s[:2] - tgt[:2]) for *_x, s in samples)
    # bước góc kẹp 6 độ và 8 bước mỗi tag: tag ở xa (quay pan hơn 45 độ) có thể chưa tới nơi, các tag còn lại phải tới
    assert sum(v < 0.03 for v in ends.values()) >= 3, (ends, [e['text'] for e in orch.events.since(0, 10_000)])
    cal = selfcal.fit(samples, PRIOR, SITE)
    assert cal.fitted and cal.rms_cm < 2.0, cal.note
    # laser sáng liên tục: không lệnh OFF (hẹn giờ 1 ms) hay stop nào giữa đầu và cuối lượt quét
    assert rig.fires and all(k == "laser" and ms == orch.cfg.laser_hold_ms for k, ms in rig.fires)
    assert rig.stops == []
    events = [e for e in orch.events.since(0, 10_000) if e.get("stage") == "shot"]
    assert any("tag" in e["text"] and "bước" in e["text"] for e in events)


def test_guided_sweep_lost_spot_backs_off_then_drops():
    rig = _GuidedRig(TRUE, PRIOR, lose_after=1)  # thấy vết khởi đầu rồi mất hết
    orch = rig.orch
    hull = _hull_of(orch)
    targets = orch._sweep_targets("s1", hull)
    samples = orch._guided_sweep("s1", hull, targets)
    assert len(samples) == 1  # chỉ vết khởi đầu
    warns = [e for e in orch.events.since(0, 10_000) if "lùi" in e["text"]]
    assert warns and any("bỏ tag" in e["text"] for e in orch.events.since(0, 10_000))
    assert len(rig.cmds) <= 1 + 2 * len(targets)  # mỗi tag tối đa hai lần thử liền


def test_guided_sweep_outside_spot_aborts_without_start():
    from nt532.orchestrator.machine import Abort
    rig = _GuidedRig(TRUE, PRIOR, outside=True)  # mọi vết ngoài khung
    orch = rig.orch
    hull = _hull_of(orch)
    with pytest.raises(Abort):
        orch._guided_sweep("s1", hull, orch._sweep_targets("s1", hull))
    # thang lệnh khởi đầu chỉ hạ tilt, không bao giờ ngóc
    assert len(rig.cmds) <= 5
    assert all(b[1] <= max(a[1] for a in rig.cmds[:1]) + 6.0 for b in rig.cmds)
