"""Pi giả (FastAPI chạy trong tiến trình qua ASGI transport) và các fixture dùng chung."""

import time

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import Settings
from app.main import create_app
from app.security import hash_password

PASSWORD = "mat-khau-thu"
PW_HASH = hash_password(PASSWORD)


class FakePi:
    """Pi giả: trạng thái và sự kiện chỉnh được từ test."""

    def __init__(self) -> None:
        self.sim = True
        self.up = True
        self.events: list[dict] = []
        self.cmds: list[dict] = []
        self.app = self._build()

    def add_event(self, seq: int, kind: str = "alert", text: str = "x", **extra) -> None:
        self.events.append({"seq": seq, "time": time.time(), "kind": kind, "level": "info", "text": text, **extra})

    def state(self) -> dict:
        sample = lambda t, temp: {"t": t, "temp": temp, "gas": 180, "hum": 55}  # noqa: E731
        return {
            "time": time.time(),
            "orch": {"phase": "IDLE", "enabled": True, "run": None,
                     "runs": [{"id": 1, "node": "s1", "started": 1000.0, "outcome": "extinguished"}],
                     "queue": 0, "overlay": {}, "decider": "rules", "decider_error": None,
                     "nozzles": {"s1": {"hb_ms": 200, "pose_s": 2.0, "blockers": []},
                                 "s2": {"hb_ms": 210, "pose_s": 2.5, "blockers": []}},
                     "health": {}},
            "sensors": {"s1": [sample(-1.0, 26.0), sample(-0.1, 26.5)], "s2": [sample(-0.1, 27.0)]},
            "limits": {"temp": 45.0, "gas": 600.0}, "sim": self.sim, "detector": "oracle",
            "camera_error": None,
        }

    def _build(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.middleware("http")
        async def gate(request: Request, call_next):
            if not fake.up:
                return Response("down", status_code=503)
            return await call_next(request)

        @app.get("/api/state")
        async def state():
            return fake.state()

        @app.get("/api/events")
        async def events(since: int = 0):
            return [e for e in fake.events if e["seq"] > since]

        @app.post("/api/cmd")
        async def cmd(request: Request):
            body = await request.json()
            fake.cmds.append(body)
            return JSONResponse({"ok": True})

        @app.get("/stream.mjpg")
        async def stream():
            async def gen():
                for _ in range(3):
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\nJPEG\r\n"
            return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

        @app.get("/snapshot.jpg")
        async def snap():
            return Response(b"JPEG", media_type="image/jpeg")

        return app


@pytest.fixture
def fake_pi() -> FakePi:
    return FakePi()


@pytest.fixture
def cfg(tmp_path) -> Settings:
    return Settings(pi_url="http://pi", secret="test-secret", admin_user="admin", admin_hash=PW_HASH,
                    db_path=str(tmp_path / "t.sqlite"), poll_s=3600.0, retention_days=14)


@pytest.fixture
async def app(cfg, fake_pi):
    pi_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_pi.app), base_url="http://pi")
    application = create_app(cfg, pi_http=pi_http)
    async with application.router.lifespan_context(application):
        await application.state.pi.stop()  # tắt poller nền; test tự gọi poll_once cho xác định
        yield application
    await pi_http.aclose()


@pytest.fixture
async def anon(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def client(anon):
    r = await anon.post("/login", data={"username": "admin", "password": PASSWORD})
    assert r.status_code == 303 and r.headers["location"] == "/"
    return anon
