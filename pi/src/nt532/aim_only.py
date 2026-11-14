"""Profile trạm `vision-aim-only`: camera thật + node A/B integrated, CHỈ NGẮM, không bơm.

Chọn tường minh bằng `scripts/run_station.py --profile vision-aim-only`; profile sim/legacy không đổi.

Trình tự khởi động (mỗi bước lỗi -> OFF, STOP, chốt STOP):
    START -> CHECK_CAMERA (15 khung Dynamic Mapping, tag_2_table) -> CONNECT_NODES (GET /info, s1=A,
    s2=B) -> HEARTBEAT -> ARM -> HOME (1500/1700) -> CALIBRATION (bật đèn ngắm TỪNG node) ->
    WAIT_OK (người vận hành gõ `OK s1` / `OK s2`) -> đèn OFF, kiểm OFF -> ... -> READY.

Sau READY: khung camera -> Vision (YOLO) -> bia ổn định qua nhiều khung -> Dynamic Mapping ->
`plan_target` (hiệu chuẩn đo riêng từng node) -> `POST /pantilt` -> chờ tới nơi -> ghi log.
Đèn ngắm chỉ bật lại qua lệnh `VERIFY` của người vận hành, có giới hạn thời gian.

Không bao giờ gửi /aim, /fire hay bơm: `NoPumpRequest` chỉ cho phép đúng tập endpoint integrated.
Không chạy selfcal, không dùng Orchestrator legacy. STOP là chốt: chỉ `RECOVER` ở console gỡ được,
sau đó chạy lại toàn bộ trình tự (kể cả WAIT_OK).
"""

import json
import math
import queue
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from .config import REPO_ROOT
from .manual_aim import FRAME, load_mount, plan_target
from .net.integrated_manual import HOME, IDENTITY, CoapTransport, IntegratedNode
from .orchestrator.events import EventLog
from .vision.types import TagPose

PROFILE = "vision-aim-only"

# Trạng thái công khai (snapshot / log).
START, CHECK_CAMERA, CONNECT_NODES, HEARTBEAT, ARM, HOME_STATE = (
    "START", "CHECK_CAMERA", "CONNECT_NODES", "HEARTBEAT", "ARM", "HOME")
CALIBRATION, WAIT_OK, READY, AIMING, VERIFY = "CALIBRATION", "WAIT_OK", "READY", "AIMING", "VERIFY"
STOP_LATCHED, CALIBRATION_REQUIRED = "STOP_LATCHED", "CALIBRATION_REQUIRED"

# Endpoint integrated được phép; mọi thứ khác (aim, fire, pump, ...) bị chặn ngay ở Pi.
_ALLOWED = {("GET", "info"), ("POST", "hb"), ("GET", "pantilt"), ("POST", "pantilt"),
            ("GET", "laser"), ("POST", "laser"), ("POST", "stop"), ("GET", "status")}


class NoPumpRequest:
    """Bọc transport: chỉ cho phép endpoint integrated đã kiểm; bơm/fire/aim không thể đi ra mạng."""

    def __init__(self, request):
        self._request = request

    def __call__(self, method: str, path: str, payload: str | None = None) -> dict:
        if (method, path) not in _ALLOWED:
            raise PermissionError(f"{method} /{path} bị chặn trong profile {PROFILE} (không bơm)")
        if path == "laser" and method == "POST" and payload not in ("ON", "OFF"):
            raise PermissionError("POST /laser chỉ nhận ON/OFF")
        return self._request(method, path, payload)


@dataclass
class AimOnlyConfig:
    calibration: Path
    nodes: tuple[str, ...] = ("s1", "s2")
    ok_timeout_s: float = 120.0       # chờ OK của người vận hành mỗi node; hết hạn -> OFF/STOP/chốt
    batch_frames: int = 15            # một lô Dynamic Mapping
    stable_frames: int = 3            # bia phải ổn định chừng này khung liên tiếp
    stable_tol_m: float = 0.02
    same_target_tol_m: float = 0.03   # bia đứng yên trong bán kính này thì không ngắm lại
    pose_max_age_s: float = 60.0      # cổng an toàn: pose cũ hơn thì chốt STOP
    remap_s: float = 20.0             # làm mới Dynamic Mapping định kỳ (< pose_max_age_s)
    verify_light_s: float = 3.0       # thời gian bật đèn ngắm khi VERIFY, tối đa 10 s
    servo_timeout_s: float = 8.0
    supervise_s: float = 0.5

    def __post_init__(self):
        if not self.nodes or any(n not in IDENTITY for n in self.nodes) or (
                len(set(self.nodes)) != len(self.nodes)):
            raise ValueError("nodes phải là tập con không lặp của s1,s2")
        if not 0 < self.ok_timeout_s <= 600:
            raise ValueError("ok_timeout_s phải trong (0, 600]")
        if not 0 < self.verify_light_s <= 10:
            raise ValueError("verify_light_s phải trong (0, 10]")
        if self.batch_frames != 15:
            raise ValueError("Dynamic Mapping của profile này dùng đúng lô 15 khung")
        if not 0 < self.remap_s < self.pose_max_age_s:
            raise ValueError("remap_s phải dương và nhỏ hơn pose_max_age_s")


class CameraFrames:
    """Nguồn khung từ camera thật (webcam hoặc stream)."""

    def __init__(self, cap):
        self.cap = cap

    def frame(self) -> np.ndarray:
        from .vision.camera import read_fresh

        return read_fresh(self.cap)

    def batch(self, n: int) -> list[np.ndarray]:
        from .vision.camera import read_frames

        return read_frames(self.cap, n)

    def close(self):
        self.cap.release()


class Latched(RuntimeError):
    """Nội bộ: thao tác bị bỏ vì STOP đã chốt."""


@dataclass
class _Aim:
    node: str
    target_m: tuple[float, float, float]
    pulses_us: tuple[int, int]
    time: float = field(default_factory=time.time)


class AimOnlyStation:
    def __init__(self, site: dict, cfg: AimOnlyConfig, vision, frames, addresses: dict,
                 transport_factory=None, events: EventLog | None = None, clock=time.monotonic):
        self.site, self.cfg, self.vision, self.frames = site, cfg, vision, frames
        self.addresses = dict(addresses)
        self.transport_factory = transport_factory or (lambda addr: CoapTransport(addr))
        self.events = events or EventLog()
        self.clock = clock
        self.state, self.reason, self.wait_node = START, "", None
        self.latched = threading.Event()
        self.nodes: dict[str, IntegratedNode] = {}
        self._transports: list = []
        self._cmds: queue.Queue = queue.Queue()
        self._lock = threading.RLock()
        self._track: deque = deque(maxlen=cfg.stable_frames)
        self._map_t: float | None = None
        self._supervised_t = -1e9
        self.last_aim: _Aim | None = None
        self.last_rejected = None
        self.results: list[dict] = []
        self.last_shutdown: dict = {}

    # --- trạng thái và lệnh -----------------------------------------------------------------

    def _set(self, state: str, text: str = "", **data):
        self.state = state
        self.events.emit("aim_only", f"{state} {text}".strip(), state=state, **data)

    def snapshot(self) -> dict:
        mapping = getattr(self.vision, "reference_map", None)
        return {"profile": PROFILE, "state": self.state, "reason": self.reason,
                "latched": self.latched.is_set(), "wait_ok_node": self.wait_node,
                "nodes": sorted(self.nodes), "frame": FRAME, "pump": "DISABLED",
                "mapping": None if mapping is None else {
                    "frames_used": mapping.frames_used, "metric_error_m": mapping.metric_error_m,
                    "age_s": None if self._map_t is None else round(self.clock() - self._map_t, 1)},
                "last_aim": None if self.last_aim is None else self.last_aim.__dict__,
                "last_shutdown": self.last_shutdown}

    def command(self, text: str, source: str = "console") -> str:
        """`STOP` từ bất kỳ nguồn nào; `OK <node>`, `VERIFY`, `RECOVER` chỉ từ console vận hành."""
        words = text.strip().split()
        if not words:
            return "empty"
        verb = words[0].upper()
        if verb == "STOP":
            self.stop(f"operator STOP ({source})", force=True)
            return "STOP latched"
        if source != "console":
            return "rejected: only STOP is accepted from " + source
        if verb in ("OK", "VERIFY", "RECOVER"):
            self._cmds.put((verb, words[1] if len(words) > 1 else None))
            return f"queued {verb}"
        return f"unknown command {verb}"

    def _check_latch(self):
        if self.latched.is_set():
            raise Latched(self.reason)

    # --- STOP chốt ----------------------------------------------------------------------------

    def stop(self, reason: str, force: bool = False, state: str = STOP_LATCHED):
        """OFF rồi STOP mọi node đã xác minh; chốt lại; bỏ mọi lệnh/bia đang chờ."""
        with self._lock:
            if self.latched.is_set() and not force:
                return
            self.latched.set()
            self.reason = reason
            self.wait_node = None
            self._track.clear()
            while not self._cmds.empty():
                try:
                    self._cmds.get_nowait()
                except queue.Empty:
                    break
            results = {name: node.shutdown() for name, node in self.nodes.items()}
            self.last_shutdown = results
            errors = {n: r["errors"] for n, r in results.items() if r["errors"]}
            self._set(state, reason, shutdown=results)
            if errors:
                self.events.emit("aim_only", "OFF/STOP NOT VERIFIED; cut physical laser power: "
                                 f"{errors}", level="error")

    def close(self):
        """Ctrl+C / tắt tiến trình: luôn OFF/STOP rồi đóng transport."""
        self.stop("process shutdown", force=True)
        for transport in self._transports:
            close = getattr(transport, "close", None)
            if close:
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - vẫn đóng các transport còn lại
                    self.events.emit("aim_only", f"transport close: {exc}", level="warn")
        self._transports.clear()

    def recover(self):
        """Chỉ từ lệnh RECOVER tường minh: gỡ chốt rồi chạy lại toàn bộ trình tự khởi động."""
        with self._lock:
            if not self.latched.is_set():
                return
            for transport in self._transports:
                close = getattr(transport, "close", None)
                if close:
                    try:
                        close()
                    except Exception as exc:  # noqa: BLE001 - vẫn đóng các transport còn lại
                        self.events.emit("aim_only", f"transport close: {exc}", level="warn")
            self._transports.clear()
            self.nodes.clear()
            self.last_aim = None
            self._map_t = None
            self.reason = ""
            self.latched.clear()
            self._set(START, "operator RECOVER: full restart, WAIT_OK required again")

    # --- khởi động ----------------------------------------------------------------------------

    def preflight_calibration(self):
        """Hiệu chuẩn góc->xung đo thật phải đủ cho mọi node, trước khi chạm phần cứng."""
        tag_ids = self.site["tags"]["nodes"]["ids"]
        for name in self.cfg.nodes:
            load_mount(self.cfg.calibration, name, TagPose(tag_ids[name], np.zeros(3), np.eye(3)))

    def _commission(self):
        frames = self.frames.batch(self.cfg.batch_frames)
        if len(frames) != self.cfg.batch_frames:
            raise ValueError("lô Dynamic Mapping không đủ 15 khung")
        self.vision.commission(frames)
        self._check_mapping()
        self._map_t = self.clock()

    def _check_mapping(self):
        vision = self.vision
        if vision.reference_mode != "auto" or vision.reference_map is None:
            raise ValueError("Dynamic Mapping auto (tag_2_table) không hợp lệ")
        reasons = []
        for name in self.cfg.nodes:
            reasons += vision.blockers(name)
            pose = vision.nodes.get(name)
            if pose is not None and pose.tag_id != self.site["tags"]["nodes"]["ids"][name]:
                reasons.append(f"tag của {name} sai ID")
        if reasons:
            raise ValueError("mapping/pose blocked: " + "; ".join(dict.fromkeys(reasons)))

    def startup(self) -> bool:
        """Chạy toàn bộ trình tự. True khi READY; False khi bị chặn/chốt."""
        step = START
        try:
            self._set(START, f"profile {PROFILE}, nodes {list(self.cfg.nodes)}, pump DISABLED")
            step = CHECK_CAMERA
            self._set(CHECK_CAMERA)
            if self.vision.reference_mode != "auto":
                raise ValueError("profile cần vision.reference_mode auto (tag_2_table)")
            try:
                self.preflight_calibration()
            except ValueError as exc:
                self.stop(str(exc), state=CALIBRATION_REQUIRED)
                return False
            self._commission()
            mapping = self.vision.reference_map
            self.events.emit("aim_only", "mapping PASS", frames_used=mapping.frames_used,
                             metric_error_m=mapping.metric_error_m)
            step = CONNECT_NODES
            self._set(CONNECT_NODES)
            missing = [n for n in self.cfg.nodes if not self.addresses.get(n)]
            if missing:
                raise ValueError(f"thiếu địa chỉ node {missing}")
            for name in self.cfg.nodes:
                self._check_latch()
                transport = self.transport_factory(self.addresses[name])
                self._transports.append(transport)
                node = IntegratedNode(name, NoPumpRequest(transport), self.cfg.servo_timeout_s)
                info = node.verify_identity()
                self.nodes[name] = node  # chỉ node đã xác minh mới nhận lệnh, kể cả OFF/STOP
                self.events.emit("aim_only", f"{name} identity {info.get('n')}/{info.get('fw')}")
            step = HEARTBEAT
            self._set(HEARTBEAT)
            for node in self.nodes.values():
                node.request("POST", "hb")
            for name, node in self.nodes.items():
                self._check_latch()
                step = ARM
                self._set(ARM, name)
                status = node.arm_home()  # hb + luồng hb 400 ms, ARM, HOME, chờ tới 1500/1700
                step = HOME_STATE
                self._set(HOME_STATE, f"{name} {status.get('pan_us')}/{status.get('tilt_us')}")
            step = CALIBRATION
            for name, node in self.nodes.items():
                self._calibrate(name, node)
            self.reason = ""
            self._set(READY, "all selected nodes passed CALIBRATION/WAIT_OK")
            return True
        except Latched:
            return False
        except Exception as exc:  # noqa: BLE001 - mọi lỗi khởi động đều fail closed
            self.stop(f"{step}: {type(exc).__name__}: {exc}")
            return False

    def _drain_ok(self):
        kept = []
        while True:
            try:
                item = self._cmds.get_nowait()
            except queue.Empty:
                break
            if item[0] != "OK":
                kept.append(item)
        for item in kept:
            self._cmds.put(item)

    def _calibrate(self, name: str, node: IntegratedNode):
        self._check_latch()
        self._set(CALIBRATION, f"{name}: aiming light ON at HOME")
        node.move(*HOME)  # xác nhận đang đứng ở HOME trước khi bật relay
        self._drain_ok()  # OK gõ trước khi đèn bật không được tính
        node.laser_on()
        self.wait_node = name
        self._set(WAIT_OK, f"type 'OK {name}' after seeing {name}'s aiming light",
                  node=name, timeout_s=self.cfg.ok_timeout_s)
        deadline = self.clock() + self.cfg.ok_timeout_s
        while True:
            self._check_latch()
            if self.clock() >= deadline:
                raise TimeoutError(f"operator OK for {name} not received in "
                                   f"{self.cfg.ok_timeout_s:g} s")
            node._check_hb()  # luồng hb của adapter báo lỗi -> dừng
            if node.request("GET", "laser").get("on") is not True:
                raise RuntimeError(f"{name} relay OFF/STOP during WAIT_OK")
            try:
                verb, arg = self._cmds.get(timeout=0.2)
            except queue.Empty:
                continue
            if verb == "OK" and arg == name:
                break
            self.events.emit("aim_only", f"ignored '{verb} {arg or ''}'".strip() +
                             f": waiting for 'OK {name}'", level="warn")
        self.wait_node = None
        self._light_off(name, node)
        self.events.emit("aim_only", f"{name} CALIBRATION PASS: OK received, light OFF verified")

    def _light_off(self, name: str, node: IntegratedNode):
        node.request("POST", "laser", "OFF")
        if node.request("GET", "laser").get("on") is not False:
            raise RuntimeError(f"{name} relay OFF not verified")

    # --- READY: camera -> bia -> ngắm ---------------------------------------------------------

    def _supervise(self):
        now = self.clock()
        if now - self._supervised_t < self.cfg.supervise_s:
            return
        self._supervised_t = now
        for name, node in self.nodes.items():
            node._check_hb()
            status = node.request("GET", "pantilt")
            if status.get("armed") is not True or status.get("pwm_active") is not True:
                raise RuntimeError(f"{name} not armed (STOP/watchdog/Thread detach)")

    def tick(self):
        """Một vòng ở READY. Mọi lỗi an toàn -> chốt STOP."""
        if self.latched.is_set() or self.state != READY:
            return
        try:
            self._supervise()
            try:
                frame = self.frames.frame()
            except Exception as exc:
                raise RuntimeError(f"camera disconnect: {exc}") from exc
            if frame is None:
                raise RuntimeError("camera disconnect: empty frame")
            if self.clock() - self._map_t >= self.cfg.remap_s:
                try:
                    self.vision.check(self.frames.batch(self.cfg.batch_frames))
                    self._check_mapping()
                except Exception as exc:
                    raise RuntimeError(f"invalid mapping: {exc}") from exc
                self._map_t = self.clock()
            pose_time = getattr(self.vision.state, "time", None)
            if pose_time is None or time.time() - pose_time > self.cfg.pose_max_age_s:
                raise RuntimeError("stale pose: Dynamic Mapping older than "
                                   f"{self.cfg.pose_max_age_s:g} s")
            self._handle_commands()
            self._track_target(frame)
        except Latched:
            return
        except Exception as exc:  # noqa: BLE001
            self.stop(f"{type(exc).__name__}: {exc}")

    def _handle_commands(self):
        while True:
            try:
                verb, _ = self._cmds.get_nowait()
            except queue.Empty:
                return
            if verb == "VERIFY":
                self._verify()
            else:
                self.events.emit("aim_only", f"ignored {verb} in {self.state}")

    def _track_target(self, frame):
        targets = self.vision.targets(frame)
        if not targets:
            self._track.clear()
            return
        point = np.asarray(targets[0].position, float)
        if not np.isfinite(point).all():
            self._track.clear()
            return
        if self._track and np.linalg.norm(point[:2] - np.mean(self._track, axis=0)[:2]) > (
                self.cfg.stable_tol_m):
            self._track.clear()
        self._track.append(point)
        if len(self._track) < self.cfg.stable_frames:
            return
        target = np.mean(self._track, axis=0)
        target[2] = 0.0  # tag_2_table: bia nằm trên mặt bàn Z = 0
        self._track.clear()
        for previous in (self.last_aim and self.last_aim.target_m, self.last_rejected):
            if previous is not None and np.linalg.norm(target[:2] - np.asarray(previous)[:2]) <= (
                    self.cfg.same_target_tol_m):
                return  # cùng một bia đứng yên: không ngắm lại
        self._aim(target)

    def _aim(self, target: np.ndarray):
        order = sorted(self.nodes, key=lambda n: float(np.linalg.norm(
            self.vision.nodes[n].position[:2] - target[:2])) if n in self.vision.nodes else 1e9)
        reasons, plan = {}, None
        for name in order:
            try:
                plan = plan_target(self.vision, name, target, self.cfg.calibration)
                break
            except ValueError as exc:
                if "mapping/pose blocked" in str(exc) or "Dynamic Mapping" in str(exc):
                    raise RuntimeError(f"invalid mapping: {exc}") from exc
                if "CALIBRATION_REQUIRED" in str(exc):
                    raise
                reasons[name] = str(exc)
        row = {"frame": FRAME, "target_m": [round(float(v), 4) for v in target]}
        if plan is None:
            self.last_rejected = tuple(map(float, target))
            row.update(result="TARGET_REJECTED", reasons=reasons)
            self.results.append(row)
            self.events.emit("aim", f"target rejected {row['target_m']}: {reasons}", **row)
            return
        self._check_latch()
        self._set(AIMING, f"{plan.node} -> {row['target_m']} pulses {plan.pulses_us}")
        status = self.nodes[plan.node].move(*plan.pulses_us)  # TimeoutError -> servo timeout -> STOP
        self._check_latch()
        self.last_aim = _Aim(plan.node, plan.target_m, plan.pulses_us)
        self.last_rejected = None
        row.update(result="REACHED", node=plan.node, pulses_us=list(plan.pulses_us),
                   command_deg=[round(v, 3) for v in plan.command_deg],
                   status={k: status.get(k) for k in ("pan_us", "tilt_us", "target_pan_us",
                                                       "target_tilt_us", "armed", "pwm_active")})
        self.results.append(row)
        self.events.emit("aim", f"{plan.node} reached {plan.pulses_us} for {row['target_m']}", **row)
        self._set(READY)

    def _verify(self):
        """Bật đèn ngắm có giới hạn trên node vừa ngắm, đo vết chỉ từ ảnh camera thật, rồi OFF."""
        if self.last_aim is None:
            self.events.emit("aim_only", "VERIFY ignored: no reached target", level="warn")
            return
        aim = self.last_aim
        node = self.nodes[aim.node]
        self._set(VERIFY, f"{aim.node} light ON for {self.cfg.verify_light_s:g} s")
        off = self.frames.frame()
        on = None
        try:
            node.laser_on()  # adapter kiểm vẫn đứng đúng xung đích và đang ARM
            deadline = self.clock() + self.cfg.verify_light_s
            time.sleep(min(0.3, self.cfg.verify_light_s / 2))
            on = self.frames.frame()
            while self.clock() < deadline:
                self._check_latch()
                node._check_hb()
                time.sleep(0.05)
        finally:
            if not self.latched.is_set():
                self._light_off(aim.node, node)
        row = {"frame": FRAME, "node": aim.node, "target_m": list(aim.target_m),
               "pulses_us": list(aim.pulses_us), "result": "NOT MEASURABLE", "error_cm": None}
        try:  # đèn đã OFF: lỗi đo vết chỉ làm kết quả thành NOT MEASURABLE
            spot = None if on is None else self.vision.find_spot(on, off)
        except Exception as exc:  # noqa: BLE001
            spot, row["spot_error"] = None, f"{type(exc).__name__}: {exc}"
        if (spot is not None and np.isfinite(spot).all() and abs(float(spot[2])) <= 0.01
                and self.vision.in_target_area(spot)):
            row.update(result="MEASURED", spot_m=[round(float(v), 4) for v in spot],
                       error_cm=round(100 * math.hypot(spot[0] - aim.target_m[0],
                                                       spot[1] - aim.target_m[1]), 2))
        self.results.append(row)
        self.events.emit("verify", f"{aim.node} {row['result']} error_cm={row['error_cm']}", **row)
        self._set(READY)

    # --- vòng chính ---------------------------------------------------------------------------

    def run(self, exit_event: threading.Event, tick_s: float = 0.1):
        while not exit_event.is_set():
            if self.latched.is_set():
                try:
                    verb, _ = self._cmds.get(timeout=tick_s)
                except queue.Empty:
                    continue
                if verb == "RECOVER":
                    self.recover()
                else:
                    self.events.emit("aim_only", f"ignored {verb}: STOP latched, RECOVER required")
            elif self.state == START:
                self.startup()
            else:
                self.tick()
                exit_event.wait(tick_s)


# --- giao diện vận hành --------------------------------------------------------------------------

def serve_health(station: AimOnlyStation, host: str, port: int) -> ThreadingHTTPServer:
    """GET /api/health (trạng thái), POST /api/stop. OK/VERIFY/RECOVER chỉ ở console vận hành."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict):
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/api/health"):
                self._send(200, station.snapshot())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/api/stop":
                self._send(200, {"result": station.command("STOP", source="http")})
            else:
                self._send(403, {"error": "only POST /api/stop; OK/VERIFY/RECOVER are console-only"})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="aim-only-http").start()
    return server


def _console(station: AimOnlyStation, stream=sys.stdin):
    for line in stream:
        print(station.command(line), flush=True)


def is_live_source(source) -> bool:
    from .vision.stream import is_stream_url

    return is_stream_url(source) or (isinstance(source, str) and source.isdigit())


def run_profile(args) -> int:
    """Điểm vào từ run_station.py --profile vision-aim-only (phần cứng thật)."""
    from .config import load_site
    from .station import resolve_nodes
    from .vision import Vision
    from .vision.calibration import load_intrinsics
    from .vision.camera import open_camera

    site = load_site()
    site["vision"] = {**site["vision"], "reference_mode": "auto", "tabletop_anchor": "center"}
    log = REPO_ROOT / "runs/station" / f"aim-only-{datetime.now().astimezone():%Y%m%d-%H%M%S}.jsonl"
    events = EventLog(path=log)
    cfg = AimOnlyConfig(calibration=Path(args.aim_calibration),
                        nodes=tuple(args.aim_nodes.split(",")), ok_timeout_s=args.ok_timeout,
                        verify_light_s=args.verify_light_s)
    cap = open_camera(site["camera"], args.source)
    frames = CameraFrames(cap)
    vision = Vision(site, load_intrinsics(REPO_ROOT / site["camera"]["intrinsics"]))
    try:
        addresses = resolve_nodes(site.get("network") or {}, events)
    except ValueError as exc:
        addresses = {}
        events.emit("aim_only", str(exc), level="error")
    station = AimOnlyStation(site, cfg, vision, frames, addresses, events=events)
    server = serve_health(station, args.host, args.port)
    print(f"{PROFILE}: health http://{args.host}:{args.port}/api/health  log {log}")
    print("Console: 'OK s1' / 'OK s2' khi thấy đèn ngắm node đó, 'VERIFY', 'STOP', 'RECOVER'.")
    threading.Thread(target=_console, args=(station,), daemon=True, name="console").start()
    exit_event = threading.Event()
    try:
        station.run(exit_event)
    except KeyboardInterrupt:
        print("Ctrl+C: OFF/STOP...")
    finally:
        exit_event.set()
        station.close()
        server.shutdown()
        frames.close()
        print(json.dumps({"state": station.state, "reason": station.reason,
                          "shutdown": station.last_shutdown}, default=str, indent=2))
    return 0 if not any(r["errors"] for r in station.last_shutdown.values()) else 1
