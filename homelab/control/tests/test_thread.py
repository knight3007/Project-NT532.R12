"""Số đo mạng Thread: Pi đẩy lên /api/pi/thread, overview, lịch sử, CSV, migration."""

import csv
import io
import json
import sqlite3
import time

from app.store import SCHEMA_VERSION, Store
from tests.test_push import AUTH, papp, pclient, plogged  # noqa: F401 - fixture dùng chung


def sample(ts=None, avg=55.0, ok=True):
    ts = ts or time.time()
    return {"ts": ts,
            "otbr": {"ok": True, "state": "leader", "routerCount": 3, "partitionId": 7, "channel": 15, "panId": 4660},
            "nodes": {
                "s1": {"addr": "fd59::1", "role": "router", "fw": "a-0.1.1", "last_ok": ts,
                       "ping": {"sent": 5, "recv": 5 if ok else 0, "loss_pct": 0.0 if ok else 100.0,
                                "rtt_min": 40.0 if ok else None, "rtt_avg": avg if ok else None,
                                "rtt_max": 80.0 if ok else None},
                       "coap": {"ok": ok, "rtt_ms": 41.5 if ok else None, "role": "router"}},
                "s2": {"addr": "fd59::2", "role": None, "ping": {"error": "OSError"}, "coap": {"ok": False}}},
            "topology": {"routers": [{"rloc16": "0xf400", "neighbors": [], "children": []}], "links": []}}


async def test_thread_auth_size_shape(pclient):  # noqa: F811
    assert (await pclient.post("/api/pi/thread", json=sample())).status_code == 401
    assert (await pclient.post("/api/pi/thread", json=sample(), headers={"Authorization": "Bearer sai"})).status_code == 401
    assert (await pclient.post("/api/pi/thread", content=b"khong json", headers=AUTH)).status_code == 400
    assert (await pclient.post("/api/pi/thread", json=[1], headers=AUTH)).status_code == 400
    assert (await pclient.post("/api/pi/thread", json={"otbr": {}}, headers=AUTH)).status_code == 400
    assert (await pclient.post("/api/pi/thread", json={"nodes": {"s1": 3}}, headers=AUTH)).status_code == 400
    assert (await pclient.post("/api/pi/thread", json={**sample(), "otbr": "x"}, headers=AUTH)).status_code == 400
    assert (await pclient.post("/api/pi/thread", json={**sample(), "topology": []}, headers=AUTH)).status_code == 400
    many = {"nodes": {f"n{i}": {} for i in range(17)}}
    assert (await pclient.post("/api/pi/thread", json=many, headers=AUTH)).status_code == 413
    big = {**sample(), "pad": "x" * (256 * 1024)}
    assert (await pclient.post("/api/pi/thread", json=big, headers=AUTH)).status_code == 413
    ok = await pclient.post("/api/pi/thread", json=sample(), headers=AUTH)
    assert ok.status_code == 200 and ok.json() == {"ok": True, "rows": 2}


async def test_thread_disabled_without_token(anon):
    assert (await anon.post("/api/pi/thread", json=sample())).status_code == 503


async def test_overview_has_thread(plogged):  # noqa: F811
    assert (await plogged.get("/api/overview")).json()["thread"] is None
    await plogged.post("/api/pi/thread", json=sample(), headers=AUTH)
    th = (await plogged.get("/api/overview")).json()["thread"]
    assert th["latest"]["otbr"]["state"] == "leader" and th["age"] < 5
    assert th["latest"]["nodes"]["s1"]["role"] == "router" and th["latest"]["topology"]["routers"]


async def test_thread_history_and_csv(plogged, papp):  # noqa: F811
    now = time.time()
    await plogged.post("/api/pi/thread", json=sample(now - 120, 50.0), headers=AUTH)
    await plogged.post("/api/pi/thread", json=sample(now - 60, 70.0), headers=AUTH)
    await plogged.post("/api/pi/thread", json=sample(now - 30, ok=False), headers=AUTH)
    assert papp.state.store.count("thread") == 6

    h = (await plogged.get("/api/thread/history?hours=1&points=10")).json()
    s1 = h["nodes"]["s1"]
    assert sum(s1["n"]) == 3 and sum(s1["n_ok"]) == 2
    assert min(v for v in s1["min"] if v is not None) == 40.0
    assert {v for v in s1["avg"] if v is not None} <= {50.0, 60.0, 70.0}
    assert "s2" in h["nodes"]
    one = (await plogged.get("/api/thread/history?hours=1&node=s2")).json()
    assert list(one["nodes"]) == ["s2"]
    assert (await plogged.get("/api/thread/history?hours=0")).status_code == 422

    r = await plogged.get("/api/thread.csv?hours=1&node=s1")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text[1:])))
    assert rows[0][:3] == ["Thời gian", "Node", "Vai trò"] and len(rows) == 4
    assert rows[1][1] == "s1" and rows[1][2] == "router" and rows[1][3] == "50.0" and rows[-1][-1] == "không"


async def test_thread_history_requires_login(pclient):  # noqa: F811
    assert (await pclient.get("/api/thread/history")).status_code == 401
    assert (await pclient.get("/api/thread.csv")).status_code == 401


def test_thread_retention_and_nonfinite_ignored():
    from app.pi_client import thread_rows

    s = Store(":memory:")
    now = time.time()
    s.add_thread([(now - 40 * 86400, "s1", "router", 1, 1, 1, 0, 1, 1), (now, "s1", "router", 1, 1, 1, 0, 1, 1)])
    s.cleanup(14)
    assert s.count("thread") == 1
    bad = {"ts": now, "nodes": {"s1": {"ping": {"rtt_avg": float("inf"), "recv": True, "loss_pct": "x"},
                                       "coap": {"rtt_ms": float("nan")}}, "s2": "nope"}}
    row = thread_rows(json.loads(json.dumps(bad)))[0]
    assert row[3:8] == (None, None, None, None, None) and row[8] == 0


def test_migration_adds_thread_table(tmp_path):
    path = str(tmp_path / "v2.sqlite")
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, seq_pi INTEGER, time REAL NOT NULL,"
                     " kind TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'info', text TEXT NOT NULL DEFAULT '',"
                     " node TEXT, data_json TEXT NOT NULL DEFAULT '{}');"
                     "PRAGMA user_version=2;")
    db.commit()
    db.close()
    for _ in range(2):
        s = Store(path)
        assert s.schema_version() == SCHEMA_VERSION == 3
        s.add_thread([(1.0, "s1", None, None, None, None, None, None, 0)])
        assert s.count("thread") >= 1
        s.close()
