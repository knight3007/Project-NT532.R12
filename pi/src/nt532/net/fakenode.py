"""Node giả chạy lõi C thật của firmware (firmware/sensor-h2/main/core) sau một server CoAP aiocoap.

Dùng để thử Pi qua UDP thật khi chưa có H2: payload do bộ parse/tạo của firmware kiểm và sinh,
state machine chấp hành và logic báo động cũng là mã C của firmware (biên dịch thành libcore.so,
gọi qua ctypes và lớp mỏng firmware/sensor-h2/test/core_shim.c). Phần Python chỉ làm việc của lớp
ESP-IDF: coap_node.c (tài nguyên, mã trả lời, gửi /status /a /t /alarm), act_task.c (hàng đợi lệnh
một luồng, tick 10 ms, /stop lên đầu hàng và tắt phần cứng ngay trong handler) và sensors.c
(mẫu 1 Hz, báo động, /t mỗi N mẫu).

`Hardware` là nơi chấp hành chạy ra: `RecordHardware` chỉ ghi lại, `SimWorldHardware` điều khiển
SimWorld. Nguồn cảm biến là đối tượng có `read() -> (nhiệt, gas, ẩm | None)`.
"""

import asyncio
import ctypes as C
import random
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from aiocoap import CHANGED, POST, Context, Message, Unreliable, resource
from aiocoap.numbers.codes import BAD_REQUEST, SERVICE_UNAVAILABLE

from ..config import REPO_ROOT

TEST_DIR = REPO_ROOT / "firmware/sensor-h2/test"
CORE_DIR = REPO_ROOT / "firmware/sensor-h2/main/core"
PROTO_MAX = 96  # như proto.h
TICK_S = 0.010  # như act_task.c
CMD_Q_LEN = 16  # như act_task.c
OUT_Q_LEN = 16  # như coap_node.c
DEV_NAMES = ("pump", "laser")
EVENTS = {0: None, 1: "alert", 2: "clear"}  # alarm_event_t


class CoreUnavailable(RuntimeError):
    """Không có libcore.so và không biên dịch được."""


# --- thư viện C ------------------------------------------------------------------------------

ANGLES_FN = C.CFUNCTYPE(None, C.c_void_p, C.c_float, C.c_float)
DEV_FN = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_int)
STATUS_FN = C.CFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_char_p, C.c_float, C.c_float, C.c_char_p)


class CoreLib:
    """libcore.so: lõi C của firmware. `CoreLib.get()` biên dịch nếu thiếu hoặc cũ hơn mã nguồn."""

    _inst: "CoreLib | None" = None
    _lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self.so = so = C.CDLL(str(path))
        p, u32, f32, i32 = C.c_void_p, C.c_uint32, C.c_float, C.c_int
        s, cp, ip = C.c_size_t, C.c_char_p, C.POINTER
        sig = {
            "shim_act_new": (p, [ip(f32), u32, u32, ANGLES_FN, DEV_FN, STATUS_FN, p]),
            "shim_act_free": (None, [p]),
            "shim_act_aim": (None, [p, u32, u32, f32, f32, u32]),
            "shim_act_fire": (None, [p, u32, u32, i32, u32]),
            "shim_act_stop": (None, [p, u32, u32]),
            "shim_act_hb": (None, [p, u32]),
            "shim_act_tick": (None, [p, u32]),
            "shim_act_all_off": (None, [p]),
            "shim_alarm_new": (p, [f32, f32, u32]),
            "shim_alarm_free": (None, [p]),
            "shim_alarm_feed": (i32, [p, u32, f32, f32]),
            "shim_parse_aim": (i32, [cp, s, ip(u32), ip(f32), ip(f32), ip(u32)]),
            "shim_parse_fire": (i32, [cp, s, ip(u32), ip(i32), ip(u32)]),
            "shim_parse_stop": (i32, [cp, s, ip(u32)]),
            "shim_parse_alarm": (i32, [cp, s, C.c_char_p, ip(u32)]),
            "shim_telemetry": (s, [p, s, cp, u32, u32, f32, f32, i32, f32]),
            "shim_alert": (s, [p, s, cp, u32, cp, f32, f32]),
            "shim_status": (s, [p, s, u32, cp, f32, f32, cp, cp]),
            "shim_alarm_msg": (s, [p, s, cp, u32]),
        }
        for name, (res, args) in sig.items():
            fn = getattr(so, name)
            fn.restype, fn.argtypes = res, args

    @classmethod
    def get(cls) -> "CoreLib":
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls(cls.build())
            return cls._inst

    @staticmethod
    def build() -> Path:
        so = TEST_DIR / "libcore.so"
        srcs = [*CORE_DIR.glob("*.[ch]"), TEST_DIR / "core_shim.c", TEST_DIR / "Makefile"]
        if so.exists() and all(so.stat().st_mtime >= f.stat().st_mtime for f in srcs):
            return so
        cc = "/usr/bin/gcc" if Path("/usr/bin/gcc").exists() else shutil.which("gcc")
        if cc is None:
            raise CoreUnavailable("cần gcc để biên dịch lõi firmware (libcore.so); cài gcc hoặc build tay: "
                                  "make -C firmware/sensor-h2/test libcore.so")
        r = subprocess.run(["make", "-C", str(TEST_DIR), "libcore.so", f"CC={cc}"],
                           capture_output=True, text=True, timeout=300, check=False)
        if r.returncode != 0 or not so.exists():
            raise CoreUnavailable(f"biên dịch libcore.so lỗi:\n{r.stdout}{r.stderr}")
        return so

    # --- bản tin (gọi bộ tạo/parse của firmware) -----------------------------------------------

    def _gen(self, fn, *args) -> bytes:
        buf = C.create_string_buffer(PROTO_MAX)
        n = fn(buf, PROTO_MAX, *args)
        return buf.raw[:n]

    def telemetry(self, n: str, s: int, up_s: int, t: float, g: float, h: float | None) -> bytes:
        return self._gen(self.so.shim_telemetry, n.encode(), s, up_s, t, g, h is not None, h or 0.0)

    def alert(self, n: str, s: int, kind: str, t: float, g: float) -> bytes:
        return self._gen(self.so.shim_alert, n.encode(), s, kind.encode(), t, g)

    def status(self, id_: int, st: str, pan: float, tilt: float, err: str | None, n: str) -> bytes:
        return self._gen(self.so.shim_status, id_, st.encode(), pan, tilt, err and err.encode(), n.encode())

    def alarm_msg(self, n: str, s: int) -> bytes:
        return self._gen(self.so.shim_alarm_msg, n.encode(), s)

    def parse_aim(self, raw: bytes):
        id_, ttl, pan, tilt = C.c_uint32(), C.c_uint32(), C.c_float(), C.c_float()
        ok = raw and self.so.shim_parse_aim(raw, len(raw), C.byref(id_), C.byref(pan), C.byref(tilt),
                                            C.byref(ttl))
        return (id_.value, pan.value, tilt.value, ttl.value) if ok else None

    def parse_fire(self, raw: bytes):
        id_, ms, dev = C.c_uint32(), C.c_uint32(), C.c_int()
        ok = raw and self.so.shim_parse_fire(raw, len(raw), C.byref(id_), C.byref(dev), C.byref(ms))
        return (id_.value, dev.value, ms.value) if ok else None

    def parse_stop(self, raw: bytes):
        id_ = C.c_uint32()
        ok = raw and self.so.shim_parse_stop(raw, len(raw), C.byref(id_))
        return id_.value if ok else None

    def parse_alarm(self, raw: bytes):
        n, s = C.create_string_buffer(8), C.c_uint32()
        ok = raw and self.so.shim_parse_alarm(raw, len(raw), n, C.byref(s))
        return (n.value.decode(errors="replace"), s.value) if ok else None


# --- phần cứng và cảm biến giả ---------------------------------------------------------------

class RecordHardware:
    """Chỉ ghi lại trạng thái chấp hành; `log` là (thời điểm, mô tả) theo thứ tự xảy ra."""

    def __init__(self) -> None:
        self.pan = self.tilt = 0.0
        self.on = {"pump": False, "laser": False}
        self.log: list[tuple[float, str]] = []
        self._lock = threading.Lock()

    def set_angles(self, pan: float, tilt: float) -> None:
        with self._lock:
            self.pan, self.tilt = pan, tilt
            self.log.append((time.monotonic(), f"angles {pan:.2f} {tilt:.2f}"))

    def set_dev(self, dev: str, on: bool) -> None:
        with self._lock:
            self.on[dev] = on
            self.log.append((time.monotonic(), f"{dev} {'on' if on else 'off'}"))

    def close(self) -> None:
        pass


class SimWorldHardware(RecordHardware):
    """Điều khiển SimWorld: góc -> `world.aim`, laser -> `world.laser`, bơm -> phun `world.spray`."""

    def __init__(self, world, node: str, spray_dt: float = 0.1) -> None:
        super().__init__()
        self.world, self.node, self.spray_dt = world, node, spray_dt
        self._pump = threading.Event()
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._spray, daemon=True, name=f"pump-{node}")
        self._thread.start()

    def set_angles(self, pan: float, tilt: float) -> None:
        super().set_angles(pan, tilt)
        self.world.aim(self.node, pan, tilt)

    def set_dev(self, dev: str, on: bool) -> None:
        super().set_dev(dev, on)
        if dev == "laser":
            self.world.laser(self.node, on)
        else:
            self.world.pumping[self.node] = on
            (self._pump.set if on else self._pump.clear)()

    def _spray(self) -> None:
        while not self._halt.is_set():
            if self._pump.wait(0.2):
                time.sleep(self.spray_dt)
                if self._pump.is_set():
                    self.world.spray(self.node, self.spray_dt)

    def close(self) -> None:
        self._halt.set()
        self.set_dev("pump", False)
        self.set_dev("laser", False)


class ConstSensors:
    """Cảm biến giả: số đọc đặt tay (nhiệt, gas, ẩm)."""

    def __init__(self, temp: float = 27.0, gas: float = 180.0, hum: float | None = 55.0) -> None:
        self.temp, self.gas, self.hum = temp, gas, hum

    def read(self) -> tuple[float, float, float | None]:
        return self.temp, self.gas, self.hum


class SimWorldSensors:
    """Số đọc của node trong SimWorld (cùng mô hình nhiệt/khí/ẩm với đường `mem`)."""

    def __init__(self, world, node: str) -> None:
        self.world, self.node = world, node

    def read(self) -> tuple[float, float, float | None]:
        with self.world.lock:
            r = self.world.reading(self.node)
        return r["temp"], r["gas"], r["hum"]


# --- hàng đợi lệnh ---------------------------------------------------------------------------

class _CmdQueue:
    """Hàng đợi lệnh dài CMD_Q_LEN; `front=True` chen lên đầu (xQueueSendToFront của /stop)."""

    def __init__(self) -> None:
        self._q: deque = deque()
        self._cv = threading.Condition()

    def put(self, cmd, front: bool = False) -> bool:
        with self._cv:
            if len(self._q) >= CMD_Q_LEN:
                return False
            (self._q.appendleft if front else self._q.append)(cmd)
            self._cv.notify()
            return True

    def get(self, timeout: float):
        with self._cv:
            if not self._q:
                self._cv.wait(timeout)
            return self._q.popleft() if self._q else None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


# --- node giả ----------------------------------------------------------------------------------

class FakeNode:
    """Server CoAP của một node (`/aim /fire /stop /hb /alarm`) và client gửi `/status /a /t /alarm`.

    `pi`: URI gốc của Pi, ví dụ "coap://[fd00::1]:5683" (None: không gửi gì lên). `alarm_peers`: URI gốc
    của các node khác nhận `/alarm` (firmware gửi multicast ff03::1).
    Lỗi giả: `online=False` (đứt mạng hai chiều: không trả lời, không gửi; phần cứng vẫn chạy watchdog),
    `drop_rx` (xác suất bỏ request mà không trả lời), `delay_s` (số giây hoặc hàm path -> giây, có thể
    làm lệnh tới đảo thứ tự). Không giả lập gói UDP mất ở tầng socket: bỏ = nằm im, Pi chờ hết giờ.
    """

    def __init__(self, name: str, bind: tuple[str, int] = ("127.0.0.1", 5683), pi: str | None = None,
                 hardware: RecordHardware | None = None, sensors=None, telemetry_s: float = 1.0,
                 sample_s: float = 1.0, temp_c: float = 45.0, gas: float = 600.0, warmup_s: float = 0.0,
                 limits: tuple[float, float, float, float] = (-50.0, 50.0, -35.0, 45.0),
                 hb_timeout_ms: int = 1500, max_fire_ms: int = 5000, transports: list[str] | None = None,
                 alarm_peers: list[str] | None = None, on_event: Callable[[str, str], None] | None = None,
                 seed: int | None = None) -> None:
        self.name, self.bind, self.pi = name, bind, pi
        self.hw = hardware or RecordHardware()
        self.sensors = sensors or ConstSensors()
        self.telemetry_s, self.sample_s = telemetry_s, sample_s
        self.alarm_cfg = (temp_c, gas, int(warmup_s * 1000))
        self.limits, self.hb_timeout_ms, self.max_fire_ms = limits, hb_timeout_ms, max_fire_ms
        self.transports = transports
        self.alarm_peers = list(alarm_peers or [])
        self.on_event = on_event
        self.online = True
        self.drop_rx = 0.0
        self.delay_s: float | Callable[[str], float] = 0.0
        self.remote_alarms: list[tuple[str, int]] = []  # /alarm nhận từ node khác
        self.tx_errors = 0
        self.rx_count: dict[str, int] = {}
        self._rng = random.Random(seed)
        self._core = CoreLib.get()
        self._cmds = _CmdQueue()
        self._halt = threading.Event()
        self._t0 = time.monotonic()
        self._seq = 0
        self._loop = asyncio.new_event_loop()
        self._ctx: Context | None = None
        self._sender_task: asyncio.Future | None = None
        self._txq: asyncio.Queue | None = None
        self._closed: asyncio.Event | None = None
        self._ready = threading.Event()
        self._err: str | None = None
        self._threads = [threading.Thread(target=self._run_loop, daemon=True, name=f"fn-{name}-coap"),
                         threading.Thread(target=self._act_loop, daemon=True, name=f"fn-{name}-act"),
                         threading.Thread(target=self._sensor_loop, daemon=True, name=f"fn-{name}-sens")]
        # giữ tham chiếu tới callback: ctypes không giữ hộ
        self._cb = (ANGLES_FN(self._io_angles), DEV_FN(self._io_dev), STATUS_FN(self._io_status))
        lim = (C.c_float * 4)(*limits)
        self._act = self._core.so.shim_act_new(lim, hb_timeout_ms, max_fire_ms, *self._cb, None)

    def _emit(self, kind: str, text: str) -> None:
        if self.on_event:
            self.on_event(kind, text)

    @property
    def url(self) -> str:
        host, port = self.bind
        return f"coap://{f'[{host}]' if ':' in host else host}:{port}"

    # --- vòng đời --------------------------------------------------------------------------

    def start(self) -> "FakeNode":
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
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        for t in self._threads:
            t.join(2)
        self.hw.close()
        self._core.so.shim_act_free(self._act)

    async def _shutdown(self) -> None:
        self._sender_task.cancel()
        await asyncio.gather(self._sender_task, return_exceptions=True)
        await self._ctx.shutdown()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.run_forever()

    async def _serve(self) -> None:
        self._txq, self._closed = asyncio.Queue(), asyncio.Event()
        root = resource.Site()
        for path in ("aim", "fire", "stop", "hb", "alarm"):
            root.add_resource([path], _Cmd(self, path))
        try:
            self._ctx = await Context.create_server_context(root, bind=self.bind, transports=self.transports)
        except OSError as e:
            self._err = f"không mở được cổng CoAP {self.bind}: {e}"
        else:
            self._sender_task = asyncio.ensure_future(self._sender())
        self._ready.set()

    # --- server (coap_node.c) ------------------------------------------------------------------

    async def handle(self, path: str, payload: bytes):
        """Handler của một request; trả mã CoAP như coap_node.c."""
        self.rx_count[path] = self.rx_count.get(path, 0) + 1
        if not self.online or self._rng.random() < self.drop_rx:
            self._emit("drop", f"/{path}")
            await self._closed.wait()  # nằm im: Pi chờ hết giờ
        d = self.delay_s(path) if callable(self.delay_s) else self.delay_s
        if d > 0:
            await asyncio.sleep(d)
        core = self._core
        if path == "hb":  # luôn 2.04, kể cả khi hàng đợi đầy
            self._cmds.put(("hb",))
            return Message(code=CHANGED)
        if path == "alarm":
            a = core.parse_alarm(payload)
            if a is None:
                return Message(code=BAD_REQUEST)
            if a[0] != self.name:
                self.remote_alarms.append(a)
                self._emit("rx", f"/alarm {a[0]} s={a[1]}")
            return Message(code=CHANGED)
        if path == "aim":
            p = core.parse_aim(payload)
            cmd = ("aim", *p) if p else None
        elif path == "fire":
            p = core.parse_fire(payload)
            cmd = ("fire", *p) if p else None
        else:
            p = core.parse_stop(payload)
            cmd = ("stop", p) if p is not None else None
        if cmd is None:
            self._emit("rx", f"/{path} sai payload {payload!r}")
            return Message(code=BAD_REQUEST)
        self._emit("rx", f"/{path} {payload.decode(errors='replace')}")
        if path == "stop":  # tắt phần cứng ngay, không chờ luồng chấp hành
            for dev in DEV_NAMES:
                self.hw.set_dev(dev, False)
        ok = self._cmds.put(cmd, front=path == "stop")
        return Message(code=CHANGED if ok else SERVICE_UNAVAILABLE)

    # --- luồng chấp hành (act_task.c) ----------------------------------------------------------

    def _io_angles(self, _ctx, pan, tilt) -> None:
        self.hw.set_angles(pan, tilt)

    def _io_dev(self, _ctx, dev, on) -> None:
        self.hw.set_dev(DEV_NAMES[dev], bool(on))

    def _io_status(self, _ctx, id_, st, pan, tilt, err) -> None:
        st, err = st.decode(), err.decode() if err else None
        self._emit("status", f"id={id_} {st}" + (f" err={err}" if err else ""))
        self._tx("status", self._core.status(id_, st, pan, tilt, err, self.name))

    def _act_loop(self) -> None:
        so, a = self._core.so, self._act
        while not self._halt.is_set():
            c = self._cmds.get(TICK_S)
            if c is not None:
                kind, now = c[0], _ms()
                if kind == "aim":
                    so.shim_act_aim(a, now, *c[1:])
                elif kind == "fire":
                    so.shim_act_fire(a, now, *c[1:])
                elif kind == "stop":
                    so.shim_act_stop(a, now, c[1])
                else:
                    so.shim_act_hb(a, now)
            so.shim_act_tick(a, _ms())

    # --- luồng cảm biến (sensors.c) ------------------------------------------------------------

    def _sensor_loop(self) -> None:
        so = self._core.so
        al = so.shim_alarm_new(*self.alarm_cfg)
        every = max(1, round(self.telemetry_s / self.sample_s))
        t, g, h = 25.0, 0.0, None
        tick = 0
        while not self._halt.wait(self.sample_s):
            try:
                t, g, h = self.sensors.read()
            except Exception as e:  # noqa: BLE001 - lỗi đọc: giữ số cũ như hal_sht31_read
                self._emit("sensor", f"lỗi đọc: {e}")
            up_ms = int((time.monotonic() - self._t0) * 1000)
            ev = EVENTS[so.shim_alarm_feed(al, up_ms, t, g)]
            if ev:
                self._seq += 1
                self._emit("alarm", f"{ev} t={t:.1f} g={g:.0f} s={self._seq}")
                self._tx("a", self._core.alert(self.name, self._seq, ev, t, g))
                if ev == "alert":
                    self._tx("alarm", self._core.alarm_msg(self.name, self._seq))
            tick += 1
            if tick % every == 0:
                self._seq += 1
                self._tx("t", self._core.telemetry(self.name, self._seq, up_ms // 1000, t, g, h))
        so.shim_alarm_free(al)

    # --- client (coap_node.c: send_msg) --------------------------------------------------------

    def _tx(self, kind: str, payload: bytes) -> None:
        if payload and self._txq is not None and not self._halt.is_set():
            self._loop.call_soon_threadsafe(self._enqueue, (kind, payload))

    def _enqueue(self, item) -> None:
        if self._txq.qsize() >= OUT_Q_LEN:
            self._emit("tx", f"hàng đợi ra đầy, bỏ /{item[0]}")
            return
        self._txq.put_nowait(item)

    async def _sender(self) -> None:
        """Phát lần lượt như coap_task: không chờ trả lời của bản tin CON."""
        while True:
            kind, payload = await self._txq.get()
            if not self.online:
                continue  # đứt mạng: mất bản tin
            dests = self.alarm_peers if kind == "alarm" else [self.pi]
            for base in filter(None, dests):
                con = kind in ("status", "a")
                msg = Message(code=POST, payload=payload, uri=f"{base}/{kind}",
                              **({} if con else {"transport_tuning": Unreliable}))
                self._emit("tx", f"/{kind} {payload.decode()}")
                fut = self._ctx.request(msg).response
                fut.add_done_callback(lambda f, k=kind: self._tx_done(k, f))
            await asyncio.sleep(0)

    def _tx_done(self, kind: str, fut) -> None:
        if fut.cancelled():
            return
        err = fut.exception()
        if err is not None:
            self.tx_errors += 1
            self._emit("tx", f"/{kind} không tới đích: {type(err).__name__}")
        elif not fut.result().code.is_successful():
            self._emit("tx", f"/{kind} bị trả {fut.result().code}")


class _Cmd(resource.Resource):
    def __init__(self, node: FakeNode, path: str) -> None:
        super().__init__()
        self.node, self.path = node, path

    async def render_post(self, request):
        return await self.node.handle(self.path, request.payload)
