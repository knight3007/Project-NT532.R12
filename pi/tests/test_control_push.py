"""ControlPusher: Pi tự đẩy trạng thái lên server control và nhận lệnh trong response."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from nt532.dashboard import push
from nt532.dashboard.push import ControlPusher


class FakeDash:
    def __init__(self):
        self.cmds = []
        self.hub = SimpleNamespace(latest=lambda: (1, None))
        self.st = SimpleNamespace(events=SimpleNamespace(since=self._since), hub=self.hub)
        self.since_args = []

    def _since(self, n):
        self.since_args.append(n)
        return [{"seq": n + 1, "text": "x"}]

    def state(self):
        return {"time": 1.0, "bad": float("inf")}

    def frame(self):
        import numpy as np

        return np.zeros((48, 64, 3), np.uint8)

    def command(self, body):
        self.cmds.append(body)
        if body["cmd"] == "boom":
            raise ValueError("hỏng")
        return {"ok": True, "got": body}


class FakeServer:
    def __init__(self, port=0):
        self.requests = []
        self.frames = []
        self.script = []  # mỗi phần tử là response cho lần gọi tới
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/api/pi/frame":
                    outer.frames.append((self.headers.get("Authorization"), self.headers.get("Content-Type"), raw))
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.loads(raw)
                outer.requests.append((self.path, self.headers.get("Authorization"), body))
                resp = outer.script.pop(0) if outer.script else {"ack": 7, "cmds": []}
                data = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def wait_for(cond, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_push_flow():
    srv, dash = FakeServer(), FakeDash()
    srv.script = [{"ack": 5, "cmds": [{"id": "a", "cmd": "stop"}, {"id": "b", "cmd": "boom"}]}]
    p = ControlPusher(dash, f"http://127.0.0.1:{srv.port}/", "tok", period=0.02).start()
    try:
        assert wait_for(lambda: any(r[2]["results"] for r in srv.requests))
    finally:
        p.stop()
        srv.close()
    path, auth, first = srv.requests[0]
    assert path == "/api/pi/push" and auth == "Bearer tok"
    assert first["boot"] == p.boot and first["results"] == []
    assert first["state"]["bad"] is None and first["events"] == [{"seq": 1, "text": "x"}]
    assert dash.since_args[0] == 0 and 5 in dash.since_args  # ack dùng cho lần sau
    assert dash.cmds[0] == {"cmd": "stop"}  # "id" bị bỏ
    res = next(r[2]["results"] for r in srv.requests if r[2]["results"])
    assert res[0] == {"id": "a", "reply": {"ok": True, "got": {"cmd": "stop"}}}
    assert res[1]["id"] == "b" and res[1]["reply"]["ok"] is False
    assert res[1]["reply"]["error"] == "ValueError: hỏng"
    assert wait_for(lambda: not p.results) or not p.results


def test_survives_server_down_and_reconnects(monkeypatch):
    monkeypatch.setattr(push, "MAX_BACKOFF", 0.05)
    srv = FakeServer()
    port = srv.port
    srv.close()
    dash = FakeDash()
    p = ControlPusher(dash, f"http://127.0.0.1:{port}", "tok", period=0.02, timeout=1.0)
    p._backoff = 0.0
    # backoff tối thiểu 1 s: ép nhỏ cho test
    orig = p._fail
    p._fail = lambda *a, **k: (orig(*a, **k), 0.02)[1]
    p.start()
    try:
        assert wait_for(lambda: p._down)
        assert p._thread.is_alive()
        srv = FakeServer(port)
        assert wait_for(lambda: srv.requests)
        assert wait_for(lambda: not p._down)
    finally:
        p.stop()
        srv.close()


def test_frame_thread_posts_jpeg_once_per_seq():
    srv, dash = FakeServer(), FakeDash()
    p = ControlPusher(dash, f"http://127.0.0.1:{srv.port}", "tok", period=0.02, frame_fps=50).start()
    try:
        assert wait_for(lambda: srv.frames)
        time.sleep(0.2)  # seq không đổi: không gửi lại
    finally:
        p.stop()
        srv.close()
    assert len(srv.frames) == 1
    auth, ctype, raw = srv.frames[0]
    assert auth == "Bearer tok" and ctype == "image/jpeg" and raw.startswith(b"\xff\xd8")


def test_frame_fps_zero_sends_nothing():
    srv, dash = FakeServer(), FakeDash()
    p = ControlPusher(dash, f"http://127.0.0.1:{srv.port}", "tok", period=0.02, frame_fps=0).start()
    try:
        assert wait_for(lambda: srv.requests)
        time.sleep(0.2)
    finally:
        p.stop()
        srv.close()
    assert p._frame_thread is None and srv.frames == []
