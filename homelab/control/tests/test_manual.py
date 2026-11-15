"""Lệnh manual: kiểm tra tham số, status không ghi sự kiện, trang /manual cần đăng nhập."""

import pytest

from app.main import validate_cmd


@pytest.mark.parametrize("body", [
    {"cmd": "manual", "node": "s1", "op": "status"},
    {"cmd": "manual", "node": "s2", "op": "arm"},
    {"cmd": "manual", "node": "s1", "op": "move", "pan_us": 500, "tilt_us": 2500},
    {"cmd": "manual", "node": "s1", "op": "laser", "on": True},
    {"cmd": "manual", "node": "s1", "op": "stop"},
    {"cmd": "manual", "node": "s1", "op": "mark", "note": "bị chân trụ chắn"},
])
def test_manual_valid(body):
    assert validate_cmd(body, sim=False) == body


@pytest.mark.parametrize("body", [
    {"cmd": "manual", "node": "s1"},
    {"cmd": "manual", "op": "arm"},
    {"cmd": "manual", "node": "s3", "op": "arm"},
    {"cmd": "manual", "node": "s1", "op": "fire"},
    {"cmd": "manual", "node": "s1", "op": "move"},
    {"cmd": "manual", "node": "s1", "op": "move", "pan_us": 499, "tilt_us": 1500},
    {"cmd": "manual", "node": "s1", "op": "move", "pan_us": 1500, "tilt_us": 2501},
    {"cmd": "manual", "node": "s1", "op": "move", "pan_us": 1500.5, "tilt_us": 1500},
    {"cmd": "manual", "node": "s1", "op": "move", "pan_us": True, "tilt_us": 1500},
    {"cmd": "manual", "node": "s1", "op": "laser"},
    {"cmd": "manual", "node": "s1", "op": "laser", "on": "yes"},
    {"cmd": "manual", "node": "s1", "op": "mark"},
    {"cmd": "manual", "node": "s1", "op": "mark", "note": "x" * 121},
    {"cmd": "manual", "node": "s1", "op": "mark", "note": "a\nb"},
    {"cmd": "manual", "node": "s1", "op": "status", "extra": 1},
])
def test_manual_invalid(body):
    with pytest.raises(ValueError):
        validate_cmd(body, sim=False)


async def test_manual_status_not_recorded_but_others_are(client, app, fake_pi):
    assert (await client.post("/api/cmd", json={"cmd": "manual", "node": "s1", "op": "status"})).status_code == 200
    assert (await client.get("/api/events?kind=cmd")).json() == []
    r = await client.post("/api/cmd", json={"cmd": "manual", "node": "s1", "op": "mark", "note": "tag 2"})
    assert r.status_code == 200
    evs = (await client.get("/api/events?kind=cmd")).json()
    assert len(evs) == 1 and evs[0]["cmd"] == "manual" and evs[0]["params"]["op"] == "mark"
    assert [c["op"] for c in fake_pi.cmds] == ["status", "mark"]


async def test_manual_bad_body_400(client):
    r = await client.post("/api/cmd", json={"cmd": "manual", "node": "s1", "op": "move", "pan_us": 9})
    assert r.status_code == 400


async def test_manual_page_requires_login(anon):
    r = await anon.get("/manual")
    assert r.status_code == 303 and r.headers["location"] == "/login"


async def test_manual_page_served_when_logged_in(client):
    r = await client.get("/manual")
    assert r.status_code == 200 and "Điều khiển tay" in r.text
