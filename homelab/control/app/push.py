"""Chế độ push: Pi (ở mạng khác, sau NAT) tự POST trạng thái lên đây, lệnh đi về trong response."""

import hmac
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

MAX_BODY = 2 * 1024 * 1024
MAX_EVENTS = 1000
MAX_FRAME = 1024 * 1024  # một khung JPEG


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status)


def _check_auth(request: Request) -> JSONResponse | None:
    """None nếu token đúng; ngược lại là response lỗi (503 push tắt, 401 sai token)."""
    cfg = request.app.state.cfg
    if not cfg.pi_token:
        return _err(503, "Chế độ push đang tắt")
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth[:7].lower() == "bearer " else ""
    if not hmac.compare_digest(token.encode(), cfg.pi_token.encode()):
        return _err(401, "Sai hoặc thiếu token")
    return None


@router.post("/api/pi/push")
async def pi_push(request: Request):
    if (bad := _check_auth(request)) is not None:
        return bad
    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return _err(413, "Body quá lớn")
    except ValueError:
        return _err(400, "Content-Length không hợp lệ")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _err(413, "Body quá lớn")
    try:
        body = json.loads(raw)
    except ValueError:
        return _err(400, "Body không phải JSON")
    if not isinstance(body, dict):
        return _err(400, "Body phải là object")
    boot, state = body.get("boot"), body.get("state")
    events, results = body.get("events") or [], body.get("results") or []
    if (not isinstance(boot, str) or not boot or not isinstance(state, dict)
            or not isinstance(events, list) or not isinstance(results, list)):
        return _err(400, "Cần boot (chuỗi), state (object), events và results (mảng)")
    if len(events) > MAX_EVENTS:
        return _err(413, f"Tối đa {MAX_EVENTS} sự kiện mỗi lần")

    p = request.app.state.pi
    p.deliver_results([r for r in results if isinstance(r, dict)])
    await p.ingest(state, events, boot=boot)
    return {"ack": p.ack_seq, "cmds": p.take_cmds()}


@router.post("/api/pi/frame")
async def pi_frame(request: Request):
    """Pi đẩy một khung JPEG thô (Content-Type image/jpeg) để dashboard hiện hình."""
    if (bad := _check_auth(request)) is not None:
        return bad
    try:
        if int(request.headers.get("content-length") or 0) > MAX_FRAME:
            return _err(413, "Khung hình quá lớn")
    except ValueError:
        return _err(400, "Content-Length không hợp lệ")
    raw = await request.body()
    if len(raw) > MAX_FRAME:
        return _err(413, "Khung hình quá lớn")
    if not raw.startswith(b"\xff\xd8"):
        return _err(400, "Body không phải JPEG")
    p = request.app.state.pi
    p.set_frame(raw)
    return {"ok": True, "calib": p.calib_order()}


MAX_CALIB = 64 * 1024
CALIB_STATES = ("idle", "capturing", "computing", "done", "error")
_NUM = ("req", "saved", "target", "corners", "rms")


@router.post("/api/pi/calib")
async def pi_calib(request: Request):
    """Pi báo trạng thái hiệu chuẩn camera (đang chụp/tính/xong/lỗi)."""
    if (bad := _check_auth(request)) is not None:
        return bad
    raw = await request.body()
    if len(raw) > MAX_CALIB:
        return _err(413, "Body quá lớn")
    try:
        body = json.loads(raw)
    except ValueError:
        return _err(400, "Body không phải JSON")
    if not isinstance(body, dict) or body.get("state") not in CALIB_STATES:
        return _err(400, "Cần object có state hợp lệ")
    status: dict = {"state": body["state"]}
    for k in _NUM:
        v = body.get(k)
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, int | float):
                return _err(400, f"{k} phải là số")
            status[k] = v
    for k in ("file", "error"):
        v = body.get(k)
        if v is not None:
            if not isinstance(v, str):
                return _err(400, f"{k} phải là chuỗi")
            status[k] = v[:300]
    request.app.state.pi.set_calib_status(status)
    return {"ok": True, "calib": request.app.state.pi.calib_order()}


MAX_THREAD = 256 * 1024
MAX_THREAD_NODES = 16


@router.post("/api/pi/thread")
async def pi_thread(request: Request):
    """Pi báo số đo mạng Thread (ping, CoAP, vai trò, topology) mỗi chu kỳ; lưu mới nhất và lịch sử."""
    if (bad := _check_auth(request)) is not None:
        return bad
    try:
        if int(request.headers.get("content-length") or 0) > MAX_THREAD:
            return _err(413, "Body quá lớn")
    except ValueError:
        return _err(400, "Content-Length không hợp lệ")
    raw = await request.body()
    if len(raw) > MAX_THREAD:
        return _err(413, "Body quá lớn")
    try:
        body = json.loads(raw)
    except ValueError:
        return _err(400, "Body không phải JSON")
    if not isinstance(body, dict):
        return _err(400, "Body phải là object")
    nodes, otbr, topo = body.get("nodes"), body.get("otbr"), body.get("topology")
    if not isinstance(nodes, dict) or not all(isinstance(v, dict) for v in nodes.values()):
        return _err(400, "Cần nodes (object, mỗi node là object)")
    if len(nodes) > MAX_THREAD_NODES:
        return _err(413, f"Tối đa {MAX_THREAD_NODES} node")
    if otbr is not None and not isinstance(otbr, dict):
        return _err(400, "otbr phải là object")
    if topo is not None and not isinstance(topo, dict):
        return _err(400, "topology phải là object")
    n = await request.app.state.pi.set_thread(body)
    return {"ok": True, "rows": n}
