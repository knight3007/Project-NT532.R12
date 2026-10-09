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

    def start(self) -> "Station":
        if self.world is not None:
            self.world.start()
        self.heartbeat.start()
        self.orch.start()
        return self

    def stop(self) -> None:
        self.orch.emergency_stop("tắt trạm")
        self.orch.stop()
        self.heartbeat.halt()
        self.hub.stop()
        if self.world is not None:
            self.world.stop()
        close = getattr(self.link, "close", None)
        if close:
            close()

    def set_decider(self, kind: str, run: str = "jev1", tau: float = 0.8) -> None:
        self.orch.decider = make_decider(kind, run, tau)
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


def _wire(site, vision, hub, link, decider, events, settings, world=None, detector_name=""):
    sensors = SensorHistory()
    orch = Orchestrator(site, vision, hub, link, decider, sensors, events, settings)
    hb = HeartbeatSender(link.heartbeat, link.nodes, site["actuator"]["heartbeat_ms"])
    warm_up(decider, events)
    return Station(site, vision, hub, link, sensors, events, orch, hb, world, detector_name)


def build_sim(decider: str = "rules", jev_run: str = "jev1", tau: float = 0.8,
              detector: str = "oracle", seed: int = 0, fps: float = 10.0,
              log_path: str | Path | None = None, settings: Settings | None = None) -> Station:
    from .sim.world import OracleDetector, SimLink, SimWorld

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
    link = SimLink(world)
    st = _wire(site, vision, hub, link, make_decider(decider, jev_run, tau), events, settings,
               world, detector)
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


def build_real(decider: str = "rules", jev_run: str = "jev1", tau: float = 0.8,
               source: int | str | None = None, fps: float = 15.0, yolo_device: str | None = None,
               log_path: str | Path | None = None, settings: Settings | None = None) -> Station:
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
    st = _wire(site, vision, hub, link, make_decider(decider, jev_run, tau), events, settings,
               detector_name="yolo")
    link.on_telemetry = st.sensors.add
    link.on_alert = st.orch.on_alert
    if vision.blockers():
        events.emit("vision", "; ".join(vision.blockers()), level="warn")
    return st
