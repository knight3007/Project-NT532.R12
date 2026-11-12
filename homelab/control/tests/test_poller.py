import httpx

from app.pi_client import PiClient
from app.store import Store


def make_client(cfg, fake_pi, probe_every=1):
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_pi.app), base_url="http://pi")
    store = Store(":memory:")
    return PiClient(cfg, store, client=http, probe_every=probe_every), store


async def test_poller_stores_events_telemetry_and_runs(cfg, fake_pi):
    fake_pi.add_event(1, "alert", "cảnh báo s1", node="s1")
    fake_pi.add_event(2, "phase", "LOCALIZE")
    p, store = make_client(cfg, fake_pi)
    await p.poll_once()
    await p.poll_once()  # lần hai không ghi trùng
    evs = store.events()
    assert [e["text"] for e in evs] == ["LOCALIZE", "cảnh báo s1"]  # mới nhất trước
    assert evs[1]["node"] == "s1"
    assert store.count("telemetry") == 4  # 2 node x 2 lần hỏi
    assert store.count("runs") == 1
    assert p.pi_online and p.last_seq == 2
    fake_pi.add_event(3, "decision", "chọn s2")
    await p.poll_once()
    assert store.events(limit=1)[0]["text"] == "chọn s2"


async def test_poller_resyncs_on_seq_reset(cfg, fake_pi):
    for i in range(1, 6):
        fake_pi.add_event(i, text=f"cũ {i}")
    p, store = make_client(cfg, fake_pi)
    await p.poll_once()
    assert p.last_seq == 5
    # Pi khởi động lại: seq về 1
    fake_pi.events.clear()
    fake_pi.add_event(1, text="mới 1")
    fake_pi.add_event(2, text="mới 2")
    await p.poll_once()
    texts = [e["text"] for e in store.events(limit=50)]
    assert "mới 1" in texts and "mới 2" in texts
    assert any("khởi động lại" in t for t in texts)
    assert p.last_seq == 2


async def test_poller_offline_and_recovery(cfg, fake_pi):
    p, store = make_client(cfg, fake_pi)
    await p.poll_once()
    assert p.pi_online
    fake_pi.up = False
    for _ in range(3):
        await p.poll_once()
    assert any(e["text"] == "Mất kết nối với Pi" for e in store.events())
    assert p.error and p.overview()["state"] is not None  # vẫn giữ trạng thái cuối
    fake_pi.up = True
    await p.poll_once()
    assert any("kết nối lại" in e["text"] for e in store.events())


async def test_last_seq_survives_restart_of_control(cfg, fake_pi, tmp_path):
    fake_pi.add_event(1)
    fake_pi.add_event(2)
    path = str(tmp_path / "r.sqlite")
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_pi.app), base_url="http://pi")
    s1 = Store(path)
    p1 = PiClient(cfg, s1, client=http, probe_every=1)
    await p1.poll_once()
    s1.close()
    s2 = Store(path)
    p2 = PiClient(cfg, s2, client=http, probe_every=1)
    assert p2.last_seq == 2
    await p2.poll_once()
    assert s2.count("events") == 2
