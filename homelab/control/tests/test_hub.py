"""Hub bộ quyết định: worker WebSocket, /decide cho Pi, API của trang /decider.

Dùng một app tối giản chỉ gắn router của hub (không phụ thuộc app.main) và TestClient đồng bộ.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app import hub as hubmod
from app.config import Settings
from app.hub import build_switch_cmd, hub, router

from .conftest import PW_HASH, FakePi

WTOKEN = "worker-secret"
DTOKEN = "decider-secret"
HELLO = {"type": "hello", "worker_id": "colab-t1", "decider": "jev", "run": "jev2", "device": "cuda",
         "gpu": "NVIDIA L4", "started": "2026-11-12T08:00:00+00:00"}
BODY = {"obs": {"stage": "verify"}, "questions": {"real_fire": {"type": "boolean", "candidates": []}}}
DECISION = {"decider": "jev", "answers": {"real_fire": {"answer": True, "confidence": 0.9, "probs": {}, "source": "model"}}}


class StubPi:
    """Đủ phần PiClient mà hub dùng: http, overview(), recent, record_events()."""

    def __init__(self, fake: FakePi) -> None:
        self.fake = fake
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://pi")
        self.recent: list[dict] = []
        self.recorded: list[dict] = []

    def overview(self, with_events: bool = True) -> dict:
        return {"pi_online": True, "state": self.fake.state()}

    async def send_cmd(self, body: dict) -> tuple[int, dict]:
        try:
            r = await self.http.post("/api/cmd", json=body)
            return (200 if r.status_code == 200 else 502), r.json()
        except (httpx.HTTPError, ValueError) as e:
            return 502, {"ok": False, "error": f"Pi không phản hồi ({type(e).__name__})"}

    async def record_events(self, events: list[dict]) -> list[dict]:
        self.recorded.extend(events)
        return events


def _reset() -> None:
    hub.worker = None
    hub.connecting = 0
    hub.ping_s, hub.pong_timeout_s = 20.0, 15.0
    hub.events.clear()
    hub.stats.update({k: 0 for k in hub.stats})
    hub.last_error = hub.last_roundtrip_ms = None


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("NT532_WORKER_TOKEN", WTOKEN)
    monkeypatch.setenv("NT532_DECIDER_TOKEN", DTOKEN)
    monkeypatch.setenv("NT532_DECIDE_DEADLINE_MS", "1500")
    monkeypatch.setenv("NT532_PUBLIC_DECIDE_URL", "http://100.64.0.9:8800/")
    _reset()
    yield
    _reset()


@pytest.fixture
def fake_pi() -> FakePi:
    return FakePi()


@pytest.fixture
def app(fake_pi) -> FastAPI:
    a = FastAPI()
    a.state.cfg = Settings(secret="test-secret", admin_hash=PW_HASH)
    a.state.pi = StubPi(fake_pi)
    a.include_router(router)
    return a


@pytest.fixture
def client(app):
    with TestClient(app, client=("100.64.1.2", 5000)) as c:
        yield c


def login(c: TestClient) -> None:
    c.cookies.set("nt532_session", URLSafeTimedSerializer("test-secret", salt="nt532-session").dumps({"u": "admin"}))


WH = {"Authorization": f"Bearer {WTOKEN}"}
PH = {"Authorization": f"Bearer {DTOKEN}"}


def connect(c: TestClient, ready: bool = True, hello: dict | None = None):
    """Mở WebSocket worker, gửi hello (+ status ready) và chờ hub đăng ký."""
    cm = c.websocket_connect("/api/worker/ws", headers=WH)
    ws = cm.__enter__()
    ws.send_json(hello or HELLO)
    ws.send_json({"type": "status", "ready": ready, "busy": False, "done": 0})
    for _ in range(100):
        if hub.worker and hub.worker.ready == ready and hub.worker.info.get("worker_id") == (hello or HELLO)["worker_id"]:
            break
        time.sleep(0.02)
    return cm, ws


# --- xác thực worker --------------------------------------------------------------------------


def test_worker_auth_rejected(client):
    for headers in ({}, {"Authorization": "Bearer sai"}):
        with pytest.raises((WebSocketDenialResponse, WebSocketDisconnect)) as ei, \
                client.websocket_connect("/api/worker/ws", headers=headers):
            pass
        if isinstance(ei.value, WebSocketDenialResponse):
            assert ei.value.status_code == 401
    assert hub.worker is None


def test_worker_disabled_without_token(client, monkeypatch):
    monkeypatch.delenv("NT532_WORKER_TOKEN")
    with pytest.raises((WebSocketDenialResponse, WebSocketDisconnect)), \
            client.websocket_connect("/api/worker/ws", headers=WH):
        pass
    login(client)
    assert client.get("/api/decider/status").json()["hub"]["state"] == "disabled"


def test_worker_query_token_and_hello(client):
    with client.websocket_connect(f"/api/worker/ws?token={WTOKEN}") as ws:
        ws.send_json(HELLO)
        ws.send_json({"type": "status", "ready": True, "busy": False, "done": 3})
        for _ in range(100):
            if hub.worker and hub.worker.ready:
                break
            time.sleep(0.02)
        login(client)
        st = client.get("/api/decider/status").json()["hub"]
        assert st["state"] == "ready"
        assert st["worker"]["gpu"] == "NVIDIA L4" and st["worker"]["run"] == "jev2" and st["worker"]["done"] == 3
    for _ in range(100):
        if hub.worker is None:
            break
        time.sleep(0.02)
    assert hub.worker is None
    assert client.get("/api/decider/status").json()["hub"]["state"] == "disconnected"


def test_replaced_by_newer(client):
    cm1, ws1 = connect(client)
    cm2, ws2 = connect(client, hello={**HELLO, "worker_id": "colab-t2"})
    try:
        with pytest.raises(WebSocketDisconnect) as ei:
            ws1.receive_json()
        assert ei.value.code == 1000 and ei.value.reason == "replaced"
        assert hub.worker.info["worker_id"] == "colab-t2"
    finally:
        cm2.__exit__(None, None, None)
        cm1.__exit__(None, None, None)


def test_heartbeat_drops_silent_worker(client):
    hub.ping_s, hub.pong_timeout_s = 0.05, 0.1
    cm, ws = connect(client)
    try:
        assert ws.receive_json() == {"type": "ping"}  # không trả pong
        with pytest.raises(WebSocketDisconnect):
            for _ in range(20):
                ws.receive_json()
    finally:
        cm.__exit__(None, None, None)  # phía worker hoàn tất bắt tay đóng
    for _ in range(100):
        if hub.worker is None:
            break
        time.sleep(0.02)
    assert hub.worker is None
    assert any("pong" in e["text"] for e in hub.events)


# --- /decide ----------------------------------------------------------------------------------


def serve_one(ws, reply: dict | None = None, error: str | None = None) -> dict:
    """Worker giả: nhận một message decide, trả result (hoặc error)."""
    msg = ws.receive_json()
    while msg["type"] != "decide":
        msg = ws.receive_json()
    if error:
        ws.send_json({"type": "error", "id": msg["id"], "error": error})
    else:
        ws.send_json({"type": "result", "id": msg["id"], "decision": reply or DECISION, "latency_ms": 12.5})
    return msg


def test_decide_happy_path(client):
    cm, ws = connect(client)
    try:
        with ThreadPoolExecutor(1) as ex:
            fut = ex.submit(lambda: client.post("/decide", json=BODY, headers=PH))
            msg = serve_one(ws)
            r = fut.result(5)
        assert r.status_code == 200 and r.json() == DECISION
        assert msg["obs"] == BODY["obs"] and msg["questions"] == BODY["questions"]
        assert msg["deadline_ms"] == 1500 and len(msg["id"]) >= 16
        assert hub.stats["ok"] == 1 and hub.worker.last_latency_ms == 12.5
    finally:
        cm.__exit__(None, None, None)


def test_decide_worker_error_is_503(client):
    cm, ws = connect(client)
    try:
        with ThreadPoolExecutor(1) as ex:
            fut = ex.submit(lambda: client.post("/decide", json=BODY, headers=PH))
            serve_one(ws, error="deadline")
            r = fut.result(5)
        assert r.status_code == 503 and r.json()["ok"] is False and "deadline" in r.json()["error"]
    finally:
        cm.__exit__(None, None, None)


def test_decide_timeout_503(client, monkeypatch):
    monkeypatch.setenv("NT532_DECIDE_DEADLINE_MS", "150")
    cm, ws = connect(client)
    try:
        t0 = time.monotonic()
        r = client.post("/decide", json=BODY, headers=PH)  # worker không trả lời
        assert r.status_code == 503 and r.json()["ok"] is False and "quá hạn" in r.json()["error"]
        assert time.monotonic() - t0 < 2
        assert hub.stats["timeouts"] == 1 and not hub.worker.pending
    finally:
        cm.__exit__(None, None, None)


def test_decide_no_worker_503(client):
    r = client.post("/decide", json=BODY, headers=PH)
    assert r.status_code == 503 and r.json()["ok"] is False


def test_decide_worker_not_ready_503(client):
    cm, _ws = connect(client, ready=False)
    try:
        assert client.post("/decide", json=BODY, headers=PH).status_code == 503
    finally:
        cm.__exit__(None, None, None)


def test_decide_bad_body_400(client):
    assert client.post("/decide", json={"obs": 1}, headers=PH).status_code == 400
    assert client.post("/decide", content=b"khong phai json", headers=PH).status_code == 400


def test_health(client):
    r = client.get("/health", headers=PH)
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["worker"] is None
    cm, _ws = connect(client)
    try:
        j = client.get("/health", headers=PH).json()
        assert j["ok"] is True and j["worker"]["worker_id"] == "colab-t1"
    finally:
        cm.__exit__(None, None, None)


def test_pi_token_enforced(client):
    assert client.post("/decide", json=BODY).status_code == 401
    assert client.post("/decide", json=BODY, headers={"Authorization": "Bearer sai"}).status_code == 401
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer sai"}).status_code == 401


def test_tailscale_allowed_without_token(app, monkeypatch):
    monkeypatch.delenv("NT532_DECIDER_TOKEN")
    with TestClient(app, client=("100.64.1.2", 1)) as c:
        assert c.get("/health").status_code == 200
    with TestClient(app, client=("100.127.255.254", 1)) as c:
        assert c.get("/health").status_code == 200
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        assert c.post("/decide", json=BODY).status_code == 503  # qua được xác thực, chỉ thiếu worker
    for ip in ("100.128.0.1", "192.168.1.5", "8.8.8.8", "testclient"):
        with TestClient(app, client=(ip, 1)) as c:
            assert c.get("/health").status_code == 401, ip


# --- API cho trang ----------------------------------------------------------------------------


def test_page_api_requires_session(client):
    assert client.get("/api/decider/status").status_code == 401
    assert client.post("/api/decider/switch", json={"kind": "rules"}).status_code == 401
    assert client.post("/api/decider/test", json={}).status_code == 401
    assert client.post("/api/decider/worker/shutdown", json={}).status_code == 401
    assert client.get("/decider", follow_redirects=False).headers["location"] == "/login"


def test_status_includes_pi(client, fake_pi):
    login(client)
    j = client.get("/api/decider/status").json()
    assert j["pi"]["decider"] == "rules" and j["pi"]["online"] is True and j["pi"]["run_active"] is False
    assert j["hub"]["state"] == "disconnected" and j["hub"]["public_url"] == "http://100.64.0.9:8800"
    assert j["hub"]["worker_enabled"] is True and j["hub"]["decider_token_set"] is True


def test_decider_page_served(client):
    login(client)
    r = client.get("/decider")
    assert r.status_code == 200 and "Bộ quyết định" in r.text


def test_switch_forwards_to_pi(client, fake_pi):
    login(client)
    r = client.post("/api/decider/switch", json={"kind": "hybrid", "run": "jev2", "tau": 0.7, "stages": "verify,decide"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert fake_pi.cmds[-1] == {"cmd": "decider", "kind": "hybrid", "run": "jev2", "tau": 0.7, "stages": "verify,decide"}
    r = client.post("/api/decider/switch", json={"kind": "remote-hub"})
    assert r.status_code == 200
    assert fake_pi.cmds[-1] == {"cmd": "decider", "kind": "remote", "url": "http://100.64.0.9:8800", "timeout": 3.0}
    r = client.post("/api/decider/switch", json={"kind": "remote-url", "url": "http://10.0.0.5:9000", "timeout": 2})
    assert fake_pi.cmds[-1] == {"cmd": "decider", "kind": "remote", "url": "http://10.0.0.5:9000", "timeout": 2.0}
    assert client.post("/api/decider/switch", json={"kind": "rules"}).status_code == 200
    assert fake_pi.cmds[-1] == {"cmd": "decider", "kind": "rules"}
    assert any("đổi bộ quyết định" in e["text"] for e in client.get("/api/decider/status").json()["hub"]["events"])
    assert any(e["kind"] == "hub" for e in client.app.state.pi.recorded)


def test_switch_validation(client, fake_pi, monkeypatch):
    login(client)
    n = len(fake_pi.cmds)
    bads = [{"kind": "x"}, {"kind": "hybrid", "tau": 2}, {"kind": "jev", "run": "a b"},
            {"kind": "hybrid", "stages": "A;B"}, {"kind": "remote-url"}, {"kind": "remote-url", "url": "ftp://x"},
            {"kind": "remote-url", "url": "http://h", "timeout": 0}, []]
    for bad in bads:
        assert client.post("/api/decider/switch", json=bad).status_code == 400, bad
    monkeypatch.delenv("NT532_PUBLIC_DECIDE_URL")
    r = client.post("/api/decider/switch", json={"kind": "remote-hub"})
    assert r.status_code == 400 and "NT532_PUBLIC_DECIDE_URL" in r.json()["error"]
    assert client.post("/api/decider/switch", content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert len(fake_pi.cmds) == n  # không có gì lọt xuống Pi
    with pytest.raises(ValueError):
        build_switch_cmd("x")


def test_switch_pi_down(client, fake_pi):
    login(client)
    fake_pi.up = False
    r = client.post("/api/decider/switch", json={"kind": "rules"})
    assert r.status_code == 502 and r.json()["ok"] is False


def test_test_endpoint(client):
    login(client)
    r = client.post("/api/decider/test", json={})
    assert r.status_code == 503 and r.json()["ok"] is False
    cm, ws = connect(client)
    try:
        with ThreadPoolExecutor(1) as ex:
            fut = ex.submit(lambda: client.post("/api/decider/test", json={}))
            msg = serve_one(ws)
            r = fut.result(5)
        j = r.json()
        assert r.status_code == 200 and j["ok"] is True
        assert j["action"] == {"real_fire": True} and j["model_ms"] == 12.5 and j["latency_ms"] > 0
        assert msg["obs"]["stage"] == "decide" and "real_fire" in msg["questions"]
    finally:
        cm.__exit__(None, None, None)


def test_worker_shutdown(client):
    login(client)
    assert client.post("/api/decider/worker/shutdown", json={}).status_code == 409
    cm, ws = connect(client)
    try:
        assert client.post("/api/decider/worker/shutdown", json={}).json() == {"ok": True}
        msg = ws.receive_json()
        while msg["type"] == "ping":
            msg = ws.receive_json()
        assert msg == {"type": "shutdown"}
    finally:
        cm.__exit__(None, None, None)


def test_sample_is_json_serializable():
    json.dumps(hubmod.SAMPLE_OBS)
    assert hubmod.sample_questions()["real_fire"]["type"] == "boolean"
