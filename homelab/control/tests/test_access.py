"""Cloudflare Access: service token đọc từ môi trường và gắn vào client gọi Pi."""

from dataclasses import replace

import pytest

from app.config import ConfigError, load
from app.pi_client import PiClient
from app.store import Store

BASE = {"NT532_ADMIN_PASSWORD": "x", "NT532_SECRET": "s"}
ID_VAR, SECRET_VAR = "NT532_PI_ACCESS_CLIENT_ID", "NT532_PI_ACCESS_CLIENT_SECRET"


def test_load_reads_access_vars():
    s = load({**BASE, ID_VAR: " abc.access ", SECRET_VAR: " shh "})
    assert (s.pi_access_id, s.pi_access_secret) == ("abc.access", "shh")
    s = load(BASE)
    assert (s.pi_access_id, s.pi_access_secret) == ("", "")


@pytest.mark.parametrize("var", [ID_VAR, SECRET_VAR])
def test_load_rejects_only_one_access_var(var):
    with pytest.raises(ConfigError):
        load({**BASE, var: "v"})


async def test_default_client_sends_access_headers(cfg):
    p = PiClient(replace(cfg, pi_access_id="id1", pi_access_secret="sec1"), Store(":memory:"))
    try:
        assert p.http.headers["CF-Access-Client-Id"] == "id1"
        assert p.http.headers["CF-Access-Client-Secret"] == "sec1"
    finally:
        await p.http.aclose()


async def test_default_client_without_access_has_no_headers(cfg):
    p = PiClient(cfg, Store(":memory:"))
    try:
        assert "CF-Access-Client-Id" not in p.http.headers
        assert "CF-Access-Client-Secret" not in p.http.headers
    finally:
        await p.http.aclose()
