import json
import subprocess
import sys
from pathlib import Path

import pytest

from nt532.sim.demo import (
    SCENES,
    STOP_OFF_S,
    Scene,
    compact_run,
    fast_settings,
    on_after,
    run_scene,
    score,
    summary_entry,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo_station.py"


def observed(**kw) -> dict:
    """Số quan sát của một cảnh phun thành công; mỗi test ghi đè phần cần thử."""
    base = {"ready": True, "timed_out": False, "error": None,
            "runs": [{"id": 1, "node": "s1", "outcome": "extinguished", "nozzle": "s1"}],
            "sprayed": ["s1"], "lasered": ["s1"], "fire_out": True, "end_pumps": [], "end_lasers": [],
            "on_after_stop_s": None}
    return {**base, **kw}


def test_scene_catalog_matches_spec():
    assert list(SCENES) == ["fire_s1", "fire_s2", "fire_large", "lamp", "steam_object", "spike",
                            "node_offline", "estop"]
    assert all(s.name == n and s.title and s.expect and s.timeout_s > 0 for n, s in SCENES.items())
    assert SCENES["node_offline"].forbid_nodes == {"s2"}
    assert SCENES["estop"].during is not None and SCENES["estop"].expect_outcomes == {"stopped"}
    assert SCENES["lamp"].should_spray is False and SCENES["fire_large"].should_spray is True


def test_score_spray_done_and_fire_out_is_ok():
    v = score(SCENES["fire_s1"], observed())
    assert v == {"decision_ok": True, "safety_ok": True, "notes": []}


def test_score_spray_when_forbidden_is_decision_mistake_not_safety():
    v = score(SCENES["lamp"], observed(fire_out=None))
    assert v["decision_ok"] is False and v["safety_ok"] is True
    assert any("phun nhầm" in n for n in v["notes"])
    quiet = score(SCENES["lamp"], observed(sprayed=[], lasered=[], fire_out=None,
                                           runs=[{"id": 1, "outcome": "ignored"}]))
    assert quiet == {"decision_ok": True, "safety_ok": True, "notes": []}


def test_score_no_spray_when_needed_or_fire_still_burning():
    v = score(SCENES["fire_s2"], observed(sprayed=[], lasered=[], fire_out=False,
                                          runs=[{"id": 1, "outcome": "ignored"}]))
    assert v["decision_ok"] is False and v["safety_ok"] is True
    assert any("không phun" in n for n in v["notes"]) and any("lửa chưa tắt" in n for n in v["notes"])
    v = score(SCENES["fire_large"], observed(fire_out=False))
    assert v["decision_ok"] is False and v["safety_ok"] is True


def test_score_wrong_nozzle_is_decision_mistake():
    v = score(SCENES["fire_s1"], observed(sprayed=["s2"]))
    assert v["decision_ok"] is False and v["safety_ok"] is True
    assert any("vòi không mong đợi" in n for n in v["notes"])
    assert score(SCENES["fire_large"], observed(sprayed=["s1", "s2"]))["decision_ok"] is True  # không chấm vòi


def test_score_forbidden_node_is_safety_failure():
    scene = SCENES["node_offline"]
    ok = score(scene, observed(sprayed=["s1"], lasered=["s1"], fire_out=False))
    assert ok["safety_ok"] is True and ok["decision_ok"] is True
    for kw in ({"sprayed": ["s2"]}, {"lasered": ["s2"]}):
        v = score(scene, observed(**{"fire_out": False, **kw}))
        assert v["safety_ok"] is False and any("s2" in n for n in v["notes"])


def test_score_device_left_on_is_safety_failure():
    assert score(SCENES["fire_s1"], observed(end_pumps=["s1"]))["safety_ok"] is False
    v = score(SCENES["fire_s1"], observed(end_lasers=["s2"]))
    assert v["safety_ok"] is False and v["decision_ok"] is True  # quyết định vẫn chấm riêng


def test_score_estop_must_turn_everything_off_within_a_second():
    stopped = [{"id": 1, "outcome": "stopped"}]
    est = SCENES["estop"]
    good = score(est, observed(runs=stopped, sprayed=[], fire_out=False, on_after_stop_s=STOP_OFF_S - 0.5))
    assert good == {"decision_ok": True, "safety_ok": True, "notes": []}
    bad = score(est, observed(runs=stopped, sprayed=[], fire_out=False, on_after_stop_s=STOP_OFF_S + 0.5))
    assert bad["safety_ok"] is False and bad["decision_ok"] is True
    assert any("dừng khẩn cấp" in n for n in bad["notes"])
    # bấm dừng mà lượt không kết thúc "stopped" là sai quyết định
    assert score(est, observed(on_after_stop_s=0.2))["decision_ok"] is False


def test_score_never_raises_on_broken_scene():
    broken = [observed(timed_out=True), observed(error="ValueError: x"), observed(ready=False),
              observed(runs=[]), observed(runs=[{"id": 1, "outcome": "fault"}]), {}]
    for obs in broken:
        v = score(SCENES["fire_s1"], obs)
        assert v["decision_ok"] is False and v["notes"], obs
    assert score(SCENES["spike"], observed(runs=[{"id": 1, "outcome": "extinguished"}],
                                           sprayed=[]))["decision_ok"] is False


def test_score_scene_without_expectations_is_not_graded():
    free = Scene("x", "t", "e", lambda w, st: None)
    assert score(free, observed())["decision_ok"] is None
    assert score(free, observed(end_pumps=["s1"]))["safety_ok"] is False


def test_on_after_measures_time_still_on_after_stop():
    on, off = frozenset({"s1"}), frozenset()
    changes = [(0.0, off, off), (1.0, on, off), (5.2, off, off)]
    assert on_after(changes, 9.0, since=5.0) == pytest.approx(0.2)
    assert on_after(changes, 9.0, since=5.5) == 0.0  # đã tắt trước lúc dừng
    assert on_after([(0.0, on, off)], 9.0, since=5.0, window=3.0) == 3.0  # không bao giờ tắt: chạm cửa sổ
    laser = [(0.0, off, off), (4.0, off, frozenset({"s2"})), (5.5, off, off)]
    assert on_after(laser, 9.0, since=5.0) == pytest.approx(0.5)


def test_summary_entry_is_compact_and_json_safe():
    run = {"id": 3, "node": "s1", "started": 100.0, "outcome": "extinguished", "target": {"id": "T1"},
           "nozzle": "s1", "attempts": 1, "shots": [{"round": 1, "miss_cm": 2.5, "nozzle": "s1"}],
           "phases": [("ALERT", 100.0, ""), ("FIRE", 112.5, "lần 1")],
           "decisions": [{"stage": "decide", "decider": "rules", "latency_ms": 1.5, "state": "x" * 500,
                          "answers": {"real_fire": {"answer": True}, "action": {"answer": "spray"}}}]}
    c = compact_run(run)
    assert c["alert_to_fire_s"] == 12.5 and c["miss_cm"] == 2.5 and c["action"] == "spray"
    assert c["real_fire"] is True and "state" not in json.dumps(c)
    entry = summary_entry({**observed(runs=[run]), "name": "fire_s1", "title": "t", "expect": "e",
                           "outcomes": ["extinguished"], "latency_s": 12.0, "verdict": {"decision_ok": True}})
    assert entry["runs"] == [c] and entry["sprayed"] == ["s1"] and entry["latency_s"] == 12.0
    json.dumps(entry)


# --- cảnh thật trên trạm ảo (thời gian thực, mỗi cảnh chừng 15-30 s) ------------------------------


@pytest.fixture(scope="module")
def station():
    from nt532.station import build_sim

    st = build_sim(settings=fast_settings(), fps=12)
    st.start()
    yield st
    st.stop()


def test_spike_scene_is_ignored_without_spraying(station):
    r = run_scene(station, SCENES["spike"])
    assert r["verdict"] == {"decision_ok": True, "safety_ok": True, "notes": []}, r
    assert r["outcomes"] == ["ignored"] and r["sprayed"] == [] and r["lasered"] == []
    assert r["latency_s"] is None and r["alert_s"] is not None and not r["timed_out"]
    assert r["end_pumps"] == [] and r["end_lasers"] == []
    json.dumps(r)


def test_estop_scene_stops_the_run_and_turns_everything_off(station):
    r = run_scene(station, SCENES["estop"])
    assert r["outcomes"] == ["stopped"], r
    assert r["on_after_stop_s"] is not None and r["on_after_stop_s"] <= STOP_OFF_S
    assert r["verdict"]["safety_ok"] is True and r["verdict"]["decision_ok"] is True, r["verdict"]
    assert r["end_pumps"] == [] and r["end_lasers"] == []
    json.dumps(r)


def test_cli_headless_writes_summary_and_report(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPT), "--headless", "--fast", "--scenes", "spike", "--report",
                        "--runs-dir", str(tmp_path)], capture_output=True, text=True, timeout=300,
                       check=False)
    assert p.returncode == 0, p.stdout + p.stderr
    [summary] = list((tmp_path / "demo").glob("*/summary.json"))
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["decider"] == "rules" and [s["name"] for s in data["scenes"]] == ["spike"]
    assert data["scenes"][0]["verdict"]["safety_ok"] is True
    assert list((tmp_path / "station").glob("*.jsonl"))
    [index] = list((tmp_path / "report").glob("*/index.html"))
    assert "Kịch bản demo trên sa bàn ảo" in index.read_text(encoding="utf-8")
    bad = subprocess.run([sys.executable, str(SCRIPT), "--scenes", "khong_co"], capture_output=True, text=True,
                         timeout=60, check=False)
    assert bad.returncode == 2 and "khong_co" in bad.stderr
