"""Chế độ push: Pi (ở mạng khác, sau NAT) tự POST trạng thái lên đây, lệnh đi về trong response."""

import hmac
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

MAX_BODY = 2 * 1024 * 1024
MAX_EVENTS = 1000


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status)


@router.post("/api/pi/push")
async def pi_push(request: Request):
    cfg = request.app.state.cfg
    if not cfg.pi_token:
        return _err(503, "Chế độ push đang tắt")
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth[:7].lower() == "bearer " else ""
    if not hmac.compare_digest(token.encode(), cfg.pi_token.encode()):
        return _err(401, "Sai hoặc thiếu token")
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
