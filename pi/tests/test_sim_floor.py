"""Sa bàn ảo: bia trên nền, camera cao cấu hình được và xê dịch được, bố cục node."""

import cv2
import numpy as np
import pytest

from nt532.sim import CAMERA_PRESETS, NODE_LAYOUTS, Scene, SimCamera, aim_angles
from nt532.sim.world import OracleDetector, SimWorld
from nt532.vision import Vision, project, read_frames, read_fresh


@pytest.fixture(scope="module")
def cards_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("cards")
    rng = np.random.default_rng(0)
    for i in range(3):
        cv2.imwrite(str(path / f"card_{i}.png"), rng.integers(100, 255, (64, 64, 3), np.uint8))
    return path


@pytest.fixture
def world():
    w = SimWorld(seed=3)
    yield w
    w.stop()


def _detect(world, tries=40):
    det = OracleDetector(world, conf=0.0, seed=5)
    det.DROP = 0.0
    return det(world.read())


def test_floor_card_is_upright_and_rendered_where_projected(cards_dir):
    scene = Scene(cards_dir=cards_dir, noise_sigma=0.0, brightness_jitter=0.0)
    empty = scene.render()
    cid = scene.add_card_3d((0.60, 0.40, 0.0), "floor")
    assert scene.cards[cid].surface == "floor"
    quad = scene.card_quad(cid)
    assert np.allclose(quad[2:, 2], 0.0) and np.allclose(quad[:2, 2], 0.07)  # đáy ở Z = 0
    np.testing.assert_allclose(scene.card_position(cid), [0.60, 0.40, 0.0])
    u, v = project([0.60, 0.40, 0.035], scene.camera)  # giữa thẻ đứng, cao nửa thẻ
    diff = np.abs(scene.render().astype(int) - empty.astype(int)).sum(axis=2)
    assert diff[int(v) - 3:int(v) + 4, int(u) - 3:int(u) + 4].mean() > 30
    far = project([0.20, 0.10, 0.035], scene.camera)
    assert diff[int(far[1]) - 3:int(far[1]) + 4, int(far[0]) - 3:int(far[0]) + 4].mean() < 1


def test_add_card_3d_infers_surface_and_old_call_still_works(cards_dir):
    scene = Scene(cards_dir=cards_dir)
    wall = scene.add_card(0.3, 0.2)
    assert scene.cards[wall].surface == "wall"
    np.testing.assert_allclose(scene.card_position(wall), [0.3, 0.80, 0.2])
    assert scene.cards[scene.add_card_3d((0.3, 0.8, 0.1))].surface == "wall"
    assert scene.cards[scene.add_card_3d((0.3, 0.3, 0.0))].surface == "floor"
    with pytest.raises(ValueError):
        scene.add_card_3d((0.3, 0.3, 0.0), "ceiling")


def test_oracle_box_bottom_centre_is_contact_point(world):
    fire = world.ignite(0.45, size="small", surface="floor", y=0.35)
    assert fire.surface == "floor" and fire.z == 0.0
    dets = _detect(world)
    assert len(dets) == 1
    d = dets[0]
    u, v = project([0.45, 0.35, 0.0], world.scene.camera)
    assert abs(d.u - u) < 2.0 and abs((d.v + d.h / 2) - v) < 2.0
    assert d.h > 20  # thẻ đứng nên hộp cao tương đương rộng


def test_vision_localizes_sim_floor_fire(world):
    vision = Vision(world.site, world.scene.intrinsics, detector=lambda f: [])
    vision.commission(read_frames(world.cap, 15))
    for x, y in ((0.30, 0.20), (0.90, 0.40)):
        world.clear()
        world.ignite(x, surface="floor", y=y)
        d = _detect(world)[0]
        point, surface = vision.localize_detection(d)
        assert surface == "floor" and np.linalg.norm(point - [x, y, 0.0]) < 0.04
    world.clear()
    world.ignite(0.5, 0.3)  # trên bảng vẫn ra mặt bảng
    point, surface = vision.localize_detection(_detect(world)[0])
    assert surface == "wall" and np.linalg.norm(point - [0.5, 0.8, 0.3]) < 0.04


def test_sensors_use_3d_distance(world):
    def temp(**kw):
        world.clear()
        s = world.ignite(**kw)
        s.level = 1.0
        return np.mean([world.reading("s1")["temp"] for _ in range(40)])

    near = temp(x=0.10, surface="floor", y=0.10)  # sát s1 (góc trái trước bàn, X 0 Y 0)
    far_y = temp(x=0.10, surface="floor", y=0.60)  # cùng X nhưng xa dọc theo Y
    far_x = temp(x=0.90, surface="floor", y=0.10)
    assert near > far_y + 2.0 and near > far_x + 2.0
    wall_high = temp(x=0.10, z=0.55)  # trên bảng, cao: xa hơn do chênh Z
    wall_low = temp(x=0.10, z=0.25)
    assert wall_low > wall_high


def test_nozzle_ray_hits_floor_and_water_extinguishes(world):
    scene = world.scene
    target = np.array([0.50, 0.30, 0.0])
    scene.set_servo_bias("s1", 0.0, 0.0)
    scene.aim("s1", *aim_angles(scene.nodes["s1"].pivot, target, scene.nodes["s1"].facing_deg))
    point, surface = scene.spot_surface("s1")
    assert surface == "floor" and np.linalg.norm(point - target) < 1e-9
    assert scene.spot("s1") is not None
    fire = world.ignite(0.50, size="small", surface="floor", y=0.30)
    wall_fire = world.ignite(0.50, 0.30)  # trên bảng, phía sau tia: không bị trúng
    for _ in range(10):
        world.spray("s1", 1.0)
    assert fire.out and not wall_fire.out
    assert np.linalg.norm(world.marks["s1"] - target) < 0.03
    # bật laser thì ảnh có vết tại điểm chạm nền
    scene.set_laser("s1", True)
    on = read_fresh(world.cap)
    scene.set_laser("s1", False)
    off = read_fresh(world.cap)
    u, v = project(target, scene.camera)
    assert (on.astype(int) - off.astype(int))[int(v) - 4:int(v) + 5, int(u) - 4:int(u) + 5, 2].max() > 60


def test_steep_down_ray_misses_wall_but_hits_floor_first(world):
    scene = world.scene
    scene.set_servo_bias("s1", 0.0, 0.0)
    scene.aim("s1", 0.0, -60.0)  # thẳng về phía bảng nhưng chúc xuống: chạm nền trước bảng
    point, surface = scene.spot_surface("s1")
    assert surface == "floor" and point[1] < 0.80 and point[2] == pytest.approx(0.0, abs=1e-9)
    scene.aim("s1", 0.0, 0.0)
    assert scene.spot_surface("s1")[1] == "wall"


@pytest.mark.parametrize("layout", ["posts", "corners"])
@pytest.mark.parametrize("name", ["front", "left", "right"])
def test_presets_cover_everything(name, layout):
    scene = Scene(camera_pose=name, node_layout=layout)
    cov = scene.coverage(margin=20)
    assert cov["ok"], cov["warnings"]
    assert cov["floor"] == 1.0 and cov["wall"] == 1.0
    assert all(cov["reference_tags"].values()) and len(cov["reference_tags"]) == 4
    assert all(cov["node_tags"].values()) and len(cov["node_tags"]) == 2


def test_default_camera_is_high_and_oblique_and_coverage_warns():
    scene = Scene()
    pos, target = scene.camera_pose()
    assert pos[2] > 0.8 and pos[1] < 0 and target[1] > pos[1]
    assert np.degrees(np.arctan2(pos[2] - target[2], target[1] - pos[1])) > 20  # chếch xuống
    assert scene.coverage(margin=30)["ok"]
    scene.set_camera([0.6, 0.2, 0.9], [0.6, 0.8, 0.3])  # sát bàn: không thấy hết
    cov = scene.coverage()
    assert not cov["ok"] and cov["floor"] < 1.0
    assert any("thiếu tag tham chiếu" in w for w in cov["warnings"])
    assert "không thấy hết sàn" in cov["warnings"]
    # camera thấp cũ không thấy tag node ở hai góc trước bàn (corners), chỉ khớp bố cục posts
    legacy = Scene(camera_pose="legacy", node_layout="posts")
    assert legacy.coverage()["floor"] < 1.0 and legacy.frame_check()


def test_set_camera_and_pose_roundtrip():
    scene = Scene()
    scene.set_camera([0.5, -0.9, 1.0], [0.6, 0.4, 0.2])
    pos, target = scene.camera_pose()
    np.testing.assert_allclose(pos, [0.5, -0.9, 1.0])
    np.testing.assert_allclose(target, [0.6, 0.4, 0.2])
    np.testing.assert_allclose(scene.camera.position, pos, atol=1e-9)
    assert set(CAMERA_PRESETS) >= {"front", "left", "right", "legacy"}


def test_jitter_is_bounded_and_vision_reposes():
    scene = Scene(noise_sigma=2.0)
    vision = Vision(scene.site, scene.intrinsics, detector=lambda f: [])
    vision.commission(read_frames(SimCamera(scene), 15))
    rng = np.random.default_rng(4)
    pos0, target0 = scene.camera_pose()
    fwd0 = scene.camera.R[2].copy()
    shifts = []
    for _ in range(20):
        scene.set_camera(pos0, target0)
        info = scene.jitter_camera(rng, 0.03, 2.0)
        assert np.linalg.norm(scene.camera.position - pos0) <= 0.03 + 1e-9
        ang = np.degrees(np.arccos(np.clip(scene.camera.R[2] @ fwd0, -1, 1)))
        assert ang <= 2.0 + 1e-6 and info["rot_deg"] <= 2.0 + 1e-6 and info["shift_m"] <= 0.03
        shifts.append(info["shift_m"])
    assert max(shifts) > 0.005  # có thật sự dịch
    scene.set_camera(pos0, target0)
    scene.jitter_camera(np.random.default_rng(1), 0.03, 2.0)
    health = vision.check(read_frames(SimCamera(scene), 5))
    assert health.camera_shift_px > 3.0 and health.repose is not None and health.stale == []
    assert np.linalg.norm(vision.camera.position - scene.camera.position) < 0.02


def test_corner_layout_aim_uses_node_yaw():
    scene = Scene(node_layout="corners")
    s1, s2 = scene.nodes["s1"], scene.nodes["s2"]
    np.testing.assert_allclose(s1.position[:2], [0.0, 0.0])
    np.testing.assert_allclose(s2.position[:2], [1.2, 0.0])
    assert s1.facing_deg < 0 < s2.facing_deg and np.isclose(s1.facing_deg, -s2.facing_deg)
    # pan = 0 hướng vào giữa bảng: s1 về phía (0,6; 0,8), s2 về phía (-0,6; 0,8)
    np.testing.assert_allclose(s1.direction[:2], np.array([0.6, 0.8]), atol=1e-9)
    np.testing.assert_allclose(s2.direction[:2], np.array([-0.6, 0.8]), atol=1e-9)
    for name, node in scene.nodes.items():
        target = np.array([0.60, 0.50, 0.0])
        assert aim_angles(node.pivot, target)[0] != pytest.approx(
            aim_angles(node.pivot, target, node.facing_deg)[0])
        scene.aim(name, *aim_angles(node.pivot, target, node.facing_deg))
        np.testing.assert_allclose(scene.spot(name), target, atol=1e-9)
    assert set(NODE_LAYOUTS) == {"posts", "corners"}


def test_corner_layout_world_spray_and_posts_unchanged():
    w = SimWorld(seed=1, node_layout="corners", camera="left")
    try:
        fire = w.ignite(0.60, surface="floor", y=0.40)
        w.aim("s2", *aim_angles(w.scene.nodes["s2"].pivot, fire.pos,
                                w.scene.nodes["s2"].facing_deg))
        w.scene.set_servo_bias("s2", 0.0, 0.0)
        for _ in range(8):
            w.spray("s2", 1.0)
        assert fire.out
    finally:
        w.stop()
    posts = Scene(node_layout="posts")
    assert posts.nodes["s1"].facing_deg == 10.0 and posts.nodes["s1"].position[1] == 0.55
    assert Scene().node_layout == "corners"


def test_sim_yaw_matches_live_aiming_convention():
    from nt532.orchestrator import aiming

    scene = Scene(node_layout="corners")
    target = np.array([0.40, 0.60, 0.0])
    for node in scene.nodes.values():
        mine = aim_angles(node.pivot, target, node.facing_deg)
        live = aiming.angles(node.pivot, target, node.facing_deg)
        assert mine == pytest.approx(tuple(live), abs=1e-6)
        assert aiming.heading_yaw_deg(node.pose) == pytest.approx(node.facing_deg, abs=1e-6)
