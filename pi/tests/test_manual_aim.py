import json
from types import SimpleNamespace

import numpy as np
import pytest
from test_integrated_manual import FakeNode

import nt532.manual_aim as manual
from nt532.manual_aim import load_mount, manual_points, plan_target, run_transaction, save_rows
from nt532.net.integrated_manual import IntegratedNode
from nt532.vision.types import TagPose


def test_missing_measurements_and_frame_mismatch_fail_closed(tmp_path):
    cfg = tmp_path / "mount.yaml"
    cfg.write_text("frame: tag_2_table\nnodes:\n  s1: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CALIBRATION_REQUIRED"):
        load_mount(cfg, "s1", None)
    with pytest.raises(ValueError, match="tag_2_table"):
        plan_target(None, "s1", np.array([0.1, 0.2, 0.0]), None, "tag_22_table")


def test_manual_points_require_finite_xy_and_known_optional_node(tmp_path):
    good = tmp_path / "points.csv"
    good.write_text("node,x,y\ns1,0.30,0.20\n,0.40,0.25\ns2,0.50,0.30\n", encoding="utf-8")
    assert manual_points(good, "s1") == [("s1", 0.30, 0.20), ("s1", 0.40, 0.25),
                                          ("s2", 0.50, 0.30)]

    invalid = tmp_path / "invalid.csv"
    invalid.write_text("node,x,y\ns3,0.3,0.2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="s1 or s2"):
        manual_points(invalid, "s1")


def test_save_rows_preserves_each_trial_shape(tmp_path):
    out = tmp_path / "aim.csv"
    rows = [{"node": "s1", "status": "MEASURED", "error_cm": 1.2},
            {"node": "s2", "status": "NOT MEASURABLE", "spot_m": None}]
    save_rows(out, rows)
    assert json.loads(out.with_suffix(".json").read_text(encoding="utf-8")) == rows
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].split(",") == ["node", "status", "error_cm", "spot_m"]
    assert len(lines) == 3


def test_outside_mapping_blocks_target():
    class Vision:
        reference_mode = "auto"
        state = object()
        reference_map = object()

        def blockers(self, node):
            return []

        def in_target_area(self, target):
            return False

    with pytest.raises(ValueError, match="outside"):
        plan_target(Vision(), "s1", np.array([10.0, 10.0, 0.0]), object(),
                    "tag_2_table")


def test_synthetic_measured_mount_produces_bounded_home_pulses(tmp_path):
    cfg = tmp_path / "mount.yaml"
    cfg.write_text("""frame: tag_2_table
nodes:
  s1:
    tag_to_pivot_m: [0, 0, 0.2]
    emitter_offset_m: [0, 0, 0]
    home_yaw_deg: 0
    home_elevation_deg: -45
    pan_sign: 1
    tilt_sign: 1
    pan_gain: 1
    tilt_gain: 1
    pan_limits_deg: [-10, 10]
    tilt_limits_deg: [-10, 10]
    pan_home_us: 1500
    tilt_home_us: 1700
    pan_us_per_deg: 8
    tilt_us_per_deg: -10
""", encoding="utf-8")
    pose = TagPose(21, np.zeros(3), np.eye(3))

    class Vision:
        reference_mode = "auto"
        reference_map = object()

        def __init__(self):
            self.nodes = {"s1": pose}
            self.state = SimpleNamespace(tag_rate={2: 1.0, 21: 1.0})
            self.cfg = {"min_tag_rate": 0.6}

        def blockers(self, node):
            return []

        def in_target_area(self, target):
            return True

    plan = plan_target(Vision(), "s1", np.array([0.0, 0.2, 0.0]), cfg)
    assert plan.pulses_us == (1500, 1700)
    assert np.allclose(plan.predicted_hit_m, [0, 0.2, 0])


def test_transaction_cleans_up_on_interrupt_and_never_sends_pump():
    fake = FakeNode()
    node = IntegratedNode("s1", fake)
    with pytest.raises(KeyboardInterrupt):
        run_transaction(node, (1540, 1680), 1.0, lambda: "OK",
                        lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert not fake.on and not fake.pwm
    assert ("POST", "laser", "OFF") in fake.calls
    assert ("POST", "stop", None) in fake.calls
    assert not any(path in ("fire", "pump", "aim") for _, path, _ in fake.calls)


def test_confirmation_rejection_never_turns_relay_on():
    fake = FakeNode()
    node = IntegratedNode("s1", fake)
    with pytest.raises(ValueError, match="OK"):
        run_transaction(node, (1540, 1680), 1.0, lambda: "no", lambda: None)
    assert ("POST", "laser", "ON") not in fake.calls
    assert ("POST", "stop", None) in fake.calls


def test_transaction_identity_mismatch_sends_no_post():
    fake = FakeNode(name="B")
    node = IntegratedNode("s1", fake)
    with pytest.raises(ValueError, match="identity"):
        run_transaction(node, (1540, 1680), 1.0, lambda: "OK", lambda: None)
    assert fake.calls == [("GET", "info", None)]


def test_hold_over_120_seconds_is_rejected_before_network():
    fake = FakeNode()
    node = IntegratedNode("s1", fake)
    with pytest.raises(ValueError, match="hold-seconds"):
        run_transaction(node, (1500, 1700), 121, lambda: "OK", lambda: None)
    assert fake.calls == []


def test_real_default_dry_run_never_opens_coap(monkeypatch, tmp_path):
    class Camera:
        def release(self):
            pass

    class Vision:
        reference_map = SimpleNamespace(frames_used=15, metric_error_m=0.01,
                                        reprojection_px=1.0, ambiguity_m=2.0)

        def __init__(self, *_):
            pass

        def commission(self, frames):
            assert len(frames) == 15

    monkeypatch.setattr(manual, "open_camera", lambda *_: Camera())
    monkeypatch.setattr(manual, "read_frames", lambda _, n: [None] * n)
    monkeypatch.setattr(manual, "load_intrinsics", lambda _: object())
    monkeypatch.setattr(manual, "Vision", Vision)
    monkeypatch.setattr(manual, "plan_target", lambda *_: manual.AimPlan(
        "s1", 21, (0.3, 0.2, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.1),
        (1.0, 2.0), (1540, 1680), (0.3, 0.2, 0.0)))
    monkeypatch.setattr(manual, "CoapTransport", lambda *_: pytest.fail("CoAP opened in dry run"))
    calibration = tmp_path / "measured.yaml"
    calibration.write_text("measured test fixture", encoding="utf-8")
    args = SimpleNamespace(surface="floor", frame="tag_2_table", x=0.3, y=0.2, z=None,
                           grid=False, points=None, hold_seconds=30, execute=False,
                           source="camera.mp4", node="s1", intrinsics=None,
                           calibration=calibration)
    row = manual.run_manual(args, {"camera": {"intrinsics": "calibration/camera.yaml"},
                                   "vision": {"reference_mode": "fixed"}})
    assert row["status"] == "DRY RUN"
