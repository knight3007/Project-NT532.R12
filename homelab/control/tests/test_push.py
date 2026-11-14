"""Chế độ push: Pi tự POST lên /api/pi/push, lệnh đi về trong response."""

import asyncio
import dataclasses
import time

import httpx
import pytest

from app.main import create_app
from tests.conftest import PASSWORD, FakePi

TOKEN = "token-thu"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def ev(seq: int, text: str = "x") -> dict:
    return {"seq": seq, "time": time.time(), "kind": "alert", "level": "info", "text": text}


def body(fake: FakePi, events=(), results=(), boot="b1") -> dict:
    return {"boot": boot, "state": fake.state(), "events": list(events), "results": list(results)}


@pytest.fixture
async def papp(cfg):
    pcfg = dataclasses.replace(cfg, pi_token=TOKEN, cmd_timeout_s=0.5, poll_s=1.0)
    application = create_app(pcfg)
    async with application.router.lifespan_context(application):
        await application.state.pi.stop()  # tắt vòng nền; test gọi check_push_timeout cho xác định
        yield application


@pytest.fixture
async def pclient(papp):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=papp), base_url="http://test") as c:
        yield c


@pytest.fixture
async def plogged(pclient):
    r = await pclient.post("/login", data={"username": "admin", "password": PASSWORD})
    assert r.status_code == 303
    return pclient


async def test_push_updates_overview_and_events(plogged, fake_pi):
    r = await plogged.post("/api/pi/push", json=body(fake_pi, [ev(1, "cảnh báo s1"), ev(2, "pha")]), headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"ack": 2, "cmds": []}
    o = (await plogged.get("/api/overview")).json()
    assert o["pi_online"] and o["sim"] and o["state"]["limits"]["temp"] == 45.0
    assert [e["text"] for e in o["last_events"]] == ["pha", "cảnh báo s1"]


async def test_push_auth(pclient, papp, fake_pi):
    b = body(fake_pi)
    assert (await pclient.post("/api/pi/push", json=b)).status_code == 401
    assert (await pclient.post("/api/pi/push", json=b, headers={"Authorization": "Bearer sai"})).status_code == 401
    assert (await pclient.post("/api/pi/push", json={"boot": "x"}, headers=AUTH)).status_code == 400
    big = {**b, "events": [ev(i) for i in range(1001)]}
    assert (await pclient.post("/api/pi/push", json=big, headers=AUTH)).status_code == 413
    assert (await pclient.post("/api/pi/push", content=b"x" * (2 * 1024 * 1024 + 1), headers=AUTH)).status_code == 413
    assert not papp.state.pi.pi_online


async def test_push_disabled_without_token(anon, fake_pi):
    r = await anon.post("/api/pi/push", json=body(fake_pi), headers=AUTH)
    assert r.status_code == 503


async def test_dedup_and_boot_change(plogged, papp, fake_pi):
    store = papp.state.store
    await plogged.post("/api/pi/push", json=body(fake_pi, [ev(1, "a"), ev(2, "b")]), headers=AUTH)
    r = await plogged.post("/api/pi/push", json=body(fake_pi, [ev(2, "b"), ev(3, "c")]), headers=AUTH)
    assert r.json()["ack"] == 3
    assert [e["text"] for e in store.events()] == ["c", "b", "a"]  # không trùng
    # Pi khởi động lại: boot mới, seq về 1
    r = await plogged.post("/api/pi/push", json=body(fake_pi, [ev(1, "mới 1")], boot="b2"), headers=AUTH)
    assert r.json()["ack"] == 1
    texts = [e["text"] for e in store.events()]
    assert "mới 1" in texts and "Pi khởi động lại, đồng bộ lại" in texts


async def _fake_pi_loop(client, fake: FakePi, stop: asyncio.Event, reply=None, seen=None):
    """Pi giả: push liên tục, chạy lệnh nhận được và báo kết quả ở lần push sau."""
    results: list[dict] = []
    while not stop.is_set():
        r = await client.post("/api/pi/push", json=body(fake, results=results), headers=AUTH)
        results = []
        for c in r.json()["cmds"]:
            if seen is not None:
                seen.append(c)
            results.append({"id": c["id"], "reply": reply or {"ok": True}})
        await asyncio.sleep(0.02)


async def test_cmd_roundtrip(plogged, fake_pi):
    await plogged.post("/api/pi/push", json=body(fake_pi), headers=AUTH)  # Pi online
    stop, seen = asyncio.Event(), []
    t = asyncio.create_task(_fake_pi_loop(plogged, fake_pi, stop, {"ok": True, "n": 7}, seen))
    try:
        r = await plogged.post("/api/cmd", json={"cmd": "enable", "on": False})
    finally:
        stop.set()
        await t
    assert r.status_code == 200 and r.json() == {"ok": True, "n": 7}
    assert len(seen) == 1 and seen[0]["cmd"] == "enable" and seen[0]["on"] is False and seen[0]["id"]
    evs = (await plogged.get("/api/overview")).json()["last_events"]
    assert evs[0]["kind"] == "cmd" and evs[0]["ok"] is True


async def test_cmd_expired_is_removed(plogged, papp, fake_pi):
    await plogged.post("/api/pi/push", json=body(fake_pi), headers=AUTH)
    r = await plogged.post("/api/cmd", json={"cmd": "stop"})  # không ai lấy lệnh
    assert r.status_code == 504 and "không nhận lệnh" in r.json()["error"]
    assert papp.state.pi._queue == {}
    r = await plogged.post("/api/pi/push", json=body(fake_pi), headers=AUTH)
    assert r.json()["cmds"] == []  # lệnh cũ không chạy muộn


async def test_cmd_when_offline(plogged):
    r = await plogged.post("/api/cmd", json={"cmd": "stop"})
    assert r.status_code == 502 and "mất kết nối" in r.json()["error"]


async def test_push_timeout_marks_offline_once(plogged, papp, fake_pi):
    p = papp.state.pi
    await plogged.post("/api/pi/push", json=body(fake_pi), headers=AUTH)
    p.last_ok = time.monotonic() - 60
    await p.check_push_timeout()
    await p.check_push_timeout()
    assert not p.pi_online
    assert [e["text"] for e in p.store.events()].count("Mất kết nối với Pi") == 1
    await plogged.post("/api/pi/push", json=body(fake_pi), headers=AUTH)
    assert p.pi_online and any("kết nối lại" in e["text"] for e in p.store.events())


async def test_camera_503_in_push_mode(plogged):
    for path in ("/camera/stream.mjpg", "/camera/snapshot.jpg"):
        r = await plogged.get(path)
        assert r.status_code == 503 and r.json() == {"error": "Không có hình"}
