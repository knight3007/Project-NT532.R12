"""Lệnh dashboard `test_alert`: giả báo động để kích một lượt chữa cháy trên trạm thật."""

from types import SimpleNamespace

from nt532.dashboard.server import Dashboard
from nt532.orchestrator.events import EventLog


def _dash(enabled=True, samples=()):
    got = []
    d = Dashboard.__new__(Dashboard)
    orch = SimpleNamespace(enabled=enabled, on_alert=got.append)
    d.st = SimpleNamespace(orch=orch, world=None, events=EventLog(), site={"sensors": {"gas_alarm": 300}},
                           sensors=SimpleNamespace(samples=lambda n, k: list(samples)[-k:]))
    return d, got


def test_valid_node_calls_on_alert_and_logs_event():
    d, got = _dash(samples=[SimpleNamespace(temp=31.5, gas=120.0)])
    assert d.command({"cmd": "test_alert", "node": "s2"}) == {"ok": True}
    (a,) = got
    assert a.n == "s2" and a.k == "alert" and a.t == 31.5 and a.g == 301.0
    assert any("giả báo động" in e["text"] for e in d.st.events.since(0))


def test_no_samples_still_alerts():
    d, got = _dash()
    assert d.command({"cmd": "test_alert", "node": "s1"})["ok"] is True
    assert got[0].n == "s1" and got[0].g > 300


def test_bad_node_refused():
    d, got = _dash()
    res = d.command({"cmd": "test_alert", "node": "s9"})
    assert res["ok"] is False and "node" in res["error"]
    assert d.command({"cmd": "test_alert"})["ok"] is False
    assert not got


def test_orchestrator_off_refused():
    d, got = _dash(enabled=False)
    assert d.command({"cmd": "test_alert", "node": "s1"})["ok"] is False
    assert not got
