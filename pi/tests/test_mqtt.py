"""Cầu nối MQTT: kiểm tra Discovery và ánh xạ trạng thái bằng client giả (không cần broker); test tích hợp
chạy khi đặt NT532_MQTT_BROKER=host:port (CI chạy mosquitto thật)."""

import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from nt532.decider.state_text import BOTH
from nt532.integrations.mqtt import COUNTERS, MqttBridge
from nt532.orchestrator.events import EventLog
from nt532.orchestrator.fusion import SensorHistory


def tel(**kw):
    return SimpleNamespace(**kw)  # SensorHistory.add chỉ đọc n, s, t, g, h


SITE = {"actuator": {"heartbeat_ms": 500}, "sensors": {"temp_alarm_c": 45, "gas_alarm": 600}}


class FakeClient:
    def __init__(self):
        self.sent, self.subs = [], []

    def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append((topic, payload, retain))

    def subscribe(self, topic, qos=0):
        self.subs.append(topic)

    def is_connected(self):
        return True


class FakeOrch:
    def __init__(self):
        self.stops, self.snap = [], {"phase": "IDLE", "run": None, "runs": [], "decider": "rules",
                                     "nozzles": {"s1": {"hb_ms": 100.0}, "s2": {"hb_ms": None}}}

    def snapshot(self):
        return self.snap

    def emergency_stop(self, reason="x"):
        self.stops.append(reason)


def make_station(decider_url=None):
    events = EventLog()
    st = SimpleNamespace(site=SITE, link=SimpleNamespace(nodes=["s1", "s2"]), events=events,
                         sensors=SensorHistory(), orch=FakeOrch(), decider_url=decider_url, set_calls=[])

    def set_decider(kind, *a, **k):
        if kind == "jev":
            raise FileNotFoundError("không có mô hình")
        st.set_calls.append(kind)

    st.set_decider = set_decider
    return st


@pytest.fixture
def bridge():
    st = make_station()
    b = MqttBridge(st, {"station_id": "t1"}, client=FakeClient())
    return b


def disc(b):
    return {t: json.loads(p) for t, p, _ in b.discovery_messages()}


def test_discovery_topics_retained_unique_and_device(bridge):
    msgs = bridge.discovery_messages()
    assert all(r for _, _, r in msgs)
    topics = [t for t, _, _ in msgs]
    assert len(set(topics)) == len(topics)
    assert all(t.startswith("homeassistant/") and t.endswith("/config") for t in topics)
    d = disc(bridge)
    uids = [c["unique_id"] for c in d.values()]
    assert len(set(uids)) == len(uids) and all(u.startswith("t1_") for u in uids)
    # ổn định giữa các lần sinh
    assert [m[1] for m in bridge.discovery_messages()] == [m[1] for m in msgs]
    devs = {json.dumps(c["device"], sort_keys=True) for c in d.values()}
    assert len(devs) == 1
    dev = next(iter(d.values()))["device"]
    assert dev["name"] == "Trạm NT532" and dev["sw_version"] and dev["identifiers"]
    t = d["homeassistant/sensor/t1_s1_temp/config"]
    assert t["unit_of_measurement"] == "°C" and t["device_class"] == "temperature"
    assert t["state_class"] == "measurement"
    assert d["homeassistant/binary_sensor/t1_s2_hb_ok/config"]["device_class"] == "connectivity"
    for key in COUNTERS:
        assert d[f"homeassistant/sensor/t1_{key}/config"]["state_class"] == "total_increasing"
    assert all(c["availability_topic"] == "nt532/t1/status" for c in d.values())


def test_only_stop_and_select_are_writable(bridge):
    cmds = {c["command_topic"] for c in disc(bridge).values() if "command_topic" in c}
    assert cmds == {"nt532/t1/cmd/stop", "nt532/t1/cmd/decider"}
    text = " ".join(t for t, _, _ in bridge.discovery_messages())
    for bad in ("aim", "fire", "pump/set", "laser/set"):
        assert bad not in text.replace("emergency", "")
    for c in disc(bridge).values():
        assert c.get("platform") is None
    comps = {t.split("/")[1] for t, p, _ in bridge.discovery_messages() if "command_topic" in json.loads(p)}
    assert comps <= {"button", "select"}


def test_select_options_follow_remote_url():
    assert "remote" not in disc(MqttBridge(make_station(), client=FakeClient()))[
        "homeassistant/select/nt532_decider_select/config"]["options"]
    b = MqttBridge(make_station("http://x:8090"), client=FakeClient())
    assert "remote" in disc(b)["homeassistant/select/nt532_decider_select/config"]["options"]


def test_state_mapping(bridge):
    st = bridge.st
    st.sensors.add(tel(n="s1", s=1, t=31.26, g=120.4, h=55))
    st.orch.snap.update(phase="FIRE", run={"nozzle": "s1", "target": {"id": "T3"}},
                        decider="hybrid(tau 0.8, mô hình ở verify)")
    st.events.emit("alert", "cảnh báo từ s1 (nhiệt 50, khí 700)", level="warn", node="s1")
    st.events.emit("phase", "FIRE", phase="FIRE")
    st.events.emit("decision", "x", decision={"latency_ms": 12.5})
    st.events.emit("run", "xong", outcome="extinguished")
    st.events.emit("run", "xong", outcome="fault")
    s = bridge.states()
    n1, n2, stn = s["nt532/t1/s1/state"], s["nt532/t1/s2/state"], s["nt532/t1/station/state"]
    assert (n1["temp"], n1["gas"], n1["hum"]) == (31.3, 120, 55)
    assert (n1["alarm"], n1["hb_ok"], n1["pump"], n1["laser"]) == ("ON", "ON", "ON", "OFF")
    # s2 chưa có số đo, mất heartbeat: không rõ bơm/laser
    assert n2["temp"] is None and n2["hb_ok"] == "OFF" and n2["pump"] is None and n2["laser"] is None
    assert stn["phase"] == "FIRE" and stn["target"] == "T3" and stn["decider_kind"] == "hybrid"
    assert (stn["runs"], stn["extinguished"], stn["faults"], stn["sprays"]) == (2, 1, 1, 1)
    assert stn["latency_ms"] == 12.5 and stn["outcome"] == "fault"
    st.events.emit("alert", "s1 hết báo động", node="s1")
    assert bridge.states()["nt532/t1/s1/state"]["alarm"] == "OFF"


def test_both_nozzles_show_pump_on_for_both_nodes(bridge):
    st = bridge.st
    for n in ("s1", "s2"):
        st.sensors.add(tel(n=n, s=1, t=30.0, g=100.0, h=55))
    st.orch.snap.update(phase="FIRE", run={"nozzle": BOTH, "target": {"id": "T1"}},
                        nozzles={"s1": {"hb_ms": 100.0}, "s2": {"hb_ms": 100.0}})
    s = bridge.states()
    assert [s[f"nt532/t1/{n}/state"]["pump"] for n in ("s1", "s2")] == ["ON", "ON"]
    st.orch.snap.update(run={"nozzle": "s2", "target": {"id": "T1"}})  # một vòi: chỉ vòi đó
    s = bridge.states()
    assert [s[f"nt532/t1/{n}/state"]["pump"] for n in ("s1", "s2")] == ["OFF", "ON"]
    st.orch.snap.update(phase="VERIFY", run={"nozzle": BOTH, "target": {"id": "T1"}})
    s = bridge.states()
    assert [s[f"nt532/t1/{n}/state"]["pump"] for n in ("s1", "s2")] == ["OFF", "OFF"]


def test_pump_and_laser_follow_overlay_active(bridge):
    st = bridge.st
    for n in ("s1", "s2"):
        st.sensors.add(tel(n=n, s=1, t=30.0, g=100.0, h=55))
    st.orch.snap.update(run={"nozzle": BOTH, "target": {"id": "T1"}},
                        nozzles={"s1": {"hb_ms": 100.0}, "s2": {"hb_ms": 100.0}})

    def flags(phase, active):
        st.orch.snap.update(phase=phase, overlay={"active": active})
        s = bridge.states()
        return {k: [s[f"nt532/t1/{n}/state"][k] for n in ("s1", "s2")] for k in ("pump", "laser")}

    # lượt hai vòi: CORRECT lần lượt từng vòi, mỗi lúc chỉ một laser, chưa bơm nào chạy
    assert flags("CORRECT", ["s1"]) == {"pump": ["OFF", "OFF"], "laser": ["ON", "OFF"]}
    assert flags("CORRECT", ["s2"]) == {"pump": ["OFF", "OFF"], "laser": ["OFF", "ON"]}
    assert flags("AIM", ["s2"]) == {"pump": ["OFF", "OFF"], "laser": ["OFF", "OFF"]}  # AIM chưa bật laser
    # FIRE: chưa bơm nào chạy lúc ngắm lần cuối (active rỗng), rồi hai bơm cùng chạy
    assert flags("FIRE", []) == {"pump": ["OFF", "OFF"], "laser": ["OFF", "OFF"]}
    assert flags("FIRE", ["s1", "s2"]) == {"pump": ["ON", "ON"], "laser": ["OFF", "OFF"]}
    # vòi bị bỏ giữa lượt thì không còn trong active dù run.nozzle chưa kịp đổi
    assert flags("FIRE", ["s1"]) == {"pump": ["ON", "OFF"], "laser": ["OFF", "OFF"]}
    assert flags("VERIFY", []) == {"pump": ["OFF", "OFF"], "laser": ["OFF", "OFF"]}
    # mất heartbeat thì không rõ, kể cả khi vòi đó nằm trong active
    st.orch.snap["nozzles"]["s2"]["hb_ms"] = None
    assert flags("FIRE", ["s1", "s2"]) == {"pump": ["ON", None], "laser": ["OFF", None]}


def test_pump_and_laser_fall_back_to_run_nozzle_without_active(bridge):
    st = bridge.st
    for n in ("s1", "s2"):
        st.sensors.add(tel(n=n, s=1, t=30.0, g=100.0, h=55))
    st.orch.snap.update(phase="CORRECT", run={"nozzle": "s2", "target": {"id": "T1"}},
                        nozzles={"s1": {"hb_ms": 100.0}, "s2": {"hb_ms": 100.0}}, overlay={})
    s = bridge.states()  # overlay có nhưng thiếu `active` (snapshot cũ): dùng vòi của lượt
    assert [s[f"nt532/t1/{n}/state"]["laser"] for n in ("s1", "s2")] == ["OFF", "ON"]
    st.orch.snap.update(phase="FIRE", run={"nozzle": BOTH, "target": {"id": "T1"}})
    st.orch.snap.pop("overlay")  # hẳn không có overlay
    s = bridge.states()
    assert [s[f"nt532/t1/{n}/state"]["pump"] for n in ("s1", "s2")] == ["ON", "ON"]


def test_events_not_consumed(bridge):
    bridge.st.events.emit("alert", "a", node="s1")
    bridge.states()
    assert len(bridge.st.events.since(0)) == 1


def test_publish_rate_and_retain(bridge):
    st, c = bridge.st, bridge.client
    st.sensors.add(tel(n="s1", s=1, t=30.0, g=100, h=None))
    bridge.publish_states(force=True)
    sent = {t: r for t, _, r in c.sent}
    assert sent["nt532/t1/station/state"] is True
    c.sent.clear()
    bridge.publish_states()
    assert c.sent == []  # không đổi thì không gửi
    st.sensors.add(tel(n="s1", s=2, t=31.0, g=100, h=None))
    bridge.publish_states()
    assert c.sent == []  # đổi số đo trong vòng 1 s: giữ nhịp
    st.events.emit("alert", "cảnh báo", node="s1")
    bridge.publish_states()
    # đổi cờ: gửi ngay dù chưa đủ 1 s; trạm cũng gửi vì last_alert đổi
    assert sorted(t for t, _, _ in c.sent) == ["nt532/t1/s1/state", "nt532/t1/station/state"]
    assert json.loads({t: p for t, p, _ in c.sent}["nt532/t1/s1/state"])["alarm"] == "ON"


def test_commands(bridge):
    st = bridge.st
    assert bridge.handle_command("nt532/t1/cmd/stop", b"PRESS")
    assert st.orch.stops == ["Home Assistant"]
    for bad in (b"", b"press now", b"\xff\xfe"):
        assert not bridge.handle_command("nt532/t1/cmd/stop", bad)
    assert len(st.orch.stops) == 1
    assert bridge.handle_command("nt532/t1/cmd/decider", b"hybrid")
    assert st.set_calls == ["hybrid"]
    assert not bridge.handle_command("nt532/t1/cmd/decider", b"; rm -rf")
    assert not bridge.handle_command("nt532/t1/cmd/decider", b"remote")  # chưa có URL
    assert not bridge.handle_command("nt532/t1/cmd/decider", b"jev")  # set_decider lỗi: nuốt, ghi nhật ký
    assert any(e["level"] == "error" for e in st.events.since(0))
    assert not bridge.handle_command("nt532/t1/cmd/aim", b"PRESS")
    assert not bridge.handle_command("nt532/t1/s1/fire", b"ON")
    assert st.set_calls == ["hybrid"]


def test_on_connect_publishes_everything(bridge):
    c = bridge.client
    bridge._on_connect(c, None, {}, 0)
    topics = {t: r for t, _, r in c.sent}
    assert topics["nt532/t1/status"] is True
    assert any(t.startswith("homeassistant/") for t in topics)
    assert set(c.subs) == {"nt532/t1/cmd/stop", "nt532/t1/cmd/decider"}
    # lệnh retained còn trên broker bị bỏ qua
    bridge._on_message(c, None, SimpleNamespace(topic="nt532/t1/cmd/stop", payload=b"PRESS", retain=True))
    assert bridge.st.orch.stops == []


# --- tích hợp với broker thật ---------------------------------------------------------------

BROKER = os.environ.get("NT532_MQTT_BROKER")


@pytest.mark.skipif(not BROKER, reason="đặt NT532_MQTT_BROKER=host:port để chạy với mosquitto thật")
def test_integration_real_broker():
    import paho.mqtt.client as mqtt

    from nt532.station import build_sim

    host, _, port = BROKER.partition(":")
    sid = f"ci{os.getpid()}"
    got: dict[str, str] = {}
    lock = threading.Lock()

    def on_msg(c, u, m):
        if m.topic.startswith("homeassistant/") and f"/{sid}_" not in m.topic:
            return  # thực thể của trạm khác trên cùng broker
        with lock:
            got[m.topic] = m.payload.decode()

    watcher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    watcher.on_message = on_msg
    watcher.connect(host, int(port or 1883))
    # `+` phải chiếm trọn một cấp chủ đề (không viết được `<sid>_+`), nên lọc theo sid trong on_msg
    watcher.subscribe([(f"nt532/{sid}/#", 1), ("homeassistant/+/+/config", 1)])
    watcher.loop_start()
    st = build_sim("rules")
    bridge = None
    try:
        st.start()
        bridge = MqttBridge(st, {"host": host, "port": int(port or 1883), "station_id": sid}).start()

        def wait(pred, timeout=20.0):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                with lock:
                    if pred(got):
                        return True
                time.sleep(0.1)
            return False

        assert wait(lambda g: g.get(f"nt532/{sid}/status") == "online")
        assert wait(lambda g: f"homeassistant/button/{sid}_emergency_stop/config" in g)
        assert wait(lambda g: f"homeassistant/sensor/{sid}_s1_temp/config" in g)
        assert wait(lambda g: json.loads(g.get(f"nt532/{sid}/s1/state", "{}")).get("temp") is not None)
        assert json.loads(got[f"nt532/{sid}/station/state"])["phase"] == "IDLE"
        n0 = len(st.events.since(0))
        watcher.publish(f"nt532/{sid}/cmd/stop", "PRESS", qos=1)
        end = time.monotonic() + 10
        while time.monotonic() < end and not any("Home Assistant" in e["text"]
                                                 for e in st.events.since(0)[n0 - 1:]):
            time.sleep(0.1)
        assert any("DỪNG: Home Assistant" in e["text"] for e in st.events.since(0))
    finally:
        if bridge:
            bridge.stop()
        st.stop()
        watcher.loop_stop()
        watcher.disconnect()
