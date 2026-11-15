"""Nhận diện lửa nền: chạy YOLO vài lần mỗi giây lúc trạm rảnh để dashboard luôn thấy hộp lửa.

Orchestrator chỉ chạy YOLO trong pha LOCALIZE của một lượt, nên lúc IDLE khung hình không có gì để vẽ.
Luồng này lấy khung mới nhất từ hub, gọi `vision.detect` và `vision.localize_detection`, giữ kết quả mới
nhất kèm thời điểm. Nó chỉ chạy khi orchestrator ở IDLE (không tranh CPU/camera với lượt thật) và nuốt mọi
ngoại lệ để không làm sập trạm.
"""

import logging
import threading
import time

import numpy as np

log = logging.getLogger("nt532.live")
FRESH_S = 1.5  # kết quả cũ hơn thế thì không vẽ, không báo
SAME_FIRE_M = 0.1  # hộp ở khung sau cách hộp khung trước dưới mức này (m) thì coi là cùng một đám lửa


class LiveDetector:
    def __init__(self, station, hz: float = 5.0, clock=time.monotonic, auto: dict | None = None):
        """`auto`: {trigger_s, gap_s, cooldown_s, conf}; None = không tự kích hoạt (xem `_auto_trigger`)."""
        self.st = station
        self.hz = hz
        self.clock = clock
        self.auto = auto
        self._prev: list[np.ndarray] = []  # điểm lửa đã định vị ở khung gần nhất thấy lửa
        self._since: float | None = None  # lúc bắt đầu thấy lửa liên tục
        self._last_seen = 0.0
        self._cool_until = float("-inf")  # trước lúc này không được tự kích hoạt
        self._lock = threading.Lock()
        self._result: tuple[float, list[dict]] | None = None  # (thời điểm, [{conf, box, point, surface}])
        self._seq = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "LiveDetector":
        if self.hz > 0:
            self._thread = threading.Thread(target=self._run, daemon=True, name="live-detect")
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        period = 1.0 / self.hz
        delay = period
        while not self._stop.wait(delay):
            t0 = self.clock()
            try:
                self.step()
            except Exception as e:  # noqa: BLE001 - nhận diện nền không được làm sập trạm
                log.warning("nhận diện nền lỗi: %s: %s", type(e).__name__, e)
            delay = max(0.0, period - (self.clock() - t0))  # trừ thời gian suy luận để đạt gần đúng hz

    def step(self) -> bool:
        """Một lượt nhận diện; False nếu bỏ qua (bận, chưa commissioning, không có khung mới)."""
        st = self.st
        vision = st.vision
        if st.orch.phase.value != "IDLE":
            # lượt đang chạy: bỏ đếm, và thời gian chờ chỉ bắt đầu tính từ lúc lượt kết thúc
            self._reset_track()
            if self.auto:
                self._cool_until = max(self._cool_until, self.clock() + self.auto["cooldown_s"])
            return False
        if vision.state is None or vision.blockers():
            return False
        seq, frame = st.hub.latest()
        if frame is None or seq == self._seq:
            return False
        self._seq = seq
        out = []
        for d in vision.detect(frame):
            hit = vision.localize_detection(d)
            out.append({"conf": float(d.conf),
                        "box": [float(d.u - d.w / 2), float(d.v - d.h / 2), float(d.u + d.w / 2),
                                float(d.v + d.h / 2)],
                        "anchor": [float(d.u), float(d.v + d.h / 2)],
                        "point": None if hit is None else [float(v) for v in hit[0]],
                        "surface": None if hit is None else hit[1]})
        with self._lock:
            self._result = (self.clock(), out)
        if self.auto:
            self._auto_trigger(self.clock(), out)
        return True

    def _reset_track(self) -> None:
        self._prev, self._since = [], None

    def _seen_s(self, now: float) -> float:
        if self._since is None or now - self._last_seen > self.auto["gap_s"]:
            return 0.0
        return max(0.0, now - self._since)

    def _auto_trigger(self, now: float, out: list[dict]) -> None:
        """Thấy lửa liên tục đủ `trigger_s` (không cần cảm biến) thì giả một báo động của node gần nhất để
        orchestrator chạy một lượt. Lỡ một khung (không quá `gap_s`) vẫn tính là liên tục."""
        cfg = self.auto
        pts = [np.asarray(d["point"], float) for d in out if d["point"] is not None and d["conf"] >= cfg["conf"]]
        if pts:
            same = any(np.linalg.norm(p - q) < SAME_FIRE_M for p in pts for q in self._prev)
            if self._since is None or now - self._last_seen > cfg["gap_s"] or (self._prev and not same):
                self._since = now  # bắt đầu đếm lại: lửa mới hoặc đã mất quá lâu
            self._prev, self._last_seen = pts, now
        elif self._since is not None and now - self._last_seen > cfg["gap_s"]:
            self._reset_track()
        seen = self._seen_s(now)
        orch = self.st.orch
        if (seen < cfg["trigger_s"] or now < self._cool_until or not getattr(orch, "enabled", True)
                or orch.phase.value != "IDLE"):
            return
        p = self._prev[0]
        self._fire(seen, p)
        self._cool_until = now + cfg["cooldown_s"]
        self._reset_track()

    def _fire(self, seen: float, p: np.ndarray) -> None:
        from ..net.protocol import Alert
        from ..orchestrator.fusion import alarm_limits
        st = self.st
        nodes = getattr(st.vision, "nodes", None) or {}
        node = (min(nodes, key=lambda n: float(np.linalg.norm(np.asarray(nodes[n].position, float)[:2] - p[:2])))
                if nodes else "s1")
        last = st.sensors.samples(node, 1)
        temp = float(last[-1].temp) if last else 0.0
        gas = max(float(last[-1].gas) if last else 0.0, alarm_limits(st.site)["gas"] + 1.0)
        st.events.emit("alert", f"camera thấy lửa liên tục {seen:.1f} s ở ({p[0]:.2f}, {p[1]:.2f}), "
                       f"tự kích hoạt cho {node}", level="warn", node=node, source="camera")
        st.orch.on_alert(Alert(node, -1, "alert", temp, gas))

    def auto_status(self) -> dict | None:
        """Cho dashboard: đã thấy lửa bao lâu, cần bao lâu, còn bao lâu nữa mới được kích lại."""
        if not self.auto:
            return None
        now = self.clock()
        return {"enabled": True, "seen_s": round(self._seen_s(now), 1), "need_s": self.auto["trigger_s"],
                "next_allowed_in_s": round(max(0.0, self._cool_until - now), 1)}

    def latest(self, max_age_s: float = FRESH_S) -> list[dict]:
        """Hộp còn tươi, mỗi hộp thêm `age_s`; rỗng nếu kết quả quá cũ."""
        with self._lock:
            res = self._result
        if res is None:
            return []
        age = self.clock() - res[0]
        if age > max_age_s:
            return []
        return [{**d, "age_s": round(age, 2)} for d in res[1]]
