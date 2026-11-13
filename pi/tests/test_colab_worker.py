"""Test colab_worker với hub giả (websockets.serve trên localhost) và decide_fn giả."""

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("websockets")
import websockets

_spec = importlib.util.spec_from_file_location(
    "colab_worker", Path(__file__).resolve().parents[1] / "scripts" / "colab_worker.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)

INFO = {"decider": "jev", "run": "jev2", "device": "cpu", "gpu": None}


class FakeHub:
    def __init__(self, handler=None):
        self.conns = []  # (headers, inbox queue)
        self.handler = handler
        self.connected = asyncio.Event()

    async def _on_conn(self, ws):
        inbox: asyncio.Queue = asyncio.Queue()
        self.conns.append((ws.request.headers, inbox, ws))
        self.connected.set()
        if self.handler:
            await self.handler(ws, inbox)
            return
        try:
            async for raw in ws:
                await inbox.put(json.loads(raw))
        except websockets.ConnectionClosed:
            pass


async def _with_hub(hub, make_worker, scenario):
    async with websockets.serve(hub._on_conn, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = make_worker(f"ws://127.0.0.1:{port}/api/worker/ws")
        task = asyncio.ensure_future(worker.run())
        try:
            await asyncio.wait_for(scenario(hub, worker), 10)
        finally:
            worker.stop()
            await asyncio.wait_for(task, 5)


def run(decide_fn, scenario, **kw):
    hub = FakeHub()
    asyncio.run(_with_hub(hub, lambda url: cw.Worker(
        url, decide_fn, token="s3cret", worker_id="w1", info=INFO,
        backoff_min_s=0.05, backoff_max_s=0.2, **kw), scenario))
    return hub


def stub(obs, questions):
    return {"decider": "stub", "answers": {q: {"answer": True, "confidence": 1.0} for q in questions}}


def test_hello_status_header_and_result():
    async def scenario(hub, worker):
        await hub.connected.wait()
        headers, inbox, ws = hub.conns[0]
        assert headers["Authorization"] == "Bearer s3cret"
        hello = await inbox.get()
        assert hello["type"] == "hello" and hello["worker_id"] == "w1"
        assert hello["decider"] == "jev" and hello["device"] == "cpu" and hello["gpu"] is None
        assert hello["started"]
        assert (await inbox.get()) == {"type": "status", "ready": True, "busy": False, "done": 0}
        await ws.send(json.dumps({"type": "decide", "id": "a", "obs": {"stage": "x"},
                                  "questions": {"q": {}}, "deadline_ms": 5000}))
        res = await inbox.get()
        assert res["type"] == "result" and res["id"] == "a"
        assert res["decision"]["answers"]["q"]["answer"] is True and res["latency_ms"] >= 0
        await ws.send(json.dumps({"type": "ping"}))
        assert (await inbox.get()) == {"type": "pong", "busy": False, "done": 1}
    run(stub, scenario)


def test_bad_requests_and_decide_exception():
    def boom(obs, questions):
        raise RuntimeError("hỏng")

    async def scenario(hub, worker):
        await hub.connected.wait()
        _, inbox, ws = hub.conns[0]
        await inbox.get(), await inbox.get()
        await ws.send(json.dumps({"type": "decide", "id": "b"}))
        await ws.send(json.dumps({"type": "decide", "id": "c", "obs": [], "questions": {}}))
        await ws.send("không phải json")
        await ws.send(json.dumps({"type": "decide", "id": "d", "obs": {}, "questions": {}}))
        errs = {m["id"]: m["error"] for m in [await inbox.get() for _ in range(3)]}
        assert "sai dạng" in errs["b"] and "sai dạng" in errs["c"]
        assert errs["d"] == "RuntimeError: hỏng"
        await ws.send(json.dumps({"type": "ping"}))  # socket vẫn sống
        assert (await inbox.get())["type"] == "pong"
    run(boom, scenario)


def test_deadline_and_serialization():
    active = {"n": 0, "max": 0}

    def slow(obs, questions):
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        time.sleep(0.3)
        active["n"] -= 1
        return {"answers": {}}

    async def scenario(hub, worker):
        await hub.connected.wait()
        _, inbox, ws = hub.conns[0]
        await inbox.get(), await inbox.get()
        for i, dl in enumerate([5000, 100, 5000]):
            await ws.send(json.dumps({"type": "decide", "id": str(i), "obs": {}, "questions": {},
                                      "deadline_ms": dl}))
        got = {m["id"]: m for m in [await inbox.get() for _ in range(3)]}
        assert got["0"]["type"] == "result" and got["2"]["type"] == "result"
        assert got["1"] == {"type": "error", "id": "1", "error": "deadline"}
        assert active["max"] == 1
    run(slow, scenario)


def test_pong_while_busy():
    def slow(obs, questions):
        time.sleep(0.5)
        return {"answers": {}}

    async def scenario(hub, worker):
        await hub.connected.wait()
        _, inbox, ws = hub.conns[0]
        await inbox.get(), await inbox.get()
        await ws.send(json.dumps({"type": "decide", "id": "x", "obs": {}, "questions": {}}))
        await asyncio.sleep(0.15)
        await ws.send(json.dumps({"type": "ping"}))
        pong = await inbox.get()
        assert pong["type"] == "pong" and pong["busy"] is True
        assert (await inbox.get())["type"] == "result"
    run(slow, scenario)


def test_reconnect_after_hub_closes():
    async def scenario(hub, worker):
        await hub.connected.wait()
        await hub.conns[0][2].close()
        for _ in range(100):
            if len(hub.conns) >= 2:
                break
            await asyncio.sleep(0.05)
        assert len(hub.conns) >= 2 and worker.connects >= 2
        assert (await hub.conns[1][1].get())["type"] == "hello"
    run(stub, scenario)


def test_shutdown_message_stops_worker():
    async def go():
        hub = FakeHub()
        async with websockets.serve(hub._on_conn, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            worker = cw.Worker(f"ws://127.0.0.1:{port}", stub, info=INFO)
            task = asyncio.ensure_future(worker.run())
            await hub.connected.wait()
            await hub.conns[0][2].send(json.dumps({"type": "shutdown"}))
            return await asyncio.wait_for(task, 5)
    assert asyncio.run(go()) == 0


def test_redact_token():
    assert "abc" not in cw._redact("wss://h/ws?token=abc&x=1")
