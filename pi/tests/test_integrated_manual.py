"""Contract tests for the real integrated-node manual path; no hardware traffic."""

import pytest

from nt532.net.integrated_manual import IntegratedNode, PulseCalibration


class FakeNode:
    def __init__(self, name="A", fw="a-integrated-0.1.2"):
        self.name, self.fw = name, fw
        self.calls = []
        self.pan = self.tilt = None
        self.armed = self.pwm = self.on = False

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "info":
            return {"n": self.name, "fw": self.fw, "mode": "integrated",
                    "outputs": "servo_gated;relay_led_simulation;laser_disabled"}
        if path == "hb":
            return {}
        if path == "pantilt" and method == "POST":
            if payload == "ARM":
                self.armed = True
            elif payload == "HOME":
                assert self.armed
                self.pan, self.tilt, self.pwm = 1500, 1700, True
            else:
                assert self.pwm
                self.pan, self.tilt = map(int, payload.split(","))
            return {}
        if path == "pantilt":
            return {"armed": self.armed, "pwm_active": self.pwm, "pan_us": self.pan,
                    "tilt_us": self.tilt, "target_pan_us": self.pan or 1500,
                    "target_tilt_us": self.tilt or 1700, "pan_min_us": 500,
                    "pan_max_us": 2500, "tilt_min_us": 600, "tilt_max_us": 2500}
        if path == "laser" and method == "POST":
            self.on = payload == "ON"
            return {}
        if path == "laser":
            return {"available": True, "mode": "relay_led_simulation", "on": self.on}
        if path == "stop":
            self.armed = self.pwm = self.on = False
            return {}
        if path == "status":
            return {"n": self.name, "stopped": not self.pwm, "relay_on": self.on,
                    "servo_pwm": self.pwm, "laser_on": False}
        raise AssertionError((method, path, payload))


def test_identity_and_home_before_target():
    fake = FakeNode()
    node = IntegratedNode("s1", fake)
    try:
        node.verify_identity()
        node.arm_home()
        actual = node.move(1540, 1680)
        assert (actual["pan_us"], actual["tilt_us"]) == (1540, 1680)
        assert ("POST", "pantilt", "HOME") in fake.calls
        assert ("POST", "pantilt", "1540,1680") in fake.calls
        assert not any(path in ("aim", "fire", "pump") for _, path, _ in fake.calls)
    finally:
        node.shutdown()
    assert fake.calls[-4:-2] == [("POST", "laser", "OFF"), ("POST", "stop", None)]
    assert not fake.on and not fake.pwm


def test_wrong_identity_blocks_all_motion():
    fake = FakeNode(name="B")
    node = IntegratedNode("s1", fake)
    with pytest.raises(ValueError, match="identity"):
        node.arm_home()
    assert all(path == "info" for _, path, _ in fake.calls)


def test_pump_rejected_and_out_of_bounds_rejected():
    fake = FakeNode()
    node = IntegratedNode("s1", fake)
    with pytest.raises(ValueError, match="pump"):
        node.fire("pump")
    with pytest.raises(ValueError, match="pulse"):
        node.move(2600, 1700)
    assert not fake.calls


def test_pulse_calibration_is_measured_and_axis_specific():
    cal = PulseCalibration(1500, 1700, 8.0, -10.0)
    assert cal.pulses(5.0, 2.0) == (1540, 1680)
    with pytest.raises(ValueError):
        PulseCalibration(1500, 1700, 0.0, 10.0)


def test_node_b_identity_and_external_stop_aborts_hold():
    fake = FakeNode(name="B", fw="b-integrated-0.2.5")
    node = IntegratedNode("s2", fake)
    try:
        node.arm_home()
        node.move(1480, 1740)
        node.laser_on()
        fake("POST", "stop")
        with pytest.raises(RuntimeError, match="STOP"):
            node.hold(0.1)
    finally:
        node.shutdown()
    assert not fake.on


def test_post_ack_without_current_position_times_out():
    fake = FakeNode()
    node = IntegratedNode("s1", fake, timeout_s=0.12)
    try:
        node.arm_home()
        original = fake.__call__

        def no_motion(method, path, payload=None):
            if method == "POST" and path == "pantilt" and payload not in ("ARM", "HOME"):
                fake.calls.append((method, path, payload))
                return {}
            return original(method, path, payload)

        node.request = no_motion
        with pytest.raises(TimeoutError, match="did not reach"):
            node.move(1540, 1680)
    finally:
        node.shutdown()


def test_relay_on_requires_reached_target_after_home():
    fake = FakeNode()
    node = IntegratedNode("s1", fake)
    try:
        node.arm_home()
        with pytest.raises(RuntimeError, match="reached target"):
            node.laser_on()
        assert ("POST", "laser", "ON") not in fake.calls

        node.move(1500, 1700)
        node.laser_on()
        assert fake.on
    finally:
        node.shutdown()
