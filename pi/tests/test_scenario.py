import json

from nt532.decider.rules import rule_answers
from nt532.decider.scenario import NORMAL, SHIFT, generate, load_geometry, pool_from_records


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
                assert t["best_nozzle"] == lab["nozzle"] and t["nozzle_ok"][lab["nozzle"]]
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
