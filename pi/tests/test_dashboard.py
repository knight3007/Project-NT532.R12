"""Dashboard trên trạm ảo: lệnh sa bàn, JSON trạng thái, vẽ overlay của từng vòi và một vòng HTTP thật.
Orchestrator không chạy (không có lượt phun nào) nên mỗi test chỉ mất vài trăm ms."""

import json
import urllib.error
import urllib.request

import pytest

from nt532.dashboard.server import Dashboard
from nt532.station import build_sim

AIMS = {"s1": {"x": 0.55, "z": 0.30}, "s2": {"x": 0.65, "z": 0.32}}
SPOTS = {"s1": {"x": 0.56, "z": 0.29}, "s2": {"x": 0.64, "z": 0.31}}


@pytest.fixture(scope="module")
def rig():
    st = build_sim()
    st.start(orchestrate=False)
    dash = Dashboard(st, "127.0.0.1", 0).start()  # cổng 0: hệ điều hành chọn cổng trống
    yield st, dash
    dash.stop()
    st.stop()


@pytest.fixture
def dash(rig):
    st, d = rig
    st.world.clear()
    st.orch.overlay = {}
    return d


def sim_events(st):
    return [e["text"] for e in st.events.since(0, 10_000) if e["kind"] == "sim"]


def test_fire_large_creates_large_source_and_event(rig, dash):
    st, _ = rig
    r = dash.command({"cmd": "fire_large", "x": 0.6, "z": 0.3})
    assert r["ok"] and r["source"]["kind"] == "fire" and r["source"]["size"] == "large"
    srcs = st.world.truth()["sources"]
    assert [(s["kind"], s["size"]) for s in srcs] == [("fire", "large")]
    assert any("đám cháy lớn" in t and "x 0.60 z 0.30" in t for t in sim_events(st))


def test_fire_size_is_validated(rig, dash):
    st, _ = rig
    r = dash.command({"cmd": "fire", "size": "huge", "x": 0.6, "z": 0.3})
    assert r["ok"] is False and "huge" in r["error"]
    assert st.world.truth()["sources"] == []
    r = dash.command({"cmd": "fire", "x": 0.4, "z": 0.2})  # không nói cỡ: lửa nhỏ như trước
    assert r["ok"] and r["source"]["size"] == "small"
    r = dash.command({"cmd": "fire", "size": "large", "x": 0.8, "z": 0.2})
    assert r["ok"] and r["source"]["size"] == "large"
    texts = sim_events(st)
    assert any("đám cháy lớn" in t for t in texts) and any(t.startswith("thêm đám cháy tại") for t in texts)


def test_state_is_json_and_carries_overlay(rig, dash):
    st, _ = rig
    json.dumps(dash.state())  # lúc chưa có lượt nào (overlay rỗng)
    st.orch.overlay = {"active": ["s1", "s2"], "aims": AIMS, "spots": SPOTS,
                       "target": {"id": "T1", "x": 0.6, "z": 0.3}, "nozzle": "both (s1 + s2)"}
    state = json.loads(json.dumps(dash.state()))
    ov = state["orch"]["overlay"]
    assert ov["active"] == ["s1", "s2"] and set(ov["aims"]) == set(ov["spots"]) == {"s1", "s2"}
    assert state["sim"] is True and "truth" in state


def test_annotate_draws_both_nozzles(rig, dash):
    st, _ = rig
    frame = st.hub.fresh()
    base = dash.annotate(frame.copy())

    def changed(ov):
        st.orch.overlay = ov
        return int((dash.annotate(frame.copy()) != base).any(axis=2).sum())

    assert changed({}) == 0
    one = changed({"aims": {"s1": AIMS["s1"]}, "spots": {"s1": SPOTS["s1"]}})
    both = changed({"aims": AIMS, "spots": SPOTS, "active": ["s1", "s2"]})
    assert 0 < one < both
    assert dash.frame() is not None  # cả đường lấy khung mới nhất của hub rồi vẽ


def test_http_round_trip(rig, dash):
    st, _ = rig
    st.orch.overlay = {"active": ["s2"], "aims": AIMS, "spots": SPOTS}

    def get(path):
        with urllib.request.urlopen(dash.url.rstrip("/") + path, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    code, ctype, body = get("/api/state")
    assert code == 200 and "json" in ctype
    state = json.loads(body)
    assert state["orch"]["overlay"]["active"] == ["s2"] and state["orch"]["phase"] == "IDLE"
    code, ctype, body = get("/")
    assert code == 200 and "html" in ctype and "Lửa lớn" in body.decode("utf-8")
    code, ctype, body = get("/snapshot.jpg")
    assert code == 200 and ctype == "image/jpeg" and body[:2] == b"\xff\xd8"

    req = urllib.request.Request(dash.url + "api/cmd", method="POST",
                                 data=json.dumps({"cmd": "fire_large", "x": 0.6, "z": 0.3}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        reply = json.loads(r.read())
    assert reply["ok"] and reply["source"]["size"] == "large"
    truth = json.loads(get("/api/state")[2])["truth"]
    assert [s["size"] for s in truth["sources"]] == ["large"]

    with pytest.raises(urllib.error.HTTPError) as e:  # đường dẫn lạ
        get("/khong-co")
    assert e.value.code == 404
