"""Thế giới ảo chạy theo thời gian thực: sa bàn, đám cháy, vật gây nhiễu, cảm biến và node chấp hành.

Dùng để chạy orchestrator và dashboard từ đầu tới cuối khi chưa có phần cứng:

- `SimWorld`: Scene + thẻ bia tổng hợp + mô hình nhiệt/khí/ẩm đơn giản quanh các nguồn; mỗi node
  lấy mẫu 1 Hz, gửi telemetry và cảnh báo (3 mẫu liên tiếp vượt ngưỡng) qua callback, giống
  `/t` và `/a` của hợp đồng CoAP.
- `SimLink`: NodeLink trên sa bàn ảo: `/aim` quay servo có độ trễ, `/fire` bật laser hoặc bơm,
  `/stop`, `/status`; có thể cắt liên lạc từng node để thử lỗi.
- `OracleDetector`: thay YOLO khi chưa có trọng số: đọc thẻ thật trong scene, cho conf theo loại
  thẻ cộng nhiễu (lửa cao, đèn và vật cam thỉnh thoảng vượt ngưỡng, thẻ đã tắt gần như không).

Số liệu vật lý là giả định để demo, không phải số đo.
"""

import math
import threading
import time
from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np

from ..net.link import NodeLink
from ..net.protocol import Alert, Status, Telemetry
from ..orchestrator.aiming import limits
from ..orchestrator.fusion import alarm_limits
from ..vision.localize import project
from ..vision.types import Detection
from .camera import SimCamera
from .cards import SYNTH_DIR, ensure_synth_cards
from .scene import CARD_SIZE, DEFAULT_LAYOUT, Scene, sim_site

AMBIENT = {"temp": 27.0, "gas": 180.0, "hum": 55.0}
NOISE = {"temp": 0.4, "gas": 10.0, "hum": 0.8}
DECAY_M = 0.35  # tín hiệu giảm theo e^(-khoảng cách/DECAY_M)
# Khoảng cách 3D từ nguồn tới cảm biến (tâm tag node). Trừ đi cự ly thường gặp giữa node và bảng
# (0,25 m) để lửa ngay trước node vẫn cho tín hiệu cỡ đỉnh như khi chỉ tính theo X.
# Bố cục "corners" đặt cảm biến ở hai góc trước bàn, xa bảng và nền hơn nhiều so với hai trụ sát bảng
# (0,8 m so với 0,25 m). Cùng độ nhạy thì không đám cháy nhỏ nào đủ gần để báo động, nên ở bố cục
# đó tín hiệu lan xa gấp từng ấy lần (giả định cảm biến đặt ở góc nhạy hơn).
DECAY_SCALE = {"posts": 1.0, "corners": 3.0}
SENSOR_STANDOFF_M = 0.25
HIT_M = 0.035  # nước rơi cách tâm thẻ lửa dưới chừng này thì có tác dụng
SPRAY_CREDIT_MAX_S = 0.5  # mỗi lần ghi nước tối đa chừng này giây, dù vòng lặp bị kẹt lâu hơn
# Lửa lớn (giả định, cùng hướng với large_gain/large_decay của bộ kịch bản): thẻ to hơn, tín hiệu
# mạnh hơn và suy giảm chậm hơn (với tới cả hai node), cần nhiều nước hơn để tắt.
FIRE_SIZES = ("small", "large")
LARGE_CARD = 1.8
LARGE_GAIN = 1.5
LARGE_DECAY = 2.0
LARGE_HP = (1.6, 5.2)
SMALL_HP = (0.8, 2.6)


@dataclass
class Source:
    """Một nguồn: thẻ (có thể có) trên bảng hoặc nền và tín hiệu cảm biến nó gây ra. Vị trí 3D
    (x, y, z): nguồn trên bảng có y = plane_y; nguồn trên nền có z = 0 (điểm đáy chạm nền)."""

    id: int
    kind: str  # fire, lamp, object, steam, spike
    x: float
    z: float
    y: float = 0.0
    surface: str = "wall"  # wall | floor
    card: int | None = None
    peak: dict = field(default_factory=dict)  # đỉnh cộng thêm cho temp, gas, hum ở khoảng cách 0
    level: float = 0.0  # 0..1, tiến về `goal` theo hằng số thời gian
    goal: float = 1.0
    tau_rise: float = 5.0
    tau_fall: float = 8.0
    node: str | None = None  # spike chỉ tác động một node
    until: float | None = None  # hết hạn (giây thế giới) với steam, spike
    hp: float = 1.0  # lửa: số "giây phun trúng" cần để tắt
    out: bool = False
    decay: float = DECAY_M  # tín hiệu cảm biến giảm theo e^(-khoảng cách/decay)
    size: str = "small"  # lửa: small hoặc large

    @property
    def pos(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z])

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "x": round(self.x, 3), "z": round(self.z, 3),
                "y": round(self.y, 3), "surface": self.surface,
                "level": round(self.level, 2), "hp": round(self.hp, 2), "out": self.out,
                "node": self.node, "size": self.size}


class SimNodeSensors:
    """Logic báo động trên node: 3 mẫu liên tiếp vượt ngưỡng thì báo, 5 mẫu dưới ngưỡng thì hủy."""

    def __init__(self, name: str) -> None:
        self.name, self.seq, self.above, self.below, self.alarm = name, 0, 0, 0, False

    def update(self, temp: float, gas: float, lim: dict) -> str | None:
        hot = temp > lim["temp"] or gas > lim["gas"]
        self.above = self.above + 1 if hot else 0
        self.below = 0 if hot else self.below + 1
        if not self.alarm and self.above >= 3:
            self.alarm = True
            return "alert"
        if self.alarm and self.below >= 5:
            self.alarm = False
            return "clear"
        return None


class SimWorld:
    def __init__(self, site: dict | None = None, seed: int = 0, servo_bias_deg: float = 3.0,
                 telemetry_period: float = 1.0, tick: float = 0.1,
                 camera=None, node_layout=None) -> None:
        """`camera`: tên preset (`CAMERA_PRESETS`) hoặc (vị trí, điểm nhìn); `node_layout`: tên
        bố cục node (`NODE_LAYOUTS`: "corners", "posts"). Bỏ trống là `DEFAULT_LAYOUT` (corners)."""
        self.site = sim_site(site)
        self.kinds = ensure_synth_cards()
        self.scene = Scene(self.site, cards_dir=SYNTH_DIR, seed=seed, camera_pose=camera,
                           node_layout=node_layout)
        self.cap = SimCamera(self.scene)
        self.lock = threading.RLock()  # mọi thay đổi scene và mỗi lần dựng khung đều giữ khóa này
        self.rng = np.random.default_rng(seed)
        self.limits = alarm_limits(self.site)
        self.decay_scale = DECAY_SCALE.get(self.scene.node_layout, DECAY_SCALE[DEFAULT_LAYOUT])
        self.nodes = list(self.scene.nodes)
        for n in self.nodes:
            self.scene.set_servo_bias(n, *self.rng.uniform(-servo_bias_deg, servo_bias_deg, 2))
        self.sources: dict[int, Source] = {}
        self._ids = 0
        self.t = 0.0
        self.tick, self.telemetry_period = tick, telemetry_period
        self._next_sample = 0.0
        self.node_sensors = {n: SimNodeSensors(n) for n in self.nodes}
        self.online = {n: True for n in self.nodes}  # False: node mất liên lạc (thử lỗi)
        self.pumping: dict[str, bool] = {n: False for n in self.nodes}
        self.marks: dict[str, np.ndarray] = {}  # điểm nước rơi gần nhất của từng vòi (ground truth)
        self.on_telemetry = None  # callback(Telemetry)
        self.on_alert = None  # callback(Alert)
        self.last = {n: dict(AMBIENT) for n in self.nodes}
        self._thread: threading.Thread | None = None
        self._halt = threading.Event()

    # --- vòng thời gian ---------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="sim-world")
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()

    def _run(self) -> None:
        last = time.monotonic()
        while not self._halt.wait(self.tick):
            now = time.monotonic()
            self.step(now - last)
            last = now

    def step(self, dt: float) -> None:
        with self.lock:
            self.t += dt
            for s in list(self.sources.values()):
                if s.until is not None and self.t >= s.until:
                    s.goal = 0.0
                tau = s.tau_rise if s.goal > s.level else s.tau_fall
                s.level += (s.goal - s.level) * (1 - math.exp(-dt / tau))
                if s.kind in ("steam", "spike") and s.goal == 0 and s.level < 0.01:
                    del self.sources[s.id]
            due = self.t >= self._next_sample
            if due:
                self._next_sample = self.t + self.telemetry_period
                readings = {n: self.reading(n) for n in self.nodes}
                self.last = readings
        if not due:
            return
        for n, r in readings.items():
            ns = self.node_sensors[n]
            kind = ns.update(r["temp"], r["gas"], self.limits)
            ns.seq += 1
            if not self.online[n]:
                continue
            tel = Telemetry(n, ns.seq, int(self.t), round(r["temp"], 1), round(r["gas"]),
                            round(r["hum"]))
            if self.on_telemetry:
                self.on_telemetry(tel)
            if kind and self.on_alert:
                ns.seq += 1
                self.on_alert(Alert(n, ns.seq, kind, tel.t, tel.g))

    def reading(self, node: str) -> dict:
        sensor = self.scene.nodes[node].position
        out = {k: AMBIENT[k] + self.rng.normal(0, NOISE[k]) for k in AMBIENT}
        for s in self.sources.values():
            if s.node is not None and s.node != node:
                continue
            if s.node:
                w = 1.0
            elif s.kind == "steam":  # hơi nước lan khắp bàn, chỉ phụ thuộc X như trước
                w = math.exp(-abs(s.x - sensor[0]) / s.decay)
            else:
                d = max(float(np.linalg.norm(s.pos - sensor)) - SENSOR_STANDOFF_M, 0.0)
                w = math.exp(-d / (s.decay * self.decay_scale))
            for k, p in s.peak.items():
                out[k] += s.level * p * w
        return out

    # --- tạo tình huống ---------------------------------------------------------------------

    def _new(self, **kw) -> Source:
        self._ids += 1
        s = Source(self._ids, **kw)
        self.sources[s.id] = s
        return s

    def _pos(self, x, z, surface: str = "wall", y=None) -> tuple[float, float, float]:
        """(x, y, z) đã điền: thiếu thì rút ngẫu nhiên trong vùng bia. Trên nền z = 0 (bỏ qua z),
        trên bảng y = plane_y (bỏ qua y)."""
        if surface not in ("wall", "floor"):
            raise ValueError(f"surface phải là 'wall' hoặc 'floor', không phải {surface!r}")
        b = self.site["board"]
        x = float(self.rng.uniform(0.12, b["width"] - 0.12)) if x is None else float(x)
        if surface == "floor":
            depth = self.scene.table["depth"]
            y = float(self.rng.uniform(0.12, depth - 0.12)) if y is None else float(y)
            return x, y, 0.0
        z = float(self.rng.uniform(0.12, b["height"] - 0.12)) if z is None else float(z)
        return x, float(b["plane_y"]), z

    def _card(self, kind: str, pos, surface: str = "wall", size: float = CARD_SIZE) -> int:
        idx = self.kinds[kind]
        return self.scene.add_card_3d(pos, surface, index=idx[int(self.rng.integers(len(idx)))],
                                      size=size)

    def _delta(self, ratio_t: float, ratio_g: float, hum: float = 0.0) -> dict:
        return {"temp": ratio_t * (self.limits["temp"] - AMBIENT["temp"]),
                "gas": ratio_g * (self.limits["gas"] - AMBIENT["gas"]), "hum": hum}

    def ignite(self, x: float | None = None, z: float | None = None, size: str = "small", *,
               surface: str = "wall", y: float | None = None) -> Source:
        """Đám cháy: thẻ lửa trên bảng (x, z) hoặc dựng trên nền (`surface="floor"`, tại x, y),
        nhiệt và khí tăng dần quanh vị trí. `size="large"`: thẻ to hơn, tín hiệu mạnh hơn và lan xa
        hơn (với tới cả hai node), cần nhiều nước hơn để tắt."""
        if size not in FIRE_SIZES:
            raise ValueError(f"size phải là một trong {FIRE_SIZES}, không phải {size!r}")
        large = size == "large"
        gain = LARGE_GAIN if large else 1.0
        with self.lock:
            x, y, z = self._pos(x, z, surface, y)
            card = self._card("fire", (x, y, z), surface,
                              CARD_SIZE * LARGE_CARD if large else CARD_SIZE)
            return self._new(kind="fire", x=x, y=y, z=z, surface=surface, card=card,
                             peak=self._delta(2.2 * gain, 2.2 * gain, -3.0 * gain),
                             tau_rise=4.0, tau_fall=10.0,
                             hp=float(self.rng.uniform(*(LARGE_HP if large else SMALL_HP))),
                             decay=DECAY_M * (LARGE_DECAY if large else 1.0), size=size)

    def add_lamp(self, x: float | None = None, z: float | None = None, *,
                 surface: str = "wall", y: float | None = None) -> Source:
        """Đèn nóng: trông giống lửa vừa vừa, nóng nhưng không có khí."""
        with self.lock:
            x, y, z = self._pos(x, z, surface, y)
            return self._new(kind="lamp", x=x, y=y, z=z, surface=surface,
                             card=self._card("lamp", (x, y, z), surface),
                             peak=self._delta(1.8, 0.05), tau_rise=6.0)

    def add_object(self, x: float | None = None, z: float | None = None, *,
                   surface: str = "wall", y: float | None = None) -> Source:
        """Vật màu cam: chỉ đánh lừa camera, không gây tín hiệu cảm biến."""
        with self.lock:
            x, y, z = self._pos(x, z, surface, y)
            return self._new(kind="object", x=x, y=y, z=z, surface=surface,
                             card=self._card("object", (x, y, z), surface), peak={})

    def add_steam(self, x: float | None = None, duration: float = 25.0) -> Source:
        """Hơi nước nấu ăn: ẩm tăng mạnh, nóng vừa, không có thẻ trên bảng."""
        with self.lock:
            x, _, _ = self._pos(x, 0.3)
            return self._new(kind="steam", x=x, z=0.0, peak=self._delta(1.5, 0.3, 30.0),
                             tau_rise=5.0, tau_fall=6.0, until=self.t + duration)

    def add_spike(self, node: str | None = None, duration: float = 4.0) -> Source:
        """Xung nhiễu ngắn trên một cảm biến."""
        with self.lock:
            node = node or self.nodes[int(self.rng.integers(len(self.nodes)))]
            x = float(self.scene.nodes[node].position[0])
            return self._new(kind="spike", x=x, z=0.0, node=node, peak=self._delta(0.2, 2.4),
                             tau_rise=0.4, tau_fall=0.6, until=self.t + duration)

    def clear(self) -> None:
        with self.lock:
            self.sources.clear()
            self.scene.clear_cards()

    def set_online(self, node: str, online: bool) -> None:
        with self.lock:
            self.online[node] = online
            if not online:  # node mất nhịp heartbeat thì tự tắt bơm và laser
                self.scene.set_laser(node, False)
                self.pumping[node] = False

    # --- chấp hành --------------------------------------------------------------------------

    def aim(self, node: str, pan: float, tilt: float) -> None:
        with self.lock:
            self.scene.aim(node, pan, tilt)

    def laser(self, node: str, on: bool) -> None:
        with self.lock:
            self.scene.set_laser(node, on)

    def spray(self, node: str, seconds: float) -> np.ndarray | None:
        """Nước của `node` phun `seconds` giây theo hướng hiện tại; trả điểm rơi (3D) trên bảng hoặc
        nền, nơi tia nước chạm mặt nào trước."""
        with self.lock:
            found = self.scene.spot_surface(node)
            if found is None:
                return None
            spot, surface = found
            jitter = self.rng.normal(0, 0.006, 2)
            # nhiễu nằm trong mặt bị trúng: bảng là (x, z), nền là (x, y)
            hit = spot + (np.array([jitter[0], 0, jitter[1]]) if surface == "wall"
                          else np.array([jitter[0], jitter[1], 0]))
            self.marks[node] = hit
            for s in self.sources.values():
                if s.kind != "fire" or s.out:
                    continue
                if float(np.linalg.norm(hit - s.pos)) <= HIT_M:
                    s.hp -= seconds * float(self.rng.uniform(0.7, 1.2))
                    if s.hp <= 0:
                        self._extinguish(s)
            return hit

    def _extinguish(self, s: Source) -> None:
        s.out, s.goal, s.hp = True, 0.0, 0.0
        if s.card is not None:
            self.scene.remove_card(s.card)
            s.card = self._card("burnt", s.pos, s.surface)

    def read(self) -> np.ndarray:
        with self.lock:
            _, frame = self.cap.read()
        return frame

    def truth(self) -> dict:
        with self.lock:
            return {"t": round(self.t, 1), "sources": [s.to_json() for s in self.sources.values()],
                    "online": dict(self.online), "pumping": dict(self.pumping),
                    "lasers": {n: self.scene.nodes[n].laser for n in self.nodes},
                    "readings": {n: {k: round(v, 1) for k, v in r.items()}
                                 for n, r in self.last.items()}}


class SimLink(NodeLink):
    """Node chấp hành ảo. Mỗi lệnh xử lý ở một luồng riêng, có độ trễ mạng và thời gian quay servo."""

    def __init__(self, world: SimWorld, deg_per_s: float = 150.0, latency_s: float = 0.03) -> None:
        super().__init__(world.nodes)
        self.world, self.deg_per_s, self.latency = world, deg_per_s, latency_s
        self._reached: dict[str, int] = {}
        self._angle = {n: (0.0, 0.0) for n in world.nodes}
        self._cancel = {n: threading.Event() for n in world.nodes}
        # id /stop lớn nhất đã nhận; /aim, /fire có id nhỏ hơn là lệnh cũ (gói đến sau /stop) và bị từ chối
        self._last_stop = dict.fromkeys(world.nodes, 0)
        self._lock = threading.Lock()

    def heartbeat(self, node: str) -> None:
        if self.world.online[node]:
            self.on_rx(node)

    def _send(self, node: str, path: str, msg) -> None:
        if not self.world.online[node]:
            return  # mất gói: Pi sẽ hết giờ chờ /status
        threading.Thread(target=self._handle, args=(node, path, msg), daemon=True).start()

    def _reply(self, node, cmd_id, st, err=None):
        if self.world.online[node]:
            pan, tilt = self._angle[node]
            self.on_status(node, Status(cmd_id, st, round(pan, 2), round(tilt, 2), err, node))

    def _handle(self, node: str, path: str, msg) -> None:
        time.sleep(self.latency)
        self.on_rx(node)
        if path in ("aim", "fire") and msg.id < self._last_stop[node]:
            self._reply(node, msg.id, "rejected", "stopped")
            return
        if path == "aim":
            if not limits(self.world.site, node).ok(msg.pan, msg.tilt):
                self._reply(node, msg.id, "rejected", "limits")
                return
            self._reply(node, msg.id, "accepted")
            p0, t0 = self._angle[node]
            time.sleep(0.15 + max(abs(msg.pan - p0), abs(msg.tilt - t0)) / self.deg_per_s)
            self.world.aim(node, msg.pan, msg.tilt)
            self._angle[node] = (msg.pan, msg.tilt)
            self._reached[node] = msg.id
            self._reply(node, msg.id, "reached")
        elif path == "fire":
            if self._reached.get(node) != msg.id:
                self._reply(node, msg.id, "rejected", "not reached")
                return
            with self._lock:  # kiểm tra lại và bật thiết bị cùng lúc, để /stop không lọt vào giữa
                if msg.id < self._last_stop[node]:
                    self._reply(node, msg.id, "rejected", "stopped")
                    return
                self._cancel[node].clear()
                if msg.dev == "laser":
                    self.world.laser(node, True)
                else:
                    self.world.pumping[node] = True
            self._reply(node, msg.id, "accepted")
            if msg.dev == "laser":
                self._cancel[node].wait(msg.ms / 1000)
                self.world.laser(node, False)
            else:
                # ghi nước theo thời gian thật đã trôi (không phải 0.1 s cố định): vòng lặp có thể bị
                # kẹt lâu khi `world.lock` đang bận dựng khung hình; tối đa SPRAY_CREDIT_MAX_S mỗi
                # lần và không quá thời gian của lệnh
                t_prev = time.monotonic()
                end = t_prev + msg.ms / 1000
                while t_prev < end and not self._cancel[node].is_set():
                    if not self.world.online[node]:
                        break
                    time.sleep(0.1)
                    now = time.monotonic()
                    if self._cancel[node].is_set():
                        break
                    self.world.spray(node, min(now - t_prev, SPRAY_CREDIT_MAX_S, end - t_prev))
                    t_prev = now
                self.world.pumping[node] = False
            self._reply(node, msg.id, "fault" if self._cancel[node].is_set() else "done",
                         "stopped" if self._cancel[node].is_set() else None)
        elif path == "stop":
            with self._lock:
                self._last_stop[node] = max(self._last_stop[node], msg.id)
                self._cancel[node].set()
                self.world.laser(node, False)
                self.world.pumping[node] = False
            self._reply(node, msg.id, "done")


class OracleDetector:
    """Detector giả đọc thẻ thật trong scene. Gắn `vision` sau khi tạo Vision để biết vùng cắt."""

    CONF: ClassVar[dict] = {"fire": (0.55, 0.92), "lamp": (0.2, 0.6), "object": (0.08, 0.45), "burnt": (0.0, 0.25)}
    DROP = 0.1  # xác suất bỏ sót một thẻ trong một khung

    def __init__(self, world: SimWorld, conf: float = 0.35, seed: int = 1) -> None:
        self.world, self.conf = world, conf
        self.rng = np.random.default_rng(seed)
        self.vision = None
        kinds = world.kinds
        self._kind_of = {i: k for k, idx in kinds.items() for i in idx}

    def __call__(self, image: np.ndarray) -> list[Detection]:
        scene = self.world.scene
        W, H = scene.size
        x0 = y0 = 0
        if image.shape[:2] != (H, W) and self.vision is not None:
            x0, y0, _, _ = self.vision.board_roi((H, W, 3))
        out = []
        with self.world.lock:
            cards = list(scene.cards.items())
        for cid, c in cards:
            kind = self._kind_of.get(c.index, "object")
            lo, hi = self.CONF[kind]
            conf = float(self.rng.uniform(lo, hi))
            if conf < self.conf or self.rng.random() < self.DROP:
                continue
            quad = scene.card_quad(cid)
            pts = np.array([project(p, scene.camera) for p in quad])
            w, h = np.ptp(pts, axis=0)
            if c.surface == "floor":  # hộp khít ngọn lửa: giữa đáy hộp là điểm chạm nền
                u, vb = project(scene.card_position(cid), scene.camera)
                v = vb - h / 2
            else:
                u, v = project(scene.card_position(cid), scene.camera)
            out.append(Detection(float(u) - x0, float(v) - y0, float(w), float(h), round(conf, 3)))
        return sorted(out, key=lambda d: -d.conf)
