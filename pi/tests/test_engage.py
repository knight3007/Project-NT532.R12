"""ENGAGE: một pha liên tục (phun, bám tia, bám lửa, xác nhận dập) với link và vision giả."""

from itertools import pairwise

import numpy as np
import pytest
from test_correct_servo import FLOOR_TARGET, build, pivot_of, true_error_cm

from nt532.decider.state_text import VERIFY
from nt532.orchestrator.machine import BOTH, Phase, Run, sweep_pattern, zone_contains
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


def px_of(pos):
    """Pixel giả mã hóa vị trí sàn: 1 pixel = 1 mm, (900, 500) là FLOOR_TARGET."""
    return 900.0 + (pos[0] - 0.5) * 1000, 500.0 + (pos[1] - 0.45) * 1000


def fake_localize(pixel):
    """Ngược của `px_of`: pixel -> điểm trên sàn."""
    return np.array([0.5 + (pixel[0] - 900.0) / 1000, 0.45 + (pixel[1] - 500.0) / 1000, 0.0])


def rig(fire_visible, fire_w=60.0, localize=False, **tcfg):
    """Orchestrator giả; `fire_visible(n)` -> vị trí 3D của lửa ở lần gọi `targets` thứ n, hoặc None.
    `localize`: vision giả có `localize` (hộp lửa rộng `fire_w` pixel = mm) nên ENGAGE chuyển sang quét."""
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
        u, v = px_of(pos)
        return [Target(Detection(u, v, fire_w, 80.0, 0.9), np.asarray(pos, float), "floor")]

    o.vision.targets = targets
    if localize:
        o.vision.localize = fake_localize
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


# --- quét vùng cháy từ gần ra xa, trái sang phải, bám theo lửa ------------------------------------------

SWEEP = {"engage_dwell_s": 0.03, "engage_sweep_step_m": 0.04, "engage_max_hit_s": 100.0,
         "engage_timeout_s": 4.0, "engage_lost_s": 3.0, "engage_clear_s": 100.0}


def sweep_rig(fire_visible, **kw):
    """Rig quét (hộp lửa 160 mm: vùng 16 x 9,6 cm, 3 hàng x 5 cột) kèm ghi lại các lượt dựng lưới."""
    o = rig(fire_visible, fire_w=160.0, localize=True, **{**SWEEP, **kw})
    o.passes = []
    real = o._new_pass

    def new_pass(*a, **k):
        sw = real(*a, **k)
        if sw is not None:
            o.passes.append(sw)
        return sw
    o._new_pass = new_pass
    return o


def test_sweep_pattern_near_to_far_left_to_right():
    zone = {"m": np.array([0.5, 0.5, 0.0]), "fwd": np.array([0.0, 1.0]), "right": np.array([1.0, 0.0]),
            "width": 0.12, "depth": 0.08}
    pts = sweep_pattern(zone, 0.04)
    assert [(r, c) for r, c, _ in pts] == [(r, c) for r in range(3) for c in range(4)]
    assert all(abs(p[2]) < 1e-9 for _, _, p in pts)
    ys = [round(p[1], 3) for _, _, p in pts]
    assert ys == sorted(ys) and ys[0] == 0.5 and ys[-1] == 0.58  # gần -> xa
    for r in range(3):
        xs = [p[0] for rr, _, p in pts if rr == r]
        assert xs == sorted(xs) and abs(xs[0] - 0.44) < 1e-9 and abs(xs[-1] - 0.56) < 1e-9  # trái -> phải
    snake = sweep_pattern(zone, 0.04, serpentine=True)
    row1 = [p[0] for rr, _, p in snake if rr == 1]
    assert row1 == sorted(row1, reverse=True)  # rắn bò: hàng lẻ đi ngược
    assert zone_contains(zone, [0.5, 0.54]) and not zone_contains(zone, [0.5, 0.7])
    assert zone_contains(zone, [0.5, 0.60], tol=0.03) and not zone_contains(zone, [0.5, 0.60])


def test_engage_sweeps_in_order_and_publishes_pattern():
    o = sweep_rig(lambda n: FLOOR_TARGET)
    engage(o)
    assert o.passes, "không vào pha quét"
    pts = o.passes[0]["pts"]
    assert (len(pts), pts[-1][0] + 1) == (15, 3)
    # thứ tự đo thật: tâm tìm vết (near) đi qua đúng các điểm của lưới đầu tiên
    near = [np.asarray(k["near"], float) for k in o.vision.calls if "near" in k]
    first = next(i for i, p in enumerate(near) if np.allclose(p, pts[0][2], atol=1e-4))
    for j, (_, _, p) in enumerate(pts):
        assert np.allclose(near[first + j], p, atol=1e-4), j
    # hướng: hàng sau xa vòi hơn hàng trước; trong hàng đi từ trái sang phải (nhìn từ vòi)
    pivot = pivot_of(o, "s1")[:2]
    z = o.passes[0]["zone"]
    b = [float((p[:2] - z["m"][:2]) @ z["fwd"]) for _, _, p in pts]
    a = [float((p[:2] - z["m"][:2]) @ z["right"]) for _, _, p in pts]
    assert float(z["fwd"] @ (z["m"][:2] - pivot)) > 0 and all(b[i] <= b[i + 5] + 1e-9 for i in range(10))
    assert all(a[i] < a[i + 1] for r in range(3) for i in range(5 * r, 5 * r + 4))
    eng = o.overlay["engage"]
    assert len(eng["pattern"]) == 15 and (eng["rows"], eng["cols"]) == (3, 5) and "row" in eng and "col" in eng
    assert any(e["kind"] == "engage" and "3 hàng x 5 cột" in e["text"] for e in o.events.since(0))


def test_next_pass_rebuilt_from_moved_fire():
    moved = FLOOR_TARGET + np.array([0.08, 0.0, 0.0])
    o = sweep_rig(lambda n: FLOOR_TARGET if n <= 45 else moved, engage_timeout_s=6.0)
    engage(o)
    assert len(o.passes) >= 2, len(o.passes)
    shift = o.passes[-1]["zone"]["m"][0] - o.passes[0]["zone"]["m"][0]
    assert 0.05 < shift < 0.11, shift  # lưới dịch theo lửa


def test_hit_counted_only_when_spot_inside_zone():
    far = FLOOR_TARGET + np.array([0.30, 0.0, 0.0])
    o = sweep_rig(lambda n: FLOOR_TARGET, engage_timeout_s=2.0)
    o.vision.find_spot = lambda on, off, **kw: far.copy()  # vết luôn ngoài vùng cháy
    _, out = engage(o)
    assert o.passes and o.overlay["engage"]["hit_s"] == 0.0 and out == "human"
    o = sweep_rig(lambda n: FLOOR_TARGET, engage_timeout_s=2.0)
    engage(o)
    assert o.passes and o.overlay["engage"]["hit_s"] > 0.3  # vết thật trúng vùng cháy


def test_no_measurement_while_moving_between_points():
    o = sweep_rig(lambda n: FLOOR_TARGET, engage_timeout_s=3.0)
    o.cfg.laser_settle_s = 0.02
    ev = []
    real_aim, real_find, real_sleep, real_new = o.link.aim, o.vision.find_spot, o._sleep, o._new_pass
    o.link.aim = lambda *a, **k: (ev.append("aim"), real_aim(*a, **k))[1]
    o.vision.find_spot = lambda *a, **k: (ev.append("obs"), real_find(*a, **k))[1]
    o._sleep = lambda s: (ev.append(("sleep", s)), real_sleep(s))[1]
    o._new_pass = lambda *a, **k: (ev.append("pass"), real_new(*a, **k))[1]
    engage(o)
    ev = ev[ev.index("pass"):]
    obs = [i for i, e in enumerate(ev) if e == "obs"]
    assert len(obs) >= 10
    for i in obs:  # trước mỗi lần đo: vừa ngắm xong và đã chờ ổn định
        j = max(k for k in range(i) if ev[k] == "aim")
        assert ("sleep", 0.02) in ev[j:i]
    for i, k in pairwise(obs):  # giữa hai lần đo liên tiếp đúng một lần ngắm: không đo lúc đứng yên hay lúc chuyển
        assert ev[i:k].count("aim") == 1


def test_many_lost_points_return_to_anchor_approach():
    o = sweep_rig(lambda n: FLOOR_TARGET, engage_timeout_s=5.0)
    real_find, lost = o.vision.find_spot, [0]

    def find(on, off, **kw):
        if o.passes and lost[0] < 12:  # 4 điểm quét liên tiếp (mỗi điểm nới bán kính 3 lần) không thấy vết
            lost[0] += 1
            return None
        return real_find(on, off, **kw)
    o.vision.find_spot = find
    engage(o)
    msgs = [e["text"] for e in o.events.since(0) if e["kind"] == "engage"]
    assert any("về dắt vào điểm neo" in m for m in msgs)
    assert sum("bắt đầu quét" in m for m in msgs) >= 2  # dắt lại rồi quét lại


def test_fire_out_during_sweep_is_extinguished():
    o = sweep_rig(lambda n: FLOOR_TARGET if n <= 60 else None, engage_clear_s=0.3, engage_min_hit_s=0.1,
                  engage_timeout_s=6.0)
    _, out = engage(o)
    assert o.passes and out == "extinguished" and Phase.HUMAN not in o.phases
    assert o.link.laser_off_cmds() == []


# --- gọi thêm vòi thứ hai khi lửa chưa tắt sau engage_escalate_s ------------------------------------------


def fired(o, node, dev):
    return [e for e in o.link.log if e[0] == "fire" and e[1] == node and e[2] == dev]


def test_second_nozzle_called_once_when_fire_persists():
    o = sweep_rig(lambda n: FLOOR_TARGET, engage_escalate_s=0.5, engage_timeout_s=2.5)
    run, _ = engage(o)
    calls = [e for e in o.events.since(0) if "gọi thêm vòi s2" in e["text"]]
    assert len(calls) == 1
    assert run.nozzle == BOTH and set(o.overlay["active"]) == {"s1", "s2"}
    assert len(fired(o, "s2", "laser")) >= 3 and fired(o, "s2", "pump")  # gia hạn laser, có bơm
    assert any(e[0] == "aim" and e[1] == "s2" for e in o.link.log)
    assert o.link.laser_off_cmds() == []  # cả hai vẫn sáng tới hết lượt


def test_second_nozzle_not_called_when_blocked_and_warns():
    o = sweep_rig(lambda n: FLOOR_TARGET, engage_escalate_s=0.5, engage_timeout_s=2.0)
    o.vision.blockers = lambda n: ["mất pose"] if n == "s2" else []
    run, _ = engage(o)
    assert not any(e[1] == "s2" for e in o.link.log if e[0] in ("aim", "fire"))
    warns = [e for e in o.events.since(0) if "không gọi thêm vòi s2" in e["text"]]
    assert len(warns) == 1 and warns[0]["level"] == "warn"
    assert run.nozzle == "s1"


def test_second_nozzle_not_called_when_fire_out_early():
    o = sweep_rig(lambda n: FLOOR_TARGET if n <= 30 else None, engage_escalate_s=5.0, engage_clear_s=0.3,
                  engage_min_hit_s=0.1, engage_timeout_s=8.0)
    _, out = engage(o)
    assert out == "extinguished"
    assert not any("gọi thêm vòi" in e["text"] for e in o.events.since(0))
    assert not any(e[1] == "s2" for e in o.link.log if e[0] in ("aim", "fire"))


def test_two_dots_ambiguous_measurement_is_skipped_not_lost():
    o = sweep_rig(lambda n: FLOOR_TARGET, engage_escalate_s=0.4, engage_timeout_s=3.0)
    state = {"esc": None, "sec": None}
    real_find, real_esc, real_extra = o.vision.find_spot, o._escalate, o._aim_extra

    def esc(run, *a, **k):
        state["esc"] = len(run.shots)
        state["run"] = run
        return real_esc(run, *a, **k)

    def extra(n, pivot, point, *a, **k):
        state["sec"] = np.asarray(point, float)
        return real_extra(n, pivot, point, *a, **k)

    def find(on, off, **kw):  # sau khi gọi vòi phụ chỉ thấy chấm của nó (sát điểm nó ngắm)
        if state["esc"] is not None and state["sec"] is not None:
            return state["sec"] + np.array([0.005, 0.0, 0.0])
        return real_find(on, off, **kw)
    o._escalate, o._aim_extra, o.vision.find_spot = esc, extra, find
    run, _ = engage(o)
    assert state["esc"] is not None
    texts = [e["text"] for e in o.events.since(0)]
    assert any("bỏ lần đo" in t for t in texts)
    assert not any("mất vết quá" in t for t in texts)  # không tính là mất vết
    assert len(run.shots) - state["esc"] <= 2  # không còn lần đo nào được nhận (chỉ vài lần trước khi vòi phụ sáng)


def test_two_dots_main_spot_still_accepted_when_distinct():
    o = sweep_rig(lambda n: FLOOR_TARGET, engage_escalate_s=0.4, engage_timeout_s=3.0)
    run, _ = engage(o)
    assert any("gọi thêm vòi s2" in e["text"] for e in o.events.since(0))
    assert sum(s["miss_cm"] is not None for s in run.shots[-5:]) >= 3  # vẫn đo và bám tia sau khi gọi vòi phụ
