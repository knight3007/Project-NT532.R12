import json

from nt532.decider.rules import rule_answers
from nt532.decider.scenario import (
    NORMAL,
    SHIFT,
    SensorModel,
    generate,
    load_geometry,
    load_sensor_model,
    pool_from_records,
)
from nt532.decider.state_text import BOTH, nozzle_candidates, nozzles_of


def fake_records(split: str = "train") -> list[dict]:
    """Cache giả: ảnh lửa (mạnh/yếu/không phát hiện), ảnh âm tính nhiễu và sạch."""
    rows = []
    for i in range(30):
        dets = [{"c": 0.4 + 0.5 * (i % 5) / 5, "w": 0.2, "h": 0.15, "hit": True}] if i % 7 else []
        rows.append({"src": "home-fire", "split": split, "name": f"f{i}", "has_fire": True,
                     "dets": dets})
    for i in range(30):
        dets = [{"c": 0.2 + 0.4 * (i % 4) / 4, "w": 0.1, "h": 0.1, "hit": False}] if i % 2 else []
        rows.append({"src": "coco-indoor", "split": split, "name": f"n{i}", "has_fire": False,
                     "dets": dets})
    return rows


def make(n=60, seed=0, prof=NORMAL):
    return generate(n, pool_from_records(fake_records(), "train"), prof, "train", seed)


def test_deterministic():
    a, b = make(seed=3), make(seed=3)
    assert json.dumps(a) == json.dumps(b)
    assert json.dumps(a) != json.dumps(make(seed=4))


def test_no_truth_in_state():
    for r in make(120):
        text = r["state"].lower()
        for word in ("truth", "real_fire", "best_nozzle", "fire_out", "miss_cm", "kind"):
            assert word not in text
        assert len(r["state"]) / 4 < 400


def test_labels_match_truth():
    recs = make(200)
    stages = {r["id"].rsplit("-", 1)[1] for r in recs}
    assert stages == {"decide", "verify"}
    for r in recs:
        t, lab = r["truth"], r["labels"]
        if r["id"].endswith("verify"):
            assert t["real_fire"]
            if t["fire_out"]:
                assert lab["after_verify"].startswith("done")
            elif t["nozzle_fault"] or t["attempt"] >= 3:
                assert lab["after_verify"].startswith("call human")
            elif t["miss"]:
                assert lab["after_verify"].startswith("re-aim")
            else:
                assert lab["after_verify"].startswith("spray more")
        else:
            assert lab["real_fire"] == t["real_fire"]
            if not t["real_fire"]:
                assert lab["action"] == "ignore"
                assert lab.get("target", "none of these") == "none of these"
                assert "nozzle" not in lab
            if lab["action"] == "spray":
                assert t["best_nozzle"] == lab["nozzle"]
                assert all(t["nozzle_ok"][n] for n in nozzles_of(lab["nozzle"]))
                assert lab["target"] != "none of these"
        for q, ans in lab.items():
            if r["questions"][q]["type"] == "choice":
                assert ans in r["questions"][q]["candidates"]


def test_rules_run_on_both_profiles():
    geo = load_geometry(with_nodes=False)
    for prof in (NORMAL, SHIFT):
        for r in make(80, prof=prof):
            ans = rule_answers(r, geo)
            assert set(ans) == set(r["labels"])
            for q, a in ans.items():
                if r["questions"][q]["type"] == "choice":
                    assert a in r["questions"][q]["candidates"]


def test_split_isolation():
    rows = fake_records("train") + fake_records("test")[:10]
    pool = pool_from_records(rows, "test")
    assert len(pool.fire) == 10
    assert not pool.neg_noisy and not pool.neg_quiet


def decide_records(n=400, seed=0, prof=NORMAL):
    return [r for r in make(n, seed, prof) if r["id"].endswith("decide")]


def test_nozzle_label_rules():
    seen = set()
    for r in decide_records(600):
        t, lab, obs = r["truth"], r["labels"], r["obs"]
        assert t["size"] in ("small", "large") if t["real_fire"] else t["size"] is None
        if "nozzle" not in lab:
            continue
        cands = r["questions"]["nozzle"]["candidates"]
        assert cands == nozzle_candidates(obs)
        able = [n for n in ("s1", "s2") if t["can_hit"][n]]
        if lab["nozzle"] == BOTH:
            assert t["size"] == "large" and len(able) == 2
            seen.add("both")
        else:
            assert lab["nozzle"] == t["nearest_nozzle"] and lab["nozzle"] in able
            if t["size"] == "large" and len(able) == 2:
                assert BOTH not in cands  # đo được không cho thấy hai vòi dùng được
            seen.add("one-large" if t["size"] == "large" else "one-small")
        if BOTH in cands:  # ứng viên both chỉ khi quan sát nói cả hai khỏe và với tới
            assert all(obs["nozzles"][n]["hb_ms"] <= 1500 and obs["nozzles"][n]["pose_s"] <= 60
                       for n in ("s1", "s2"))
    assert seen == {"both", "one-large", "one-small"}


def test_rules_never_both_and_pick_nearest():
    geo = load_geometry(with_nodes=False)
    for r in decide_records(300):
        if "nozzle" not in r["questions"]:
            continue
        ans = rule_answers(r, geo)["nozzle"]
        assert ans in ("s1", "s2")
    noz = {n: {"hb_ms": 200, "pose_s": 5, "reach": {"T1": True}} for n in ("s1", "s2")}
    tgt = {"id": "T1", "x": 0.6, "z": 0.2, "w": 0.2, "h": 0.2, "conf": [0.9, 0.9, 0.9],
           "dist": {"s1": 0.3, "s2": 0.3}, "dist3": {"s1": 0.9, "s2": 0.7}}
    obs = {"stage": "decide", "alarm": "s1", "limits": {"temp": 45.0, "gas": 600.0}, "frames": 3,
           "sensors": {n: {"temp": [30.0] * 5, "gas": [200] * 5, "hum": [50] * 5} for n in noz},
           "targets": [tgt], "nozzles": noz}
    q = {"nozzle": {"type": "choice", "candidates": nozzle_candidates(obs)}}
    assert BOTH in q["nozzle"]["candidates"]
    assert rule_answers({"obs": obs, "questions": q}, geo)["nozzle"] == "s2"  # gần hơn, dù alarm là s1
    noz["s2"]["hb_ms"] = 5000
    assert BOTH not in nozzle_candidates(obs)
    assert rule_answers({"obs": obs, "questions": q}, geo)["nozzle"] == "s1"


def test_size_is_observable_but_not_in_state():
    recs = decide_records(800)
    box = {"small": [], "large": []}
    for r in recs:
        size = r["truth"]["size"]
        if size and r["truth"]["true_target"]:
            tid = r["truth"]["true_target"]
            t = next(t for t in r["obs"]["targets"] if t["id"] == tid)
            box[size].append(t["w"] * t["h"])
        assert "small" not in r["state"] and "large" not in r["state"]
    mean = {k: sum(v) / len(v) for k, v in box.items()}
    assert mean["large"] > 1.8 * mean["small"]
    frac = sum(r["truth"]["size"] == "large" for r in recs if r["truth"]["real_fire"]) / sum(
        r["truth"]["real_fire"] for r in recs)
    assert 0.2 < frac < 0.4


def test_large_fire_sensor_model_is_stronger():
    import numpy as np

    from nt532.decider.scenario import simulate_sensors

    geo = load_geometry()
    far = max(geo.nodes, key=lambda n: abs(geo.nodes[n][0] - 0.05))  # nút xa nguồn đặt ở x = 0.05

    def rise(large, sm=None):
        sm = sm or SensorModel()
        out = []
        for i in range(60):
            rng = np.random.default_rng(i)
            win, _, _ = simulate_sensors(rng, "real_fire", 0.05, geo, NORMAL, sm, large)
            out.append(np.mean(win[far]["temp"]))
        return float(np.mean(out))

    assert rise(True) > rise(False)
    sm = load_sensor_model(None)
    assert sm.large_gain > 1 and sm.large_decay > 1


def test_single_nozzle_on_large_fire_leaves_more_fire():
    def still_burning(prof, only_both):
        n = k = 0
        for r in make(1500, 1, prof):
            t = r["truth"]
            if (r["id"].endswith("verify") and t["size"] == "large" and not t["miss"]
                    and (r["obs"]["verify"]["nozzle"] == BOTH) == only_both):
                n += 1
                k += not t["fire_out"]
        assert n >= 20
        return k / n

    assert still_burning(SHIFT, False) > still_burning(SHIFT, True)
