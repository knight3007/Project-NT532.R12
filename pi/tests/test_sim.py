import time

import cv2
import numpy as np
import pytest

from nt532.net.fakenode import SimWorldHardware
from nt532.net.protocol import Aim, Fire
from nt532.sim import Scene, SimCamera, aim_angles, sim_site
from nt532.sim.loop import correct
from nt532.sim.world import SimLink
from nt532.vision import Detection, Vision, project, read_frames, read_fresh

TARGET = np.array([0.50, 0.80, 0.30])


@pytest.fixture(scope="module")
def cards_dir(tmp_path_factory):
    """Ảnh thẻ giả (nhiễu màu) để test không phụ thuộc data/sim/cards."""
    path = tmp_path_factory.mktemp("cards")
    rng = np.random.default_rng(0)
    for i in range(3):
        cv2.imwrite(str(path / f"card_{i}.png"), rng.integers(0, 255, (64, 64, 3), np.uint8))
    return path


@pytest.fixture
def scene(cards_dir) -> Scene:
    return Scene(cards_dir=cards_dir)


@pytest.fixture
def cap(scene) -> SimCamera:
    return SimCamera(scene)


@pytest.fixture(scope="module")
def commissioned(cards_dir):
    """Kết quả commissioning của cảnh mặc định, đo một lần (cảnh mặc định luôn giống nhau)."""
    scene = Scene(cards_dir=cards_dir)
    v = Vision(scene.site, scene.intrinsics, detector=lambda frame: [])
    v.commission(read_frames(SimCamera(scene), 15))
    return v.state


@pytest.fixture
def vision(scene, commissioned) -> Vision:
    v = Vision(scene.site, scene.intrinsics, detector=lambda frame: [])
    v.state = commissioned
    return v


def test_everything_is_in_frame_and_frames_differ(scene, cap):
    assert scene.frame_check(margin=20)
    a, b = read_fresh(cap), read_fresh(cap)
    assert a.shape == (720, 1280, 3) and a.dtype == np.uint8
    assert np.any(a != b)
    assert cap.isOpened() and cap.get(cv2.CAP_PROP_FRAME_WIDTH) == 1280
    cap.release()
    assert not cap.isOpened() and cap.read()[0] is False


def test_commission_matches_ground_truth(commissioned, scene):
    state = commissioned
    assert np.linalg.norm(state.camera.position - scene.camera.position) < 0.01
    assert state.missing == [] and state.reprojection_px < 1.0
    for name, node in scene.nodes.items():
        assert np.linalg.norm(state.nodes[name].position - node.position) < 0.01


def test_check_flags_moved_node_and_camera(vision, scene, cap):
    assert vision.check(read_frames(cap, 5)).stale == []
    scene.move_node("s1", delta=[0.05, 0.0, 0.0])
    assert vision.check(read_frames(cap, 5)).stale == ["s1"]
    assert vision.blockers("s1") and vision.blockers("s2") == []

    scene.move_node("s1", delta=[-0.05, 0.0, 0.0])
    scene.shift_camera(delta=[0.02, 0.0, 0.0])
    health = vision.check(read_frames(cap, 5))
    assert "camera" in health.stale and health.camera_shift_px > 3.0


def test_laser_aim_and_find_spot(vision, scene, cap):
    for name in scene.nodes:
        scene.aim(name, *aim_angles(scene.nodes[name].pivot, TARGET))
        np.testing.assert_allclose(scene.spot(name), TARGET, atol=1e-9)
        scene.set_laser(name, False)
        off = read_fresh(cap)
        scene.set_laser(name, True)
        on = read_fresh(cap)
        scene.set_laser(name, False)
        spot = vision.find_spot(on, off)
        assert spot is not None and np.linalg.norm(spot - scene.spot(name)) < 0.01


def test_large_fire_is_bigger_stronger_and_reaches_far_node():
    from nt532.sim.scene import CARD_SIZE
    from nt532.sim.world import SimWorld

    w = SimWorld(seed=3)
    hp = {}
    for size in ("small", "large"):
        hp[size] = [w.ignite(0.30, 0.30, size=size).hp for _ in range(30)]
        w.clear()
    assert 0.8 <= min(hp["small"]) and max(hp["small"]) <= 2.6
    assert 1.6 <= min(hp["large"]) and 2.6 < max(hp["large"]) <= 5.2  # nhiều nước hơn để tắt
    with pytest.raises(ValueError):
        w.ignite(size="huge")

    def far_temp(size):  # s2 ở X 0,90, lửa ở X 0,30: xa 0,6 m
        w.clear()
        s = w.ignite(0.30, 0.30, size=size)
        s.level = 1.0
        return s, float(np.mean([w.reading("s2")["temp"] for _ in range(30)]))

    small, t_small = far_temp("small")
    assert small.size == "small" and small.to_json()["size"] == "small"
    assert w.scene.cards[small.card].size == CARD_SIZE
    large, t_large = far_temp("large")
    assert large.to_json()["size"] == "large" and large.decay > small.decay
    assert w.scene.cards[large.card].size > 1.5 * CARD_SIZE
    assert t_large - 27.0 > 2.5 * (t_small - 27.0)  # node xa thấy rõ hơn hẳn

    from nt532.sim.world import OracleDetector

    det = OracleDetector(w, seed=2)
    box = [d.w for _ in range(20) for d in det(w.read())]  # thẻ lớn: hộp YOLO giả lớn hơn
    assert box
    w.clear()
    w.ignite(0.30, 0.30)
    box_small = [d.w for _ in range(20) for d in det(w.read())]
    assert box_small and np.mean(box) > 1.5 * np.mean(box_small)


def test_aim_convention():
    pivot = np.array([0.5, 0.55, 0.3])
    assert aim_angles(pivot, [0.5, 0.8, 0.3]) == (0.0, 0.0)
    pan, tilt = aim_angles(pivot, [0.3, 0.8, 0.5])
    assert pan > 0 and tilt > 0  # pan dương về phía -X, tilt dương ngẩng lên


def test_laser_outside_board_leaves_no_spot(scene, cap):
    scene.aim("s1", 80.0, 0.0)
    scene.set_laser("s1", True)
    assert scene.spot("s1") is None
    on = read_fresh(cap)
    scene.set_laser("s1", False)
    assert np.abs(on.astype(int) - read_fresh(cap).astype(int)).max() < 40


@pytest.mark.parametrize("node", ["s1", "s2"])
def test_correct_loop_removes_servo_bias(vision, scene, cap, node):
    scene.set_servo_bias(node, 2.0, -2.0)
    result = correct(cap, vision, node, TARGET, max_iters=3, done_m=0.005)
    assert not result.lost and result.rounds <= 3
    assert result.errors_m[0] > 0.005  # sai lệch servo 2 độ lúc đầu thấy được
    assert np.linalg.norm((scene.spot(node) - TARGET)[[0, 2]]) < 0.01


def test_targets_localize_cards(vision, scene, cap):
    ids = [scene.add_card(0.25, 0.20), scene.add_card(0.95, 0.40), scene.add_card(0.60, 0.50)]
    x0, y0, _, _ = vision.board_roi((720, 1280, 3))

    def fake(frame):  # hộp tại vị trí chiếu của thẻ thật, tính trên ảnh đã cắt vùng bảng
        out = []
        for i in ids:
            u, v = project(scene.card_position(i), scene.camera)
            out.append(Detection(u - x0, v - y0, 40, 40, 0.9))
        return out

    vision._detector = fake
    found = vision.targets(read_fresh(cap))
    assert len(found) == 3
    for t in found:
        truth = min(
            (scene.card_position(i) for i in ids), key=lambda p: np.linalg.norm(p - t.position)
        )
        assert np.linalg.norm((t.position - truth)[[0, 2]]) < 0.01
    near_s1 = vision.targets(read_fresh(cap), sensor="s1")  # s1 ở x = 0,30: chỉ giữ bia x <= 0,65
    assert sorted(round(t.position[0], 2) for t in near_s1) == [0.25, 0.60]


def test_missing_cards_error_is_clear(tmp_path):
    scene = Scene(cards_dir=tmp_path)
    with pytest.raises(FileNotFoundError, match="make_target_cards"):
        scene.add_card(0.3, 0.3)


# --- thời gian phun: ghi theo thời gian thật đã trôi, không phải 0.1 s cho mỗi vòng lặp ----------------

class StallWorld:
    """Thế giới giả đủ cho SimLink/SimWorldHardware: ghi số giây của mỗi lần `spray`; lần đầu bị kẹt
    0.3 s như lúc `world.lock` bận dựng khung hình."""

    def __init__(self, stall_s: float = 0.3) -> None:
        self.site, self.stall_s = sim_site(write=False), stall_s
        self.nodes = ["s1", "s2"]
        self.online = dict.fromkeys(self.nodes, True)
        self.pumping = dict.fromkeys(self.nodes, False)
        self.credits: list[float] = []

    def aim(self, node, pan, tilt) -> None:
        pass

    def laser(self, node, on) -> None:
        pass

    def spray(self, node, seconds) -> None:
        self.credits.append(seconds)
        if len(self.credits) == 1:
            time.sleep(self.stall_s)


def test_simlink_pump_credits_real_elapsed_time_but_not_beyond_command():
    world = StallWorld()
    link = SimLink(world, latency_s=0.0)
    link._handle("s1", "aim", Aim(1, 0.0, 0.0, 1500))
    link._handle("s1", "fire", Fire(1, "pump", 1000))  # chạy tới hết 1 s rồi mới trả về
    assert link.wait(1, ("done",), 0.1) is not None
    assert not world.pumping["s1"]
    total = sum(world.credits)
    assert total <= 1.0 + 1e-6  # không quá thời gian của lệnh
    assert total == pytest.approx(1.0, abs=0.2)  # vòng kẹt 0.3 s vẫn được tính đủ (cũ: ~0,6)
    assert max(world.credits) <= 0.5 + 1e-6


def test_simlink_pump_credit_is_capped_per_iteration():
    world = StallWorld(stall_s=0.8)  # kẹt lâu hơn mức trần: phần thừa không được cộng bù
    link = SimLink(world, latency_s=0.0)
    link._handle("s1", "aim", Aim(1, 0.0, 0.0, 1500))
    link._handle("s1", "fire", Fire(1, "pump", 1500))
    assert max(world.credits) <= 0.5 + 1e-6
    assert sum(world.credits) <= 1.5 + 1e-6


def test_sim_world_hardware_credits_real_elapsed_time_until_pump_off():
    world = StallWorld()
    hw = SimWorldHardware(world, "s1")
    try:
        t0 = time.monotonic()
        hw.set_dev("pump", True)
        time.sleep(1.0)
        hw.set_dev("pump", False)
        ran = time.monotonic() - t0
        time.sleep(0.15)
        n = len(world.credits)
        time.sleep(0.4)
        assert len(world.credits) == n  # bơm đã tắt thì không ghi thêm
    finally:
        hw.close()
    total = sum(world.credits)
    assert total <= ran + 0.05
    assert total >= 0.8  # vòng kẹt 0.3 s vẫn được tính đủ (cũ: ~0,6)
    assert max(world.credits) <= 0.5 + 1e-6
