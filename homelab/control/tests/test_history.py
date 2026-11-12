"""Lịch sử: lọc và phân trang sự kiện, CSV, gộp ô telemetry, lưu lượt, migration."""

import csv
import io
import sqlite3
import time

import pytest

from app.store import SCHEMA_VERSION, Store

NOW = 1_800_000_000.0


def ev(i, kind="alert", level="info", node="s1", text=None, t=None, **extra):
    return {"seq": i, "time": t if t is not None else NOW + i, "kind": kind, "level": level,
            "text": text or f"sự kiện {i}", "node": node, **extra}


def seed_events(store):
    store.add_events([
        ev(1, "alert", "warn", "s1", "Cảnh báo nhiệt độ s1", cmd="x"),
        ev(2, "phase", "info", None, "LOCALIZE"),
        ev(3, "decision", "info", "s2", "chọn vòi s2"),
        ev(4, "fault", "error", "s2", "Lỗi camera"),
        ev(5, "cmd", "info", None, "admin: stop"),
        ev(6, "alert", "info", "s2", "Cảnh báo khí GAS s2"),
    ])


def parse_csv(text):
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


@pytest.fixture
def seeded(app):
    seed_events(app.state.store)
    return app.state.store


# --- sự kiện ---------------------------------------------------------------------------------


async def test_events_filters(client, seeded):
    j = (await client.get("/api/events")).json()
    assert [e["seq"] for e in j] == [6, 5, 4, 3, 2, 1]  # mới nhất trước
    assert [e["seq"] for e in (await client.get("/api/events?node=s2")).json()] == [6, 4, 3]
    # kind lặp lại = nhiều loại
    j = (await client.get("/api/events?kind=alert&kind=fault")).json()
    assert [e["seq"] for e in j] == [6, 4, 1]
    assert [e["seq"] for e in (await client.get("/api/events?level=error")).json()] == [4]
    assert [e["seq"] for e in (await client.get("/api/events?level=warn&level=error")).json()] == [4, 1]
    assert [e["seq"] for e in (await client.get("/api/events?kind=alert&node=s2")).json()] == [6]


async def test_events_time_range_and_text_search(client, seeded):
    j = (await client.get(f"/api/events?from={NOW + 3}&to={NOW + 5}")).json()
    assert [e["seq"] for e in j] == [5, 4, 3]
    # tìm không phân biệt hoa thường, kể cả chữ có dấu
    assert [e["seq"] for e in (await client.get("/api/events", params={"q": "cảnh BÁO"})).json()] == [6, 1]
    assert [e["seq"] for e in (await client.get("/api/events", params={"q": "gas"})).json()] == [6]
    assert (await client.get("/api/events?q=khong-co")).json() == []


async def test_events_pagination(client, app):
    app.state.store.add_events([ev(i) for i in range(1, 251)])
    r = await client.get("/api/events?limit=100")
    assert len(r.json()) == 100 and r.headers["x-total-count"] == "250"
    assert r.json()[0]["seq"] == 250
    p2 = (await client.get("/api/events?limit=100&page=2")).json()
    assert p2[0]["seq"] == 150 and len(p2) == 100
    p3 = (await client.get("/api/events?limit=100&offset=200")).json()
    assert [e["seq"] for e in p3][0] == 50 and len(p3) == 50
    p1 = (await client.get("/api/events?limit=100")).json()
    ids = {e["id"] for e in p1 + p2 + p3}
    assert len(ids) == 250  # không trùng, không sót


async def test_events_extra_data_roundtrip(client, seeded):
    first = (await client.get("/api/events?node=s1")).json()[0]
    assert first["cmd"] == "x"  # data_json trải phẳng vào sự kiện


async def test_events_csv(client, seeded):
    r = await client.get("/api/events.csv?kind=alert")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = parse_csv(r.text)
    assert rows[0] == ["Thời gian", "Node", "Loại", "Mức", "Nội dung", "Dữ liệu thêm (JSON)"]
    assert [row[4] for row in rows[1:]] == ["Cảnh báo khí GAS s2", "Cảnh báo nhiệt độ s1"]
    assert '"cmd": "x"' in rows[2][5]


async def test_history_endpoints_need_login(anon):
    for path in ("/api/events.csv", "/api/telemetry", "/api/telemetry.csv", "/api/runs", "/history", "/runs"):
        r = await anon.get(path)
        assert r.status_code in (401, 303), path


# --- telemetry -------------------------------------------------------------------------------


def seed_tel(store, n=100, node="s1"):
    # 1 mẫu/giây từ NOW; temp = i (0..n-1), gas = 2*i
    store.add_telemetry([(NOW + i, node, float(i), 2.0 * i, 50.0) for i in range(n)])


async def test_telemetry_bucketing(client, app):
    seed_tel(app.state.store, 100)
    r = await client.get(f"/api/telemetry?node=s1&from={NOW}&to={NOW + 100}&points=10")
    assert r.status_code == 200
    j = r.json()
    s = j["nodes"]["s1"]
    assert len(s["t"]) == 10 and j["bucket_s"] == 10.0
    assert s["n"] == [10] * 10
    # ô 0 gồm i = 0..9
    assert s["temp"]["min"][0] == 0 and s["temp"]["max"][0] == 9 and s["temp"]["avg"][0] == 4.5
    assert s["gas"]["max"][9] == 198 and s["temp"]["avg"][9] == 94.5
    assert s["hum"]["avg"] == [50.0] * 10
    assert j["limits"] == {"temp": 45.0, "gas": 600.0} or j["limits"] is None  # chưa poll thì chưa có ngưỡng


async def test_telemetry_points_capped_and_both_nodes(client, app):
    seed_tel(app.state.store, 1000, "s1")
    seed_tel(app.state.store, 1000, "s2")
    j = (await client.get(f"/api/telemetry?from={NOW}&to={NOW + 1000}&points=5000")).json()
    assert set(j["nodes"]) == {"s1", "s2"}
    assert all(len(v["t"]) <= 600 for v in j["nodes"].values())
    assert j["points"] == 600


async def test_telemetry_gaps_leave_no_empty_buckets_and_include_edge(client, app):
    store = app.state.store
    store.add_telemetry([(NOW, "s1", 1.0, 1.0, 1.0), (NOW + 100, "s1", 3.0, 3.0, 3.0)])  # mẫu ở cả hai mép
    j = (await client.get(f"/api/telemetry?node=s1&from={NOW}&to={NOW + 100}&points=10")).json()
    assert len(j["nodes"]["s1"]["t"]) == 2
    assert j["nodes"]["s1"]["temp"]["avg"] == [1.0, 3.0]


async def test_telemetry_bad_range_and_runs_overlay(client, app):
    assert (await client.get(f"/api/telemetry?from={NOW}&to={NOW - 1}")).status_code == 400
    app.state.store.upsert_runs([{"id": 1, "node": "s1", "started": NOW + 10, "outcome": "extinguished",
                                  "phases": [["IDLE", NOW + 10, ""], ["DONE", NOW + 30, ""]]}])
    j = (await client.get(f"/api/telemetry?from={NOW}&to={NOW + 100}")).json()
    assert j["runs"] == [{"id": 1, "node": "s1", "start": NOW + 10, "end": NOW + 30, "outcome": "extinguished"}]


async def test_telemetry_csv(client, app):
    seed_tel(app.state.store, 5)
    rows = parse_csv((await client.get(f"/api/telemetry.csv?node=s1&from={NOW}&to={NOW + 10}")).text)
    assert rows[0] == ["Thời gian", "Node", "Nhiệt độ (°C)", "Khí gas (ppm)", "Độ ẩm (%)"]
    assert len(rows) == 6 and rows[3][2] == "2.0"


# --- lượt ------------------------------------------------------------------------------------


def run_json(i, outcome=None, started=None, node="s1"):
    started = started if started is not None else NOW + i * 100
    r = {"id": i, "node": node, "started": started,
         "phases": [["LOCALIZE", started + 1, ""]], "decisions": [{"decider": "rules", "answers": {}}],
         "outcome": outcome, "target": {"id": "T7", "x": 1.2, "y": 0.5, "z": 0.3, "surface": "floor"},
         "nozzle": "s2", "attempts": 2, "shots": [{"round": 1, "miss_cm": 9.5, "nozzle": "s2"},
                                                   {"round": 2, "miss_cm": 3.25, "nozzle": "s2"}]}
    if outcome:
        r["phases"].append(["DONE", started + 20, ""])
    return r


def test_runs_dedup_and_outcome_update():
    s = Store(":memory:")
    s.upsert_runs([run_json(1)])
    s.upsert_runs([run_json(1), run_json(1)])
    assert s.count("runs") == 1
    assert s.runs()["runs"][0]["outcome"] is None and s.runs()["summary"]["running"] == 1
    s.upsert_runs([run_json(1, "extinguished")])
    s.upsert_runs([run_json(1)])  # bản cũ hơn (chưa có kết quả) không được xóa kết quả
    got = s.runs()["runs"]
    assert len(got) == 1 and got[0]["outcome"] == "extinguished"
    assert got[0]["ended"] == NOW + 100 + 20
    assert (got[0]["target_id"], got[0]["x"], got[0]["surface"], got[0]["nozzle"]) == ("T7", 1.2, "floor", "s2")
    assert (got[0]["decider"], got[0]["attempts"], got[0]["shots"], got[0]["miss_cm"]) == ("rules", 2, 2, 3.25)
    assert got[0]["raw"]["id"] == 1
    # Pi khởi động lại, id 1 dùng lại với lúc bắt đầu khác = lượt khác
    s.upsert_runs([run_json(1, "fault", started=NOW + 99999)])
    assert s.count("runs") == 2


async def test_poller_stores_current_and_past_runs(cfg, fake_pi):
    import httpx

    from app.pi_client import PiClient
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_pi.app), base_url="http://pi")
    store = Store(":memory:")
    p = PiClient(cfg, store, client=http, probe_every=1)
    orig = fake_pi.state

    def with_current():
        st = orig()
        st["orch"]["run"] = run_json(2)
        st["orch"]["runs"].append(run_json(3, "human"))
        return st

    fake_pi.state = with_current
    await p.poll_once()
    await p.poll_once()
    assert store.count("runs") == 3  # 1 lượt cũ có sẵn + lượt đang chạy + lượt đã xong
    fake_pi.state = orig
    await http.aclose()


async def test_runs_api(client, app):
    s = app.state.store
    s.upsert_runs([run_json(1, "extinguished"), run_json(2, "ignored"), run_json(3, "human"),
                   run_json(4, "fault"), run_json(5, "extinguished"), run_json(6)])
    j = (await client.get("/api/runs")).json()
    assert [r["id"] for r in j["runs"]] == [6, 5, 4, 3, 2, 1]
    assert j["summary"] == {"total": 6, "running": 1, "extinguished": 2, "ignored": 1, "alarm_only": 0,
                            "human": 1, "fault": 1, "stopped": 0}
    j = (await client.get("/api/runs?outcome=extinguished")).json()
    assert [r["id"] for r in j["runs"]] == [5, 1] and j["summary"]["total"] == 6  # tổng hợp không bị lọc
    assert [r["id"] for r in (await client.get("/api/runs?outcome=running")).json()["runs"]] == [6]
    j = (await client.get(f"/api/runs?from={NOW + 250}&to={NOW + 450}")).json()
    assert [r["id"] for r in j["runs"]] == [4, 3] and j["summary"]["total"] == 2
    assert len((await client.get("/api/runs?limit=2")).json()["runs"]) == 2


# --- migration và dọn dữ liệu ----------------------------------------------------------------

M1_SCHEMA = """
CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, seq_pi INTEGER, time REAL NOT NULL, kind TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info', text TEXT NOT NULL DEFAULT '', node TEXT, data_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX ix_events_time ON events(time);
CREATE TABLE telemetry(time REAL NOT NULL, node TEXT NOT NULL, temp REAL, gas REAL, hum REAL);
CREATE INDEX ix_tel_node_time ON telemetry(node, time);
CREATE TABLE runs(id_pi INTEGER NOT NULL, started REAL NOT NULL, node TEXT, outcome TEXT, data_json TEXT NOT NULL,
    PRIMARY KEY(id_pi, started));
"""


def test_migration_from_m1_database_is_idempotent(tmp_path):
    import json
    path = str(tmp_path / "m1.sqlite")
    db = sqlite3.connect(path)
    db.executescript(M1_SCHEMA)
    db.execute("INSERT INTO events(seq_pi,time,kind,text) VALUES(1,?,?,?)", (NOW, "alert", "cũ"))
    db.execute("INSERT INTO runs VALUES(1,?,?,?,?)", (NOW, "s1", "extinguished", json.dumps(run_json(1, "extinguished"))))
    db.commit()
    db.close()

    for _ in range(3):  # mở lại nhiều lần
        s = Store(path)
        assert s.schema_version() == SCHEMA_VERSION
        assert s.count("events") == 1 and s.count("runs") == 1
        r = s.runs()["runs"][0]
        assert r["target_id"] == "T7" and r["decider"] == "rules" and r["ended"] == NOW + 120  # điền từ data_json
        names = {row[1] for row in s._db.execute("SELECT * FROM sqlite_master WHERE type='index'")}
        assert {"ix_events_time", "ix_events_node_time", "ix_events_kind_time"} <= names
        s.close()


def test_retention_covers_runs():
    s = Store(":memory:")
    old = time.time() - 40 * 86400
    s.upsert_runs([run_json(1, "ignored", started=old), run_json(2, "ignored", started=time.time())])
    s.cleanup(14)
    assert [r["id"] for r in s.runs()["runs"]] == [2]


def test_indexes_used_for_time_queries():
    s = Store(":memory:")
    plan = " ".join(str(tuple(r)) for r in s._db.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM events WHERE node=? AND time>=? ORDER BY time DESC", ("s1", 0)))
    assert "ix_events_node_time" in plan
