"""Pi tự đẩy trạng thái lên server giám sát (homelab/control) và nhận lệnh trong response.

    POST <url>/api/pi/push   Authorization: Bearer <token>
    body     {"boot", "state", "events", "results": [{"id", "reply"}]}
    response {"ack": N, "cmds": [{"id", "cmd", ...tham số}]}

    POST <url>/api/pi/frame  Authorization: Bearer <token>, body là JPEG thô (hình camera cho dashboard)

Pi là HTTP client, không mở cổng nào ra ngoài. Chỉ dùng thư viện chuẩn (cv2 chỉ để mã hóa JPEG).
"""

import json
import logging
import os
import secrets
import threading
import urllib.error
import urllib.request

from .server import _clean, _default

log = logging.getLogger("nt532.push")
MAX_BACKOFF = 10.0
JPEG_QUALITY = 70


def _read_json(resp) -> dict:
    try:
        out = json.loads(resp.read() or b"{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def post_jpeg(endpoint: str, token: str, jpeg: bytes, timeout: float = 5.0) -> dict:
    """POST một khung JPEG thô lên control, trả JSON trong response ({} nếu không có); lỗi mạng/HTTP thì ném ngoại lệ."""
    req = urllib.request.Request(endpoint, data=jpeg, method="POST", headers={
        "Content-Type": "image/jpeg", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _read_json(resp)


def post_json(endpoint: str, token: str, body: dict, timeout: float = 5.0) -> dict:
    """POST một object JSON lên control, trả JSON trong response."""
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _read_json(resp)


def describe_error(e: Exception) -> tuple[str, bool]:
    """(mô tả không chứa token, có phải lỗi xác thực)."""
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}" + (" (sai token)" if e.code == 401 else ""), e.code == 401
    return f"{type(e).__name__}: {e}", False


class ControlPusher:
    def __init__(self, dash, url: str, token: str, period: float = 1.0, timeout: float = 5.0,
                 frame_fps: float | None = None):
        self.dash = dash
        self.endpoint = url.rstrip("/") + "/api/pi/push"
        self.frame_endpoint = url.rstrip("/") + "/api/pi/frame"
        if frame_fps is None:
            frame_fps = float(os.environ.get("NT532_CONTROL_FPS", "2"))
        self.frame_fps = frame_fps  # 0 = không đẩy hình
        self._token = token
        self.period = period
        self.timeout = timeout
        self.boot = secrets.token_hex(8)
        self.ack = 0
        self.results: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_thread: threading.Thread | None = None
        self._down = False
        self._frame_down = False
        self._frame_backoff = 0.0

    def start(self) -> "ControlPusher":
        self._thread = threading.Thread(target=self._run, daemon=True, name="control-push")
        self._thread.start()
        if self.frame_fps > 0:
            self._frame_thread = threading.Thread(target=self._run_frames, daemon=True, name="control-frames")
            self._frame_thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for t in (self._thread, self._frame_thread):
            if t:
                t.join(timeout=self.timeout + 1)

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

    # --- hình camera ----------------------------------------------------------------------

    def _run_frames(self) -> None:
        import cv2  # import lười: chỉ cần khi có đẩy hình

        period = 1.0 / self.frame_fps
        last_seq = None
        delay = 0.0
        while not self._stop.wait(delay):
            delay = period
            try:
                seq = self.dash.st.hub.latest()[0]
                if seq == last_seq:
                    continue
                img = self.dash.frame()
                if img is None:
                    continue
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if not ok:
                    continue
                post_jpeg(self.frame_endpoint, self._token, buf.tobytes(), self.timeout)
                last_seq = seq
            except Exception as e:  # noqa: BLE001 - mạng, mã hóa hỏng: thử lại
                why, auth = describe_error(e)
                if not self._frame_down:
                    (log.error if auth else log.warning)("không đẩy được hình lên %s: %s", self.frame_endpoint, why)
                    self._frame_down = True
                self._frame_backoff = min(MAX_BACKOFF, max(1.0, self._frame_backoff * 2))
                delay = self._frame_backoff
                continue
            if self._frame_down:
                log.warning("đã đẩy hình lại được lên %s", self.frame_endpoint)
                self._frame_down = False
            self._frame_backoff = 0.0
