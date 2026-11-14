"""Node thật (firmware A/B tích hợp): poll GET /status thành Telemetry/Alert, chọn link theo network.link."""

import json
import time
from types import SimpleNamespace

import pytest
from aiocoap.numbers.codes import CONTENT, NOT_FOUND

from nt532.net.coap import CoapLink
from nt532.net.fakenode import free_port
from nt532.net.integrated_link import IntegratedLink
from nt532.net.link import LinkError
from nt532.station import _make_link

A_STATUS = {"n": "A", "role": "leader", "stopped": True, "hb_age_ms": -1, "hb_fresh": False, "stop_count": 0,
            "servo_armed": False, "servo_pwm": False, "relay_on": False, "laser_on": False,
            "mq2_sensor_ok": True, "mq2_adc_raw": 812, "mq2_alarm": None}
B_STATUS = {"n": "B", "role": "router", "stopped": True, "hb_age_ms": -1, "hb_fresh": False, "stop_count": 0,
            "servo_armed": False, "servo_pwm": False, "relay_on": False, "laser_on": False,
            "sensor_ok": True, "temperature_c": 31.5, "humidity_pct": 62.0, "sample_age_ms": 800,
            "last_attempt_age_ms": 800, "sensor_error": None}


class Rig:
    """IntegratedLink chưa start; `_call` trả JSON đã đặt (hoặc ném LinkError) thay cho mạng."""

    def __init__(self):
        self.link = IntegratedLink({"A": "127.0.0.1:1", "B": "127.0.0.1:2"})
        self.body: dict[str, object] = {}
        self.tel, self.alerts = [], []
        self.link.on_telemetry, self.link.on_alert = self.tel.append, self.alerts.append
        self.link._call = self._call

    def _call(self, node, method, path, payload=b""):
        assert path == "status"
        b = self.body[node]
        if b is None:
            raise LinkError("timeout")
        if isinstance(b, int):
            return SimpleNamespace(code=b, payload=b"")
        self.link.on_rx(node)
        return SimpleNamespace(code=CONTENT, payload=json.dumps(b).encode() if not isinstance(b, bytes) else b)


def test_node_a_gas_raw_no_alarm():
    r = Rig()
    r.body["A"] = A_STATUS
    r.link.poll_once("A")
    assert len(r.tel) == 1
    t = r.tel[0]
    assert (t.n, t.g, t.t, t.h) == ("A", 812.0, 0.0, None)
    assert not r.alerts
    assert r.link.hb_age_ms("A") < 1000


def test_node_b_dht11():
    r = Rig()
    r.body["B"] = B_STATUS
    r.link.poll_once("B")
    t = r.tel[0]
    assert (t.n, t.t, t.h, t.g) == ("B", 31.5, 62.0, 0.0)


def test_alarm_rising_edge_once_then_clear():
    r = Rig()
    for alarm, expect in [(None, []), (True, ["alert"]), (True, ["alert"]), (None, ["alert"]),
                          (False, ["alert", "clear"]), (False, ["alert", "clear"]), (True, ["alert", "clear", "alert"])]:
        r.body["A"] = {**A_STATUS, "mq2_alarm": alarm}
        r.link.poll_once("A")
        assert [a.k for a in r.alerts] == expect
    assert r.alerts[0].g == 812.0
    assert len({a.s for a in r.alerts}) == len(r.alerts)


def test_sensor_failure_no_fabricated_value():
    r = Rig()
    r.body["A"] = {**A_STATUS, "mq2_sensor_ok": False, "mq2_adc_raw": None}
    r.body["B"] = {**B_STATUS, "sensor_ok": False, "temperature_c": None, "humidity_pct": None}
    r.link.poll_once("A")
    r.link.poll_once("B")
    assert r.tel == []
    assert r.link.hb_age_ms("A") < 1000  # vẫn trả lời nên còn sống


def test_unreachable_and_garbage_are_skipped():
    r = Rig()
    r.body["A"] = None
    r.link.poll_once("A")
    r.body["A"] = NOT_FOUND
    r.link.poll_once("A")
    assert r.link.hb_age_ms("A") == float("inf")  # chưa từng nghe: orchestrator coi là offline
    r.body["A"] = b"{not json"
    r.link.poll_once("A")
    r.body["A"] = b"[1]"
    r.link.poll_once("A")
    assert r.tel == [] and r.alerts == []


def test_poll_loop_start_and_close():
    port = free_port()
    link = IntegratedLink({"A": "127.0.0.1:1"}, ("127.0.0.1", port), timeout_s=0.5,
                          transports=["simplesocketserver"], status_poll_s=0.05)
    got = []
    link.on_telemetry = got.append

    def fake_call(node, method, path, payload=b""):
        link.on_rx(node)
        return SimpleNamespace(code=CONTENT, payload=json.dumps(A_STATUS).encode())

    link._call = fake_call
    link.start()
    try:
        end = time.monotonic() + 5
        while len(got) < 3 and time.monotonic() < end:
            time.sleep(0.02)
        assert len(got) >= 3
        assert [t.s for t in got[:3]] == [1, 2, 3]
    finally:
        link.close()
    assert not link._poll_thread.is_alive()


def test_make_link_selection():
    nodes = {"A": "fd00::1"}
    assert type(_make_link({}, nodes)) is CoapLink
    assert type(_make_link({"link": "coap"}, nodes)) is CoapLink
    il = _make_link({"link": "integrated", "status_poll_s": 2.5, "port": 5700}, nodes)
    assert isinstance(il, IntegratedLink) and il.status_poll_s == 2.5 and il.bind[1] == 5700
    assert _make_link({"link": "integrated"}, nodes).status_poll_s == 1.0
    with pytest.raises(ValueError):
        _make_link({"link": "mqtt"}, nodes)


def test_pulse_maps_per_node_sign():
    """Chiều pan của từng cụm theo actuator.nodes.<n>.pulse; node không khai báo dùng bảng mặc định."""
    from nt532.station import _make_link

    nodes = {"s1": "::1", "s2": "::2"}
    act = {"nodes": {"s1": {"pulse": {"pan_sign": -1}}, "s2": {"yaw_deg": None}}}
    il = _make_link({"link": "integrated", "port": 5701}, nodes, act)
    assert il.pulse_map("s1").to_us("pan", 9.0) == 1400  # đảo chiều: +9° -> xung giảm
    assert il.pulse_map("s2").to_us("pan", 9.0) == 1600
    assert il.pulse_map("s1").to_us("tilt", 9.0) == 1600  # tilt giữ chiều thuận
    with pytest.raises(ValueError):
        _make_link({"link": "integrated"}, nodes, {"nodes": {"s1": {"pulse": {"pan_sign": 2}}}})
    with pytest.raises(ValueError):
        _make_link({"link": "integrated"}, nodes, {"nodes": {"s1": {"pulse": {"gain": 1}}}})


def test_pulse_map_per_axis_home():
    """Cụm treo ngược: HOME 1700 µs chĩa xuống (-90°), 0° tilt ở 700 µs."""
    from nt532.net.integrated_node import PulseMap

    m = PulseMap(tilt_home_us=700, tilt_sign=-1)
    assert m.to_deg("tilt", 1700) == pytest.approx(-90.0)
    assert m.to_us("tilt", -45.0) == 1200
    assert m.to_us("pan", 9.0) == 1600  # pan vẫn theo home_us


def test_find_spot_excludes_head():
    """Vết sáng nhất nằm ở đầu phát bị che, vết thật (yếu hơn) được chọn."""
    import numpy as np

    from nt532.vision.spot import find_spot

    off = np.zeros((100, 200, 3), np.uint8)
    on = off.copy()
    on[20:26, 20:26, 2] = 250  # đầu phát
    on[70:74, 150:154, 2] = 120  # vết thật
    mask = np.full((100, 200), 255, np.uint8)
    assert find_spot(on, off, mask=mask)[0] < 50
    import cv2

    cv2.circle(mask, (23, 23), 15, 0, -1)
    u, v = find_spot(on, off, mask=mask)
    assert u > 140 and v > 60


def test_aim_wait_covers_servo_ramp():
    """Hạn chờ ngắm cộng thời gian ramp 20 µs/20 ms: 1000 µs mất 1 s, không biết xung hiện tại thì cả dải."""
    from nt532.net.integrated_link import RAMP_MARGIN_S, IntegratedLink

    t = IntegratedLink._travel_s({"pan_us": 1500, "tilt_us": 1700}, 1500, 2700 - 1000)
    assert t == pytest.approx(RAMP_MARGIN_S)
    assert IntegratedLink._travel_s({"pan_us": 1500, "tilt_us": 1700}, 2500, 1700) == pytest.approx(1.0 + RAMP_MARGIN_S)
    assert IntegratedLink._travel_s({"pan_us": None, "tilt_us": None}, 1500, 1700) == pytest.approx(2.0 + RAMP_MARGIN_S)


def test_servo_speed_limit_waypoints():
    """Giới hạn 300 µs/s, bước 0,1 s: 600 µs chia 20 bước đều, kết thúc đúng đích; thời gian chờ theo tốc độ chậm."""
    from nt532.net.integrated_link import RAMP_MARGIN_S, IntegratedLink

    st = {"pan_us": 1500, "tilt_us": 1700}
    w = IntegratedLink._waypoints(st, 2100, 1700, 300)
    assert len(w) == 20 and w[-1] == (2100, 1700) and w[0] == (1530, 1700)
    assert IntegratedLink._waypoints(st, 2100, 1700, None) == [(2100, 1700)]
    assert IntegratedLink._waypoints({"pan_us": None}, 2100, 1700, 300) == [(2100, 1700)]
    assert IntegratedLink._travel_s(st, 2100, 1700, 300) == pytest.approx(2.0 + RAMP_MARGIN_S)
    assert IntegratedLink._travel_s(st, 2100, 1700, 5000) == pytest.approx(0.6 + RAMP_MARGIN_S)  # firmware chậm hơn


def test_max_aim_s_follows_speed_limit():
    from nt532.net.integrated_link import RAMP_MARGIN_S, IntegratedLink

    il = IntegratedLink({"s1": "::1"}, ("::1", 5702), servo_speed_us_s=400)
    assert il.max_aim_s == pytest.approx(5.0 + RAMP_MARGIN_S)
    il.servo_speed_us_s = None
    assert il.max_aim_s == pytest.approx(2.0 + RAMP_MARGIN_S)


def test_calibration_drops_duplicate_spots():
    """Vết trùng (dưới 1 cm) của một góc lệnh khác vài độ bị bỏ; góc gần như cùng lệnh thì không."""
    import numpy as np

    from nt532.orchestrator.machine import Orchestrator

    samples = [(None, -29.6, -14.0, np.array([0.713, 0.477, 0.0]))]
    assert Orchestrator._duplicate_spot(samples, -29.6, -4.0, np.array([0.713, 0.477, 0.0])) == (-29.6, -14.0)
    assert Orchestrator._duplicate_spot(samples, -29.6, -4.0, np.array([0.80, 0.50, 0.0])) is None
    assert Orchestrator._duplicate_spot(samples, -29.5, -14.0, np.array([0.713, 0.477, 0.0])) is None
