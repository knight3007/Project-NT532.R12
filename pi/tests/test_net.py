import asyncio
import socket
import threading
import time

import pytest

from nt532.net.link import NodeLink
from nt532.net.protocol import (
    Aim,
    Alert,
    Deduper,
    Fire,
    PayloadError,
    Status,
    Stop,
    Telemetry,
    dumps,
)


def test_examples_from_plan_parse_and_fit():
    # ví dụ payload ở kế hoạch mục 4
    a = Alert.parse(b'{"n":"s1","s":124,"k":"alert","t":52.1,"g":832}')
    assert (a.n, a.s, a.k, a.t, a.g) == ("s1", 124, "alert", 52.1, 832.0)
    aim = Aim.parse(b'{"id":42,"pan":31.8,"tilt":-7.4,"ttl":1500}')
    assert aim == Aim(42, 31.8, -7.4, 1500)
    assert Fire.parse(b'{"id":42,"dev":"pump","ms":2000}').dev == "pump"
    st = Status.parse(b'{"id":42,"st":"reached","pan":31.5,"tilt":-7.2}')
    assert st.st == "reached" and st.err is None
    for msg in (a, aim, Fire(42, "pump", 2000), st, Stop(7), Telemetry("s2", 9, 3600, 27.4, 180, 55)):
        raw = dumps(msg)
        assert len(raw) < 60, raw
        assert type(msg).parse(raw) == msg


@pytest.mark.parametrize("cls,raw", [
    (Alert, b'{"n":"s1","s":1,"k":"boom","t":1,"g":1}'),
    (Alert, b'{"n":"s1","k":"alert","t":1,"g":1}'),
    (Fire, b'{"id":1,"dev":"laser2","ms":5}'),
    (Status, b'{"id":1,"st":"weird"}'),
    (Aim, b'{"id":true,"pan":1,"tilt":1,"ttl":1}'),
    (Aim, b'not json'),
    (Aim, b'[1,2]'),
])
def test_bad_payloads(cls, raw):
    with pytest.raises(PayloadError):
        cls.parse(raw)


def test_deduper_window():
    now = [0.0]
    d = Deduper(window_s=10, clock=lambda: now[0])
    assert not d.seen(("s1", 5))
    assert d.seen(("s1", 5))
    assert not d.seen(("s2", 5))
    now[0] = 11
    assert not d.seen(("s1", 5))  # node khởi động lại, số thứ tự lặp lại sau cửa sổ


class EchoLink(NodeLink):
    """Node giả trả `reached` ngay cho mỗi lệnh ngắm."""

    def __init__(self):
        super().__init__(["s1"])
        self.sent = []

    def _send(self, node, path, msg):
        self.sent.append((path, msg))
        if path == "aim":
            threading.Timer(0.05, self.on_status, (node, Status(msg.id, "reached"))).start()


def test_link_wait_and_heartbeat_age():
    link = EchoLink()
    assert link.hb_age_ms("s1") == float("inf")
    cmd = link.aim("s1", 10, 5, 1500)
    st = link.wait(cmd, timeout=1)
    assert st is not None and st.st == "reached"
    assert link.hb_age_ms("s1") < 1000
    assert link.wait(999, timeout=0.1) is None
    link.stop()
    assert link.sent[-1][0] == "stop"


TRANSPORTS = ["simplesocketserver"]  # chạy được cả ở máy không có IPv6 (CI, container)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_coap_gateway_receives_and_sends():
    """Qua localhost: node giả gửi /t, /a (trùng), nhận /aim rồi trả /status."""
    aiocoap = pytest.importorskip("aiocoap")
    from aiocoap import resource

    from nt532.net.coap import CoapLink

    pi_port, node_port = _free_port(), _free_port()
    link = CoapLink({"s1": f"127.0.0.1:{node_port}"}, bind=("127.0.0.1", pi_port), timeout_s=2,
                     transports=TRANSPORTS)
    got_t, got_a = [], []
    link.on_telemetry, link.on_alert = got_t.append, got_a.append
    link.start()

    received = []
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder = {}

    class AimRes(resource.Resource):
        async def render_post(self, request):
            aim = Aim.parse(request.payload)
            received.append(aim)
            reply = aiocoap.Message(code=aiocoap.POST, payload=dumps(Status(aim.id, "reached", n="s1")),
                                    uri=f"coap://127.0.0.1:{pi_port}/status")
            asyncio.ensure_future(holder["ctx"].request(reply).response)
            return aiocoap.Message(code=aiocoap.CHANGED)

    async def node():
        site = resource.Site()
        site.add_resource(["aim"], AimRes())
        holder["ctx"] = await aiocoap.Context.create_server_context(site, bind=("127.0.0.1", node_port),
                                                              transports=TRANSPORTS)
        ready.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(node())
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(5)

    async def post(path, msg):
        req = aiocoap.Message(code=aiocoap.POST, payload=dumps(msg), uri=f"coap://127.0.0.1:{pi_port}/{path}")
        return await holder["ctx"].request(req).response

    def call(path, msg):
        return asyncio.run_coroutine_threadsafe(post(path, msg), loop).result(5)

    try:
        call("t", Telemetry("s1", 1, 10, 27.0, 180))
        alert = Alert("s1", 2, "alert", 52.0, 800)
        call("a", alert)
        call("a", alert)  # gửi lại: phải bị chống lặp
        assert len(got_t) == 1 and got_a == [alert]
        bad = call("a", Telemetry("s1", 3, 1, 1, 1))
        assert not bad.code.is_successful()

        cmd = link.aim("s1", 12.5, -3.0, 1500)
        st = link.wait(cmd, timeout=3)
        assert received and received[0].pan == 12.5
        assert st is not None and st.st == "reached"
        time.sleep(0.1)
    finally:
        link.close()
        asyncio.run_coroutine_threadsafe(holder["ctx"].shutdown(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
