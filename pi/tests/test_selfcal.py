"""Tự hiệu chuẩn vòi: mô hình hình học, khớp, lưu file; và chạy trên sa bàn ảo có servo lệch."""

import threading
import time

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
    cal = selfcal.fit(samples, PRIOR, SITE)
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
    cal = NodeCal((0.1, 0.0, 0.35), 1.2, -0.7, 1.01, 0.99, 9, 0.8, True, 123.0)
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
    assert len(calib_events(st, "shot")) >= 6
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
