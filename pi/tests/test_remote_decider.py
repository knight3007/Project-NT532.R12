import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
from test_orchestrator import NODE_X, history, nozzles, tgt

from nt532.orchestrator.decide import RuleDecider, make_decider
from nt532.orchestrator.fusion import aggregate, decide_obs, questions_for, verify_obs
from nt532.orchestrator.remote import RemoteDecider, make_server

LIM = {"temp": 45.0, "gas": 600.0}


def cases():
    """Vài obs: có bia, không bia, vòi s1 hỏng, và một verify."""
    tracks = aggregate([[tgt(0.32, 0.25, 0.8)]] * 3, (1280, 720))
    obs, _ = decide_obs("s1", history("s1"), tracks, nozzles(), NODE_X, 3, LIM)
    empty, _ = decide_obs("s2", history("s2"), [], nozzles(), NODE_X, 4, LIM)
    noz = nozzles()
    noz["s1"]["hb_ms"] = 5000
    dead, _ = decide_obs("s1", history("s1"), tracks, noz, NODE_X, 3, LIM)
    ver = verify_obs(obs, "T1", "s1", 1, "ok", [50.0, 45.0, 40.0], [None] * 3, 1.2, history("s1"))
    return [(o, questions_for(o)) for o in (obs, empty, dead, ver)]


class Slow:
    """Bộ quyết định luật nhưng chậm."""

    name = "slow-rules"

    def __init__(self, delay):
        self.delay, self.inner = delay, RuleDecider()

    def decide(self, obs, questions):
        time.sleep(self.delay)
        return self.inner.decide(obs, questions)


def serve(decider):
    server = make_server(decider, "127.0.0.1", 0, log=lambda m: None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def rules_server():
    server, url = serve(RuleDecider())
    yield url
    server.shutdown()
    server.server_close()


def answers_of(dec):
    return {q: a.answer for q, a in dec.answers.items()}


def test_remote_matches_rules(rules_server):
    remote, local = RemoteDecider(rules_server, 5.0), RuleDecider()
    for obs, q in cases():
        d = remote.decide(obs, q)
        assert answers_of(d) == answers_of(local.decide(obs, q))
        assert d.decider.startswith("remote:") and remote.last_error is None
        assert all(a.source == "rules" for a in d.answers.values())
        assert d.state == local.decide(obs, q).state


def test_health(rules_server):
    import urllib.request

    with urllib.request.urlopen(f"{rules_server}/health", timeout=3) as r:
        assert json.load(r) == {"ok": True, "decider": "rules"}


def check_fallback(remote, started):
    obs, q = cases()[0]
    d = remote.decide(obs, q)
    assert answers_of(d) == answers_of(RuleDecider().decide(obs, q))
    assert all(a.source == "rules-fallback" for a in d.answers.values())
    assert "dự phòng" in d.decider and remote.last_error
    return time.monotonic() - started


def test_server_down_falls_back_fast():
    server, url = serve(RuleDecider())
    server.shutdown()
    server.server_close()  # cổng đã đóng: kết nối bị từ chối
    assert check_fallback(RemoteDecider(url, 1.0), time.monotonic()) < 1.5


def test_slow_server_falls_back_within_timeout():
    server, url = serve(Slow(2.0))
    try:
        elapsed = check_fallback(RemoteDecider(url, 0.4), time.monotonic())
        assert elapsed < 1.5
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("payload", [b"not json", b'{"answers": 3}', b'{"answers": {}}',
                                     b'{"answers": {"real_fire": {"answer": "yes", "confidence": 1}}}'])
def test_malformed_reply_falls_back(payload):
    class Bad(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Bad)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        check_fallback(RemoteDecider(f"http://127.0.0.1:{server.server_address[1]}", 2.0), time.monotonic())
    finally:
        server.shutdown()
        server.server_close()


def test_http_error_falls_back_and_recovers(rules_server):
    remote = RemoteDecider(rules_server + "/sai", 2.0)  # /sai/decide -> 404
    check_fallback(remote, time.monotonic())
    remote.url = rules_server
    obs, q = cases()[0]
    remote.decide(obs, q)
    assert remote.last_error is None


def test_make_decider_remote():
    d = make_decider("remote", url="http://127.0.0.1:1", timeout_s=0.5)
    assert isinstance(d, RemoteDecider) and d.timeout_s == 0.5
    with pytest.raises(ValueError):
        make_decider("remote")


class HeaderSpy(BaseHTTPRequestHandler):
    """Ghi lại header Authorization của mọi yêu cầu để kiểm tra Pi có gửi token không."""

    seen: ClassVar[list] = []

    def log_message(self, *a):
        pass

    def _reply(self):
        HeaderSpy.seen.append((self.command, self.path, self.headers.get("Authorization")))
        data = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _reply


def spy_server():
    HeaderSpy.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), HeaderSpy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_remote_sends_bearer_only_when_token_set(monkeypatch):
    monkeypatch.delenv("NT532_DECIDER_TOKEN", raising=False)
    server, url = spy_server()
    try:
        obs, q = cases()[0]
        RemoteDecider(url, 3.0).decide(obs, q)  # trả lời sai dạng nên rơi về luật, nhưng vẫn có yêu cầu
        RemoteDecider(url, 3.0).health()
        assert [a for _, _, a in HeaderSpy.seen] == [None, None]
        HeaderSpy.seen.clear()
        d = RemoteDecider(url, 3.0, token="s3cret")
        d.decide(obs, q)
        d.health()
        assert HeaderSpy.seen == [("POST", "/decide", "Bearer s3cret"), ("GET", "/health", "Bearer s3cret")]
        assert "s3cret" not in d.name and "s3cret" not in (d.last_error or "")
        # mặc định lấy từ biến môi trường
        monkeypatch.setenv("NT532_DECIDER_TOKEN", "fromenv")
        HeaderSpy.seen.clear()
        RemoteDecider(url, 3.0).health()
        assert HeaderSpy.seen == [("GET", "/health", "Bearer fromenv")]
    finally:
        server.shutdown()
        server.server_close()


def test_server_token_rejects_missing_or_wrong_bearer():
    import urllib.error
    import urllib.request

    server = make_server(RuleDecider(), "127.0.0.1", 0, log=lambda m: None, token="tok")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for hdr in ({}, {"Authorization": "Bearer sai"}, {"Authorization": "tok"}):
            for path, data in (("/health", None), ("/decide", b"{}")):
                req = urllib.request.Request(url + path, data, hdr)
                with pytest.raises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(req, timeout=3)
                assert e.value.code == 401
        assert RemoteDecider(url, 3.0, token="tok").health() == {"ok": True, "decider": "rules"}
        obs, q = cases()[0]
        remote = RemoteDecider(url, 5.0, token="tok")
        remote.decide(obs, q)
        assert remote.last_error is None
        bad = RemoteDecider(url, 5.0, token="sai")  # sai token: rơi về luật, vẫn quyết định được
        d = bad.decide(obs, q)
        assert "401" in bad.last_error and "dự phòng" in d.decider
    finally:
        server.shutdown()
        server.server_close()
