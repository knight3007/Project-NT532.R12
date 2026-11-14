"""Pi tự đẩy trạng thái lên server giám sát (homelab/control) và nhận lệnh trong response.

    POST <url>/api/pi/push   Authorization: Bearer <token>
    body     {"boot", "state", "events", "results": [{"id", "reply"}]}
    response {"ack": N, "cmds": [{"id", "cmd", ...tham số}]}

Pi là HTTP client, không mở cổng nào ra ngoài. Chỉ dùng thư viện chuẩn.
"""

import json
import logging
import secrets
import threading
import urllib.error
import urllib.request

from .server import _clean, _default

log = logging.getLogger("nt532.push")
MAX_BACKOFF = 10.0


class ControlPusher:
    def __init__(self, dash, url: str, token: str, period: float = 1.0, timeout: float = 5.0):
        self.dash = dash
        self.endpoint = url.rstrip("/") + "/api/pi/push"
        self._token = token
        self.period = period
        self.timeout = timeout
        self.boot = secrets.token_hex(8)
        self.ack = 0
        self.results: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._down = False

    def start(self) -> "ControlPusher":
        self._thread = threading.Thread(target=self._run, daemon=True, name="control-push")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.timeout + 1)

    # --- một lượt đẩy ---------------------------------------------------------------------

    def push_once(self) -> list[dict]:
        """Gửi một lần; trả về danh sách lệnh nhận về (đã cập nhật ack, xóa results đã gửi)."""
        sent = list(self.results)
        body = {"boot": self.boot, "state": self.dash.state(),
                "events": self.dash.st.events.since(self.ack), "results": sent}
        data = json.dumps(_clean(body), ensure_ascii=False, default=_default).encode()
        req = urllib.request.Request(self.endpoint, data=data, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self._token}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            reply = json.loads(resp.read() or b"{}")
        del self.results[:len(sent)]
        self.ack = int(reply.get("ack", self.ack))
        return list(reply.get("cmds") or [])

    def _execute(self, cmds: list[dict]) -> None:
        for c in cmds:
            rest = {k: v for k, v in c.items() if k != "id"}
            try:
                reply = self.dash.command(rest)
            except Exception as e:  # noqa: BLE001 - lỗi lệnh thành reply, không làm chết luồng
                reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            self.results.append({"id": c.get("id"), "reply": reply})

    def _run(self) -> None:
        delay = 0.0
        while not self._stop.wait(delay):
            try:
                cmds = self.push_once()
            except urllib.error.HTTPError as e:
                delay = self._fail(f"HTTP {e.code}" + (" (sai token)" if e.code == 401 else ""),
                                   error=e.code == 401)
                continue
            except Exception as e:  # noqa: BLE001 - mạng, JSON hỏng, state() lỗi: thử lại
                delay = self._fail(f"{type(e).__name__}: {e}")
                continue
            if self._down:
                log.warning("đã nối lại control %s", self.endpoint)
                self._down = False
            self._backoff = 0.0
            self._execute(cmds)
            delay = 0.0 if self.results else self.period

    _backoff = 0.0

    def _fail(self, why: str, error: bool = False) -> float:
        if not self._down:
            (log.error if error else log.warning)("mất liên lạc với control %s: %s", self.endpoint, why)
            self._down = True
        self._backoff = min(MAX_BACKOFF, max(1.0, self._backoff * 2))
        return self._backoff
