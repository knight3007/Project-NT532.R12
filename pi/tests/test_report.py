import csv
import json

from nt532.report import build_report, collect
from nt532.report.build import build_runs, parse_since, pctl


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_jsonl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def target_rows():
    rows = []
    for i, (ex, ez) in enumerate([(0.01, 0.0), (0.0, 0.03), (-0.02, 0.02), (0.04, 0.03)]):
        rows.append({"time": f"2026-10-09 10:00:0{i}", "label": "p1", "run": i + 1, "truth_x": 0.3,
                     "truth_z": 0.2, "meas_x": 0.3 + ex, "meas_z": 0.2 + ez, "err_x": ex, "err_z": ez,
                     "err": (ex**2 + ez**2) ** 0.5})
    rows.append({"time": "2026-10-09 10:00:09", "label": "p1", "run": 5, "truth_x": 0.3, "truth_z": 0.2,
                 "meas_x": "", "meas_z": "", "err_x": "", "err_z": "", "err": ""})
    return rows


def aim_rows():
    rows = []
    for node, x, z, miss in [("s1", 0.2, 0.2, 2.0), ("s1", 0.4, 0.2, 6.5), ("s2", 0.2, 0.2, 3.0),
                             ("s2", 0.4, 0.2, "")]:
        rows.append({"time": "2026-10-09 11:00:00", "label": node, "run": 1, "node": node, "target_x": x,
                     "target_z": z, "pan": 1, "tilt": 2, "spot_x": x, "spot_z": z, "miss_cm": miss,
                     "pass": "" if miss == "" else str(miss <= 5), "note": "" if miss != "" else "không thấy vết laser"})
    return rows


def station_events():
    t = 1_791_532_700.0
    ev = lambda dt, kind, **kw: {"time": t + dt, "kind": kind, **kw}
    return [
        ev(0, "phase", phase="ALERT"), ev(0.1, "phase", phase="LOCALIZE"), ev(1.0, "phase", phase="DECIDE"),
        ev(1.1, "decision", decision={"decider": "rules", "latency_ms": 2.0, "real_fire": True}),
        ev(2.0, "phase", phase="AIM"), ev(2.5, "phase", phase="CORRECT"),
        ev(3.0, "correct", text="vòng 1: lệch 4.0 cm"), ev(3.5, "correct", text="vòng 2: lệch 1.5 cm"),
        ev(4.0, "phase", phase="FIRE"), ev(6.0, "run", outcome="extinguished", run=1, node="s1",
                                          target={"id": "T1"}, nozzle="s1", attempts=1),
        ev(7, "phase", phase="IDLE"),
        ev(20, "phase", phase="ALERT"), ev(21, "decision", decision={"decider": "rules", "latency_ms": 1.0,
                                                                     "real_fire": False}),
        ev(22, "run", outcome="ignored", run=2, node="s2", target=None, nozzle=None, attempts=0),
        ev(30, "tel", n="s1", s=1, t=30.5, g=400, h=50), ev(31, "tel", n="s1", s=2, t=31.0, g=420, h=50),
    ]


def eval_json():
    sysd = {"false_spray": 1, "no_fire": 10, "missed_spray": 0, "should_spray": 20, "wrong_target": 2,
            "wrong_nozzle": 1, "sprayed_correctly": 17, "one_when_two": 0, "two_when_one": 0}
    return {"test": {"n_records": 120, "accuracy": {"real_fire": 0.95, "action": 0.9}, "system": sysd,
                     "asked": {}, "nozzle_by_size": {}}}


def jev_json():
    sysd = {"false_spray": 0, "no_fire": 10, "missed_spray": 1, "should_spray": 20, "wrong_target": 0,
            "wrong_nozzle": 0, "sprayed_correctly": 19}
    r = {"n_records": 120, "accuracy": {"real_fire": 0.97}, "system": sysd}
    return {"test": {"n_records": 120, "rules": eval_json()["test"], "model": r,
                     "hybrid": {"0.9": {**r, "coverage": 0.6}}, "latency": {"median_ms": 300, "p95_ms": 450, "n": 50}}}


def full_tree(runs):
    write_csv(runs / "measure" / "target.csv", target_rows())
    write_csv(runs / "measure" / "aim_20261009-110000.csv", aim_rows())
    write_csv(runs / "measure" / "tag.csv", [{"time": "2026-10-09 10:00:00", "label": "p1", "run": 1,
                                               "truth_x": 0.4, "truth_y": 0.58, "meas_x": 0.41, "meas_y": 0.58,
                                               "err_x": 0.01, "err_y": 0.0, "err": 0.01, "meas_z": 0.0,
                                               "node": "s1", "tag_rate": 0.9}])
    write_csv(runs / "measure" / "stream_latency.csv", [{"latency_ms": 420}, {"latency_ms": 480}])
    write_csv(runs / "pi_load.csv", [{"time": "2026-10-09 12:00:00", "elapsed_s": i, "cpu_pct": 10 + i,
                                       "ram_used_mb": 900, "temp_c": 50 + i, "throttled": "0x0"} for i in range(5)])
    write_jsonl(runs / "station" / "20261009-100000.jsonl", station_events())
    (runs / "decider").mkdir(parents=True, exist_ok=True)
    (runs / "decider" / "rules_eval.json").write_text(json.dumps(eval_json()), encoding="utf-8")
    (runs / "decider" / "rules_eval_data.json").write_text(json.dumps(eval_json()), encoding="utf-8")
    (runs / "decider" / "jev" / "jev1").mkdir(parents=True)
    (runs / "decider" / "jev" / "jev1" / "eval.json").write_text(json.dumps(jev_json()), encoding="utf-8")


def test_empty_dir_gives_page_without_sections(tmp_path):
    html = build_report(collect(tmp_path / "khong_co"))
    assert "Chưa có số đo nào" in html
    assert "<section" not in html


def test_full_report_has_every_section_and_numbers(tmp_path):
    full_tree(tmp_path)
    html = build_report(collect(tmp_path))
    for sid in ("localization", "tag", "aim", "convergence", "latency", "outcomes", "decision", "load",
                "stream", "telemetry"):
        assert f'id="{sid}"' in html, sid
    assert "<svg" in html
    assert "target.csv" in html and "rules_eval_data.json" in html and "jev1" in html
    assert "N = 5 lượt" in html  # target.csv: 5 lượt, 1 không thấy bia
    # aim: s1 qua 1/2, tổng qua 2/3
    assert "1/2 (50%)" in html and "2/3 (67%)" in html
    # hội tụ: vòng cuối 1.5 cm, qua cổng 1/1
    assert "1/1" in html
    # độ trễ alert -> FIRE 4.00 s
    assert "4.00" in html
    # bảng kết cục
    assert "extinguished" in html and "ignored" in html
    # độ trễ stream trung vị 450 ms
    assert "450" in html
    # độ chính xác quyết định
    assert "95.0%" in html and "97.0%" in html


def test_missing_sources_are_skipped(tmp_path):
    write_csv(tmp_path / "measure" / "target.csv", target_rows())
    html = build_report(collect(tmp_path))
    assert 'id="localization"' in html
    for sid in ("aim", "convergence", "latency", "outcomes", "decision", "load", "stream", "telemetry"):
        assert f'id="{sid}"' not in html, sid


def test_garbage_files_are_ignored(tmp_path):
    (tmp_path / "measure").mkdir()
    (tmp_path / "measure" / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "station").mkdir()
    (tmp_path / "station" / "bad.jsonl").write_text("không phải json\n{\n", encoding="utf-8")
    (tmp_path / "decider").mkdir()
    (tmp_path / "decider" / "rules_eval.json").write_text("{hỏng", encoding="utf-8")
    assert "<section" not in build_report(collect(tmp_path))


def test_since_filters_rows(tmp_path):
    full_tree(tmp_path)
    data = collect(tmp_path, parse_since("2026-10-10"))
    assert data["target"] == [] and data["aim"] == [] and data["load"] == []
    assert data["station"] == []  # sự kiện năm 2026-10-09 07:xx UTC trở về trước mốc
    html = build_report(collect(tmp_path, parse_since("2026-10-09")))
    assert 'id="localization"' in html


def test_build_runs_and_stats(tmp_path):
    write_jsonl(tmp_path / "s.jsonl", station_events())
    from nt532.report.build import read_jsonl
    runs = build_runs([(tmp_path / "s.jsonl", read_jsonl(tmp_path / "s.jsonl", 0))])
    assert [r["outcome"] for r in runs] == ["extinguished", "ignored"]
    assert runs[0]["correct"] == [(1, 4.0), (2, 1.5)]
    assert runs[0]["phases"]["FIRE"] - runs[0]["t0"] == 4.0
    assert pctl([1, 2, 3, 4, 5], 0.5) == 3
    assert abs(pctl([0, 10], 0.95) - 9.5) < 1e-9


def test_since_parsing():
    assert parse_since(None) == 0.0
    assert parse_since("20261009") == parse_since("2026-10-09")
    try:
        parse_since("hôm qua")
    except ValueError:
        pass
    else:
        raise AssertionError
