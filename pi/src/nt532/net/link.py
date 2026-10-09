"""Giao diện orchestrator dùng để ra lệnh cho node chấp hành, độc lập với cách truyền.

`NodeLink` giữ phần chung: cấp id lệnh, ghi nhận `/status` trả về, chờ trạng thái, đo tuổi
heartbeat. Lớp con chỉ cần cài `_send` (gửi một bản tin tới node). `SimLink` (nt532.sim.world) cài
trên sa bàn ảo; `CoapLink` (nt532.net.coap) cài bằng aiocoap.
"""

import itertools
import threading
import time
from collections.abc import Callable

from .protocol import Aim, Fire, Info, Status, Stop

FINAL = ("reached", "done", "rejected", "fault")


class LinkError(RuntimeError):
    """Gửi lệnh không thành công (không có đường tới node, node không trả lời, bị từ chối...)."""


class NodeLink:
    def __init__(self, nodes: list[str], clock: Callable[[], float] = time.monotonic) -> None:
        self.nodes = list(nodes)
        self.clock = clock
        self._ids = itertools.count(1)
        self._cv = threading.Condition()
        self._status: dict[int, Status] = {}
        self._last_rx: dict[str, float] = {}
        self.listeners: list[Callable[[str, object], None]] = []  # (node, bản tin) mỗi lần gửi/nhận

    # --- lớp con cài ------------------------------------------------------------------------

    def _send(self, node: str, path: str, msg) -> None:
        raise NotImplementedError

    # --- lệnh -------------------------------------------------------------------------------

    def aim(self, node: str, pan: float, tilt: float, ttl_ms: int) -> int:
        cmd = Aim(next(self._ids), pan, tilt, int(ttl_ms))
        self._post(node, "aim", cmd)
        return cmd.id

    def fire(self, node: str, cmd_id: int, dev: str, ms: int) -> None:
        self._post(node, "fire", Fire(cmd_id, dev, int(ms)))

    def stop(self, node: str | None = None) -> None:
        """Tắt laser và bơm của một node, hoặc mọi node. Lỗi gửi chỉ ghi lại, không ném ra."""
        for n in [node] if node else self.nodes:
            try:
                self._post(n, "stop", Stop(next(self._ids)))
            except LinkError as e:
                self._notify(n, f"stop lỗi: {e}")

    def heartbeat(self, node: str) -> None:
        """`/hb` (không xác nhận, payload rỗng)."""
        self._send(node, "hb", None)

    def info(self, node: str, timeout: float = 2.0) -> Info | None:
        """`GET /info` của node; None nếu link không hỗ trợ (sa bàn ảo trong bộ nhớ). Lỗi mạng ném LinkError."""
        if node not in self.nodes:
            raise LinkError(f"không biết node {node!r}")
        return self._get_info(node, timeout)

    def _get_info(self, node: str, timeout: float) -> Info | None:
        return None

    def _post(self, node: str, path: str, msg) -> None:
        if node not in self.nodes:
            raise LinkError(f"không biết node {node!r}")
        self._notify(node, (path, msg))
        self._send(node, path, msg)

    # --- nhận -------------------------------------------------------------------------------

    def on_status(self, node: str, st: Status) -> None:
        """Gọi khi nhận `/status` (từ server CoAP hoặc từ sim)."""
        with self._cv:
            self._status[st.id] = st
            self._last_rx[node] = self.clock()
            self._cv.notify_all()
        self._notify(node, ("status", st))

    def on_rx(self, node: str) -> None:
        """Gọi khi nhận bất kỳ bản tin nào từ node (telemetry, cảnh báo, trả lời heartbeat)."""
        with self._cv:
            self._last_rx[node] = self.clock()

    def wait(self, cmd_id: int, states=FINAL, timeout: float = 2.0) -> Status | None:
        """Chờ tới khi lệnh `cmd_id` có trạng thái thuộc `states`; None nếu hết giờ."""
        deadline = self.clock() + timeout
        with self._cv:
            while True:
                st = self._status.get(cmd_id)
                if st is not None and st.st in states:
                    return st
                left = deadline - self.clock()
                if left <= 0:
                    return None
                self._cv.wait(min(left, 0.05))

    def hb_age_ms(self, node: str) -> float:
        """Thời gian từ lần cuối nghe thấy node, ms; vô cùng nếu chưa từng nghe."""
        with self._cv:
            t = self._last_rx.get(node)
        return float("inf") if t is None else (self.clock() - t) * 1000

    def _notify(self, node: str, item) -> None:
        for fn in self.listeners:
            fn(node, item)


class HeartbeatSender(threading.Thread):
    """Gửi `/hb` tới mọi node mỗi `period_ms` (node mất 3 nhịp thì tự tắt bơm và laser)."""

    def __init__(self, send: Callable[[str], None], nodes: list[str], period_ms: int = 500):
        super().__init__(daemon=True, name="heartbeat")
        self.send, self.nodes, self.period = send, list(nodes), period_ms / 1000
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.wait(self.period):
            for n in self.nodes:
                try:
                    self.send(n)
                except Exception:  # noqa: BLE001, S110 - một node lỗi không được dừng nhịp của node khác
                    pass

    def halt(self) -> None:
        self._halt.set()
