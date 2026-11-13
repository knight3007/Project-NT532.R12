"""Bộ quyết định chạy ở máy khác (laptop có GPU) qua HTTP trong LAN của phòng lab.

    Pi: RemoteDecider(url) --POST {obs, questions}--> <url>/decide --> Decision.to_json()
    Laptop: scripts/serve_decider.py (jev, hybrid hoặc rules) dùng `make_server` ở đây.

Chỉ dùng thư viện chuẩn. Không mã hóa. Xác thực tùy chọn bằng bearer token dùng chung (env
NT532_DECIDER_TOKEN hoặc tham số `token`); không đặt thì không xác thực, chỉ chạy trong LAN tin cậy. Mất mạng, quá hạn, HTTP lỗi hoặc trả lời sai dạng thì cả lượt dùng
bộ dự phòng (luật) và đặt `last_error` để dashboard báo "mô hình lỗi, đang dùng luật" như HybridDecider.
Các chặn an toàn cứng vẫn ở orchestrator, nên máy từ xa không thể phá chúng.
"""

import hmac
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..decider.state_text import render_state
from .decide import Answer, Decision, RuleDecider

MAX_BODY = 1_000_000  # byte, cho cả yêu cầu lẫn trả lời
TOKEN_ENV = "NT532_DECIDER_TOKEN"


class BadReply(ValueError):
    """Trả lời của máy chủ không đúng dạng Decision cho các câu hỏi đã gửi."""


def parse_decision(reply: dict, questions: dict, obs: dict) -> Decision:
    """Dựng `Decision` từ JSON của máy chủ; thiếu câu hỏi hoặc đáp án không thuộc ứng viên thì BadReply."""
    answers = reply.get("answers") if isinstance(reply, dict) else None
    if not isinstance(answers, dict):
        raise BadReply("thiếu answers")
    out = {}
    for qid, q in questions.items():
        a = answers.get(qid)
        if not isinstance(a, dict) or "answer" not in a:
            raise BadReply(f"thiếu đáp án cho {qid}")
        ans, conf = a["answer"], a.get("confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
            raise BadReply(f"độ tin cậy của {qid} không hợp lệ")
        if q.get("type") == "boolean":
            if not isinstance(ans, bool):
                raise BadReply(f"đáp án {qid} phải là true/false")
        elif ans not in q.get("candidates", ()):
            raise BadReply(f"đáp án {qid} {ans!r} không thuộc ứng viên")
        probs = a.get("probs", {})
        if not isinstance(probs, dict) or not all(isinstance(v, (int, float)) for v in probs.values()):
            raise BadReply(f"probs của {qid} không hợp lệ")
        out[qid] = Answer(ans, float(conf), {str(k): float(v) for k, v in probs.items()},
                    "rules" if a.get("source") == "rules" else "model")
    return Decision(out, render_state(obs), 0.0, f"remote:{reply.get('decider', '?')}")


class RemoteDecider:
    """Gọi bộ quyết định ở máy khác; lỗi thì dùng `fallback` (mặc định luật) và đặt `last_error`."""

    def __init__(self, url: str, timeout_s: float = 3.0, fallback=None, token: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token if token is not None else os.environ.get(TOKEN_ENV) or None  # không bao giờ ghi log
        self.timeout_s = timeout_s
        self.fallback = fallback or RuleDecider()
        self.name = f"remote({self.url})"
        self.last_error: str | None = None

    def decide(self, obs: dict, questions: dict) -> Decision:
        t0 = time.perf_counter()
        try:
            dec = self._ask(obs, questions)
        except Exception as e:  # noqa: BLE001 - mạng hay máy kia hỏng thì vẫn phải quyết định được
            self.last_error = f"{type(e).__name__}: {e}"
            dec = self.fallback.decide(obs, questions)
            for a in dec.answers.values():
                a.source = f"{a.source}-fallback"
            dec.decider = f"{self.name} lỗi -> {dec.decider} (dự phòng)"
        dec.latency_ms = (time.perf_counter() - t0) * 1000
        return dec

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def health(self, timeout_s: float = 3.0) -> dict:
        """GET /health (kèm token nếu có); lỗi thì ném ngoại lệ cho nơi gọi xử lý."""
        req = urllib.request.Request(f"{self.url}/health", headers=self._auth())
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.loads(r.read(MAX_BODY))

    def _ask(self, obs: dict, questions: dict) -> Decision:
        body = json.dumps({"obs": obs, "questions": questions}, default=str).encode()
        headers = {"Content-Type": "application/json", **self._auth()}
        req = urllib.request.Request(f"{self.url}/decide", body, headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            raw = r.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise BadReply("trả lời quá lớn")
        try:
            reply = json.loads(raw)
        except ValueError as e:
            raise BadReply(f"không phải JSON: {e}") from e
        dec = parse_decision(reply, questions, obs)
        # máy chủ lai tự rơi về luật khi mô hình lỗi: báo lên dashboard như HybridDecider
        err = reply.get("error")
        self.last_error = f"máy chủ: {err}" if err else None
        return dec


# --- phía máy chủ -----------------------------------------------------------------------------

def make_server(decider, host: str, port: int, log=None, token: str | None = None) -> ThreadingHTTPServer:
    """HTTP server cho một bộ quyết định. Một thể hiện mô hình, khóa quanh `decide` nên các yêu cầu
    đồng thời xếp hàng. `port=0` để hệ điều hành chọn cổng rảnh (server.server_address[1]).
    `token` đặt thì cả /health lẫn /decide phải có `Authorization: Bearer <token>`, sai thì 401."""
    lock = threading.Lock()
    log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # tắt log mặc định của http.server
            pass

        def _send(self, code: int, obj: dict) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not token:
                return True
            got = self.headers.get("Authorization", "")
            if hmac.compare_digest(got.encode(), f"Bearer {token}".encode()):
                return True
            self._send(401, {"error": "thiếu hoặc sai token"})
            return False

        def do_GET(self) -> None:
            if not self._authorized():
                return
            if self.path == "/health":
                self._send(200, {"ok": True, "decider": getattr(decider, "name", "?")})
            else:
                self._send(404, {"error": "không có đường dẫn này"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if self.path != "/decide":
                return self._send(404, {"error": "không có đường dẫn này"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= MAX_BODY:
                    return self._send(413, {"error": "thân yêu cầu rỗng hoặc quá lớn"})
                body = json.loads(self.rfile.read(n))
                obs, questions = body["obs"], body["questions"]
                if not isinstance(obs, dict) or not isinstance(questions, dict):
                    raise TypeError("obs và questions phải là object")
            except (ValueError, KeyError, TypeError) as e:
                return self._send(400, {"error": f"yêu cầu sai dạng: {e}"})
            t0 = time.perf_counter()
            try:
                with lock:
                    dec = decider.decide(obs, questions)
            except Exception as e:  # noqa: BLE001
                log(f"decide lỗi: {type(e).__name__}: {e}")
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            log(f"decide {obs.get('stage', '?')} {sorted(questions)} -> {dec.decider} "
                f"{(time.perf_counter() - t0) * 1000:.0f} ms")
            reply = dec.to_json()
            err = getattr(decider, "last_error", None)
            if err:  # bộ lai ở máy chủ vừa phải dùng luật vì mô hình lỗi
                reply["error"] = err
            self._send(200, reply)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
