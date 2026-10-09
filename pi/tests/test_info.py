"""GET /info (node tự mô tả), so với site.yaml, và góc bù tia nước theo vòi."""

import pytest

from nt532.net.coap import CoapLink
from nt532.net.fakenode import CoreLib, CoreUnavailable, FakeNode, free_port
from nt532.net.link import LinkError, NodeLink
from nt532.net.protocol import Info, PayloadError
from nt532.orchestrator import aiming
from nt532.orchestrator.events import EventLog
from nt532.station import check_node_info

TRANSPORTS = ["simplesocketserver"]


def site(**act):
    return {"actuator": {"heartbeat_ms": 500, "fire_ms": 2000, "nodes": {"s1": {}}, **act}}


@pytest.fixture
def core():
    try:
        return CoreLib.get()
    except CoreUnavailable as e:
        pytest.skip(f"không có lõi C của firmware (libcore.so): {e}")


class Rig:
    def __init__(self, **kw):
        pi_port, port = free_port(), free_port()
        self.link = CoapLink({"s1": f"127.0.0.1:{port}"}, ("127.0.0.1", pi_port), timeout_s=1.0,
                             transports=TRANSPORTS).start()
        self.node = FakeNode("s1", ("127.0.0.1", port), f"coap://127.0.0.1:{pi_port}",
                             transports=TRANSPORTS, **kw).start()

    def close(self):
        self.node.stop()
        self.link.close()


@pytest.fixture
def rig_factory(core):
    rigs = []

    def make(**kw):
        rigs.append(Rig(**kw))
        return rigs[-1]

    yield make
    for r in rigs:
        r.close()


def test_parse_info():
    i = Info.parse(b'{"n":"s1","fw":"v1","pan":[-50,50],"tilt":[-35,45.5],"hb":1500,"fire":5000,"srp":1}')
    assert i == Info("s1", "v1", (-50.0, 50.0), (-35.0, 45.5), 1500, 5000, True)
    with pytest.raises(PayloadError):
        Info.parse(b'{"n":"s1","fw":"v1","pan":[50,-50],"tilt":[0,1],"hb":1,"fire":1,"srp":0}')
    with pytest.raises(PayloadError):
        Info.parse(b'{"n":"s1"}')


def test_base_link_has_no_info():
    assert NodeLink(["s1"]).info("s1") is None


def test_fake_node_info(rig_factory):
    r = rig_factory(limits=(-40.0, 40.0, -20.0, 30.0), hb_timeout_ms=1500, max_fire_ms=3000, fw="t-1")
    info = r.link.info("s1", 2.0)
    assert info == Info("s1", "t-1", (-40.0, 40.0), (-20.0, 30.0), 1500, 3000, True)
    with pytest.raises(LinkError):
        r.link.info("zz")


def test_info_offline_is_link_error(rig_factory):
    r = rig_factory()
    r.node.online = False
    with pytest.raises(LinkError):
        r.link.info("s1", 0.3)


def test_match_no_warning(rig_factory):
    r = rig_factory()
    s = site()
    ev = EventLog()
    check_node_info(s, r.link, ev)
    assert all(e["level"] == "info" for e in ev.since())
    assert aiming.limits(s, "s1") == aiming.Limits(aiming.DEFAULT_PAN, aiming.DEFAULT_TILT)


def test_mismatch_warns_and_narrower_wins(rig_factory):
    r = rig_factory(limits=(-30.0, 80.0, -35.0, 45.0), hb_timeout_ms=600, max_fire_ms=1000)
    s = site()
    ev = EventLog()
    check_node_info(s, r.link, ev)
    warns = [e for e in ev.since() if e["level"] == "warn"]
    assert len(warns) == 1
    t = warns[0]["text"]
    assert "pan" in t and "heartbeat" in t and "fire_ms" in t
    lim = aiming.limits(s, "s1")
    assert lim.pan == (-30.0, 70.0)  # hẹp hơn mỗi phía: -30 của node, 70 của site
    assert lim.tilt == (-35.0, 45.0)
    assert not lim.ok(-40.0, 0.0)


def test_node_wider_keeps_site(rig_factory):
    r = rig_factory(limits=(-60.0, 60.0, -35.0, 45.0))
    s = site(nodes={"s1": {"pan_limits": [-10, 10]}})
    ev = EventLog()
    check_node_info(s, r.link, ev)
    assert aiming.limits(s, "s1").pan == (-10, 10)
    assert any(e["level"] == "warn" for e in ev.since())


def test_unreachable_node_warns_and_continues(rig_factory):
    r = rig_factory()
    r.node.online = False
    s = site()
    ev = EventLog()
    check_node_info(s, r.link, ev, timeout=0.3)
    assert [e["level"] for e in ev.since()] == ["warn"]
    assert aiming.limits(s, "s1").pan == aiming.DEFAULT_PAN


# --- góc bù tia nước theo vòi -------------------------------------------------------------------


def test_water_tilt_fallbacks():
    s = site(water_tilt_deg=2.0)
    assert aiming.water_tilt(s) == 2.0  # gọi cũ một đối số
    assert aiming.water_tilt(s, "s1") == 2.0
    assert aiming.water_tilt(s, "s9", distance_m=1.0) == 2.0
    s["actuator"]["nodes"]["s1"]["water_tilt_deg"] = 3.5
    assert aiming.water_tilt(s, "s1") == 3.5
    assert aiming.water_tilt(s) == 2.0
    assert aiming.water_tilt({"actuator": {}}) == 0.0
    assert aiming.water_tilt({}, "s1") == 0.0


def test_water_tilt_table_interpolates():
    s = site(water_tilt_deg=1.0)
    s["actuator"]["nodes"]["s1"].update(water_tilt_deg=3.0, water_tilt_table=[[1.0, 4.0], [0.5, 2.0], [1.5, 8.0]])
    f = lambda d: aiming.water_tilt(s, "s1", distance_m=d)
    assert f(0.5) == pytest.approx(2.0)
    assert f(0.75) == pytest.approx(3.0)
    assert f(1.25) == pytest.approx(6.0)
    assert f(0.1) == pytest.approx(2.0) and f(9.0) == pytest.approx(8.0)  # ngoài bảng: giữ đầu mút
    assert aiming.water_tilt(s, "s1") == 3.0  # không có khoảng cách: dùng giá trị riêng của vòi


def test_horizontal_distance():
    assert aiming.horizontal_distance((0, 0, 0.1), (0.3, 0.4, 2.0)) == pytest.approx(0.5)
