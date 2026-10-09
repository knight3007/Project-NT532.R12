"""Đưa trạm lên Home Assistant qua MQTT Discovery: HA tự tạo thực thể, lưu lịch sử, gửi thông báo.

    bridge = MqttBridge(station, cfg).start()      # cfg = site["mqtt"]
    ...
    bridge.stop()

Chủ đề (base = nt532/<station_id>):
    <base>/status                 online/offline, retained, LWT
    <base>/<node>/state           JSON: temp, gas, hum, alarm, hb_ok, pump, laser
    <base>/station/state          JSON retained: phase, target, decider, bộ đếm, độ trễ, kết quả lượt cuối
    <base>/cmd/stop, cmd/decider  lệnh từ HA

AN TOÀN: chỉ có HAI lệnh ghi được, dừng khẩn cấp và đổi bộ quyết định. KHÔNG BAO GIỜ thêm lệnh ngắm, bắn,
bơm hay laser ở đây: mọi lệnh tác động phần cứng chỉ đi qua orchestrator sau khi qua các kiểm tra an toàn.
Trạng thái bơm/laser của node thật là suy ra từ pha orchestrator (FIRE/CORRECT của vòi đó), không đọc từ
phần cứng; mất heartbeat thì để "không rõ".
"""

import json
import logging
import os
import threading
import time
from importlib import metadata

from ..decider.state_text import nozzles_of
from ..orchestrator.fusion import alarm_limits
from ..orchestrator.machine import Phase

log = logging.getLogger("nt532.mqtt")

DEFAULT = {"enabled": False, "host": "localhost", "port": 1883, "username": None,
           "password_env": "NT532_MQTT_PASSWORD", "station_id": "nt532", "discovery_prefix": "homeassistant"}
OUTCOMES = ["extinguished", "ignored", "alarm_only", "human", "fault", "stopped", "none"]
DECIDERS = ["rules", "hybrid", "jev", "remote"]
COUNTERS = {  # khoá -> (tên, biểu tượng)
    "runs": ("Số lượt xử lý", "mdi:counter"),
    "extinguished": ("Số lượt dập tắt", "mdi:fire-off"),
    "alarm_only": ("Số lượt chỉ báo động", "mdi:alarm-light"),
    "ignored": ("Số lượt bỏ qua", "mdi:eye-off"),
    "human": ("Số lượt gọi người", "mdi:account-alert"),
    "faults": ("Số lỗi", "mdi:alert-octagon"),
    "sprays": ("Số lần phun", "mdi:water"),
}
MIN_PERIOD_S = 1.0  # số đo từng node: tối đa 1 Hz, đổi trạng thái thì gửi ngay
POLL_S = 0.2


def _onoff(v: bool | None) -> str | None:
    return None if v is None else ("ON" if v else "OFF")


class MqttBridge:
    def __init__(self, station, cfg: dict | None = None, client=None) -> None:
        self.st = station
        self.cfg = {**DEFAULT, **(cfg or {})}
        self.sid = str(self.cfg["station_id"])
        self.base = f"nt532/{self.sid}"
        self.prefix = str(self.cfg["discovery_prefix"])
        self.nodes = sorted(station.link.nodes)
        self.client = client
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: dict[str, str] = {}  # topic -> payload đã gửi
        self._last_t: dict[str, float] = {}
        self._seq = station.events.last_seq
        self._count = dict.fromkeys(COUNTERS, 0)
        self._alarm = dict.fromkeys(self.nodes, False)
        self._latency: float | None = None
        self._outcome = "none"
        self._alert_text = "không có"
        self._kind: str | None = None
        runs = station.orch.snapshot().get("runs") or []
        if runs and runs[-1].get("outcome"):
            self._outcome = runs[-1]["outcome"]
        self.heartbeat_ok_ms = max(3 * float(station.site["actuator"]["heartbeat_ms"]), 2000.0)
        self.limits = alarm_limits(station.site)

    # --- chủ đề -----------------------------------------------------------------------------

    @property
    def status_topic(self) -> str:
        return f"{self.base}/status"

    def node_topic(self, n: str) -> str:
        return f"{self.base}/{n}/state"

    @property
    def station_topic(self) -> str:
        return f"{self.base}/station/state"

    @property
    def cmd_stop(self) -> str:
        return f"{self.base}/cmd/stop"

    @property
    def cmd_decider(self) -> str:
        return f"{self.base}/cmd/decider"

    def _deciders(self) -> list[str]:
        return [d for d in DECIDERS if d != "remote" or self.st.decider_url]

    # --- Discovery --------------------------------------------------------------------------

    def device(self) -> dict:
        try:
            ver = metadata.version("nt532")
        except metadata.PackageNotFoundError:
            ver = "dev"
        return {"identifiers": [f"nt532_{self.sid}"], "name": "Trạm NT532", "manufacturer": "NT532",
                "model": "Trạm phát hiện cháy và phun nước", "sw_version": ver}

    def discovery_messages(self) -> list[tuple[str, str, bool]]:
        """[(topic, payload JSON, retain)] cho mọi thực thể; unique_id ổn định theo station_id."""
        out: list[tuple[str, str, bool]] = []
        dev = self.device()

        def add(component: str, obj: str, name: str, **cfg) -> None:
            uid = f"{self.sid}_{obj}"
            payload = {"name": name, "unique_id": uid,
                       "default_entity_id": f"{component}.{uid}", "device": dev,
                       "availability_topic": self.status_topic, "payload_available": "online",
                       "payload_not_available": "offline", **cfg}
            out.append((f"{self.prefix}/{component}/{uid}/config",
                        json.dumps(payload, ensure_ascii=False), True))

        for n in self.nodes:
            st, label = self.node_topic(n), f"Node {n}"

            def sensor(key, name, unit=None, dc=None, icon=None, st=st, n=n, label=label):
                extra = {k: v for k, v in (("unit_of_measurement", unit), ("device_class", dc),
                                           ("icon", icon)) if v}
                add("sensor", f"{n}_{key}", f"{label} {name}", state_topic=st,
                    value_template=f"{{{{ value_json.{key} }}}}", state_class="measurement", **extra)

            sensor("temp", "nhiệt độ", "°C", "temperature")
            sensor("gas", "khí gas", icon="mdi:molecule")
            sensor("hum", "độ ẩm", "%", "humidity")
            for key, name, dc in (("alarm", "báo động", "smoke"), ("hb_ok", "kết nối", "connectivity"),
                                  ("pump", "bơm chạy", "running"), ("laser", "laser bật", None)):
                extra = {"device_class": dc} if dc else {"icon": "mdi:laser-pointer"}
                add("binary_sensor", f"{n}_{key}", f"{label} {name}", state_topic=st,
                    value_template=f"{{{{ value_json.{key} }}}}", payload_on="ON", payload_off="OFF", **extra)

        stn = self.station_topic
        phases = [p.value for p in Phase]
        add("sensor", "phase", "Pha", state_topic=stn, value_template="{{ value_json.phase }}",
            device_class="enum", options=phases)
        add("sensor", "target", "Bia đang xử lý", state_topic=stn, value_template="{{ value_json.target }}",
            icon="mdi:target")
        add("sensor", "decider", "Bộ quyết định", state_topic=stn, value_template="{{ value_json.decider }}",
            icon="mdi:brain")
        for key, (name, icon) in COUNTERS.items():
            add("sensor", key, name, state_topic=stn, value_template=f"{{{{ value_json.{key} }}}}",
                state_class="total_increasing", icon=icon)
        add("sensor", "latency", "Độ trễ quyết định gần nhất", state_topic=stn,
            value_template="{{ value_json.latency_ms }}", unit_of_measurement="ms",
            device_class="duration", state_class="measurement")
        add("sensor", "outcome", "Kết quả lượt gần nhất", state_topic=stn,
            value_template="{{ value_json.outcome }}", device_class="enum", options=OUTCOMES)
        add("sensor", "last_alert", "Cảnh báo gần nhất", state_topic=stn,
            value_template="{{ value_json.last_alert }}", icon="mdi:bell-alert")
        # Điều khiển: chỉ dừng khẩn cấp và đổi bộ quyết định (xem ghi chú đầu tệp).
        add("button", "emergency_stop", "Dừng khẩn cấp", command_topic=self.cmd_stop, payload_press="PRESS",
            icon="mdi:stop-circle")
        add("select", "decider_select", "Chọn bộ quyết định", command_topic=self.cmd_decider,
            state_topic=stn, value_template="{{ value_json.decider_kind }}", options=self._deciders(),
            icon="mdi:brain")
        return out

    # --- trạng thái -------------------------------------------------------------------------

    def _poll_events(self) -> None:
        """Đọc sự kiện mới bằng events.since (không lấy mất của dashboard) để cập nhật bộ đếm."""
        for ev in self.st.events.since(self._seq):
            self._seq = max(self._seq, ev["seq"])
            kind = ev.get("kind")
            if kind == "run":
                oc = ev.get("outcome") or "none"
                self._count["runs"] += 1
                self._outcome = oc
                key = "faults" if oc == "fault" else oc
                if key in self._count:
                    self._count[key] += 1
            elif kind == "phase" and ev.get("phase") == Phase.FIRE.value:
                self._count["sprays"] += 1
            elif kind == "decision":
                lat = (ev.get("decision") or {}).get("latency_ms")
                if lat is not None:
                    self._latency = float(lat)
            elif kind == "alert":
                node = ev.get("node")
                if node in self._alarm:
                    cleared = "hết báo động" in str(ev.get("text", ""))
                    self._alarm[node] = not cleared
                    if not cleared:
                        self._alert_text = str(ev["text"])[:200]

    def states(self) -> dict[str, dict]:
        """{topic: payload} hiện tại, tính từ Station."""
        self._poll_events()
        snap = self.st.orch.snapshot()
        run = snap.get("run") or {}
        phase = snap.get("phase", "IDLE")
        out: dict[str, dict] = {}
        for n in self.nodes:
            s = (self.st.sensors.samples(n, 1) or [None])[-1]
            hb = ((snap.get("nozzles") or {}).get(n) or {}).get("hb_ms")
            hb_ok = hb is not None and hb < self.heartbeat_ok_ms
            busy = bool(run.get("nozzle")) and n in nozzles_of(run["nozzle"])  # BOTH: cả hai vòi
            out[self.node_topic(n)] = {
                "temp": None if s is None else round(s.temp, 1),
                "gas": None if s is None else round(s.gas),
                "hum": None if s is None or s.hum is None else round(s.hum),
                "alarm": _onoff(self._alarm[n]), "hb_ok": _onoff(hb_ok),
                # suy ra từ pha, không đọc phần cứng; mất heartbeat thì không rõ
                "pump": _onoff(phase == Phase.FIRE.value and busy) if hb_ok else None,
                "laser": _onoff(phase == Phase.CORRECT.value and busy) if hb_ok else None,
            }
        name = str(snap.get("decider") or "")
        kind = name.split("(")[0] if name.split("(")[0] in DECIDERS else None
        tgt = run.get("target") or {}
        out[self.station_topic] = {
            "phase": phase, "target": tgt.get("id") or "none", "decider": name, "decider_kind": kind,
            **self._count, "latency_ms": self._latency, "outcome": self._outcome,
            "last_alert": self._alert_text,
        }
        return out

    def publish_states(self, force: bool = False) -> None:
        now = time.monotonic()
        for topic, state in self.states().items():
            payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
            prev = self._last.get(topic)
            if not force and prev == payload:
                continue
            # chỉ đổi số đo (không đổi cờ) thì giữ nhịp tối đa 1 Hz
            if (not force and prev is not None and topic != self.station_topic
                    and now - self._last_t.get(topic, 0) < MIN_PERIOD_S
                    and self._flags(json.loads(prev)) == self._flags(state)):
                continue
            self._last[topic], self._last_t[topic] = payload, now
            self._pub(topic, payload, retain=topic == self.station_topic)

    @staticmethod
    def _flags(state: dict) -> tuple:
        return tuple(state.get(k) for k in ("alarm", "hb_ok", "pump", "laser"))

    def _pub(self, topic: str, payload: str, retain: bool = False) -> None:
        try:
            self.client.publish(topic, payload, qos=1, retain=retain)
        except Exception as e:  # noqa: BLE001 - mất kết nối không được làm sập trạm
            log.warning("không gửi được %s: %s", topic, e)

    # --- lệnh -------------------------------------------------------------------------------

    def handle_command(self, topic: str, payload) -> bool:
        """Trả True nếu lệnh hợp lệ và đã thi hành. Chủ đề lạ hoặc payload sai thì bỏ qua."""
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes | bytearray) else str(payload)
        text = text.strip()
        if topic == self.cmd_stop:
            if text != "PRESS":
                return False
            self.st.orch.emergency_stop("Home Assistant")
            return True
        if topic == self.cmd_decider:
            if text not in self._deciders():
                log.warning("bộ quyết định lạ từ HA: %r", text[:40])
                return False
            try:
                self.st.set_decider(text)
            except Exception as e:  # noqa: BLE001 - thiếu mô hình/URL: báo lên nhật ký, trạm vẫn chạy
                self.st.events.emit("config", f"HA đổi bộ quyết định {text} thất bại: {e}", level="error")
                return False
            self.publish_states()
            return True
        return False

    # --- vòng đời ---------------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if getattr(reason_code, "is_failure", False):
            log.warning("MQTT từ chối kết nối: %s", reason_code)
            return
        log.info("MQTT đã nối %s:%s", self.cfg["host"], self.cfg["port"])
        self.client = client
        for topic, payload, retain in self.discovery_messages():
            self._pub(topic, payload, retain)
        for t in (self.cmd_stop, self.cmd_decider):
            client.subscribe(t, qos=1)
        self._pub(self.status_topic, "online", retain=True)
        self.publish_states(force=True)

    def _on_message(self, client, userdata, msg) -> None:
        if getattr(msg, "retain", False):
            return  # lệnh cũ còn lưu trên broker không được thi hành lại
        try:
            self.handle_command(msg.topic, msg.payload)
        except Exception:
            log.exception("lỗi xử lý lệnh %s", msg.topic)

    def _make_client(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:
            raise RuntimeError("thiếu gói paho-mqtt cho cầu nối MQTT: cài bằng `uv sync --extra mqtt`") from e
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"nt532-{self.sid}")
        user = self.cfg.get("username")
        if user:
            c.username_pw_set(user, os.environ.get(self.cfg.get("password_env") or "") or None)
        c.will_set(self.status_topic, "offline", qos=1, retain=True)
        c.reconnect_delay_set(1, 30)
        return c

    def start(self) -> "MqttBridge":
        if self.client is None:
            self.client = self._make_client()
        self.client.on_connect, self.client.on_message = self._on_connect, self._on_message
        self.client.connect_async(self.cfg["host"], int(self.cfg["port"]), keepalive=30)
        self.client.loop_start()  # luồng mạng của paho tự nối lại khi mất kết nối
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mqtt-bridge")
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._halt.wait(POLL_S):
            try:
                if self.client.is_connected():
                    self.publish_states()
                else:
                    self._poll_events()  # vẫn đếm sự kiện lúc mất kết nối
            except Exception:
                log.exception("lỗi vòng MQTT")

    def stop(self) -> None:
        self._halt.set()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            self._pub(self.status_topic, "offline", retain=True)
            self.client.disconnect()
            self.client.loop_stop()
        except Exception as e:  # noqa: BLE001
            log.debug("lỗi khi đóng MQTT: %s", e)
