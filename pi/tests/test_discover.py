import subprocess

import pytest

from nt532.net import discover
from nt532.net.discover import discover_nodes, parse_srp_services
from nt532.orchestrator.events import EventLog
from nt532.station import resolve_nodes

SAMPLE = """\
s1._nt532._udp.default.service.arpa.
    deleted: false
    subtypes: (null)
    port: 5683
    priority: 0
    weight: 0
    ttl: 7200
    lease: 7200
    key-lease: 1209600
    remaining lease: 6345.459
    remaining key-lease: 1208734.459
    TXT: [6e=7331]
    host: s1.default.service.arpa.
    addresses: [fe80:0:0:0:1:2:3:4, fd00:db8:a0:0:aaaa:bbbb:cccc:dddd, fd12:3456:789a:1:1:2:3:4]
s2._nt532._udp.default.service.arpa.
    deleted: true
    subtypes: (null)
    port: 5683
    priority: 0
    weight: 0
    ttl: 7200
    lease: 7200
    key-lease: 1209600
    remaining lease: 0
    remaining key-lease: 1208734.459
    TXT: [6e=7332]
    host: s2.default.service.arpa.
    addresses: [fd00:db8:a0:0:1:2:3:5]
printer._ipps._tcp.default.service.arpa.
    deleted: false
    subtypes: (null)
    port: 631
    priority: 0
    weight: 0
    ttl: 7200
    lease: 7200
    key-lease: 1209600
    remaining lease: 100.0
    remaining key-lease: 100.0
    TXT: [616263]
    host: printer.default.service.arpa.
    addresses: [fdde:ad00:beef:0:0:ff:fe00:fc10]
Done
"""


def test_parse_skips_deleted_foreign_and_linklocal():
    got = parse_srp_services(SAMPLE)
    assert set(got) == {"s1"}
    # hai địa chỉ ULA, không có global: lấy cái đầu hợp lệ; link-local bị bỏ
    assert got["s1"] == "fd00:db8:a0:0:aaaa:bbbb:cccc:dddd"


def test_prefers_omr_over_mesh_local_and_global_over_ula():
    got = parse_srp_services(SAMPLE, mesh_local_prefix="fd00:db8:a0::/64")
    assert got["s1"] == "fd12:3456:789a:1:1:2:3:4"
    text = SAMPLE.replace("fd12:3456:789a:1:1:2:3:4", "2001:db8::1")
    assert parse_srp_services(text)["s1"] == "2001:db8::1"


def test_rloc_ranked_last():
    text = SAMPLE.replace("fe80:0:0:0:1:2:3:4, fd00:db8:a0:0:aaaa:bbbb:cccc:dddd, fd12:3456:789a:1:1:2:3:4",
                          "fd00:db8:a0:0:0:ff:fe00:5c00, fd00:db8:a0:0:1:2:3:4")
    assert parse_srp_services(text)["s1"] == "fd00:db8:a0:0:1:2:3:4"


def test_discover_reports_missing(monkeypatch):
    def run(cmd, **kw):
        assert cmd == ["ot-ctl", "srp", "server", "service"]
        return subprocess.CompletedProcess(cmd, 0, SAMPLE, "")

    monkeypatch.setattr(discover.subprocess, "run", run)
    got, why = discover_nodes(["s1", "s2"], 3)
    assert got == {"s1": "fd00:db8:a0:0:aaaa:bbbb:cccc:dddd"}
    assert "s2" in why


def test_command_missing_and_timeout(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(discover.subprocess, "run", missing)
    got, why = discover_nodes(["s1"], 1)
    assert got == {} and "ot-ctl" in why

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(discover.subprocess, "run", slow)
    got, why = discover_nodes(["s1"], 1)
    assert got == {} and "không trả lời" in why


def test_permission_error_is_explained(monkeypatch):
    monkeypatch.setattr(discover.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "connect session failed: Permission denied\n"))
    got, why = discover_nodes(["s1"], 1)
    assert got == {} and "quyền" in why


def test_resolve_falls_back_to_static(monkeypatch):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, SAMPLE, "")

    monkeypatch.setattr(discover.subprocess, "run", run)
    ev = EventLog()
    nodes = resolve_nodes({"nodes": {"s1": None, "s2": "fd11::2"}}, ev)
    assert nodes == {"s1": "fd00:db8:a0:0:aaaa:bbbb:cccc:dddd", "s2": "fd11::2"}
    texts = [e["text"] for e in ev.since(0)]
    assert any("(SRP)" in t for t in texts) and any("(site.yaml)" in t for t in texts)


def test_static_mode_never_runs_command(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("không được gọi ot-ctl ở chế độ static")

    monkeypatch.setattr(discover.subprocess, "run", boom)
    assert resolve_nodes({"discover": "static", "nodes": {"s1": "fd11::1", "s2": None}}, EventLog()) == {
        "s1": "fd11::1"}
    with pytest.raises(ValueError):
        resolve_nodes({"discover": "static", "nodes": {"s1": None}}, EventLog())
