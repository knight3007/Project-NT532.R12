"""Số đo Thread: bộ phân tích thuần và collect() với ping/CoAP/OTBR giả."""

import asyncio
import json
import urllib.error

import aiocoap
import pytest

from nt532.net import thread_stats as ts

PING_OK = """PING fd59::1 (fd59::1) 56 data bytes
64 bytes from fd59::1: icmp_seq=1 ttl=255 time=76.2 ms
64 bytes from fd59::1: icmp_seq=2 ttl=255 time=44.8 ms

--- fd59::1 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 402ms
rtt min/avg/max/mdev = 43.743/54.921/76.205/15.056 ms
"""
PING_LOSS = """--- x ping statistics ---
5 packets transmitted, 2 received, 60% packet loss, time 801ms
rtt min/avg/max/mdev = 29.7/59.0/88.3/29.3 ms
"""
PING_DEAD = """--- x ping statistics ---
5 packets transmitted, 0 received, +5 errors, 100% packet loss, time 804ms
"""
DS = {"networkKey": "SECRET-KEY", "pskc": "SECRET-PSKC", "channel": 15, "panId": 4660, "networkName": "NT532",
      "extPanId": "612d63dce6c5448c", "meshLocalPrefix": "fd59:74c1:942:44c8::/64", "securityPolicy": {}}
NODE = {"state": "leader", "routerCount": 3, "rloc16": "0xf400", "networkName": "NT532", "routerId": 61,
        "extPanId": "612d63dce6c5448c", "baState": "", "leaderData": {"partitionId": 7, "leaderRouterId": 61},
        "rlocAddress": "fd59::ff:fe00:f400", "extAddress": "92f8ce11af51391a"}


def diag(rloc, ext, neighbors, children=()):
    return {"extAddress": ext, "rloc16": rloc, "routerId": 1, "children": list(children),
            "routerNeighbors": [{"rloc16": r, "extAddress": "e" + r, "linkMargin": m, "averageRssi": -55,
                                 "lastRssi": -56, "frameErrorRate": 0.01, "messageErrorRate": 0, "linkAge": 99,
                                 "threadVersion": 5} for r, m in neighbors]}


def test_parse_ping():
    r = ts.parse_ping(PING_OK, 3)
    assert (r["sent"], r["recv"], r["loss_pct"]) == (3, 3, 0.0)
    assert (r["rtt_min"], r["rtt_avg"], r["rtt_max"]) == (43.743, 54.921, 76.205)
    r = ts.parse_ping(PING_LOSS, 5)
    assert (r["recv"], r["loss_pct"], r["rtt_avg"]) == (2, 60.0, 59.0)
    r = ts.parse_ping(PING_DEAD, 5)
    assert (r["recv"], r["loss_pct"], r["rtt_avg"]) == (0, 100.0, None)
    assert ts.parse_ping("lỗi lạ", 4)["loss_pct"] == 100.0


def test_parse_otbr_node_drops_secrets():
    out = ts.parse_otbr_node(NODE, DS)
    assert out["state"] == "leader" and out["partitionId"] == 7 and out["channel"] == 15 and out["panId"] == 4660
    assert out["meshLocalPrefix"].startswith("fd59")
    text = json.dumps(out)
    assert "SECRET" not in text and "networkKey" not in text and "pskc" not in text
    assert "extAddress" not in out and "rlocAddress" not in out


def test_parse_diagnostics_and_topology():
    a = ts.parse_diagnostics(diag("0xf400", "aa", [("0x2800", 60), ("0x4c00", 69)]))
    b = ts.parse_diagnostics(diag("0x2800", "bb", [("0xf400", 60)], [{"rloc16": "0x2801", "linkMargin": 40, "x": 1}]))
    assert a["neighbors"][0] == {"rloc16": "0x2800", "extAddress": "e0x2800", "linkMargin": 60, "averageRssi": -55,
                                 "lastRssi": -56, "frameErrorRate": 0.01, "messageErrorRate": 0, "linkAge": 99}
    assert b["children"] == [{"rloc16": "0x2801", "linkMargin": 40}]
    topo = ts.build_topology([a, b])
    assert len(topo["routers"]) == 2
    assert len(topo["links"]) == 2  # f400-2800 chỉ một cạnh dù hai đầu cùng báo
    assert {tuple(sorted((link["a"], link["b"]))) for link in topo["links"]} == {("0x2800", "0xf400"), ("0x4c00", "0xf400")}


class FakeCtx:
    async def shutdown(self):
        pass


@pytest.fixture
def fake_net(monkeypatch):
    calls = {"coap": [], "http": [], "ping": []}

    async def create():
        return FakeCtx()

    monkeypatch.setattr(aiocoap.Context, "create_client_context", staticmethod(create))

    async def run_ping(addr, count):
        calls["ping"].append(addr)
        return ts.parse_ping(PING_OK if addr == "a1" else PING_DEAD, count)

    async def coap_get(ctx, addr, path, timeout=3.0):
        calls["coap"].append((addr, path))
        if addr != "a1":
            raise TimeoutError("hết giờ")
        return 40.0, json.dumps({"role": "router"} if path == "/status" else {"fw": "b-0.2.5"}).encode()

    async def http_json(method, url, body=None, accept="application/json", timeout=5.0):
        calls["http"].append((method, url))
        if url.endswith("/node"):
            return NODE
        if url.endswith("/node/dataset/active"):
            return DS
        if url.endswith("/api/actions") and method == "POST":
            attrs = body["data"][0]["attributes"]
            assert attrs["destinationType"] == "rloc"
            return {"data": [{"id": "act-" + attrs["destination"]}]}
        if "/api/actions/" in url:
            rloc = url.rsplit("act-", 1)[1]
            return {"data": {"attributes": {"status": "completed"},
                             "relationships": {"result": {"data": {"id": "res-" + rloc}}}}}
        if "/api/diagnostics/" in url:
            rloc = url.rsplit("res-", 1)[1]
            table = {"0xf400": diag("0xf400", "aa", [("0x2800", 60)]), "0x2800": diag("0x2800", "bb", [("0xf400", 60)])}
            return {"data": {"attributes": table[rloc]}}
        raise AssertionError(url)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ts, "run_ping", run_ping)
    monkeypatch.setattr(ts, "coap_get", coap_get)
    monkeypatch.setattr(ts, "http_json", http_json)
    monkeypatch.setattr(ts.asyncio, "sleep", no_sleep)
    return calls


def test_collect_shape(fake_net):
    out = ts.collect({"s1": "a1", "s2": "a2"}, "http://x:8081", 3)
    assert out["otbr"]["ok"] and out["otbr"]["state"] == "leader" and out["otbr"]["channel"] == 15
    assert "SECRET" not in json.dumps(out)
    s1, s2 = out["nodes"]["s1"], out["nodes"]["s2"]
    assert s1["ping"]["recv"] == 3 and s1["coap"] == {"ok": True, "rtt_ms": 40.0, "role": "router"}
    assert s1["role"] == "router" and s1["fw"] == "b-0.2.5" and s1["last_ok"] is not None
    assert s2["ping"]["loss_pct"] == 100.0 and s2["coap"]["ok"] is False and "hết giờ" in s2["coap"]["error"]
    assert s2["role"] is None and s2["last_ok"] is None
    assert {r["rloc16"] for r in out["topology"]["routers"]} == {"0xf400", "0x2800"}
    assert "topology_error" not in out
    json.dumps(out)  # phải tuần tự hóa được


def test_fw_cached_and_last_ok_kept(fake_net):
    c = ts.Collector("http://x:8081")

    async def twice():
        await c.collect_async({"s1": "a1"}, 2)
        await c.collect_async({"s1": "a1"}, 2)

    asyncio.run(twice())
    assert [p for _, p in fake_net["coap"] if p == "/info"] == ["/info"]  # chỉ hỏi fw một lần
    assert c.last_ok["s1"] > 0


def test_collect_otbr_down(monkeypatch, fake_net):
    async def http_json(method, url, body=None, accept="application/json", timeout=5.0):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(ts, "http_json", http_json)
    out = ts.collect({"s1": "a1"}, "http://x:8081", 2)
    assert out["otbr"]["ok"] is False and "URLError" in out["otbr"]["error"]
    assert out["nodes"]["s1"]["ping"]["recv"] == 3  # OTBR hỏng không làm mất số đo node
    assert "topology" not in out


def test_collect_topology_unsupported(monkeypatch, fake_net):
    orig = ts.http_json

    async def http_json(method, url, body=None, accept="application/json", timeout=5.0):
        if "/api/actions" in url:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return await orig(method, url, body, accept, timeout)

    monkeypatch.setattr(ts, "http_json", http_json)
    out = ts.collect({"s1": "a1"}, "http://x:8081", 2)
    assert out["topology_error"] == "OTBR /api/actions: HTTP 404" and "topology" not in out
    assert out["otbr"]["ok"]
