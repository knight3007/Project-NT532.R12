"""Trung tâm điều khiển NT532: xác thực, proxy tới Pi, SSE cho trang Tổng quan."""

import asyncio
import json
import logging
import re
import sys
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config, history
from .hub import hub
from .hub import router as hub_router
from .pi_client import PiClient
from .push import router as push_router
from .security import verify_password
from .store import Store

STATIC = Path(__file__).parent / "static"
COOKIE = "nt532_session"
LOGIN_FAILS, LOGIN_WINDOW_S = 5, 60
log = logging.getLogger("nt532.main")

# --- lệnh cho phép chuyển tiếp xuống Pi ------------------------------------------------------

NODES = ("s1", "s2")


def _num(lo: float, hi: float):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, int | float) or not lo <= v <= hi:
            raise ValueError(f"cần số trong [{lo}, {hi}]")
        return float(v)
    return check


def _choice(*opts: str):
    def check(v):
        if v not in opts:
            raise ValueError(f"phải là một trong {', '.join(opts)}")
        return v
    return check


def _bool(v):
    if not isinstance(v, bool):
        raise ValueError("cần true hoặc false")
    return v


def _regex(pat: str):
    rx = re.compile(pat)

    def check(v):
        if not isinstance(v, str) or not rx.fullmatch(v):
            raise ValueError("giá trị không hợp lệ")
        return v
    return check


_POS = {"x": _num(0, 5), "y": _num(0, 5), "z": _num(0, 5), "surface": _choice("wall", "floor"),
        "u": _num(0, 10000), "v": _num(0, 10000)}
# lệnh -> (tham số hợp lệ, tham số bắt buộc, chỉ có trên sa bàn ảo)
COMMANDS: dict[str, tuple[dict, tuple, bool]] = {
    "stop": ({}, (), False),
    "enable": ({"on": _bool}, (), False),
    "recommission": ({}, (), False),
    "check_camera": ({}, (), False),
    "decider": ({"kind": _choice("rules", "hybrid", "jev", "remote"), "run": _regex(r"[A-Za-z0-9_.-]{1,40}"),
                 "tau": _num(0, 1), "stages": _regex(r"[a-z_,]{1,60}")}, ("kind",), False),
    "fire": ({**_POS, "size": _choice("small", "large")}, (), True),
    "fire_large": (_POS, (), True),
    "lamp": (_POS, (), True),
    "object": (_POS, (), True),
    "steam": ({"x": _num(0, 5)}, (), True),
    "spike": ({"node": _choice(*NODES)}, (), True),
    "clear": ({}, (), True),
    "online": ({"node": _choice(*NODES), "on": _bool}, ("node",), True),
    "move_node": ({"node": _choice(*NODES), "dx": _num(-0.5, 0.5)}, ("node",), True),
}


def validate_cmd(body, sim: bool) -> dict:
    """Trả về body đã làm sạch hoặc ném ValueError (thông báo tiếng Việt)."""
    if not isinstance(body, dict) or not isinstance(body.get("cmd"), str):
        raise ValueError("thiếu trường cmd")
    cmd = body["cmd"]
    if cmd not in COMMANDS:
        raise ValueError(f"lệnh {cmd!r} không được phép")
    params, required, sim_only = COMMANDS[cmd]
    if sim_only and not sim:
        raise ValueError(f"lệnh {cmd!r} chỉ có trên sa bàn ảo")
    extra = set(body) - {"cmd"} - set(params)
    if extra:
        raise ValueError(f"tham số không hợp lệ: {', '.join(sorted(extra))}")
    missing = [k for k in required if k not in body]
    if missing:
        raise ValueError(f"thiếu tham số: {', '.join(missing)}")
    clean = {"cmd": cmd}
    for k, check in params.items():
        if k in body:
            try:
                clean[k] = check(body[k])
            except ValueError as e:
                raise ValueError(f"tham số {k}: {e}") from None
    return clean


# --- giới hạn đăng nhập sai ------------------------------------------------------------------


class LoginLimiter:
    def __init__(self, fails: int = LOGIN_FAILS, window_s: float = LOGIN_WINDOW_S) -> None:
        self.fails, self.window_s = fails, window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def _prune(self, ip: str, now: float) -> deque:
        q = self._hits[ip]
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def blocked(self, ip: str) -> bool:
        return len(self._prune(ip, time.monotonic())) >= self.fails

    def fail(self, ip: str) -> None:
        self._prune(ip, time.monotonic()).append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._hits.pop(ip, None)


def create_app(cfg: config.Settings | None = None, pi_http: httpx.AsyncClient | None = None) -> FastAPI:
    cfg = cfg or config.load()
    signer = URLSafeTimedSerializer(cfg.secret, salt="nt532-session")
    limiter = LoginLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(cfg.db_path)
        app.state.pi = PiClient(cfg, app.state.store, client=pi_http)
        app.state.pi.start()
        await hub.startup(app)
        yield
        await hub.shutdown()
        await app.state.pi.stop()
        app.state.store.close()

    app = FastAPI(title="NT532 control", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg
    app.state.limiter = limiter
    app.include_router(push_router)  # /api/pi/push: Pi tự đẩy dữ liệu lên, nhận lệnh trong response
    app.include_router(hub_router)  # /decider, /api/decider/*, /api/worker/ws, /decide, /health

    def user_of(request: Request) -> str | None:
        tok = request.cookies.get(COOKIE)
        if not tok:
            return None
        try:
            return signer.loads(tok, max_age=cfg.session_days * 86400).get("u")
        except BadSignature:
            return None

    def require_user(request: Request) -> str:
        u = user_of(request)
        if u is None:
            raise HTTPException(401, "Cần đăng nhập")
        return u

    def pi(request: Request) -> PiClient:
        return request.app.state.pi

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "?"

    # --- trang và xác thực --------------------------------------------------------------------

    @app.get("/healthz")
    async def healthz(request: Request):
        return {"ok": True, "pi_online": pi(request).pi_online}

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        if user_of(request) is None:
            return RedirectResponse("/login", 303)
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    def page(name: str, soon: str = "Trang này sắp có."):
        async def handler(request: Request):
            if user_of(request) is None:
                return RedirectResponse("/login", 303)
            f = STATIC / name
            if not f.exists():
                return HTMLResponse(f"<!doctype html><meta charset=utf-8><p>{soon}</p>", 404)
            return FileResponse(f, headers={"Cache-Control": "no-cache"})
        return handler

    for route, name in (("/history", "history.html"), ("/runs", "runs.html")):  # /decider do hub.py phục vụ
        app.add_api_route(route, page(name), methods=["GET"], include_in_schema=False)

    @app.get("/login", include_in_schema=False)
    async def login_page(request: Request):
        if user_of(request) is not None:
            return RedirectResponse("/", 303)
        return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-cache"})

    @app.post("/login", include_in_schema=False)
    async def login(request: Request, username: str = Form(""), password: str = Form("")):
        ip = client_ip(request)
        if limiter.blocked(ip):
            return RedirectResponse("/login?e=2", 303)
        # luôn băm mật khẩu, kể cả khi sai tên, để thời gian phản hồi không lộ tên hợp lệ
        ok_pw = await asyncio.to_thread(verify_password, password, cfg.admin_hash)
        if ok_pw and username == cfg.admin_user:
            limiter.reset(ip)
            resp = RedirectResponse("/", 303)
            resp.set_cookie(COOKIE, signer.dumps({"u": username}), max_age=cfg.session_days * 86400,
                            httponly=True, samesite="lax", secure=cfg.https, path="/")
            return resp
        limiter.fail(ip)
        log.warning("đăng nhập sai từ %s", ip)
        return RedirectResponse("/login?e=1", 303)

    @app.post("/logout", include_in_schema=False)
    async def logout():
        resp = RedirectResponse("/login", 303)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # --- API ----------------------------------------------------------------------------------

    app.include_router(history.make_router(require_user))

    @app.get("/api/me")
    async def me(user: str = Depends(require_user)):
        return {"user": user}

    @app.get("/api/overview")
    async def overview(request: Request, user: str = Depends(require_user)):
        return pi(request).overview()

    @app.get("/api/stream")
    async def stream(request: Request, user: str = Depends(require_user)):
        p = pi(request)

        async def gen():
            q = p.subscribe()
            try:
                yield "retry: 2000\n\n"
                yield _sse("overview", {"overview": p.overview(), "events": [], "init": True})
                while not await request.is_disconnected():
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=10)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield _sse("overview", msg)
            finally:
                p.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/cmd")
    async def cmd(request: Request, user: str = Depends(require_user)):
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"ok": False, "error": "Content-Type phải là application/json"}, 415)
        p = pi(request)
        try:
            body = validate_cmd(await request.json(), sim=bool(p.overview(False)["sim"]))
        except (ValueError, json.JSONDecodeError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, 400)
        params = {k: v for k, v in body.items() if k != "cmd"}
        status, reply = await p.send_cmd(body)
        ok = status == 200 and bool(reply.get("ok"))
        text = f"{user}: {body['cmd']}" + (f" {json.dumps(params, ensure_ascii=False)}" if params else "")
        if not ok:
            text += f" — lỗi: {reply.get('error', 'không rõ')}"
        await p.record_events([{"kind": "cmd", "level": "info" if ok else "warn", "text": text,
                                "user": user, "cmd": body["cmd"], "params": params, "ok": ok}])
        return JSONResponse(reply, status)

    # --- camera -------------------------------------------------------------------------------

    @app.get("/camera/stream.mjpg")
    async def camera_stream(request: Request, user: str = Depends(require_user)):
        p = pi(request)
        if p.push_mode:
            return JSONResponse({"error": "Không có hình"}, 503)
        req = p.http.build_request("GET", "/stream.mjpg", timeout=httpx.Timeout(5.0, read=None))
        try:
            up = await p.http.send(req, stream=True)
        except httpx.HTTPError:
            return JSONResponse({"error": "Không có hình"}, 502)
        if up.status_code != 200:
            await up.aclose()
            return JSONResponse({"error": "Không có hình"}, 502)

        async def gen():
            try:
                async for chunk in up.aiter_raw():
                    yield chunk
            except httpx.HTTPError:
                pass
            finally:
                await up.aclose()

        return StreamingResponse(gen(), media_type=up.headers.get("content-type", "multipart/x-mixed-replace"),
                                 headers={"Cache-Control": "no-store"})

    @app.get("/camera/snapshot.jpg")
    async def camera_snapshot(request: Request, user: str = Depends(require_user)):
        if pi(request).push_mode:
            return JSONResponse({"error": "Không có hình"}, 503)
        try:
            r = await pi(request).http.get("/snapshot.jpg")
        except httpx.HTTPError:
            return JSONResponse({"error": "Không có hình"}, 502)
        if r.status_code != 200:
            return JSONResponse({"error": "Không có hình"}, 502)
        return StreamingResponse(iter([r.content]), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    return app


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def __getattr__(name: str):
    """`uvicorn app.main:app`: chỉ dựng app khi được hỏi, để import trong test không đòi biến môi trường."""
    if name == "app":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)  # mỗi lần hỏi Pi đều in một dòng INFO
        try:
            application = create_app()
        except config.ConfigError as e:
            print(f"\nLỗi cấu hình: {e}\n", file=sys.stderr)
            raise SystemExit(1) from None
        globals()["app"] = application
        return application
    raise AttributeError(name)

