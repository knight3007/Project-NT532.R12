"""Profile vision-aim-only: node giả A/B, Vision thật (Dynamic Mapping tag_2_table trên ảnh dựng).

Không có lưu lượng phần cứng. Kiểm trình tự khởi động, WAIT_OK, cổng READY, camera -> pan/tilt,
không bơm, chốt STOP, lỗi camera/Thread/mapping/pose/servo/danh tính và phục hồi tường minh.
"""

import copy
import importlib.util
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from test_dynamic_mapping import LAYOUTS, camera, rendered_scene
from test_integrated_manual import FakeNode

from nt532.aim_only import (
    CALIBRATION_REQUIRED,
    READY,
    STOP_LATCHED,
    WAIT_OK,
    AimOnlyConfig,
    AimOnlyStation,
    NoPumpRequest,
)
from nt532.manual_aim import plan_target
from nt532.net.integrated_manual import IntegratedNode
from nt532.orchestrator.events import EventLog
from nt532.vision import Vision
from nt532.vision.types import Detection, Intrinsics

MOUNT = """    tag_to_pivot_m: [0, 0, 0.2]
    emitter_offset_m: [0, 0, 0]
    home_yaw_deg: 0
    home_elevation_deg: -45
    pan_sign: 1
    tilt_sign: 1
    pan_gain: 1
    tilt_gain: 1
    pan_limits_deg: [-80, 80]
    tilt_limits_deg: [-40, 40]
    pan_home_us: 1500
    tilt_home_us: 1700
    pan_us_per_deg: 8
    tilt_us_per_deg: -10
"""
SITE = {
    "board": {"plane_y": 0.80, "width": 1.20, "height": 0.60},
    "tags": {"reference": {"size": 0.08, "positions": {i: None for i in range(4)}},
             "nodes": {"size": 0.08, "ids": {"s1": 21, "s2": 22}}},
    "vision": {"reference_mode": "auto", "tabletop_anchor": "center", "board_margin_m": 0.02,
               "min_tag_rate": 0.6, "camera_shift_px": 3.0, "node_moved_m": 0.015,
               "laser_min_rise": 40},
    "targeting": {"sensor_match_radius_x": 0.35},
}
INTRINSICS = Intrinsics(np.array([[1050., 0, 640.], [0, 1030., 360.], [0, 0, 1.]]),
                        np.array([0.01, -0.005, 0.001, 0.0, 0.0]), (1280, 720))
CAM = camera(INTRINSICS)


@pytest.fixture(scope="module")
def scenes():
    bgr = cv2.COLOR_GRAY2BGR  # camera thật trả ảnh BGR
    return {"ok": cv2.cvtColor(rendered_scene(LAYOUTS["60x40"], CAM), bgr),
            "no_anchor": cv2.cvtColor(rendered_scene(LAYOUTS["60x40"], CAM, anchor=False), bgr)}


def pixel(x, y):
    uv, _ = cv2.projectPoints(np.array([[x, y, 0.]]), cv2.Rodrigues(CAM.R)[0], CAM.t,
                              INTRINSICS.K, INTRINSICS.dist)
    return uv.reshape(2)


class Frames:
    def __init__(self, image):
        self.image, self.fail = image, False

    def frame(self):
        if self.fail:
            raise RuntimeError("không đọc được khung hình")
        return self.image

    def batch(self, n):
        return [self.frame() for _ in range(n)]


class Rig:
    """Một trạm aim-only với hai node giả; `log` ghi mọi request theo thứ tự toàn cục."""

    def __init__(self, tmp_path, scenes, calibration=None, addresses=None, swap_b=False,
                 **cfg):
        self.det = []
        self.vision = Vision(copy.deepcopy(SITE), INTRINSICS, detector=lambda frame: list(self.det))
        self.frames = Frames(scenes["ok"])
        self.fakes = {"s1": FakeNode("A", "a-integrated-0.1.2"),
                      "s2": FakeNode("A", "a-integrated-0.1.2") if swap_b
                      else FakeNode("B", "b-integrated-0.2.5")}
        self.log, self.hb_fail, self.frozen = [], set(), set()
        cal = tmp_path / "mount.yaml"
        cal.write_text(calibration or f"frame: tag_2_table\nnodes:\n  s1:\n{MOUNT}  s2:\n{MOUNT}",
                       encoding="utf-8")
        self.cfg = AimOnlyConfig(calibration=cal, **{"ok_timeout_s": 5.0, **cfg})
        addresses = {"s1": "fd00::a", "s2": "fd00::b"} if addresses is None else addresses
        self.events = EventLog(path=tmp_path / "aim-only.jsonl")
        self.st = AimOnlyStation(SITE, self.cfg, self.vision, self.frames, addresses,
                                 transport_factory=self.transport, events=self.events)

    def transport(self, address):
        name = {"fd00::a": "s1", "fd00::b": "s2"}[address]
        fake = self.fakes[name]

        def request(method, path, payload=None):
            self.log.append((name, method, path, payload))
            if path == "hb" and name in self.hb_fail:
                raise TimeoutError("Thread lost")
            if (name in self.frozen and method == "POST" and path == "pantilt"
                    and payload not in ("ARM", "HOME")):
                return {}
            return fake(method, path, payload)
        return request

    def calls(self, name=None):
        return [c for c in self.log if name is None or c[0] == name]

    def startup_thread(self):
        thread = threading.Thread(target=self.st.startup, daemon=True)
        thread.start()
        return thread

    def ready(self):
        thread = self.startup_thread()
        for name in self.cfg.nodes:
            wait_for(lambda name=name: self.st.wait_node == name)
            assert self.st.command(f"OK {name}") == "queued OK"
        thread.join(5)
        assert self.st.state == READY, self.st.reason

    def see(self, x, y):
        u, v = pixel(x, y)
        self.det = [Detection(float(u), float(v), 0, 0, 0.9)]

    def target_posts(self):
        return [c for c in self.log if c[1:3] == ("POST", "pantilt")
                and c[3] not in ("ARM", "HOME")]


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timeout")


def assert_safe(rig, names=("s1", "s2")):
    assert rig.st.latched.is_set()
    for name in names:
        fake = rig.fakes[name]
        assert not fake.on and not fake.pwm, name
        calls = [c[1:] for c in rig.calls(name)]
        off = len(calls) - 1 - calls[::-1].index(("POST", "laser", "OFF"))
        assert ("POST", "stop", None) in calls[off:]  # OFF trước, rồi STOP


def assert_no_pump(rig):
    assert not any(c[2] in ("aim", "fire", "pump") for c in rig.log)


def test_startup_order_wait_ok_and_light_off(tmp_path, scenes):
    rig = Rig(tmp_path, scenes)
    rig.st.command("OK s1")  # gõ trước khi đèn bật: không được tính
    thread = rig.startup_thread()
    wait_for(lambda: rig.st.wait_node == "s1")
    assert rig.st.command("OK s1", source="http").startswith("rejected")
    rig.st.command("OK")
    rig.st.command("OK s2")
    time.sleep(0.5)
    assert rig.st.state == WAIT_OK and rig.st.wait_node == "s1" and rig.fakes["s1"].on
    rig.st.command("OK s1")
    wait_for(lambda: rig.st.wait_node == "s2")
    assert not rig.fakes["s1"].on and rig.fakes["s2"].on
    rig.st.command("OK s2")
    thread.join(5)
    assert rig.st.state == READY and not rig.fakes["s2"].on
    a = [c[1:] for c in rig.calls("s1")]
    order = [a.index(("GET", "info", None)), a.index(("POST", "hb", None)),
             a.index(("POST", "pantilt", "ARM")), a.index(("POST", "pantilt", "HOME")),
             a.index(("POST", "laser", "ON")), a.index(("POST", "laser", "OFF"))]
    assert order == sorted(order)
    lit = set()  # không bao giờ hai đèn cùng sáng; mỗi node bật đúng một lần
    for name, method, path, payload in rig.log:
        if (method, path) == ("POST", "laser"):
            (lit.add if payload == "ON" else lit.discard)(name)
            assert len(lit) <= 1
    assert sum(c[1:] == ("POST", "laser", "ON") for c in rig.log) == 2
    infos = [i for i, c in enumerate(rig.log) if c[2] == "info"]
    first_motion = min(i for i, c in enumerate(rig.log) if c[2] in ("hb", "pantilt", "laser"))
    assert max(infos) < first_motion  # cả hai danh tính đã kiểm trước mọi heartbeat/ARM
    assert_no_pump(rig)
    rig.st.close()


def test_ok_timeout_turns_light_off_and_stops(tmp_path, scenes):
    rig = Rig(tmp_path, scenes, ok_timeout_s=0.4)
    assert rig.st.startup() is False
    assert rig.st.state == STOP_LATCHED and "OK" in rig.st.reason
    assert_safe(rig)
    assert ("POST", "laser", "ON") not in [c[1:] for c in rig.calls("s2")]


def test_ready_gate_vision_target_and_no_reaim(tmp_path, scenes):
    rig = Rig(tmp_path, scenes)
    rig.see(0.30, 0.40)
    rig.st.tick()  # chưa READY: không làm gì
    assert rig.log == []
    rig.ready()
    posts = len(rig.target_posts())
    rig.st.tick()
    rig.st.tick()
    assert len(rig.target_posts()) == posts  # chưa đủ khung ổn định
    rig.st.tick()
    row = rig.st.results[-1]
    assert row["result"] == "REACHED" and row["frame"] == "tag_2_table" and row["node"] == "s1"
    assert np.hypot(row["target_m"][0] - 0.30, row["target_m"][1] - 0.40) < 0.01
    assert row["target_m"][2] == 0.0
    plan = plan_target(rig.vision, "s1", np.array(row["target_m"]), rig.cfg.calibration)
    assert tuple(row["pulses_us"]) == plan.pulses_us
    assert rig.target_posts()[-1] == ("s1", "POST", "pantilt", "{},{}".format(*plan.pulses_us))
    assert (rig.fakes["s1"].pan, rig.fakes["s1"].tilt) == plan.pulses_us
    posts = len(rig.target_posts())
    for _ in range(9):
        rig.st.tick()
    assert len(rig.target_posts()) == posts  # bia đứng yên: không ngắm lại
    rig.see(0.52, 0.36)  # bia mới, gần s2 hơn
    for _ in range(3):
        rig.st.tick()
    assert rig.st.results[-1]["node"] == "s2" and len(rig.target_posts()) == posts + 1
    for i in range(6):  # bia nhảy liên tục: không đủ ổn định, không ngắm
        rig.see(0.10 + 0.08 * i, 0.10)
        rig.st.tick()
    assert len(rig.target_posts()) == posts + 1
    assert rig.st.state == READY
    assert_no_pump(rig)
    rig.st.close()
    assert_safe(rig)


def test_target_outside_mapping_is_rejected_without_motion(tmp_path, scenes):
    rig = Rig(tmp_path, scenes)
    rig.ready()
    posts = len(rig.target_posts())
    rig.det = [Detection(5.0, 5.0, 0, 0, 0.9)]  # ngoài vùng bàn / sau camera
    for _ in range(4):
        rig.st.tick()
    assert len(rig.target_posts()) == posts and rig.st.state == READY
    rig.st.close()


def test_no_pump_guarantee():
    calls = []
    guard = NoPumpRequest(lambda *a: calls.append(a) or {})
    for method, path, payload in (("POST", "fire", "pump"), ("POST", "aim", "1"),
                                  ("POST", "pump", "ON"), ("POST", "laser", "PUMP")):
        with pytest.raises(PermissionError):
            guard(method, path, payload)
    assert calls == []
    with pytest.raises(ValueError, match="pump"):
        IntegratedNode("s1", guard).fire("pump")
    source = (Path(__file__).parents[1] / "src/nt532/aim_only.py").read_text(encoding="utf-8")
    assert "selfcal" not in source.split('"""', 2)[2] and "Orchestrator(" not in source


def test_stop_latch_rejects_targets_and_requires_explicit_recover(tmp_path, scenes):
    rig = Rig(tmp_path, scenes)
    exit_event = threading.Event()
    loop = threading.Thread(target=rig.st.run, args=(exit_event, 0.02), daemon=True)
    loop.start()
    for name in ("s1", "s2"):
        wait_for(lambda name=name: rig.st.wait_node == name)
        rig.st.command(f"OK {name}")
    wait_for(lambda: rig.st.state == READY)
    assert rig.st.command("STOP", source="http") == "STOP latched"
    assert_safe(rig)
    arms = sum(c[3] == "ARM" for c in rig.log)
    after_stop = len(rig.log)
    posts = len(rig.target_posts())
    rig.see(0.30, 0.40)
    rig.st.command("VERIFY")
    rig.st.command("OK s1")
    time.sleep(0.6)
    assert rig.st.state == STOP_LATCHED and rig.st.latched.is_set()
    assert len(rig.target_posts()) == posts and sum(c[3] == "ARM" for c in rig.log) == arms
    assert not any(c[2] in ("laser", "pantilt") and c[1] == "POST" for c in rig.log[after_stop:])
    assert rig.st.command("RECOVER", source="http").startswith("rejected")
    time.sleep(0.2)
    assert rig.st.latched.is_set()
    rig.st.command("RECOVER")
    wait_for(lambda: rig.st.wait_node == "s1")  # khởi động lại toàn bộ, lại phải WAIT_OK
    assert sum(c[3] == "ARM" for c in rig.log) > arms
    rig.st.command("OK s1")
    wait_for(lambda: rig.st.wait_node == "s2")
    rig.st.command("OK s2")
    wait_for(lambda: rig.st.state == READY)
    exit_event.set()
    loop.join(3)
    rig.st.close()
    assert_safe(rig)
    assert_no_pump(rig)


def _camera(rig, scenes):
    rig.frames.fail = True


def _thread(rig, scenes):
    rig.hb_fail.add("s1")
    time.sleep(0.6)


def _mapping(rig, scenes):
    rig.frames.image = scenes["no_anchor"]
    rig.st.cfg.remap_s = 0.0


def _stale(rig, scenes):
    rig.vision.check = lambda frames: None
    rig.st.cfg.remap_s = 0.0
    rig.vision.state.time -= 120


def _servo(rig, scenes):
    rig.frozen.add("s1")
    rig.see(0.30, 0.40)
    rig.st.tick()
    rig.st.tick()


@pytest.mark.parametrize("fault,reason", [(_camera, "camera disconnect"),
                                          (_thread, "heartbeat lost"),
                                          (_mapping, "invalid mapping"),
                                          (_stale, "stale pose"),
                                          (_servo, "did not reach")])
def test_runtime_faults_latch_off_and_stop(tmp_path, scenes, fault, reason):
    rig = Rig(tmp_path, scenes, servo_timeout_s=0.3)
    rig.ready()
    fault(rig, scenes)
    rig.st.tick()
    assert rig.st.state == STOP_LATCHED and reason in rig.st.reason, rig.st.reason
    assert_safe(rig)
    posts = len(rig.target_posts())
    rig.frames.fail, rig.frames.image = False, scenes["ok"]
    rig.see(0.45, 0.20)
    for _ in range(4):
        rig.st.tick()
    assert len(rig.target_posts()) == posts and rig.st.latched.is_set()


def test_missing_node_address_blocks_before_any_traffic(tmp_path, scenes):
    rig = Rig(tmp_path, scenes, addresses={"s1": "fd00::a"})
    assert rig.st.startup() is False
    assert "s2" in rig.st.reason and rig.log == []


def test_wrong_identity_blocks_all_motion(tmp_path, scenes):
    rig = Rig(tmp_path, scenes, swap_b=True)
    assert rig.st.startup() is False and "identity" in rig.st.reason
    assert [c[2] for c in rig.calls("s2")] == ["info"]
    assert not any(c[3] in ("ARM", "HOME", "ON") for c in rig.log)


def test_unmeasured_calibration_is_calibration_required(tmp_path, scenes):
    template = Path(__file__).parents[2] / "calibration" / "manual_aim_measured.example.yaml"
    rig = Rig(tmp_path, scenes, calibration=template.read_text(encoding="utf-8"))
    assert rig.st.startup() is False
    assert rig.st.state == CALIBRATION_REQUIRED and "CALIBRATION_REQUIRED" in rig.st.reason
    assert rig.log == []


def test_invalid_mapping_at_startup_blocks_before_nodes(tmp_path, scenes):
    rig = Rig(tmp_path, scenes)
    rig.frames.image = scenes["no_anchor"]
    assert rig.st.startup() is False and "CHECK_CAMERA" in rig.st.reason
    assert rig.log == []


def test_verify_light_is_bounded_and_not_measurable_without_spot(tmp_path, scenes):
    rig = Rig(tmp_path, scenes, verify_light_s=0.4)
    rig.ready()
    rig.st.command("VERIFY")
    rig.st.tick()  # chưa có bia đã ngắm: bỏ qua
    assert not any(r.get("result") == "NOT MEASURABLE" for r in rig.st.results)
    rig.see(0.30, 0.40)
    for _ in range(3):
        rig.st.tick()
    ons = sum(c[1:] == ("POST", "laser", "ON") for c in rig.log)
    rig.st.command("VERIFY")
    started = time.monotonic()
    rig.st.tick()
    assert time.monotonic() - started < 2.0
    assert sum(c[1:] == ("POST", "laser", "ON") for c in rig.log) == ons + 1
    lasers = [c[1:] for c in rig.calls("s1") if c[1:3] == ("POST", "laser")]
    assert lasers[-2:] == [("POST", "laser", "ON"), ("POST", "laser", "OFF")]
    assert not rig.fakes["s1"].on
    row = rig.st.results[-1]
    assert row["result"] == "NOT MEASURABLE" and row["error_cm"] is None and "spot_error" not in row
    assert rig.st.state == READY
    rig.st.close()
    assert_safe(rig)


def test_process_shutdown_cleans_up_from_wait_ok(tmp_path, scenes):
    rig = Rig(tmp_path, scenes)
    thread = rig.startup_thread()
    wait_for(lambda: rig.st.wait_node == "s1")
    rig.st.close()  # Ctrl+C trong lúc đèn đang sáng
    thread.join(5)
    assert rig.st.state == STOP_LATCHED and rig.st.reason == "process shutdown"
    assert_safe(rig)


def test_config_bounds():
    with pytest.raises(ValueError):
        AimOnlyConfig(calibration=Path("x"), verify_light_s=20)
    with pytest.raises(ValueError):
        AimOnlyConfig(calibration=Path("x"), nodes=("s3",))
    with pytest.raises(ValueError):
        AimOnlyConfig(calibration=Path("x"), batch_frames=5)


def _run_station(monkeypatch, *argv):
    script = Path(__file__).parents[1] / "scripts" / "run_station.py"
    spec = importlib.util.spec_from_file_location("run_station_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", ["run_station.py", *argv])
    with pytest.raises(SystemExit) as exc:
        module.main()
    return exc.value.code


@pytest.mark.parametrize("argv", [("--source", "sim"), (), ("--source", "clip.mp4"),
                                  ("--source", "0", "--mqtt"),
                                  ("--source", "0", "--decider", "hybrid")])
def test_profile_flag_requires_live_camera_and_rejects_legacy_options(monkeypatch, argv):
    assert _run_station(monkeypatch, "--profile", "vision-aim-only", *argv) == 2


def test_http_exposes_health_and_stop_only(tmp_path, scenes):
    import json
    import urllib.error
    import urllib.request

    from nt532.aim_only import serve_health

    rig = Rig(tmp_path, scenes)
    rig.ready()
    server = serve_health(rig.st, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        health = json.loads(urllib.request.urlopen(f"{base}/api/health", timeout=5).read())
        assert health["state"] == READY and health["pump"] == "DISABLED"
        assert health["frame"] == "tag_2_table" and health["mapping"]["frames_used"] >= 9
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(urllib.request.Request(f"{base}/api/ok", b"", method="POST"),
                                   timeout=5)
        assert denied.value.code == 403
        urllib.request.urlopen(urllib.request.Request(f"{base}/api/stop", b"", method="POST"),
                               timeout=5)
        assert rig.st.state == STOP_LATCHED
        assert_safe(rig)
    finally:
        server.shutdown()
        rig.st.close()
