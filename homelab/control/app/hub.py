"""Hub bộ quyết định: nhận worker Colab qua WebSocket, phục vụ /decide cho Pi, và API cho trang /decider.

    Pi --POST /decide--> hub --"decide"--> worker (Colab, nối RA hub qua /api/worker/ws)

Giao thức worker: homelab/colab/protocol.md. Biến môi trường được đọc mỗi lần dùng (không đọc lúc import):
NT532_WORKER_TOKEN, NT532_DECIDER_TOKEN, NT532_DECIDE_DEADLINE_MS, NT532_PUBLIC_DECIDE_URL.
"""

import asyncio
import contextlib
import hmac
import ipaddress
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

log = logging.getLogger("nt532.hub")

STATIC = Path(__file__).parent / "static"
COOKIE = "nt532_session"  # trùng app.main.COOKIE
MAX_BODY = 1_000_000
HELLO_TIMEOUT_S = 10.0
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
KINDS = ("rules", "hybrid", "jev", "remote-hub", "remote-url")

# Mẫu obs (lấy từ pi/tests/test_remote_decider.py::cases()[0]) để thử worker.
SAMPLE_OBS = {
    "stage": "decide", "alarm": "s1", "limits": {"temp": 45.0, "gas": 600.0},
    "sensors": {"s1": {"temp": [27.0, 27.0, 55.0, 55.0, 55.0], "gas": [180, 180, 820, 820, 820], "hum": [50] * 5},
                "s2": {"temp": [27.0] * 5, "gas": [180] * 5, "hum": [50] * 5}},
    "frames": 3,
    "targets": [{"id": "T1", "x": 0.32, "z": 0.25, "y": 0.8, "surface": "wall", "w": 0.031, "h": 0.056,
                 "conf": [0.8, 0.8, 0.8], "dist": {"s1": 0.02, "s2": 0.58}, "dist3": {"s1": 0.02, "s2": 0.58}}],
    "nozzles": {"s1": {"hb_ms": 200, "pose_s": 10, "reach": {"T1": True}},
                "s2": {"hb_ms": 200, "pose_s": 10, "reach": {"T1": True}}},
}


def sample_questions() -> dict:
    """Dạng câu hỏi giống `questions_for` của Pi; đủ để worker trả lời thử."""
    return {"real_fire": {"type": "boolean", "candidates": [True, False]},
            "target": {"type": "choice", "candidates": ["T1"]},
            "nozzle": {"type": "choice", "candidates": ["s1", "s2"]}}


# --- cấu hình (đọc mỗi lần) ------------------------------------------------------------------


def worker_token() -> str:
    return os.environ.get("NT532_WORKER_TOKEN", "")


def decider_token() -> str:
    return os.environ.get("NT532_DECIDER_TOKEN", "")


def deadline_ms() -> int:
    try:
        return max(100, int(os.environ.get("NT532_DECIDE_DEADLINE_MS", "2500")))
    except ValueError:
        return 2500


def public_decide_url() -> str:
    return os.environ.get("NT532_PUBLIC_DECIDE_URL", "").strip().rstrip("/")


def _bearer_ok(header: str, token: str) -> bool:
    return hmac.compare_digest(header.encode(), f"Bearer {token}".encode())


def _ip_allowed(host: str | None) -> bool:
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    return ip.is_loopback or ip in TAILSCALE


class HubError(Exception):
    """Không có quyết định từ worker (không worker, quá hạn, lỗi); `str(e)` là lý do cho người đọc."""


class WorkerConn:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.lock = asyncio.Lock()
        self.info: dict = {}
        self.connected_at = time.time()
        self.ready = False
        self.busy = False
        self.done = 0
        self.last_latency_ms: float | None = None
        self.last_pong = time.monotonic()
        self.requests = 0
        self.errors = 0
        self.last_error: str | None = None
        self.pending: dict[str, asyncio.Future] = {}

    async def send(self, msg: dict) -> None:
        async with self.lock:
            await self.ws.send_text(json.dumps(msg, ensure_ascii=False))

    def fail_pending(self, why: str) -> None:
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(HubError(why))
        self.pending.clear()

    def summary(self) -> dict:
        return {"worker_id": self.info.get("worker_id"), "decider": self.info.get("decider"),
                "run": self.info.get("run"), "device": self.info.get("device"), "gpu": self.info.get("gpu"),
                "started": self.info.get("started"), "connected_at": self.connected_at,
                "ready": self.ready, "busy": self.busy or bool(self.pending), "done": self.done,
                "last_latency_ms": self.last_latency_ms, "requests": self.requests, "errors": self.errors,
                "last_error": self.last_error}


class Hub:
    def __init__(self) -> None:
        self.app = None
        self.worker: WorkerConn | None = None
        self.connecting = 0  # socket đã nhận nhưng chưa gửi hello
        self.ping_s = 20.0
        self.pong_timeout_s = 15.0
        self.events: deque[dict] = deque(maxlen=100)  # cũ -> mới
        self.stats = {"requests": 0, "ok": 0, "failed": 0, "timeouts": 0, "no_worker": 0}
        self.last_error: str | None = None
        self.last_roundtrip_ms: float | None = None

    # --- vòng đời -----------------------------------------------------------------------------

    def bind(self, app) -> None:
        if app is not None:
            self.app = app

    async def startup(self, app=None) -> None:
        self.bind(app)

    async def shutdown(self) -> None:
        w, self.worker = self.worker, None
        if w:
            w.fail_pending("hub đang tắt")
            with contextlib.suppress(Exception):
                await w.ws.close(code=1001, reason="hub shutdown")

    # --- sự kiện ------------------------------------------------------------------------------

    async def event(self, text: str, level: str = "info") -> None:
        self.events.append({"time": time.time(), "level": level, "text": text})
        pi = getattr(getattr(self.app, "state", None), "pi", None)
        if pi is not None and hasattr(pi, "record_events"):
            try:
                await pi.record_events([{"kind": "hub", "level": level, "text": text}])
            except Exception:  # noqa: BLE001 - ghi nhật ký hỏng không được làm hỏng hub
                log.exception("không ghi được sự kiện hub")

    # --- trạng thái ---------------------------------------------------------------------------

    def state_name(self) -> str:
        if not worker_token():
            return "disabled"
        w = self.worker
        if w:
            if w.busy or w.pending:
                return "busy"
            return "ready" if w.ready else "connecting"
        return "connecting" if self.connecting else "disconnected"

    def status(self) -> dict:
        w = self.worker
        return {"state": self.state_name(), "worker_enabled": bool(worker_token()),
                "decider_token_set": bool(decider_token()), "deadline_ms": deadline_ms(),
                "public_url": public_decide_url(), "worker": w.summary() if w else None,
                "stats": dict(self.stats), "last_error": self.last_error,
                "last_roundtrip_ms": self.last_roundtrip_ms, "now": time.time(),
                "events": list(reversed(self.events))[:30]}

    # --- worker -------------------------------------------------------------------------------

    async def _heartbeat(self, w: WorkerConn) -> None:
        while True:
            await asyncio.sleep(self.ping_s)
            sent = time.monotonic()
            try:
                await w.send({"type": "ping"})
            except Exception:  # noqa: BLE001
                return
            await asyncio.sleep(self.pong_timeout_s)
            if w.last_pong < sent:
                await self.event("Worker không trả pong, ngắt kết nối", "warn")
                with contextlib.suppress(Exception):
                    await w.ws.close(code=1011, reason="pong timeout")
                return

    def _handle(self, w: WorkerConn, msg: dict) -> None:
        t = msg.get("type")
        w.last_pong = time.monotonic()  # bất kỳ frame nào cũng chứng tỏ worker còn sống
        if t in ("status", "pong"):
            if "ready" in msg:
                w.ready = bool(msg["ready"])
            if "busy" in msg:
                w.busy = bool(msg["busy"])
            if isinstance(msg.get("done"), int):
                w.done = msg["done"]
        elif t == "result":
            fut = w.pending.pop(str(msg.get("id")), None)
            lat = msg.get("latency_ms")
            if isinstance(lat, int | float):
                w.last_latency_ms = float(lat)
            if fut and not fut.done():
                fut.set_result(msg)
        elif t == "error":
            err = str(msg.get("error") or "lỗi không rõ")
            w.errors += 1
            w.last_error = err
            fut = w.pending.pop(str(msg.get("id")), None)
            if fut and not fut.done():
                fut.set_exception(HubError(f"worker: {err}"))

    async def serve_worker(self, ws: WebSocket) -> None:
        self.bind(ws.app)
        tok = worker_token()
        got = ws.headers.get("authorization", "")
        if not got and ws.query_params.get("token"):
            got = f"Bearer {ws.query_params['token']}"
        if not tok or not _bearer_ok(got, tok):
            reason = "Chưa bật worker (thiếu NT532_WORKER_TOKEN)" if not tok else "Thiếu hoặc sai token"
            try:
                await ws.send_denial_response(JSONResponse({"error": reason}, 401))
            except RuntimeError:
                await ws.close(code=1008)
            return
        await ws.accept()
        self.connecting += 1
        w = WorkerConn(ws)
        registered = False
        hb: asyncio.Task | None = None
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), None if registered else HELLO_TIMEOUT_S)
                except TimeoutError:
                    await ws.close(code=1008, reason="thiếu hello")
                    return
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if registered:
                    self._handle(w, msg)
                    continue
                if msg.get("type") != "hello":
                    continue
                w.info = {k: msg.get(k) for k in ("worker_id", "decider", "run", "device", "gpu", "started")}
                old, self.worker = self.worker, w
                registered = True
                self.connecting -= 1
                if old:
                    old.fail_pending("worker bị thay bằng worker mới")
                    with contextlib.suppress(Exception):
                        await old.ws.close(code=1000, reason="replaced")
                hb = asyncio.create_task(self._heartbeat(w))
                gpu = w.info.get("gpu") or w.info.get("device") or "?"
                await self.event(f"Worker {w.info.get('worker_id') or '?'} đã nối ({gpu}, {w.info.get('decider')})")
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.exception("lỗi kết nối worker")
        finally:
            if hb:
                hb.cancel()
            if not registered:
                self.connecting -= 1
            w.fail_pending("worker mất kết nối")
            if self.worker is w:
                self.worker = None
                await self.event("Worker đã ngắt kết nối", "warn")

    # --- quyết định ---------------------------------------------------------------------------

    async def decide(self, obs: dict, questions: dict) -> tuple[dict, float, float | None]:
        """Chuyển một yêu cầu cho worker. Trả (decision, thời gian khứ hồi ms, thời gian mô hình ms)."""
        self.stats["requests"] += 1
        w = self.worker
        if w is None or not w.ready:
            self.stats["no_worker"] += 1
            self.stats["failed"] += 1
            self.last_error = "chưa có worker sẵn sàng"
            raise HubError(self.last_error)
        rid = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        w.pending[rid] = fut
        w.requests += 1
        dl = deadline_ms()
        t0 = time.perf_counter()
        try:
            await w.send({"type": "decide", "id": rid, "obs": obs, "questions": questions, "deadline_ms": dl})
            msg = await asyncio.wait_for(fut, dl / 1000)
        except TimeoutError:
            self.stats["timeouts"] += 1
            self.stats["failed"] += 1
            self.last_error = f"quá hạn {dl} ms"
            raise HubError(self.last_error) from None
        except HubError as e:
            self.stats["failed"] += 1
            self.last_error = str(e)
            raise
        except Exception as e:  # noqa: BLE001 - gửi vào socket hỏng
            self.stats["failed"] += 1
            self.last_error = f"{type(e).__name__}: {e}"
            raise HubError(self.last_error) from e
        finally:
            w.pending.pop(rid, None)
        decision = msg.get("decision")
        if not isinstance(decision, dict):
            self.stats["failed"] += 1
            self.last_error = "worker trả decision sai dạng"
            raise HubError(self.last_error)
        rt = (time.perf_counter() - t0) * 1000
        self.stats["ok"] += 1
        self.last_roundtrip_ms = round(rt, 1)
        lat = msg.get("latency_ms")
        return decision, rt, float(lat) if isinstance(lat, int | float) else None


hub = Hub()
router = APIRouter()


# --- xác thực ---------------------------------------------------------------------------------


def _session_user(request: Request) -> str | None:
    cfg = getattr(request.app.state, "cfg", None)
    tok = request.cookies.get(COOKIE)
    if cfg is None or not tok:
        return None
    try:
        return URLSafeTimedSerializer(cfg.secret, salt="nt532-session").loads(
            tok, max_age=cfg.session_days * 86400).get("u")
    except BadSignature:
        return None


def require_user(request: Request) -> str:
    hub.bind(request.app)
    u = _session_user(request)
    if u is None:
        raise HTTPException(401, "Cần đăng nhập")
    return u


def require_pi_caller(request: Request) -> None:
    """Pi gọi /decide, /health: bearer NT532_DECIDER_TOKEN; không đặt thì chỉ nhận Tailscale hoặc loopback."""
    hub.bind(request.app)
    tok = decider_token()
    if tok:
        if not _bearer_ok(request.headers.get("authorization", ""), tok):
            raise HTTPException(401, "thiếu hoặc sai token")
    elif not _ip_allowed(request.client.host if request.client else None):
        raise HTTPException(401, "chỉ nhận từ Tailscale hoặc loopback khi chưa đặt NT532_DECIDER_TOKEN")


# --- cổng cho worker và Pi --------------------------------------------------------------------


@router.websocket("/api/worker/ws")
async def worker_ws(ws: WebSocket) -> None:
    await hub.serve_worker(ws)


@router.post("/decide")
async def decide(request: Request, _: None = Depends(require_pi_caller)):
    def fail(msg: str, code: int = 503):
        return JSONResponse({"ok": False, "error": msg}, code)

    raw = await request.body()
    if not 0 < len(raw) <= MAX_BODY:
        return fail("thân yêu cầu rỗng hoặc quá lớn", 413)
    try:
        body = json.loads(raw)
        obs, questions = body["obs"], body["questions"]
        if not isinstance(obs, dict) or not isinstance(questions, dict):
            raise TypeError("obs và questions phải là object")
    except (ValueError, KeyError, TypeError) as e:
        return fail(f"yêu cầu sai dạng: {e}", 400)
    try:
        decision, _rt, _model = await hub.decide(obs, questions)
    except HubError as e:
        return fail(str(e))
    return JSONResponse(decision)


@router.get("/health")
async def health(_: None = Depends(require_pi_caller)):
    w = hub.worker
    return {"ok": bool(w and w.ready), "worker": w.summary() if w else None, "state": hub.state_name()}


# --- API cho trang ----------------------------------------------------------------------------


def _pi_view(request: Request) -> dict:
    pi = getattr(request.app.state, "pi", None)
    if pi is None:
        return {"online": False, "decider": None, "decider_error": None, "run_active": False, "last_decision": None}
    ov = pi.overview(False)
    orch = (ov.get("state") or {}).get("orch") or {}
    last = next((e for e in reversed(list(pi.recent)) if e.get("kind") == "decision"), None)
    return {"online": bool(ov.get("pi_online")), "decider": orch.get("decider"),
            "decider_error": orch.get("decider_error"), "run_active": orch.get("run") is not None,
            "phase": orch.get("phase"),
            "last_decision": {"time": last.get("time"), "text": last.get("text")} if last else None}


@router.get("/decider", include_in_schema=False)
async def decider_page(request: Request):
    hub.bind(request.app)
    if _session_user(request) is None:
        return RedirectResponse("/login", 303)
    return FileResponse(STATIC / "decider.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/decider/status")
async def status(request: Request, user: str = Depends(require_user)):
    return {"hub": hub.status(), "pi": _pi_view(request)}


def _need_json(request: Request) -> None:
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "Content-Type phải là application/json")


_RUN = re.compile(r"[A-Za-z0-9_.-]{1,40}")
_STAGES = re.compile(r"[a-z_,]{1,60}")


def build_switch_cmd(body) -> dict:
    """Từ yêu cầu của trang sang lệnh `decider` của Pi; ném ValueError (tiếng Việt) nếu sai."""
    if not isinstance(body, dict):
        raise ValueError("thân yêu cầu phải là object")
    kind = body.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind phải là một trong {', '.join(KINDS)}")
    cmd: dict = {"cmd": "decider"}
    if kind == "rules":
        return {**cmd, "kind": "rules"}
    if kind in ("hybrid", "jev"):
        cmd["kind"] = kind
        if body.get("run") is not None:
            if not isinstance(body["run"], str) or not _RUN.fullmatch(body["run"]):
                raise ValueError("run không hợp lệ")
            cmd["run"] = body["run"]
        if body.get("tau") is not None:
            tau = body["tau"]
            if isinstance(tau, bool) or not isinstance(tau, int | float) or not 0 <= tau <= 1:
                raise ValueError("tau phải là số trong [0, 1]")
            cmd["tau"] = float(tau)
        if body.get("stages") is not None:
            if not isinstance(body["stages"], str) or not _STAGES.fullmatch(body["stages"]):
                raise ValueError("stages không hợp lệ")
            cmd["stages"] = body["stages"]
        return cmd
    if kind == "remote-hub":
        url = public_decide_url()
        if not url:
            raise ValueError("chưa đặt NT532_PUBLIC_DECIDE_URL cho hub (vd http://<ip-tailscale-của-LXC>:8800)")
    else:
        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("cần url cho remote-url")
        url = url.strip()
    if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
        raise ValueError("url phải có dạng http://host:cổng hoặc https://host")
    timeout = body.get("timeout", 3.0)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not 0.2 <= timeout <= 30:
        raise ValueError("timeout phải là số giây trong [0.2, 30]")
    return {**cmd, "kind": "remote", "url": url, "timeout": float(timeout)}


@router.post("/api/decider/switch")
async def switch(request: Request, user: str = Depends(require_user)):
    _need_json(request)
    try:
        cmd = build_switch_cmd(await request.json())
    except (ValueError, json.JSONDecodeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)
    pi = getattr(request.app.state, "pi", None)
    if pi is None:
        return JSONResponse({"ok": False, "error": "chưa có kết nối tới Pi"}, 502)
    try:
        r = await pi.http.post("/api/cmd", json=cmd)
        reply = r.json()
        ok = r.status_code == 200 and bool(reply.get("ok"))
        code = 200 if r.status_code == 200 else 502
    except (httpx.HTTPError, ValueError) as e:
        reply, ok, code = {"ok": False, "error": f"Pi không phản hồi ({type(e).__name__})"}, False, 502
    shown = {k: v for k, v in cmd.items() if k not in ("cmd", "url")}
    text = f"{user}: đổi bộ quyết định {json.dumps(shown, ensure_ascii=False)}"
    if not ok:
        text += f" — lỗi: {reply.get('error', 'không rõ')}"
    await hub.event(text, "info" if ok else "warn")
    return JSONResponse(reply, code)


def _summarize(decision: dict) -> dict:
    answers = decision.get("answers")
    if not isinstance(answers, dict):
        return {}
    return {q: a.get("answer") for q, a in answers.items() if isinstance(a, dict)}


@router.post("/api/decider/test")
async def test(request: Request, user: str = Depends(require_user)):
    _need_json(request)
    try:
        decision, rt, model_ms = await hub.decide(SAMPLE_OBS, sample_questions())
    except HubError as e:
        return JSONResponse({"ok": False, "error": str(e)}, 503)
    return {"ok": True, "latency_ms": round(rt, 1), "model_ms": model_ms, "decider": decision.get("decider"),
            "action": _summarize(decision), "error": decision.get("error"), "decision": decision}


@router.post("/api/decider/worker/shutdown")
async def worker_shutdown(request: Request, user: str = Depends(require_user)):
    _need_json(request)
    w = hub.worker
    if w is None:
        return JSONResponse({"ok": False, "error": "không có worker đang nối"}, 409)
    try:
        await w.send({"type": "shutdown"})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"không gửi được: {type(e).__name__}"}, 502)
    await hub.event(f"{user}: tắt worker {w.info.get('worker_id') or '?'}", "warn")
    return {"ok": True}
