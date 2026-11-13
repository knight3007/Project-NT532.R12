import pytest

from app import config
from app.security import hash_password, verify_password

from .conftest import PASSWORD


async def test_healthz_is_public(anon):
    r = await anon.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


@pytest.mark.parametrize("path", ["/api/overview", "/api/events", "/api/stream", "/api/me",
                                  "/camera/stream.mjpg", "/camera/snapshot.jpg"])
async def test_api_requires_session(anon, path):
    assert (await anon.get(path)).status_code == 401


async def test_cmd_requires_session(anon):
    r = await anon.post("/api/cmd", json={"cmd": "stop"})
    assert r.status_code == 401


async def test_pages_redirect_to_login(anon):
    r = await anon.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert (await anon.get("/login")).status_code == 200


async def test_login_sets_httponly_samesite_cookie(anon):
    r = await anon.post("/login", data={"username": "admin", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert (await anon.get("/")).status_code == 200
    r = await anon.post("/logout")
    assert r.status_code == 303
    assert (await anon.get("/api/me")).status_code == 401


async def test_login_wrong_password(anon):
    r = await anon.post("/login", data={"username": "admin", "password": "sai"})
    assert r.status_code == 303 and r.headers["location"] == "/login?e=1"
    assert "set-cookie" not in r.headers


async def test_login_rate_limit(anon):
    for _ in range(5):
        r = await anon.post("/login", data={"username": "admin", "password": "sai"})
        assert r.headers["location"] == "/login?e=1"
    # từ lần thứ sáu bị chặn, kể cả khi mật khẩu đúng
    r = await anon.post("/login", data={"username": "admin", "password": PASSWORD})
    assert r.headers["location"] == "/login?e=2"
    assert (await anon.get("/api/me")).status_code == 401


async def test_overview_shape(client, app):
    await app.state.pi.poll_once()
    j = (await client.get("/api/overview")).json()
    assert set(j) >= {"pi_online", "state", "limits", "sim", "last_events"}
    assert j["pi_online"] is True and j["sim"] is True
    assert j["limits"] == {"temp": 45.0, "gas": 600.0}
    assert j["state"]["orch"]["phase"] == "IDLE"
    assert isinstance(j["last_events"], list)


async def test_overview_when_pi_never_answered(client, app, fake_pi):
    fake_pi.up = False
    await app.state.pi.poll_once()
    j = (await client.get("/api/overview")).json()
    assert j["pi_online"] is False and j["state"] is None and j["last_events"] == []
    assert (await client.get("/camera/snapshot.jpg")).status_code in (502, 200)


async def test_events_endpoint_filters(client, app, fake_pi):
    fake_pi.add_event(1, "alert", "a", node="s1")
    fake_pi.add_event(2, "phase", "b", node="s2")
    await app.state.pi.poll_once()
    assert [e["text"] for e in (await client.get("/api/events")).json()] == ["b", "a"]
    assert [e["text"] for e in (await client.get("/api/events?kind=alert")).json()] == ["a"]
    assert [e["text"] for e in (await client.get("/api/events?node=s2")).json()] == ["b"]
    first = (await client.get("/api/events")).json()[-1]["id"]
    assert [e["text"] for e in (await client.get(f"/api/events?since={first}")).json()] == ["b"]


async def test_cmd_forwarded_and_logged(client, app, fake_pi):
    await app.state.pi.poll_once()
    r = await client.post("/api/cmd", json={"cmd": "enable", "on": False})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert fake_pi.cmds == [{"cmd": "enable", "on": False}]
    ev = (await client.get("/api/events?kind=cmd")).json()[0]
    assert ev["user"] == "admin" and ev["cmd"] == "enable" and "admin" in ev["text"]


async def test_cmd_decider_params(client, app, fake_pi):
    await app.state.pi.poll_once()
    r = await client.post("/api/cmd", json={"cmd": "decider", "kind": "hybrid", "tau": 0.7})
    assert r.status_code == 200 and fake_pi.cmds[-1] == {"cmd": "decider", "kind": "hybrid", "tau": 0.7}
    bad = [{"cmd": "decider", "kind": "evil"}, {"cmd": "decider"}, {"cmd": "decider", "kind": "rules", "tau": 5},
           {"cmd": "decider", "kind": "jev", "run": "../../etc"}, {"cmd": "stop", "extra": 1}]
    for body in bad:
        assert (await client.post("/api/cmd", json=body)).status_code == 400, body


@pytest.mark.parametrize("body", [{"cmd": "rm -rf"}, {"cmd": "camera_set"}, {"cmd": 5}, {}, [], {"cmd": "online"}])
async def test_cmd_rejects_unknown(client, app, fake_pi, body):
    await app.state.pi.poll_once()
    r = await client.post("/api/cmd", json=body)
    assert r.status_code == 400 and r.json()["ok"] is False
    assert fake_pi.cmds == []


async def test_cmd_rejects_non_json_content_type(client, app):
    await app.state.pi.poll_once()
    r = await client.post("/api/cmd", content='{"cmd":"stop"}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


async def test_sim_cmd_rejected_when_not_sim(client, app, fake_pi):
    fake_pi.sim = False
    await app.state.pi.poll_once()
    r = await client.post("/api/cmd", json={"cmd": "fire", "size": "small"})
    assert r.status_code == 400 and "sa bàn ảo" in r.json()["error"]
    assert fake_pi.cmds == []
    assert (await client.post("/api/cmd", json={"cmd": "stop"})).status_code == 200


async def test_sim_cmd_allowed_when_sim(client, app, fake_pi):
    await app.state.pi.poll_once()
    assert (await client.post("/api/cmd", json={"cmd": "fire", "size": "large", "x": 0.4})).status_code == 200
    assert (await client.post("/api/cmd", json={"cmd": "fire", "size": "huge"})).status_code == 400
    assert fake_pi.cmds == [{"cmd": "fire", "size": "large", "x": 0.4}]


async def test_cmd_pi_down_returns_502_and_logs(client, app, fake_pi):
    await app.state.pi.poll_once()
    fake_pi.up = False
    r = await client.post("/api/cmd", json={"cmd": "stop"})
    assert r.status_code == 502 and r.json()["ok"] is False
    ev = (await client.get("/api/events?kind=cmd")).json()[0]
    assert ev["ok"] is False and ev["level"] == "warn"


async def test_mjpeg_proxy_passes_boundary(client):
    async with client.stream("GET", "/camera/stream.mjpg") as r:
        assert r.status_code == 200
        assert r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
        body = b"".join([c async for c in r.aiter_bytes()])
    assert body.count(b"--frame") == 3


async def test_snapshot_proxy(client):
    r = await client.get("/camera/snapshot.jpg")
    assert r.status_code == 200 and r.content == b"JPEG" and r.headers["content-type"] == "image/jpeg"


async def test_camera_502_when_pi_down(client, fake_pi):
    fake_pi.up = False
    assert (await client.get("/camera/stream.mjpg")).status_code == 502


# --- cấu hình và mật khẩu ---------------------------------------------------------------------


def test_password_hash_roundtrip():
    h = hash_password("abc12345")
    assert verify_password("abc12345", h) and not verify_password("abc12346", h)
    assert not verify_password("x", "rác")


def test_config_refuses_without_password():
    with pytest.raises(config.ConfigError, match="mật khẩu"):
        config.load({})


def test_config_defaults_and_plain_password():
    c = config.load({"NT532_ADMIN_PASSWORD": "tam-thoi-123"})
    assert c.pi_url == "http://127.0.0.1:8080" and c.poll_s == 1.0 and c.retention_days == 14
    assert c.admin_user == "admin" and c.secret  # khóa sinh ngẫu nhiên
    assert verify_password("tam-thoi-123", c.admin_hash)
