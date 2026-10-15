"""Trạm ảo với camera và bố cục node tùy chọn: dựng trạm, lệnh camera và đặt nguồn trên nền của dashboard,
cảnh demo trên nền chạy hết một lượt (cảnh báo, phun, tắt)."""

import argparse

import numpy as np
import pytest

from nt532.dashboard.server import Dashboard
from nt532.orchestrator.machine import Phase
from nt532.sim import CAMERA_PRESETS, add_sim_args, camera_spec, sim_options
from nt532.sim.demo import SCENES, Skip, fast_settings, reach_nodes, reachable_spot, run_scene
from nt532.station import build_sim
from nt532.vision.localize import project


def events(st, kind):
    return [e["text"] for e in st.events.since(0, 10_000) if e["kind"] == kind]


# --- tùy chọn dòng lệnh ---------------------------------------------------------------------------


def test_camera_spec_parses_presets_and_coordinates():
    assert camera_spec(None, None) is None
    assert camera_spec("left", None) == "left"
    assert camera_spec("0.5,-1,1", None) == ((0.5, -1.0, 1.0), (0.6, 0.4, 0.2))
    assert camera_spec("0.5,-1,1", "0.5,0.4,0") == ((0.5, -1.0, 1.0), (0.5, 0.4, 0.0))
    pos, _ = CAMERA_PRESETS["right"]
    assert camera_spec("right", "0.6,0.5,0.1") == (pos, (0.6, 0.5, 0.1))
    assert camera_spec(None, "0.6,0.5,0.1")[1] == (0.6, 0.5, 0.1)  # chỉ --look-at: giữ vị trí preset mặc định
    with pytest.raises(ValueError, match="x,y,z"):
        camera_spec("1,2", None)


def test_sim_options_from_argparse():
    p = argparse.ArgumentParser()
    add_sim_args(p)
    args = p.parse_args(["--camera", "left", "--layout", "corners"])
    assert sim_options(args, p) == {"camera": "left", "node_layout": "corners"}
    assert sim_options(p.parse_args([]), p) == {"camera": None, "node_layout": None}
    with pytest.raises(SystemExit):
        sim_options(p.parse_args(["--camera", "a,b,c"]), p)


# --- build_sim ------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corners():
    st = build_sim(camera="left", node_layout="corners", settings=fast_settings(), fps=12)
    st.start()
    yield st
    st.stop()


def test_build_sim_commissions_the_chosen_camera_and_layout(corners):
    st = corners
    scene = st.world.scene
    assert scene.node_layout == "corners"
    assert np.allclose(st.vision.camera.position, CAMERA_PRESETS["left"][0], atol=0.03)
    for n, node in scene.nodes.items():  # pose đo được khớp vị trí thật của node ở bố cục góc
        assert np.linalg.norm(st.vision.nodes[n].position - node.position) < 0.03
    assert st.vision.cfg["commissioning"].endswith("station_commissioning.yaml")
    assert any("corners" in t for t in events(st, "vision"))


def test_floor_fire_demo_scene_extinguishes_with_corners_layout(corners):
    r = run_scene(corners, SCENES["fire_floor"])
    assert r["skipped"] is None and not r["timed_out"], r
    assert r["verdict"] == {"decision_ok": True, "safety_ok": True, "notes": []}, r
    assert r["outcomes"][-1] == "extinguished" and r["fire_out"] is True and r["sprayed"]
    tgt = [rn["target"] for rn in r["runs"] if rn.get("target")]
    assert tgt and all(t["surface"] == "floor" for t in tgt), tgt


def test_wall_scenes_pick_reachable_spots_for_corners(corners):
    for node in ("s1", "s2"):
        x, z = reachable_spot(corners, node, 0.32 if node == "s1" else 0.90, 0.30)
        assert node in reach_nodes(corners, x, z)
    x, y = reachable_spot(corners, "s1", 0.30, None, 0.50)
    assert "s1" in reach_nodes(corners, x, 0.0, y)


def test_unreachable_floor_skips_scene_with_note():
    st = build_sim(settings=fast_settings(), fps=12)  # bố cục default: vòi trên trụ không với tới nền gần
    st.start()
    try:
        try:
            reachable_spot(st, "s1", 0.30, None, 0.50)
        except Skip:
            r = run_scene(st, SCENES["fire_floor"])
            assert r["skipped"] and r["runs"] == [] and r["sprayed"] == []
            assert r["verdict"]["decision_ok"] is None and r["verdict"]["safety_ok"] is True
            assert "bỏ qua cảnh" in r["verdict"]["notes"][0]
        else:  # bố cục default lại với tới được: cảnh phải chạy bình thường
            r = run_scene(st, SCENES["fire_floor"])
            assert r["skipped"] is None
    finally:
        st.stop()


# --- dashboard: camera và đặt nguồn trên nền -------------------------------------------------------


@pytest.fixture(scope="module")
def rig():
    st = build_sim(camera="left", node_layout="corners", settings=fast_settings(), fps=12)
    st.start(orchestrate=False)
    dash = Dashboard(st, "127.0.0.1", 0).start()
    yield st, dash
    dash.stop()
    st.stop()


def test_camera_preset_command_returns_coverage(rig):
    st, dash = rig
    r = dash.command({"cmd": "camera_preset", "name": "front"})
    assert r["ok"] and set(r["coverage"]) >= {"floor", "wall", "warnings", "ok"}
    pos, _ = st.world.scene.camera_pose()
    assert np.allclose(pos, CAMERA_PRESETS["front"][0], atol=1e-6)
    assert any("preset front" in t for t in events(st, "sim"))
    bad = dash.command({"cmd": "camera_preset", "name": "nope"})
    assert bad["ok"] is False and "nope" in bad["error"]


def test_state_carries_layout_camera_and_coverage(rig):
    _, dash = rig
    truth = dash.state()["truth"]
    assert truth["layout"] == "corners"
    assert len(truth["camera"]["position"]) == 3 and len(truth["camera"]["look_at"]) == 3
    assert 0.0 <= truth["coverage"]["floor"] <= 1.0 and isinstance(truth["coverage"]["warnings"], list)


def test_camera_set_command(rig):
    st, dash = rig
    r = dash.command({"cmd": "camera_set", "x": 0.7, "y": -0.9, "z": 1.0, "lx": 0.6, "ly": 0.4, "lz": 0.2})
    assert r["ok"] and r["camera"]["position"] == [0.7, -0.9, 1.0]
    pos, look = st.world.scene.camera_pose()
    assert np.allclose(pos, [0.7, -0.9, 1.0]) and np.allclose(look, [0.6, 0.4, 0.2])
    assert dash.command({"cmd": "camera_set", "x": 1})["ok"] is False
    same = dash.command({"cmd": "camera_set", "x": 0, "y": 0, "z": 1, "lx": 0, "ly": 0, "lz": 1})
    assert same["ok"] is False


def test_jitter_then_check_camera_reposes_vision(rig):
    st, dash = rig
    assert dash.command({"cmd": "camera_preset", "name": "left"})["ok"]
    assert dash.command({"cmd": "check_camera"})["ok"]  # pose về đúng preset trước khi xê dịch
    before = st.vision.camera.position.copy()
    assert dash.command({"cmd": "camera_jitter"})["ok"]
    assert any("xê dịch" in t for t in events(st, "sim"))
    c = dash.command({"cmd": "check_camera"})
    assert c["ok"], c
    assert any("tính lại pose" in t for t in events(st, "vision")), events(st, "vision")
    assert np.linalg.norm(st.vision.camera.position - st.world.scene.camera.position) < 0.02
    assert np.linalg.norm(st.vision.camera.position - before) > 1e-4  # pose đã được cập nhật


def test_check_camera_refused_while_busy(rig):
    st, dash = rig
    st.orch.phase = Phase.AIM
    try:
        assert dash.command({"cmd": "check_camera"})["ok"] is False
    finally:
        st.orch.phase = Phase.IDLE


@pytest.mark.parametrize("cmd,kind,size", [("fire", "fire", "small"), ("fire_large", "fire", "large"),
                                           ("lamp", "lamp", "small"), ("object", "object", "small")])
def test_click_on_floor_places_source_on_floor(rig, cmd, kind, size):
    st, dash = rig
    st.world.clear()
    dash.command({"cmd": "camera_preset", "name": "left"})
    dash.command({"cmd": "check_camera"})
    u, v = project(np.array([0.55, 0.30, 0.0]), st.vision.camera)
    r = dash.command({"cmd": cmd, "u": float(u), "v": float(v)})
    assert r["ok"], r
    s = r["source"]
    assert s["kind"] == kind and s["surface"] == "floor" and s["size"] == size
    assert abs(s["x"] - 0.55) < 0.03 and abs(s["y"] - 0.30) < 0.03 and s["z"] == 0.0
    assert any("trên nền" in t for t in events(st, "sim"))
    assert st.world.scene.cards  # có thẻ thật trên nền để detector nhìn thấy


def test_click_on_wall_still_places_on_wall_and_direct_floor_coordinates(rig):
    st, dash = rig
    st.world.clear()
    plane_y = st.site["board"]["plane_y"]
    u, v = project(np.array([0.6, plane_y, 0.3]), st.vision.camera)
    s = dash.command({"cmd": "fire", "u": float(u), "v": float(v)})["source"]
    assert s["surface"] == "wall" and abs(s["x"] - 0.6) < 0.03 and abs(s["z"] - 0.3) < 0.03
    s = dash.command({"cmd": "fire", "surface": "floor", "x": 0.4, "y": 0.4})["source"]
    assert s["surface"] == "floor" and (s["x"], s["y"]) == (0.4, 0.4)
    assert dash.command({"cmd": "fire", "surface": "ceiling", "x": 0.4})["ok"] is False
    assert dash.command({"cmd": "fire", "u": 5.0, "v": 5.0})["ok"] is False
