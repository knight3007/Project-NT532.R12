"""Cả trạm ảo qua IntegratedLink + IntegratedFakeNode (lõi C node B tích hợp)."""

import time

import pytest

from nt532.net.fakenode import CoreUnavailable
from nt532.net.integrated_node import IntegratedCore
from nt532.orchestrator.machine import Settings
from nt532.station import build_sim


@pytest.fixture(scope="module", autouse=True)
def core():
    try:
        return IntegratedCore.get()
    except CoreUnavailable as e:
        pytest.skip(f"không có lõi C node B tích hợp: {e}")


def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def station():
    st = build_sim(settings=Settings(frame_gap_s=0.05, verify_wait_s=1.0), fps=12, link="coap-integrated")
    st.start()
    yield st
    st.stop()


def test_build_sim_rejects_unknown_link():
    with pytest.raises(ValueError, match="coap-integrated"):
        build_sim(link="xyz")


def test_station_starts_and_nodes_answer_info(station):
    assert set(station.fake_nodes) == set(station.link.nodes) == set(station.world.nodes)
    for n in station.link.nodes:
        info = station.link.info(n)
        assert info.n == n and info.fw.startswith("sim-integrated-")
        assert info.pan[0] < info.pan[1] and info.tilt[0] < info.tilt[1]
    assert wait_for(lambda: all(station.link.hb_age_ms(n) < 1500 for n in station.link.nodes), 5)


def test_station_full_run_aims_and_fires_laser_but_pump_is_rejected(station):
    station.world.ignite(0.32, 0.30)
    assert wait_for(lambda: station.orch.runs, 90), "không có lượt nào hoàn tất"
    run = station.orch.runs[0]
    assert run.shots, run.to_json()  # CORRECT: laser đã bật và camera thấy vết
    node_events = [e["text"] for e in station.events.since(0, 10_000) if e["kind"] == "node"]
    assert any("/pantilt" in t and "-> 0" in t for t in node_events)  # ngắm được chấp nhận
    assert any("/laser ON" in t for t in node_events)
    # firmware không có bơm: lệnh phun bị từ chối nên lượt không thể "extinguished" bằng nước
    assert run.outcome != "extinguished", run.to_json()
    assert not any(station.world.truth()["lasers"].values())
    assert not any(station.world.pumping.values())
