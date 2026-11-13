"""Node giả chạy lõi C của firmware node B tích hợp (firmware/node-b-integrated/main) sau server CoAP aiocoap.

Lõi C thật là `integrated_control.c` (ARM/HOME/target, watchdog heartbeat, ramp 20 µs mỗi 20 ms, cổng laser)
và `probe_state.c` (/info /hb /status /stop), biên dịch thành `integrated.dll` (Windows) hoặc
`libintegrated.so` (Linux) cùng lớp mỏng `core_shim.c`, gọi qua ctypes. Phần Python chỉ làm việc của lớp
ESP-IDF trong `app_main.c`: tài nguyên CoAP và mã trả lời (`on_request`, `pantilt_post`, `laser_post`),
JSON của /info /status /pantilt /laser, và vòng servo 20 ms (`servo_task`: watchdog, bật PWM khi nhận HOME,
bước ramp) đẩy ra phần cứng giả.

Hợp đồng CoAP khác hợp đồng cũ của orchestrator (`/aim /fire` JSON): xem `integrated_link.py` cho adapter
phía Pi. Góc ra phần cứng giả đổi từ µs qua `PulseMap`, bảng ánh xạ TẠM THỜI (chưa có số đo thật).

LỚP SIM-ONLY (KHÔNG có trong firmware của Hậu): firmware node B tích hợp không đẩy `/t` hay `/a` lên Pi
(chỉ đọc DHT11 để trả trong /status). Để sa bàn ảo chạy được một chu trình cảnh báo, node giả có thêm luồng
đọc `sensors.read()` mỗi mẫu, POST `/t` mỗi `telemetry_s` và POST `/a` (alert/clear) theo cùng luật báo động
của sensor-h2 (nhiệt hoặc gas vượt ngưỡng 3 mẫu liên tiếp thì alert, 5 mẫu dưới ngưỡng thì clear).
"""

import asyncio
import ctypes as C
import json
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from aiocoap import CHANGED, CONTENT, POST, Context, Message, resource
from aiocoap.numbers.codes import (
    BAD_REQUEST,
    FORBIDDEN,
    INTERNAL_SERVER_ERROR,
    METHOD_NOT_ALLOWED,
    REQUEST_ENTITY_TOO_LARGE,
)

from ..config import REPO_ROOT
from .fakenode import ConstSensors, CoreUnavailable, RecordHardware
from .protocol import Alert, Telemetry, dumps

TEST_DIR = REPO_ROOT / "firmware/node-b-integrated/test"
CORE_DIR = REPO_ROOT / "firmware/node-b-integrated/main"
LIB_NAME = "integrated.dll" if sys.platform == "win32" else "libintegrated.so"
STEP_PERIOD_S = 0.02  # như servo_task: chờ tối đa 20 ms mỗi vòng
BODY_MAX = 16  # như pantilt_post/laser_post: payload dài hơn thì 4.13

# resource của probe_state.h và hai resource thêm trong app_main.c
INFO, HB, STATUS, STOP, PANTILT, LASER = range(6)
PATHS = {"info": INFO, "hb": HB, "status": STATUS, "stop": STOP, "pantilt": PANTILT, "laser": LASER}
GET_M, POST_M = 1, 2
CODES = {68: CHANGED, 69: CONTENT, 133: METHOD_NOT_ALLOWED}
PT_CODES = {0: CHANGED, 1: BAD_REQUEST, 2: FORBIDDEN, 3: FORBIDDEN}  # DISABLED cũng là 4.03 như app_main.c
FLAG_ATTACHED, FLAG_ARMED, FLAG_HOME, FLAG_PWM, FLAG_LASER, FLAG_HB = 1, 2, 4, 8, 16, 32
CONTENT_JSON = 50


# --- thư viện C ------------------------------------------------------------------------------

class IntegratedCore:
    """integrated.dll / libintegrated.so: lõi C của firmware node B tích hợp. `IntegratedCore.get()` nạp
    một lần; trên Linux tự `make` nếu thiếu hoặc cũ hơn mã nguồn, trên Windows chỉ nạp file có sẵn."""

    _inst: "IntegratedCore | None" = None
    _lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self.so = so = C.CDLL(str(path))
        p, u64, i32, i64 = C.c_void_p, C.c_uint64, C.c_int, C.c_int64
        ip, cp, s = C.POINTER(C.c_int), C.c_char_p, C.c_size_t
        sig = {
            "ic_new": (p, []),
            "ic_free": (None, [p]),
            "ic_set_attached": (None, [p, i32]),
            "ic_probe": (i32, [p, i32, i32, u64]),
            "ic_pantilt_post": (i32, [p, cp, s, u64]),
            "ic_laser_post": (i32, [p, cp, s, u64]),
            "ic_watchdog": (i32, [p, u64]),
            "ic_pwm_started": (i32, [p]),
            "ic_next_step": (i32, [p, u64, ip, ip]),
            "ic_snapshot": (None, [p, ip, ip, ip, ip, ip]),
            "ic_hb_age_ms": (i64, [p, u64]),
            "ic_hb_count": (C.c_uint32, [p]),
            "ic_stop_count": (C.c_uint32, [p]),
            "ic_pantilt_json": (s, [p, cp, s]),
            "ic_hb_timeout_ms": (i32, []),
            "ic_limits": (i32, [ip] * 7),
        }
        for name, (res, args) in sig.items():
            fn = getattr(so, name)
            fn.restype, fn.argtypes = res, args
        v = [C.c_int() for _ in range(7)]
        so.ic_limits(*[C.byref(x) for x in v])
        self.pan_us = (v[0].value, v[1].value)
        self.tilt_us = (v[2].value, v[3].value)
        self.home_us, self.step_us, self.step_period_ms = v[4].value, v[5].value, v[6].value
        self.hb_timeout_ms = so.ic_hb_timeout_ms()

    @classmethod
    def get(cls) -> "IntegratedCore":
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls(cls.build())
            return cls._inst

    @staticmethod
    def build() -> Path:
        lib = TEST_DIR / LIB_NAME
        if sys.platform == "win32":
            if lib.exists():
                return lib
            raise CoreUnavailable(
                f"chưa có {lib}: build bằng mingw (ví dụ trong Docker có x86_64-w64-mingw32-gcc): "
                f"make -C firmware/node-b-integrated/test integrated.dll")
        srcs = [*CORE_DIR.glob("*.[ch]"), TEST_DIR / "core_shim.c", TEST_DIR / "Makefile"]
        if lib.exists() and all(lib.stat().st_mtime >= f.stat().st_mtime for f in srcs):
            return lib
        cc = "/usr/bin/gcc" if Path("/usr/bin/gcc").exists() else shutil.which("gcc")
        if cc is None:
            raise CoreUnavailable("cần gcc để biên dịch lõi node B tích hợp (libintegrated.so); cài gcc hoặc "
                                  "build tay: make -C firmware/node-b-integrated/test libintegrated.so")
        r = subprocess.run(["make", "-C", str(TEST_DIR), LIB_NAME, f"CC={cc}"],
                           capture_output=True, text=True, timeout=300, check=False)
        if r.returncode != 0 or not lib.exists():
            raise CoreUnavailable(f"biên dịch {LIB_NAME} lỗi:\n{r.stdout}{r.stderr}")
        return lib


# --- ánh xạ độ <-> µs --------------------------------------------------------------------------

class PulseMap:
    """Ánh xạ độ <-> độ rộng xung servo (µs), TUYẾN TÍNH: `us = home_us + sign * deg / deg_per_us`.

    TẠM THỜI, chỉ cho sa bàn ảo. Chưa có số đo góc–xung thật của servo pan/tilt (bàn giao 13/11 mục E):
    `deg_per_us = 0.09` chỉ là giả định 180° trên 2000 µs, `home_us = 1500` ứng với 0°, dấu +1 cả hai trục.
    PHẢI thay bằng bảng đo (không nhất thiết tuyến tính, có thể lệch tâm hay đảo dấu) trước khi dùng trên
    board thật, nếu không Pi sẽ ngắm lệch.

    `limits_us` mặc định lấy từ hằng số firmware (integrated_control.h): pan 500–2500, tilt 600–2500.
    """

    def __init__(self, deg_per_us: float = 0.09, home_us: int = 1500, pan_sign: int = 1, tilt_sign: int = 1,
                 limits_us: dict[str, tuple[int, int]] | None = None) -> None:
        self.deg_per_us, self.home_us = deg_per_us, home_us
        self.sign = {"pan": pan_sign, "tilt": tilt_sign}
        self.limits_us = limits_us or {"pan": (500, 2500), "tilt": (600, 2500)}

    def _axis(self, axis: str) -> str:
        if axis not in self.sign:
            raise ValueError(f"trục phải là pan hoặc tilt, nhận {axis!r}")
        return axis

    def to_us(self, axis: str, deg: float) -> int:
        """Độ -> µs (làm tròn); ngoài biên của firmware thì ValueError."""
        axis = self._axis(axis)
        us = round(self.home_us + self.sign[axis] * deg / self.deg_per_us)
        lo, hi = self.limits_us[axis]
        if not lo <= us <= hi:
            raise ValueError(f"{axis} {deg:.2f}° ứng với {us} µs, ngoài [{lo}, {hi}]")
        return us

    def to_deg(self, axis: str, us: float) -> float:
        axis = self._axis(axis)
        return self.sign[axis] * (us - self.home_us) * self.deg_per_us

    def limits_deg(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """((pan_min, pan_max), (tilt_min, tilt_max)) theo độ."""
        out = []
        for axis in ("pan", "tilt"):
            a, b = (self.to_deg(axis, u) for u in self.limits_us[axis])
            out.append((min(a, b), max(a, b)))
        return out[0], out[1]


# --- node giả ----------------------------------------------------------------------------------

def _ms() -> int:
    return int(time.monotonic() * 1000)


class IntegratedFakeNode:
    """Server CoAP của node B tích hợp (`/info /status /hb /stop /pantilt /laser`, payload text như firmware)
    chạy trên lõi C thật, cộng lớp telemetry sim-only (xem docstring module).

    `pi`: URI gốc của Pi (None: không gửi /t /a). `relay_dev`: thiết bị của phần cứng giả mà relay điều
    khiển ("laser": relay chỉ mô phỏng laser theo bàn giao; không nối bơm vào relay). Giả lỗi: `online=False`
    (đứt mạng hai chiều, không trả lời, không gửi; vòng servo vẫn chạy watchdog), `set_attached(False)` (giả
    mất Thread: lõi C tắt PWM, relay, xoá ARM).
    """

    def __init__(self, name: str, bind: tuple[str, int] = ("127.0.0.1", 5683), pi: str | None = None,
                 hardware: RecordHardware | None = None, sensors=None, telemetry_s: float = 1.0,
                 sample_s: float = 1.0, temp_c: float = 45.0, gas: float = 600.0, warmup_s: float = 0.0,
                 pulse_map: PulseMap | None = None, relay_dev: str = "laser",
                 transports: list[str] | None = None, on_event: Callable[[str, str], None] | None = None,
                 fw: str = "sim-integrated-0.2.4") -> None:
        self.name, self.bind, self.pi, self.fw = name, bind, pi, fw
        self.hw = hardware or RecordHardware()
        self.sensors = sensors or ConstSensors()
        self.telemetry_s, self.sample_s = telemetry_s, sample_s
        self.alarm_cfg = (temp_c, gas, warmup_s)
        self.map = pulse_map or PulseMap()
        self.relay_dev = relay_dev
        self.transports, self.on_event = transports, on_event
        self.online = True
        self.rx_count: dict[str, int] = {}
        self.tx_errors = 0
        self._core = IntegratedCore.get()
        self._ic = self._core.so.ic_new()
        self._lock = threading.Lock()  # mọi lời gọi ic_*
        self._relay_lock = threading.Lock()
        self._relay = False
        self._reading: tuple[float, float, float | None] | None = None
        self._halt = threading.Event()
        self._t0 = time.monotonic()
        self._seq = 0
        self._loop = asyncio.new_event_loop()
        self._ctx: Context | None = None
        self._closed: asyncio.Event | None = None
        self._ready = threading.Event()
        self._err: str | None = None
        self._threads = [threading.Thread(target=self._run_loop, daemon=True, name=f"in-{name}-coap"),
                         threading.Thread(target=self._servo_loop, daemon=True, name=f"in-{name}-servo"),
                         threading.Thread(target=self._sensor_loop, daemon=True, name=f"in-{name}-sens")]
        self.set_attached(True)  # coi như Thread đã attach

    def _emit(self, kind: str, text: str) -> None:
        if self.on_event:
            self.on_event(kind, text)

    @property
    def url(self) -> str:
        host, port = self.bind
        return f"coap://{f'[{host}]' if ':' in host else host}:{port}"

    # --- vòng đời ----------------------------------------------------------------------------

    def start(self) -> "IntegratedFakeNode":
        for t in self._threads:
            t.start()
        if not self._ready.wait(10) or self._err:
            raise OSError(self._err or "không khởi động được node giả")
        return self

    def stop(self) -> None:
        if self._halt.is_set():
            return
        self._halt.set()
        if self._ctx is not None:
            self._loop.call_soon_threadsafe(self._closed.set)
            asyncio.run_coroutine_threadsafe(self._ctx.shutdown(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        for t in self._threads:
            t.join(2)
        self.set_attached(False)  # tắt mọi thứ như detach
        self.hw.close()
        with self._lock:
            self._core.so.ic_free(self._ic)
            self._ic = None

    def set_attached(self, attached: bool) -> None:
        """Giả attach/detach Thread. Detach: lõi C xoá ARM, tắt PWM và relay."""
        with self._lock:
            self._core.so.ic_set_attached(self._ic, int(attached))
        self._sync_relay()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.run_forever()

    async def _serve(self) -> None:
        self._closed = asyncio.Event()
        root = resource.Site()
        for path in PATHS:
            root.add_resource([path], _Res(self, path))
        try:
            self._ctx = await Context.create_server_context(root, bind=self.bind, transports=self.transports)
        except OSError as e:
            self._err = f"không mở được cổng CoAP {self.bind}: {e}"
        self._ready.set()

    # --- trạng thái ----------------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Trạng thái lõi C: cờ, xung hiện tại và đích (µs)."""
        v = [C.c_int() for _ in range(5)]
        with self._lock:
            self._core.so.ic_snapshot(self._ic, *(C.byref(x) for x in v))
        f = v[0].value
        return {"attached": bool(f & FLAG_ATTACHED), "armed": bool(f & FLAG_ARMED),
                "home_requested": bool(f & FLAG_HOME), "pwm_active": bool(f & FLAG_PWM),
                "laser_on": bool(f & FLAG_LASER), "hb_seen": bool(f & FLAG_HB),
                "pan_us": v[1].value, "tilt_us": v[2].value,
                "target_pan_us": v[3].value, "target_tilt_us": v[4].value}

    def _sync_relay(self) -> None:
        """Như relay_apply_locked: đưa relay (laser giả) về đúng `laser_on` của lõi, chỉ gọi phần cứng khi đổi."""
        with self._relay_lock:
            on = self.snapshot()["laser_on"]
            if on != self._relay:
                self._relay = on
                self.hw.set_dev(self.relay_dev, on)

    # --- server (app_main.c: on_request) ---------------------------------------------------------

    async def handle(self, path: str, method: int, payload: bytes) -> Message:
        self.rx_count[path] = self.rx_count.get(path, 0) + 1
        if not self.online:
            self._emit("drop", f"/{path}")
            await self._closed.wait()  # nằm im: Pi chờ hết giờ
        res, so, now = PATHS[path], self._core.so, _ms()
        if res in (INFO, HB, STATUS, STOP):
            with self._lock:
                code = so.ic_probe(self._ic, res, method, now)
            if code == 68 and res == STOP:
                self._sync_relay()
                self._emit("rx", "/stop")
            if code != 69:
                return Message(code=CODES.get(code, INTERNAL_SERVER_ERROR))
            body = self.info_json() if res == INFO else self.status_json()
            return Message(code=CONTENT, payload=body.encode(), content_format=CONTENT_JSON)
        if method == GET_M:
            body = self.pantilt_json() if res == PANTILT else self.laser_json()
            return Message(code=CONTENT, payload=body.encode(), content_format=CONTENT_JSON)
        if method != POST_M:
            return Message(code=METHOD_NOT_ALLOWED)
        if len(payload) > BODY_MAX:
            return Message(code=REQUEST_ENTITY_TOO_LARGE)
        fn = so.ic_pantilt_post if res == PANTILT else so.ic_laser_post
        with self._lock:
            result = fn(self._ic, payload, len(payload), now)
        if res == LASER:
            self._sync_relay()
        self._emit("rx", f"/{path} {payload.decode(errors='replace')} -> {result}")
        return Message(code=PT_CODES.get(result, INTERNAL_SERVER_ERROR))

    def info_json(self) -> str:
        return json.dumps({"n": self.name, "fw": self.fw, "mode": "integrated",
                           "outputs": "servo_gated;relay_led_simulation;laser_disabled"}, separators=(",", ":"))

    def status_json(self) -> str:
        so, now = self._core.so, _ms()
        with self._lock:
            age = so.ic_hb_age_ms(self._ic, now)
            stops = so.ic_stop_count(self._ic)
        s = self.snapshot()
        fresh = s["hb_seen"] and 0 <= age <= self._core.hb_timeout_ms
        d = {"n": self.name, "role": "router" if s["attached"] else "detached", "stopped": not s["pwm_active"],
             "hb_age_ms": age, "hb_fresh": fresh, "stop_count": stops, "servo_armed": s["armed"],
             "servo_pwm": s["pwm_active"], "relay_on": s["laser_on"], "laser_on": False}
        r = self._reading
        if r is None:
            d.update(sensor_ok=False, temperature_c=None, humidity_pct=None)
        else:
            d.update(sensor_ok=True, temperature_c=r[0], humidity_pct=r[2])
        return json.dumps(d, separators=(",", ":"))

    def pantilt_json(self) -> str:
        buf = C.create_string_buffer(512)
        with self._lock:
            n = self._core.so.ic_pantilt_json(self._ic, buf, 512)
        return buf.raw[:n].decode()

    def laser_json(self) -> str:
        on = self.snapshot()["laser_on"]
        return json.dumps({"available": True, "mode": "relay_led_simulation", "on": on}, separators=(",", ":"))

    # --- luồng servo (app_main.c: servo_task) ----------------------------------------------------

    def _servo_loop(self) -> None:
        so, home = self._core.so, self._core.home_us
        pan, tilt = C.c_int(), C.c_int()
        while not self._halt.wait(STEP_PERIOD_S):
            started = step = False
            snap = [C.c_int() for _ in range(5)]
            with self._lock:
                if so.ic_watchdog(self._ic, _ms()):
                    self._emit("servo", "hết heartbeat hoặc detach, dừng PWM")
                so.ic_snapshot(self._ic, *(C.byref(x) for x in snap))
                flags = snap[0].value
                if flags & FLAG_HOME and not flags & FLAG_PWM:
                    started = bool(so.ic_pwm_started(self._ic))
                elif flags & FLAG_PWM:
                    step = bool(so.ic_next_step(self._ic, _ms(), C.byref(pan), C.byref(tilt)))
            self._sync_relay()
            if started:  # servo_output_start_home: xung về HOME
                self.hw.set_angles(self.map.to_deg("pan", home), self.map.to_deg("tilt", home))
            elif step:
                self.hw.set_angles(self.map.to_deg("pan", pan.value), self.map.to_deg("tilt", tilt.value))

    # --- lớp sim-only: telemetry và cảnh báo (KHÔNG có trong firmware) ---------------------------

    def _sensor_loop(self) -> None:
        temp_c, gas, warmup_s = self.alarm_cfg
        every = max(1, round(self.telemetry_s / self.sample_s))
        over = under = tick = 0
        active = False
        t, g, h = 25.0, 0.0, None
        first = True
        while first or not self._halt.wait(self.sample_s):
            first = False
            try:
                t, g, h = self.sensors.read()
                self._reading = (t, g, h)
            except Exception as e:  # noqa: BLE001 - lỗi đọc: giữ số cũ
                self._emit("sensor", f"lỗi đọc: {e}")
            up = time.monotonic() - self._t0
            bad = t >= temp_c or (up >= warmup_s and g >= gas)
            ev = None
            if bad:
                under, over = 0, over + 1
                if not active and over >= 3:
                    active, ev = True, "alert"
            else:
                over, under = 0, under + 1
                if active and under >= 5:
                    active, ev = False, "clear"
            if ev:
                self._seq += 1
                self._emit("alarm", f"{ev} t={t:.1f} g={g:.0f} s={self._seq}")
                self._tx("a", dumps(Alert(self.name, self._seq, ev, float(t), float(g))))
            tick += 1
            if tick % every == 0:
                self._seq += 1
                self._tx("t", dumps(Telemetry(self.name, self._seq, int(up), float(t), float(g),
                                              None if h is None else float(h))))

    def _tx(self, kind: str, payload: bytes) -> None:
        if self.pi and self.online and self._ctx is not None and not self._halt.is_set():
            self._loop.call_soon_threadsafe(self._send, kind, payload)

    def _send(self, kind: str, payload: bytes) -> None:
        msg = Message(code=POST, payload=payload, uri=f"{self.pi}/{kind}")
        fut = self._ctx.request(msg).response
        fut.add_done_callback(lambda f, k=kind: self._tx_done(k, f))

    def _tx_done(self, kind: str, fut) -> None:
        if fut.cancelled():
            return
        if fut.exception() is not None:
            self.tx_errors += 1
            self._emit("tx", f"/{kind} không tới đích: {type(fut.exception()).__name__}")


class _Res(resource.Resource):
    def __init__(self, node: IntegratedFakeNode, path: str) -> None:
        super().__init__()
        self.node, self.path = node, path

    async def render_get(self, request):
        return await self.node.handle(self.path, GET_M, request.payload)

    async def render_post(self, request):
        return await self.node.handle(self.path, POST_M, request.payload)
