"""Node giả chạy lõi C của firmware node B tích hợp + IntegratedLink qua UDP localhost."""

import asyncio
import json
import time

import pytest
from aiocoap import GET, POST, Context, Message, resource

from nt532.net.fakenode import ConstSensors, CoreUnavailable, free_port
from nt532.net.integrated_link import IntegratedLink
from nt532.net.integrated_node import IntegratedCore, IntegratedFakeNode, PulseMap
from nt532.net.link import HeartbeatSender

TRANSPORTS = ["simplesocketserver"]


@pytest.fixture(scope="module")
def core():
    try:
        return IntegratedCore.get()
    except CoreUnavailable as e:
        pytest.skip(f"không có lõi C node B tích hợp: {e}")


def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class Rig:
    """IntegratedLink thật + một IntegratedFakeNode s1 qua UDP localhost."""

    def __init__(self, **kw):
        self.pi_port, self.port = free_port(), free_port()
        self.link = IntegratedLink({"s1": f"127.0.0.1:{self.port}"}, ("127.0.0.1", self.pi_port),
                                   timeout_s=1.0, transports=TRANSPORTS).start()
        self.node = IntegratedFakeNode("s1", ("127.0.0.1", self.port), f"coap://127.0.0.1:{self.pi_port}",
                                       transports=TRANSPORTS, **kw).start()
        self.hb: HeartbeatSender | None = None

    def start_hb(self, period_ms=200):
        self.hb = HeartbeatSender(self.link.heartbeat, ["s1"], period_ms)
        self.hb.start()
        assert wait_for(lambda: self.link.hb_age_ms("s1") < 1000, 5)
        assert wait_for(lambda: self.node.snapshot()["hb_seen"], 5)  # /t cũng làm mới hb_age: chờ /hb thật

    def stop_hb(self):
        self.hb.halt()
        self.hb = None

    def _req(self, method, path, payload=b""):
        async def go():
            ctx = await Context.create_server_context(resource.Site(), bind=("127.0.0.1", free_port()),
                                                          transports=TRANSPORTS)
            try:
                return await asyncio.wait_for(ctx.request(Message(
                    code=method, payload=payload, uri=f"coap://127.0.0.1:{self.port}/{path}")).response, 2)
            finally:
                await ctx.shutdown()
        return asyncio.run(go())

    def get(self, path):
        """GET trực tiếp tới node bằng một client riêng, trả (mã, JSON)."""
        r = self._req(GET, path)
        return r.code, json.loads(r.payload)

    def post(self, path, payload: bytes) -> str:
        return self._req(POST, path, payload).code.dotted

    def close(self):
        if self.hb:
            self.hb.halt()
        self.node.stop()
        self.link.close()

    def aim(self, pan=10.0, tilt=5.0, ttl=4000):
        cid = self.link.aim("s1", pan, tilt, ttl)
        return cid, self.link.wait(cid, ("reached", "rejected", "fault"), 5)


@pytest.fixture
def rig(core):
    made = []

    def make(**kw):
        r = Rig(**kw)
        made.append(r)
        return r

    yield make
    for r in made:
        r.close()


def test_core_limits_match_pulse_map_defaults(core):
    m = PulseMap()
    assert core.pan_us == m.limits_us["pan"] and core.tilt_us == m.limits_us["tilt"]
    assert core.home_us == m.home_us and core.step_us == 20 and core.step_period_ms == 20


def test_info(rig):
    r = rig()
    info = r.link.info("s1")
    assert info.n == "s1" and info.fw.startswith("sim-integrated-") and info.hb == 1500 and info.fire == 0
    assert (info.pan, info.tilt) == PulseMap().limits_deg()
    code, body = r.get("info")
    assert code.is_successful() and body["mode"] == "integrated" and "laser_disabled" in body["outputs"]


def test_status_fields(rig):
    r = rig()
    assert wait_for(lambda: r.get("status")[1]["sensor_ok"], 3)
    _, st = r.get("status")
    assert st["role"] == "router" and st["stopped"] and not st["hb_fresh"] and st["laser_on"] is False
    assert st["temperature_c"] == 27.0 and st["humidity_pct"] == 55.0
    assert not st["servo_armed"] and not st["servo_pwm"] and not st["relay_on"]


def test_aim_without_heartbeat_is_forbidden(rig):
    r = rig()
    _, st = r.aim()
    assert st.st == "rejected" and st.err == "forbidden"
    assert not r.node.snapshot()["armed"]


def test_aim_reaches_with_ramp_not_faster_than_firmware(rig):
    r = rig()
    r.start_hb()
    t_acc = {}
    r.link.listeners.append(
        lambda n, it: t_acc.setdefault("t", time.monotonic())
        if isinstance(it, tuple) and it[0] == "status" and it[1].st == "accepted" else None)
    _, st = r.aim(10.0, 5.0)
    t_reached = time.monotonic()
    assert st.st == "reached" and (st.pan, st.tilt) == (10.0, 5.0) and st.n == "s1"
    m = r.node.map
    assert abs(r.node.hw.pan - m.to_deg("pan", m.to_us("pan", 10.0))) < 0.1
    assert abs(r.node.hw.tilt - m.to_deg("tilt", m.to_us("tilt", 5.0))) < 0.1
    steps = max(abs(m.to_us("pan", 10.0) - 1500), abs(m.to_us("tilt", 5.0) - 1500)) / 20
    assert t_reached - t_acc["t"] >= (steps - 1) * 0.02


def test_aim_out_of_range_is_rejected_without_sending(rig):
    r = rig()
    r.start_hb()
    before = r.node.rx_count.get("pantilt", 0)
    _, st = r.aim(200.0, 0.0)
    assert st.st == "rejected" and st.err == "out_of_range"
    assert r.node.rx_count.get("pantilt", 0) == before


def test_fire_laser_done_and_relay_toggles(rig):
    r = rig()
    r.start_hb()
    cid, _ = r.aim()
    r.link.fire("s1", cid, "laser", 300)
    assert wait_for(lambda: r.node.hw.on["laser"], 2)
    assert r.link.wait(cid, ("done",), 3) is not None
    assert not r.node.hw.on["laser"]
    laser = [m for _, m in r.node.hw.log if m.startswith("laser")]
    assert laser[-2:] == ["laser on", "laser off"]


def test_fire_laser_after_stop_is_forbidden(rig):
    r = rig()
    r.start_hb()
    cid, _ = r.aim()
    r.link.stop("s1")
    r.link.fire("s1", cid, "laser", 300)
    st = r.link.wait(cid, ("rejected",), 2)
    assert st is not None and st.err == "forbidden" and not r.node.hw.on["laser"]


def test_fire_pump_is_rejected_without_traffic(rig):
    r = rig()
    r.start_hb()
    cid, _ = r.aim()
    laser_rx = r.node.rx_count.get("laser", 0)
    r.link.fire("s1", cid, "pump", 300)
    st = r.link.wait(cid, ("rejected",), 2)
    assert st is not None and st.err == "no_pump"
    assert r.node.rx_count.get("laser", 0) == laser_rx and not r.node.hw.on["pump"]


def test_heartbeat_loss_stops_servo_then_rearm(rig):
    r = rig()
    r.start_hb()
    _, st = r.aim()
    assert st.st == "reached"
    r.stop_hb()
    time.sleep(1.8)  # hết 1500 ms
    _, s = r.get("status")
    assert s["servo_pwm"] is False and s["stopped"] and not s["servo_armed"] and not s["hb_fresh"]
    r.start_hb()  # cần heartbeat mới rồi ARM lại
    _, st = r.aim(-8.0, 3.0)
    assert st.st == "reached" and r.node.snapshot()["pwm_active"]


def test_stop_turns_relay_off_and_clears_arm(rig):
    r = rig()
    r.start_hb()
    cid, _ = r.aim()
    r.link.fire("s1", cid, "laser", 4000)
    assert wait_for(lambda: r.node.hw.on["laser"], 2)
    r.link.stop("s1")
    assert wait_for(lambda: not r.node.hw.on["laser"], 2)
    s = r.node.snapshot()
    assert not s["armed"] and not s["pwm_active"] and not s["laser_on"]
    st = r.link.wait(cid, ("fault",), 3)
    assert st is not None and st.err == "stopped"
    assert r.get("status")[1]["stop_count"] == 1


def test_detach_stops_everything(rig):
    r = rig()
    r.start_hb()
    cid, _ = r.aim()
    r.link.fire("s1", cid, "laser", 4000)
    assert wait_for(lambda: r.node.hw.on["laser"], 2)
    r.node.set_attached(False)
    assert wait_for(lambda: not r.node.hw.on["laser"], 2)
    assert not r.node.snapshot()["armed"] and r.get("status")[1]["role"] == "detached"


def test_raw_contract_codes(rig):
    r = rig()
    r.start_hb()
    assert r.post("pantilt", b"x" * 17) == "4.13"
    assert r.post("pantilt", b"nonsense") == "4.00"
    assert r.post("pantilt", b"1500,1500") == "4.03"  # chưa ARM
    assert r.post("pantilt", b"ARM") == "2.04"
    assert r.post("laser", b"ON") == "4.03"  # chưa có PWM
    assert r.post("laser", b"OFF") == "2.04"
    assert r.post("info", b"") == "4.05"


def test_telemetry_and_alert_reach_pi(rig):
    src = ConstSensors(temp=27.0, gas=180.0, hum=55.0)
    r = rig(sensors=src, telemetry_s=0.2, sample_s=0.2)
    tel, alerts = [], []
    r.link.on_telemetry, r.link.on_alert = tel.append, alerts.append
    assert wait_for(lambda: len(tel) >= 2, 5)
    assert tel[0].n == "s1" and tel[0].h == 55.0
    src.temp = 60.0
    assert wait_for(lambda: alerts, 5) and alerts[0].k == "alert"
    src.temp = 25.0
    assert wait_for(lambda: len(alerts) >= 2, 8) and alerts[1].k == "clear" and alerts[1].s > alerts[0].s


def test_pulse_map_limits_and_errors():
    m = PulseMap()
    assert m.to_us("pan", 0.0) == 1500 and m.to_us("tilt", 0.0) == 1500
    assert m.to_us("pan", -90.0) == 500 and m.to_us("pan", 90.0) == 2500
    assert m.to_us("tilt", 90.0) == 2500 and m.to_us("tilt", -81.0) == 600
    for axis, deg in (("pan", 90.5), ("pan", -91.0), ("tilt", -82.0), ("tilt", 91.0)):
        with pytest.raises(ValueError, match="ngoài"):
            m.to_us(axis, deg)
    with pytest.raises(ValueError, match="trục"):
        m.to_us("roll", 0.0)
    assert m.to_deg("pan", 2500) == pytest.approx(90.0) and m.to_deg("tilt", 600) == pytest.approx(-81.0)
    assert m.limits_deg() == ((pytest.approx(-90.0), pytest.approx(90.0)),
                              (pytest.approx(-81.0), pytest.approx(90.0)))
    flipped = PulseMap(pan_sign=-1)
    assert flipped.to_us("pan", 10.0) == 1500 - round(10 / 0.09)
    assert flipped.limits_deg()[0] == (pytest.approx(-90.0), pytest.approx(90.0))
