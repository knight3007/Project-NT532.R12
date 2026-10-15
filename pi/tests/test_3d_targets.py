"""Bia ba chiều: bia trên nền (Z = 0) và trên bảng (Y = plane_y) trong orchestrator, ngắm, gộp bia,
obs cho bộ quyết định, dashboard, aim_point."""

import copy
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from conftest import look_at, make_scene

from nt532.dashboard.server import Dashboard
from nt532.orchestrator import aiming
from nt532.orchestrator.events import EventLog
from nt532.orchestrator.fusion import aggregate, decide_obs, verify_obs
from nt532.orchestrator.machine import Orchestrator
from nt532.sim import sim_site
from nt532.station import build_sim
from nt532.vision import Vision, project
from nt532.vision.types import Detection, Target

PIVOT = np.array([0.30, 0.558, 0.327])  # tâm quay s1 trong commissioning của sa bàn ảo
NODE_X = {"s1": 0.30, "s2": 0.90}

_path = Path(__file__).resolve().parents[1] / "scripts" / "aim_point.py"
_spec = importlib.util.spec_from_file_location("aim_point", _path)
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


def site_with(tilt=None, **act) -> dict:
    site = copy.deepcopy(sim_site(write=False))
    node = {} if tilt is None else {"tilt_limits": list(tilt)}
    site["actuator"]["nodes"] = {n: dict(node) for n in ("s1", "s2")}
    site["actuator"].update(act)
    return site


def target(x, y, z, conf=0.8, surface=None):
    surface = surface or ("floor" if z == 0 else "wall")
    return Target(Detection(0, 0, 40, 40, conf), np.array([x, y, z]), surface)


# --- ngắm 3D -----------------------------------------------------------------------------

def test_floor_point_needs_steep_tilt():
    point = (0.30, 0.75, 0.0)  # trên nền, sát chân bảng, cách tâm quay 0,19 m theo ngang
    _, tilt = aiming.angles(PIVOT, point)
    assert tilt < -55
    assert not aiming.reachable_point(site_with(), "s1", PIVOT, point)  # mặc định -35 độ
    assert not aiming.reachable_point(site_with((-35, 45)), "s1", PIVOT, point)
    assert aiming.reachable_point(site_with((-70, 45)), "s1", PIVOT, point)


def test_floor_point_behind_the_nozzle_is_out_of_pan():
    # bia ở trước tâm quay (y nhỏ hơn): vòi quay lưng lại, pan vượt xa +-70
    assert not aiming.reachable_point(site_with((-90, 45)), "s1", PIVOT, (0.30, 0.20, 0.0))


def test_wall_reachability_keeps_old_signature():
    site = site_with()
    assert aiming.reachable(site, "s1", PIVOT, 0.35, 0.30)
    assert aiming.reachable(site, "s1", PIVOT, 0.35, 0.30, site["board"]["plane_y"])
    assert aiming.reachable(site, "s1", PIVOT, 0.30, 0.0, y=0.75) == aiming.reachable_point(
        site, "s1", PIVOT, (0.30, 0.75, 0.0))


def test_water_tilt_uses_horizontal_xy_distance_for_floor_and_wall():
    site = site_with(water_tilt_deg=0.0)
    site["actuator"]["nodes"]["s1"]["water_tilt_table"] = [[0.2, 1.0], [0.6, 5.0]]
    floor = (0.30 + 0.30, 0.558 + 0.30, 0.0)  # cách ngang hypot(0.3, 0.3) = 0.424
    wall_high = (floor[0], floor[1], 0.55)  # cùng khoảng cách ngang, cao hơn rất nhiều
    d = aiming.horizontal_distance(PIVOT, floor)
    assert d == pytest.approx(np.hypot(0.30, 0.30))
    assert aiming.horizontal_distance(PIVOT, wall_high) == pytest.approx(d)
    expect = 1.0 + (d - 0.2) / 0.4 * 4.0
    assert aiming.water_tilt(site, "s1", d) == pytest.approx(expect)


# --- sai lệch CORRECT là chuẩn 3D --------------------------------------------------------

def bare_orchestrator(site, spot):
    o = Orchestrator.__new__(Orchestrator)
    o.site, o.tcfg = site, {"correct_max_iters": 1, "correct_done_m": 0.02}
    o.cfg = SimpleNamespace(laser_ms=100)
    o.overlay, o._lock, o.events = {}, threading.Lock(), EventLog()
    o.hub = SimpleNamespace(fresh=lambda: None)
    o.link = SimpleNamespace(fire=lambda *a, **k: None, wait=lambda *a, **k: None)
    o.vision = SimpleNamespace(find_spot=lambda on, off: spot)
    o._set = lambda *a, **k: None
    o._sleep = lambda s: None
    o._aim = lambda node, pan, tilt: 1
    return o


def test_miss_is_3d_norm():
    site = site_with()
    goal = np.array([0.40, 0.50, 0.0])
    spot = np.array([0.43, 0.54, 0.0])  # lệch 3 cm theo X, 4 cm theo Y: chuẩn 5 cm
    o = bare_orchestrator(site, spot)
    run = SimpleNamespace(shots=[])
    _, miss_cm = o._aim_and_correct(run, "s1", PIVOT, goal, goal.copy())
    assert miss_cm == pytest.approx(5.0)
    assert run.shots[0]["miss_cm"] == pytest.approx(5.0)
    assert o.overlay["spots"]["s1"] == {"x": 0.43, "y": 0.54, "z": 0.0, "surface": "floor"}
    assert o.overlay["aims"]["s1"]["surface"] == "floor"


def test_correct_keeps_aim_point_on_its_surface():
    site = site_with()
    o = bare_orchestrator(site, None)
    assert list(o._plane_mask((0.4, 0.5, 0.0))) == [1.0, 1.0, 0.0]
    assert list(o._plane_mask((0.4, site["board"]["plane_y"], 0.3))) == [1.0, 0.0, 1.0]


def test_blocked_checks_3d_reach():
    site = site_with((-35, 45))
    o = bare_orchestrator(site, None)
    o.vision = SimpleNamespace(blockers=lambda n: [], on_board=lambda p: True)
    o.pose_age = lambda n: 0.0
    o.link = SimpleNamespace(hb_age_ms=lambda n: 0)
    floor = np.array([0.30, 0.75, 0.0])
    assert o._blocked("s1", floor, {"s1": PIVOT}) == ["s1 không với tới bia"]
    o.site = site_with((-70, 45))
    assert o._blocked("s1", floor, {"s1": PIVOT}) == []


# --- gộp bia và obs ----------------------------------------------------------------------

def test_two_floor_fires_same_x_different_y_stay_apart():
    near, far = target(0.50, 0.30, 0.0), target(0.50, 0.40, 0.0)  # cách nhau 10 cm theo Y
    tracks = aggregate([[near, far], [near, far]], (1280, 720))
    assert len(tracks) == 2
    assert sorted(round(float(t.position[1]), 2) for t in tracks) == [0.30, 0.40]
    assert all(t.surface == "floor" for t in tracks)
    # trong 5 cm theo 3D thì vẫn là một bia
    assert len(aggregate([[near], [target(0.50, 0.33, 0.0)]], (1280, 720))) == 1


def test_floor_and_wall_target_never_merge_even_if_close_in_xz():
    tracks = aggregate([[target(0.5, 0.78, 0.0), target(0.5, 0.80, 0.02, surface="wall")]],
                       (1280, 720))
    assert len(tracks) == 2


def history():
    from nt532.net.protocol import Telemetry
    from nt532.orchestrator.fusion import SensorHistory

    h = SensorHistory()
    for i in range(6):
        for n in ("s1", "s2"):
            h.add(Telemetry(n, i, i, 27.0, 180, 50))
    return h


def test_decide_obs_carries_y_surface_and_3d_dist():
    tracks = aggregate([[target(0.30, 0.75, 0.0), target(0.60, 0.80, 0.30)]] * 2, (1280, 720))
    seen = []

    def reach(x, z, y=None):
        seen.append((x, z, y))
        return True

    noz = {n: {"hb_ms": 100, "pose_s": 1, "reach": reach, "pivot": PIVOT} for n in ("s1", "s2")}
    obs, by_id = decide_obs("s1", history(), tracks, noz, NODE_X, 2, {"temp": 45.0, "gas": 600.0})
    floor, wall = obs["targets"]
    assert (floor["surface"], floor["y"], floor["x"], floor["z"]) == ("floor", 0.75, 0.30, 0.0)
    assert (wall["surface"], wall["y"]) == ("wall", 0.8)
    assert floor["dist"]["s1"] == 0.0  # `dist` giữ nghĩa cũ: chỉ lệch X
    assert floor["dist3"]["s1"] == pytest.approx(np.sqrt(0.192**2 + 0.327**2), abs=0.01)
    assert wall["dist3"]["s1"] == pytest.approx(
        float(np.linalg.norm(np.array([0.60, 0.80, 0.30]) - PIVOT)), abs=0.01)
    assert (0.30, 0.0, 0.75) in seen  # reach nhận cả y
    assert by_id["T1"].surface == "floor"
    v = verify_obs(obs, "T1", "s1", 1, "ok", [30.0] * 3, [None] * 3, None, history())
    assert v["verify"]["surface"] == "floor" and v["verify"]["y"] == 0.75


# --- repose: sự kiện và lưu pose ---------------------------------------------------------

def test_repose_emits_event_and_saves_pose(site, intrinsics, tmp_path):
    site = {**site, "floor": {"enabled": True, "width": 1.2, "depth": 0.8},
            "vision": {**site["vision"], "auto_repose_camera": True,
                       "commissioning": str(tmp_path / "commissioning.yaml")}}
    cam = look_at([0.60, -0.20, 1.50], [0.60, 0.50, 0.10], intrinsics)
    vision = Vision(site, intrinsics, detector=lambda frame: [])
    vision.commission([make_scene(site, cam)] * 3)
    bumped = look_at([0.63, -0.22, 1.50], [0.60, 0.50, 0.10], intrinsics)
    scene = make_scene(site, bumped)

    o = Orchestrator.__new__(Orchestrator)
    o.vision, o.hub, o.events = vision, SimpleNamespace(frames=lambda n: [scene] * 3), EventLog()
    o.clock, o.pose_time, o.last_health = (lambda: 5.0), {}, None
    o._health_lock = threading.Lock()
    o._health_check()

    events = [e for e in o.events.since(0, 100) if e["kind"] == "vision"]
    assert len(events) == 1 and events[0]["level"] == "warn"
    assert "xê dịch" in events[0]["text"] and "cm" in events[0]["text"]
    assert events[0]["repose"]["moved_m"] > 0.02
    assert (tmp_path / "commissioning.yaml").exists()
    fresh = Vision(site, intrinsics, detector=lambda frame: [])
    fresh.load(tmp_path / "commissioning.yaml")
    assert np.linalg.norm(fresh.camera.position - bumped.position) < 0.01
    assert o.pose_time == {"s1": 5.0, "s2": 5.0}  # node đo lại bằng pose camera mới, không bị hết hạn


# --- dashboard ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rig():
    st = build_sim()
    st.start(orchestrate=False)
    dash = Dashboard(st, "127.0.0.1", 0).start()
    yield st, dash
    dash.stop()
    st.stop()


def test_dashboard_state_has_floor_polygon(rig):
    _, dash = rig
    floor = dash.state()["floor"]
    assert floor["width"] == pytest.approx(1.2) and floor["depth"] == pytest.approx(0.8)
    assert len(floor["polygon"]) >= 4 and all(len(p) == 2 for p in floor["polygon"])


def test_dashboard_draws_floor_outline_and_floor_overlay(rig):
    st, dash = rig
    h, w = 720, 1280
    blank = np.zeros((h, w, 3), np.uint8)
    st.orch.overlay = {}
    base = dash.annotate(blank.copy())
    floor_pts = st.vision.floor_polygon().astype(int)
    inside = [p for p in floor_pts if 0 <= p[0] < w and 0 <= p[1] < h]
    assert inside and any(base[p[1], p[0]].any() for p in inside)
    st.orch.overlay = {"target": {"id": "T1", "x": 0.6, "y": 0.4, "z": 0.0, "surface": "floor"},
                       "aims": {"s1": {"x": 0.6, "y": 0.4, "z": 0.0, "surface": "floor"}}}
    try:
        marked = dash.annotate(blank.copy())
    finally:
        st.orch.overlay = {}
    u, v = project((0.6, 0.4, 0.0), st.vision.camera)
    assert 0 <= u < w and 0 <= v < h
    assert marked[int(v) - 3:int(v) + 4, int(u) - 3:int(u) + 4].any()  # chữ thập đặt đúng chỗ trên nền


def test_dashboard_click_on_floor_places_source_on_floor(rig):
    st, dash = rig
    st.world.clear()
    u, v = project((0.6, 0.3, 0.0), st.vision.camera)
    r = dash.command({"cmd": "fire", "u": float(u), "v": float(v)})
    assert r["ok"] is True and r["source"]["surface"] == "floor"
    assert abs(r["source"]["x"] - 0.6) < 0.03 and abs(r["source"]["y"] - 0.3) < 0.03
    st.world.clear()


# --- aim_point ---------------------------------------------------------------------------

def test_floor_grid_only_reachable_points():
    default = site_with()
    steep = site_with((-70, 45))
    assert ap.floor_grid_points(default, PIVOT, "s1") == []
    pts = ap.floor_grid_points(steep, PIVOT, "s1")
    assert pts and all(0 < x < 1.2 and 0 < y < 0.8 for x, y in pts)
    assert all(aiming.reachable_point(steep, "s1", PIVOT, (x, y, 0.0)) for x, y in pts)
    wide = copy.deepcopy(steep)
    wide["actuator"]["nodes"]["s1"]["pan_limits"] = [-180, 180]
    assert len(ap.floor_grid_points(wide, PIVOT, "s1")) > len(pts)


def test_read_points_floor(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("node,x,y\ns2,0.5,0.7\n", encoding="utf-8")
    assert ap.read_points(f, "s1", "floor") == [("s2", 0.5, 0.0, 0.7)]
    with pytest.raises(ValueError):
        ap.read_points(f, "s1", "wall")  # thiếu cột z
