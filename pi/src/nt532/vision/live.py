"""Nhận diện lửa nền: chạy YOLO vài lần mỗi giây lúc trạm rảnh để dashboard luôn thấy hộp lửa.

Orchestrator chỉ chạy YOLO trong pha LOCALIZE của một lượt, nên lúc IDLE khung hình không có gì để vẽ.
Luồng này lấy khung mới nhất từ hub, gọi `vision.detect` và `vision.localize_detection`, giữ kết quả mới
nhất kèm thời điểm. Nó chỉ chạy khi orchestrator ở IDLE (không tranh CPU/camera với lượt thật) và nuốt mọi
ngoại lệ để không làm sập trạm.
"""

import logging
import threading
import time

log = logging.getLogger("nt532.live")
FRESH_S = 1.5  # kết quả cũ hơn thế thì không vẽ, không báo


class LiveDetector:
    def __init__(self, station, hz: float = 5.0, clock=time.monotonic):
        self.st = station
        self.hz = hz
        self.clock = clock
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
        if st.orch.phase.value != "IDLE" or vision.state is None or vision.blockers():
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
        return True

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
