"""Lắp các phần lại thành một trạm chạy được: camera, thị giác, cảm biến, bộ quyết định, link, orchestrator.

    station = build_sim(decider="rules")       # sa bàn ảo, chạy ngay không cần phần cứng
    station = build_real(decider="hybrid")     # webcam + CoAP tới node thật
    station.start()

Dashboard (nt532.dashboard) chỉ đọc `Station` và gửi lệnh qua nó.
"""

import threading
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .config import REPO_ROOT, load_site
from .net.link import HeartbeatSender, NodeLink
from .net.protocol import Deduper
from .orchestrator.decide import JevDecider, make_decider
from .orchestrator.events import EventLog
from .orchestrator.frames import FrameHub
from .orchestrator.fusion import SensorHistory
from .orchestrator.machine import Orchestrator, Settings
from .vision import Vision


@dataclass
class Station:
    site: dict
    vision: Vision
    hub: FrameHub
    link: NodeLink
    sensors: SensorHistory
    events: EventLog
    orch: Orchestrator
    heartbeat: HeartbeatSender
    world: object | None = None  # SimWorld khi chạy sa bàn ảo
    detector_name: str = ""
    fake_nodes: dict | None = None  # {tên: FakeNode} khi build_sim(link="coap")

    def start(self, orchestrate: bool = True) -> "Station":
        """`orchestrate=False`: chỉ bật sa bàn ảo và heartbeat, orchestrator không chạy nên không tự
        xử lý cảnh báo (dùng cho các script đo tay như aim_point.py)."""
        if self.world is not None:
            self.world.start()
        self.heartbeat.start()
        if orchestrate:
            self.orch.start()
        return self

    def stop(self) -> None:
        self.orch.emergency_stop("tắt trạm")
        self.orch.stop()
        self.heartbeat.halt()
        self.hub.stop()
        if self.world is not None:
            self.world.stop()
        for fn in (self.fake_nodes or {}).values():
            fn.stop()
        close = getattr(self.link, "close", None)
        if close:
            close()

    def set_decider(self, kind: str, run: str = "jev1", tau: float = 0.8, stages: str = "verify") -> None:
        self.orch.decider = make_decider(kind, run, tau, stages=stages)
        self.events.emit("config", f"bộ quyết định: {self.orch.decider.name}")
        warm_up(self.orch.decider, self.events)


def warm_up(decider, events: EventLog) -> None:
    """Nạp mô hình ở luồng nền (mất vài chục giây) để lần cảnh báo đầu không phải chờ."""
    jev = getattr(decider, "model_decider", decider)
    if not isinstance(jev, JevDecider):
        return

    def load():
        t0 = time.monotonic()
        try:
            # backbone nạp lười ở lần chạy thuận đầu: chạy thử một bản ghi giả
            jev.model.predict_logits([{"state": "stage: decide", "questions": {
                "real_fire": {"type": "boolean", "candidates": []}}}])
            events.emit("config", f"đã nạp mô hình {jev.path.name} sau {time.monotonic() - t0:.0f} s")
        except Exception as e:  # noqa: BLE001
            events.emit("config", f"không nạp được mô hình: {type(e).__name__}: {e}", level="error")

    threading.Thread(target=load, daemon=True, name="warm-up").start()


def _yolo(site: dict, device: str | None = None):
    from .vision.detect import detect

    cfg = site["vision"]
    weights = REPO_ROOT / cfg["weights"]
    if not weights.exists():
        raise FileNotFoundError(f"chưa có trọng số YOLO {weights}")
    return partial(detect, weights=weights, conf=cfg["detect_conf"], imgsz=cfg["imgsz"], device=device)


def _wire(site, vision, hub, link, decider, events, settings, world=None, detector_name="",
          fake_nodes=None):
    sensors = SensorHistory()
    orch = Orchestrator(site, vision, hub, link, decider, sensors, events, settings)
    hb = HeartbeatSender(link.heartbeat, link.nodes, site["actuator"]["heartbeat_ms"])
    warm_up(decider, events)
    return Station(site, vision, hub, link, sensors, events, orch, hb, world, detector_name, fake_nodes)


def build_sim(decider: str = "rules", jev_run: str = "jev1", tau: float = 0.8,
              detector: str = "oracle", seed: int = 0, fps: float = 10.0,
              log_path: str | Path | None = None, settings: Settings | None = None,
              link: str = "mem", model_stages: str = "verify") -> Station:
    """`link="mem"`: node ảo trong bộ nhớ (SimLink). `"coap"`: mỗi node là một FakeNode chạy lõi C của
    firmware, lệnh và telemetry đi qua UDP localhost bằng CoapLink thật."""
    from .sim.world import OracleDetector, SimLink, SimWorld

    if link not in ("mem", "coap"):
        raise ValueError(f"link phải là mem hoặc coap, nhận {link!r}")

    events = EventLog(path=log_path)
    world = SimWorld(seed=seed)
    site = world.site
    if detector == "oracle":
        det = OracleDetector(world, site["vision"]["detect_conf"], seed=seed + 1)
    else:
        det = _yolo(site)
    vision = Vision(site, world.scene.intrinsics, det)
    if isinstance(det, OracleDetector):
        det.vision = vision
    hub = FrameHub(world.read, fps).start()
    state = vision.commission(hub.frames(site["vision"]["commission_frames"]))
    events.emit("vision", f"commissioning sa bàn ảo: chiếu lại {state.reprojection_px:.2f} px, "
                          f"node {sorted(state.nodes)}")
    fakes = None
    if link == "coap":
        link, fakes = _coap_nodes(world, events)
    else:
        link = SimLink(world)
    st = _wire(site, vision, hub, link, make_decider(decider, jev_run, tau, stages=model_stages),
               events, settings, world, detector, fakes)
    if fakes:
        # telemetry và cảnh báo do node giả gửi qua UDP; world không tự đẩy nữa (tránh nạp hai lần)
        link.on_telemetry, link.on_alert = st.sensors.add, st.orch.on_alert
        events.emit("config", f"trạm ảo qua CoAP: node giả {sorted(fakes)} trên 127.0.0.1")
        return st
    dedup = Deduper()

    def on_tel(tel):
        st.sensors.add(tel)
        link.on_rx(tel.n)

    def on_alert(alert):
        if not dedup.seen((alert.n, alert.s)):
            st.orch.on_alert(alert)

    world.on_telemetry, world.on_alert = on_tel, on_alert
    events.emit("config", f"trạm ảo sẵn sàng: detector {detector}, bộ quyết định {st.orch.decider.name}")
    return st


def _coap_nodes(world, events):
    """CoapLink thật tới một FakeNode cho mỗi node của sa bàn; mọi thứ trên 127.0.0.1."""
    from .net.coap import CoapLink
    from .net.fakenode import FakeNode, SimWorldHardware, SimWorldSensors, free_port

    transports = ["simplesocketserver"]
    pi_port = free_port()
    ports = {n: free_port() for n in world.nodes}
    link = CoapLink({n: f"127.0.0.1:{p}" for n, p in ports.items()}, ("127.0.0.1", pi_port),
                    transports=transports).start()
    temp, gas = world.limits["temp"], world.limits["gas"]
    fakes = {}
    for n, port in ports.items():
        peers = [f"coap://127.0.0.1:{p}" for m, p in ports.items() if m != n]
        fakes[n] = FakeNode(
            n, ("127.0.0.1", port), f"coap://127.0.0.1:{pi_port}", SimWorldHardware(world, n),
            SimWorldSensors(world, n), telemetry_s=world.telemetry_period, temp_c=temp, gas=gas,
            transports=transports, alarm_peers=peers,
            on_event=lambda kind, text, n=n: kind in ("status", "alarm", "drop") and
            events.emit("node", f"{n} {kind} {text}"))
    try:
        for fn in fakes.values():
            fn.start()
    except Exception:
        for fn in fakes.values():
            fn.stop()
        link.close()
        raise
    return link, fakes


def build_real(decider: str = "rules", jev_run: str = "jev1", tau: float = 0.8,
               source: int | str | None = None, fps: float = 15.0, yolo_device: str | None = None,
               log_path: str | Path | None = None, settings: Settings | None = None,
               model_stages: str = "verify") -> Station:
    """Webcam + CoAP. Cần calibration/camera.yaml, commissioning và `network.nodes` trong site.yaml."""
    from .net.coap import CoapLink
    from .vision import open_camera

    events = EventLog(path=log_path)
    site = load_site()
    vision = Vision.from_site(site, _yolo(site, yolo_device))
    cap = open_camera(site["camera"], source)

    def read():
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("không đọc được khung hình")
        return frame

    # Stream từ điện thoại tự đóng dấu thời điểm chụp (đã trừ độ trễ), để fresh() sau khi bật laser đúng
    hub = FrameHub(getattr(cap, "read_stamped", read), fps).start()
    net = site.get("network") or {}
    nodes = {n: a for n, a in (net.get("nodes") or {}).items() if a}
    if not nodes:
        raise ValueError("chưa khai địa chỉ node trong network.nodes của config/site.yaml")
    link = CoapLink(nodes, (net.get("bind", "::"), int(net.get("port", 5683))),
                    transports=net.get("transports")).start()
    st = _wire(site, vision, hub, link, make_decider(decider, jev_run, tau, stages=model_stages),
               events, settings, detector_name="yolo")
    link.on_telemetry = st.sensors.add
    link.on_alert = st.orch.on_alert
    if vision.blockers():
        events.emit("vision", "; ".join(vision.blockers()), level="warn")
    return st
