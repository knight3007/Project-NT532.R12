"""Node giả chạy lõi C của firmware, nói CoAP/UDP thật với CoapLink (localhost)."""

import asyncio
import time

import pytest
from aiocoap import POST, Context, Message, resource

from nt532.net.coap import CoapLink
from nt532.net.fakenode import ConstSensors, CoreLib, CoreUnavailable, FakeNode, free_port
from nt532.net.link import HeartbeatSender, LinkError

TRANSPORTS = ["simplesocketserver"]


@pytest.fixture(scope="module", autouse=True)
def core():
    try:
        return CoreLib.get()
    except CoreUnavailable as e:
        pytest.skip(f"không có lõi C của firmware (libcore.so): {e}")


def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class Rig:
    """CoapLink thật + một FakeNode s1 qua UDP localhost."""

    def __init__(self, **kw):
        self.pi_port, self.port = free_port(), free_port()
        self.link = CoapLink({"s1": f"127.0.0.1:{self.port}"}, ("127.0.0.1", self.pi_port), timeout_s=1.0,
                             transports=TRANSPORTS).start()
        self.node = FakeNode("s1", ("127.0.0.1", self.port), f"coap://127.0.0.1:{self.pi_port}",
                             transports=TRANSPORTS, **kw).start()
        self.hb: HeartbeatSender | None = None

    def start_hb(self, period_ms=200):
        self.hb = HeartbeatSender(self.link.heartbeat, ["s1"], period_ms)
        self.hb.start()
        assert wait_for(lambda: self.link.hb_age_ms("s1") < 1000, 5)

    def close(self):
        if self.hb:
            self.hb.halt()
        self.node.stop()
        self.link.close()

    def aim(self, pan=10.0, tilt=5.0):
        cid = self.link.aim("s1", pan, tilt, 3000)
        return cid, self.link.wait(cid, ("reached", "rejected", "fault"), 3)


@pytest.fixture
def rig():
    made = []

    def make(**kw):
        r = Rig(**kw)
        made.append(r)
        return r

    yield make
    for r in made:
        r.close()


def test_aim_fire_pump_done(rig):
    r = rig()
    r.start_hb()
    cid, st = r.aim()
    assert st.st == "reached" and (st.pan, st.tilt) == (10.0, 5.0) and st.n == "s1"
    assert (r.node.hw.pan, r.node.hw.tilt) == (10.0, 5.0)
    r.link.fire("s1", cid, "pump", 300)
    assert r.link.wait(cid, ("done",), 3) is not None
    pump = [m for _, m in r.node.hw.log if m.startswith("pump")]
    assert pump[-2:] == ["pump on", "pump off"] and not r.node.hw.on["pump"]


def test_fire_before_reached_is_rejected(rig):
    r = rig()
    r.start_hb()
    seen = []  # NodeLink chỉ giữ trạng thái cuối theo id, mà "reached" của aim đến sau "rejected" của fire
    r.link.listeners.append(lambda n, it: seen.append(it[1]) if isinstance(it, tuple) and it[0] == "status" else None)
    cid = r.link.aim("s1", 40.0, 0.0, 3000)  # quay ~0,4 s
    r.link.fire("s1", cid, "pump", 300)
    assert wait_for(lambda: any(x.st == "rejected" for x in seen), 3)
    assert next(x for x in seen if x.st == "rejected").err == "not reached"
    assert not r.node.hw.on["pump"]


def test_stop_during_pump_faults_and_turns_off(rig):
    r = rig()
    r.start_hb()
    cid, _ = r.aim()
    r.link.fire("s1", cid, "pump", 4000)
    assert wait_for(lambda: r.node.hw.on["pump"], 3)
    r.link.stop("s1")
    st = r.link.wait(cid, ("fault",), 3)
    assert st is not None and st.err == "stopped"
    assert not r.node.hw.on["pump"]
    assert wait_for(lambda: any(s.st == "done" and s.id > cid for s in r.link._status.values()), 3)


def test_fire_older_than_stop_is_rejected(rig):
    r = rig()
    r.start_hb()
    cid, _ = r.aim()
    r.link.stop("s1")
    assert wait_for(lambda: any(s.st == "done" and s.id > cid for s in r.link._status.values()), 3)
    r.link.fire("s1", cid, "laser", 300)  # gói /fire đến muộn sau /stop
    st = r.link.wait(cid, ("rejected",), 2)
    assert st is not None and st.err == "stopped"
    assert not r.node.hw.on["laser"]


def test_bad_payload_gets_4_00(rig):
    r = rig()

    async def post(path, payload):
        site = resource.Site()
        ctx = await Context.create_server_context(site, bind=("127.0.0.1", free_port()), transports=TRANSPORTS)
        try:
            msg = Message(code=POST, payload=payload, uri=f"{r.node.url}/{path}")
            return (await asyncio.wait_for(ctx.request(msg).response, 3)).code
        finally:
            await ctx.shutdown()

    for path, bad in [("aim", b'{"id":1}'), ("aim", b""), ("fire", b'{"id":1,"dev":"gun","ms":5}'),
                      ("stop", b"not json"), ("alarm", b"[]")]:
        assert asyncio.run(post(path, bad)).is_successful() is False, (path, bad)
        assert str(asyncio.run(post(path, bad))).startswith("4.00"), (path, bad)
    assert asyncio.run(post("stop", b'{"id":9}')).is_successful()
    assert asyncio.run(post("hb", b"")).is_successful()  # /hb luôn 2.04
    assert not r.node.hw.on["pump"]


def test_hb_replies_keep_age_low(rig):
    r = rig()  # node không gửi gì khác ngoài /t: tuổi chỉ giữ thấp nhờ trả lời /hb
    r.start_hb(200)
    for _ in range(8):
        time.sleep(0.2)
        assert r.link.hb_age_ms("s1") < 1000


def test_heartbeat_loss_turns_pump_off_with_fault_hb(rig):
    r = rig(hb_timeout_ms=600)
    r.start_hb(150)
    cid, _ = r.aim()
    r.link.fire("s1", cid, "pump", 4000)
    assert wait_for(lambda: r.node.hw.on["pump"], 3)
    r.hb.halt()
    st = r.link.wait(cid, ("fault",), 4)
    assert st is not None and st.err == "hb"
    assert not r.node.hw.on["pump"]


def test_offline_node_turns_pump_off_by_watchdog(rig):
    r = rig(hb_timeout_ms=600)
    r.start_hb(150)
    cid, _ = r.aim()
    r.link.fire("s1", cid, "pump", 4000)
    assert wait_for(lambda: r.node.hw.on["pump"], 3)
    r.node.online = False  # đứt mạng: không nhận /hb, không gửi được /status
    assert wait_for(lambda: not r.node.hw.on["pump"], 4)
    assert r.link.wait(cid, ("fault",), 0.5) is None  # Pi không biết, vì /status không tới


def test_drop_rx_and_delay(rig):
    r = rig()
    r.start_hb()
    r.node.drop_rx = 1.0
    with pytest.raises(LinkError, match="lỗi"):
        r.link.aim("s1", 1.0, 1.0, 1000)
    r.node.drop_rx = 0.0
    r.node.delay_s = 0.3
    t0 = time.monotonic()
    r.aim()
    assert time.monotonic() - t0 >= 0.3


def test_sensor_alarm_alert_clear_and_telemetry(rig):
    src = ConstSensors(27.0, 180.0, 55.0)
    r = rig(sensors=src, sample_s=0.1, telemetry_s=0.2)
    tel, alerts = [], []
    r.link.on_telemetry, r.link.on_alert = tel.append, alerts.append
    peer = FakeNode("s2", ("127.0.0.1", free_port()), None, transports=TRANSPORTS).start()
    r.node.alarm_peers = [peer.url]
    try:
        assert wait_for(lambda: len(tel) >= 2, 5)
        assert tel[0].n == "s1" and tel[0].h == 55.0 and tel[-1].s > tel[0].s
        src.temp = 60.0
        assert wait_for(lambda: alerts, 5)
        assert alerts[0].k == "alert" and alerts[0].t == 60.0
        assert wait_for(lambda: peer.remote_alarms, 3) and peer.remote_alarms[0][0] == "s1"
        src.temp = 25.0
        assert wait_for(lambda: len(alerts) >= 2, 5)
        assert alerts[1].k == "clear" and alerts[1].s > alerts[0].s
    finally:
        peer.stop()


# --- cả trạm qua CoAP -----------------------------------------------------------------------

@pytest.fixture
def station():
    from nt532.orchestrator.machine import Settings
    from nt532.station import build_sim

    st = build_sim(settings=Settings(frame_gap_s=0.05, verify_wait_s=1.0), fps=12, link="coap")
    st.start()
    yield st
    st.stop()


def test_station_coap_puts_out_fire_end_to_end(station):
    station.world.ignite(0.32, 0.30)
    assert wait_for(lambda: station.orch.runs, 90), "không có lượt nào hoàn tất"
    run = station.orch.runs[0]
    assert run.node == "s1" and run.nozzle == "s1"
    assert run.outcome in ("extinguished", "human"), run.to_json()
    assert run.decisions[0]["answers"]["action"]["answer"] == "spray"
    assert run.shots and run.shots[-1]["miss_cm"] is not None and run.shots[-1]["miss_cm"] < 3
    assert "s1" in station.world.marks
    assert not any(station.world.truth()["lasers"].values())
    assert not any(station.world.pumping.values())


def test_station_coap_emergency_stop(station):
    station.world.ignite(0.90, 0.30)
    assert wait_for(lambda: station.orch.phase.value in ("CORRECT", "FIRE"), 90)
    station.orch.emergency_stop("thử")
    assert wait_for(lambda: station.orch.runs, 15)
    assert station.orch.runs[0].outcome == "stopped"
    time.sleep(0.3)
    assert not any(station.world.truth()["lasers"].values())
    assert not any(station.world.pumping.values())


def test_station_coap_lost_node_ends_in_safe_state(station):
    station.fake_nodes["s2"].online = False
    station.world.ignite(0.90, 0.30)
    station.world.add_spike("s1")
    assert wait_for(lambda: station.orch.runs, 90)
    run = station.orch.runs[0]
    assert run.nozzle != "s2"
    assert run.outcome in ("ignored", "alarm_only", "extinguished", "human"), run.to_json()
    time.sleep(0.3)
    assert not any(station.world.truth()["lasers"].values())
    assert not any(station.world.pumping.values())
