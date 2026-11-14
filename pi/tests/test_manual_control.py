"""Điều khiển tay (trang /manual): IntegratedLink.manual và lệnh `manual` của Dashboard."""

import json
from types import SimpleNamespace

from aiocoap import GET, POST
from aiocoap.numbers.codes import CHANGED, CONTENT, FORBIDDEN

from nt532.dashboard.server import Dashboard
from nt532.net.integrated_link import IntegratedLink
from nt532.net.link import LinkError
from nt532.orchestrator.events import EventLog


class Rig:
    def __init__(self, armed=False, pwm=False):
        self.link = IntegratedLink({"s1": "127.0.0.1:1"})
        self.pt = {"armed": armed, "pwm_active": pwm, "pan_us": 1500, "tilt_us": 1500, "target_pan_us": 1500,
                   "target_tilt_us": 1500, "pan_min_us": 500, "pan_max_us": 2500, "tilt_min_us": 600,
                   "tilt_max_us": 2500}
        self.laser, self.calls, self.fail = False, [], False
        self.link._call = self._call

    def _call(self, node, method, path, payload=b""):
        if self.fail:
            raise LinkError("timeout")
        self.calls.append((method, path, payload))
        if method == GET:
            body = self.pt if path == "pantilt" else {"on": self.laser}
            return SimpleNamespace(code=CONTENT, payload=json.dumps(body).encode())
        if path == "pantilt":
            if payload == b"ARM":
                self.pt["armed"] = True
            elif payload == b"HOME":
                self.pt["pwm_active"] = True
        elif path == "laser":
            if payload == b"ON" and not (self.pt["armed"] and self.pt["pwm_active"]):
                return SimpleNamespace(code=FORBIDDEN, payload=b"")
            self.laser = payload == b"ON"
        return SimpleNamespace(code=CHANGED, payload=b"")


def test_status():
    r = Rig()
    res = r.link.manual("s1", "status")
    assert res["ok"] and res["laser_on"] is False and res["pantilt"]["pan_us"] == 1500
    assert all(c[0] == GET for c in r.calls)


def test_move_out_of_range_sends_nothing():
    r = Rig(armed=True, pwm=True)
    for pan, tilt in ((400, 1500), (1500, 599), (3000, 1500), ("a", 1500)):
        res = r.link.manual("s1", "move", pan_us=pan, tilt_us=tilt)
        assert res["ok"] is False
    assert not [c for c in r.calls if c[0] == POST]


def test_move_when_not_armed_sends_arm_home_then_target():
    r = Rig()
    res = r.link.manual("s1", "move", pan_us=1400, tilt_us=1700)
    assert res["ok"]
    posts = [c[2] for c in r.calls if c[0] == POST]
    assert posts == [b"ARM", b"HOME", b"1400,1700"]


def test_move_when_ready_only_target():
    r = Rig(armed=True, pwm=True)
    assert r.link.manual("s1", "move", pan_us=1000, tilt_us=1000)["ok"]
    assert [c[2] for c in r.calls if c[0] == POST] == [b"1000,1000"]


def test_laser_on_from_idle_arms_and_homes_first():
    r = Rig()
    res = r.link.manual("s1", "laser", on=True)
    assert res["ok"] and res["laser_on"] is True
    assert [c[2] for c in r.calls if c[0] == POST] == [b"ARM", b"HOME", b"ON"]


def test_laser_forbidden_explains():
    r = Rig(armed=True, pwm=True)
    orig = r._call
    r.link._call = lambda n, m, p, pl=b"": (SimpleNamespace(code=FORBIDDEN, payload=b"")
                                           if p == "laser" and m == POST else orig(n, m, p, pl))
    res = r.link.manual("s1", "laser", on=True)
    assert res["ok"] is False and "heartbeat" in res["error"]


def test_laser_and_stop():
    r = Rig()
    assert r.link.manual("s1", "laser", on=True)["laser_on"] is True
    assert r.link.manual("s1", "laser", on=False)["laser_on"] is False
    assert r.link.manual("s1", "laser")["ok"] is False
    r.link.manual("s1", "laser", on=True)
    r.calls.clear()
    res = r.link.manual("s1", "stop")
    assert res["ok"] and res["laser_on"] is False
    assert [(c[1], c[2]) for c in r.calls if c[0] == POST] == [("laser", b"OFF"), ("stop", b"")]


def test_errors_never_raise():
    r = Rig()
    r.fail = True
    res = r.link.manual("s1", "status")
    assert res["ok"] is False and "timeout" in res["error"]
    assert r.link.manual("s1", "bogus")["ok"] is False


def _dash(enabled, link, tmp_path=None):
    d = Dashboard.__new__(Dashboard)
    d.st = SimpleNamespace(orch=SimpleNamespace(enabled=enabled, emergency_stop=lambda *_: None, world=None),
                           world=None, link=link, events=EventLog())
    return d


def test_dashboard_refuses_when_auto_on():
    r = Rig()
    res = _dash(True, r.link).command({"cmd": "manual", "node": "s1", "op": "arm"})
    assert res == {"ok": False, "error": "Tắt chế độ Tự động trước khi điều khiển tay"}
    assert not r.calls


def test_dashboard_refuses_without_manual_link():
    res = _dash(False, SimpleNamespace(nodes=["s1"])).command({"cmd": "manual", "node": "s1", "op": "arm"})
    assert "integrated" in res["error"]


def test_dashboard_unknown_node():
    r = Rig()
    assert _dash(False, r.link).command({"cmd": "manual", "node": "s9", "op": "arm"})["ok"] is False


def test_dashboard_move_event_has_machine_fields_and_status_has_none():
    r = Rig()
    d = _dash(False, r.link)
    assert d.command({"cmd": "manual", "node": "s1", "op": "status"})["ok"]
    assert d.st.events.since(0) == []
    assert d.command({"cmd": "manual", "node": "s1", "op": "move", "pan_us": 1400, "tilt_us": 1700})["ok"]
    ev = d.st.events.since(0)[-1]
    assert ev["kind"] == "manual" and ev["node"] == "s1" and ev["op"] == "move"
    assert (ev["pan_us"], ev["tilt_us"]) == (1400, 1700)
    assert "reported_pan_us" in ev and "laser_on" in ev


def test_mark_records_position_and_note():
    r = Rig(armed=True, pwm=True)
    d = _dash(False, r.link)
    res = d.command({"cmd": "manual", "node": "s1", "op": "mark", "note": "bị chân trụ chắn"})
    assert res["ok"] and not [c for c in r.calls if c[0] == POST]
    ev = d.st.events.since(0)[-1]
    assert ev["op"] == "mark" and ev["note"] == "bị chân trụ chắn"
    assert (ev["reported_pan_us"], ev["reported_tilt_us"]) == (1500, 1500)
    assert d.command({"cmd": "manual", "node": "s1", "op": "mark", "note": "x" * 121})["ok"] is False
