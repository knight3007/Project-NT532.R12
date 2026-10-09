"""Kịch bản đầu cuối trên sa bàn ảo bằng YOLO thật: commissioning, tìm bia, ngắm, CORRECT.

    uv run python scripts/sim_demo.py --show                    # một lượt, hiện ảnh từng bước
    uv run python scripts/sim_demo.py --runs 30 --seed 1        # 30 lượt, in thống kê
    uv run python scripts/sim_demo.py --runs 30 --diagnose      # thêm: vì sao YOLO bỏ sót bia

Mỗi lượt: đặt 1–3 thẻ bia ở vị trí ngẫu nhiên trên bảng, giả cảnh báo từ s1 hoặc s2, gọi
`vision.targets(frame, sensor=...)`, chọn bia tin cậy nhất, ngắm bằng `aim_angles` (bản tham chiếu
của sim) rồi chạy vòng CORRECT bằng find_spot với servo lệch ngẫu nhiên tới 2 độ. Mọi sai số tính
so với ground truth của sim. Cần data/sim/cards/ (make_target_cards.py) và models/fire-n.pt.
Ảnh minh họa của vài lượt đầu lưu vào runs/sim/.
"""

import argparse
from functools import partial
from pathlib import Path

import cv2
import numpy as np

from nt532.config import REPO_ROOT
from nt532.sim import Scene, SimCamera, default_intrinsics, sim_site
from nt532.sim.loop import correct
from nt532.vision import Vision, project, read_frames, read_fresh
from nt532.vision.detect import detect

GREEN, RED, YELLOW, CYAN = (0, 220, 0), (0, 0, 255), (0, 220, 220), (255, 200, 0)
MATCH_M = 0.05  # hộp YOLO coi là đúng bia khi cách tâm thẻ thật không quá chừng này
SPACING_M = 0.15  # hai thẻ cách nhau ít nhất chừng này


def place_cards(scene: Scene, vision: Vision, rng, sensor: str, n: int) -> list[int]:
    """Đặt n thẻ ngẫu nhiên, thẻ đầu nằm trong tầm khớp của `sensor`; tránh vùng bị trụ node che."""
    radius = scene.site["targeting"]["sensor_match_radius_x"]
    sx = vision.nodes[sensor].position[0]
    board = scene.board
    hulls = [scene.node_hull_px(name, pad=0.04) for name in scene.nodes]
    scene.clear_cards()
    placed: list[tuple[float, float]] = []
    ids = []
    while len(ids) < n:
        lo, hi = (max(sx - radius, 0.10), min(sx + radius, board["width"] - 0.10))
        x = rng.uniform(lo, hi) if not ids else rng.uniform(0.10, board["width"] - 0.10)
        z = rng.uniform(0.10, board["height"] - 0.10)
        if any(np.hypot(x - px, z - pz) < SPACING_M for px, pz in placed):
            continue
        corners = [(x + a, board["plane_y"], z + b) for a in (-0.04, 0.04) for b in (-0.04, 0.04)]
        px = [
            (float(u), float(v)) for u, v in _project(scene, corners + [(x, board["plane_y"], z)])
        ]
        if any(
            cv2.pointPolygonTest(h.astype(np.float32), p, False) >= 0 for h in hulls for p in px
        ):
            continue
        placed.append((x, z))
        ids.append(scene.add_card(x, z))
    return ids


def _project(scene: Scene, points) -> np.ndarray:
    return np.array([project(np.array(p), scene.camera) for p in points])


def annotate(frame, vision, scene, targets, chosen, ids, spot_truth, text) -> np.ndarray:
    view = frame.copy()
    cv2.polylines(view, [vision.board_polygon().astype(np.int32)], True, CYAN, 1)
    for i in ids:  # thẻ thật
        u, v = _project(scene, [scene.card_position(i)])[0]
        cv2.drawMarker(view, (int(u), int(v)), GREEN, cv2.MARKER_CROSS, 14, 1)
    for t in targets:
        d = t.detection
        p0, p1 = (int(d.u - d.w / 2), int(d.v - d.h / 2)), (int(d.u + d.w / 2), int(d.v + d.h / 2))
        cv2.rectangle(view, p0, p1, YELLOW if t is not chosen else RED, 2)
        cv2.putText(view, f"{d.conf:.2f}", (p0[0], p0[1] - 5), 0, 0.5, YELLOW, 1)
    if spot_truth is not None:
        u, v = _project(scene, [spot_truth])[0]
        cv2.circle(view, (int(u), int(v)), 9, RED, 1)
    cv2.putText(view, text, (10, 25), 0, 0.7, YELLOW, 2)
    return view


def diagnose(scene: Scene, vision: Vision, frame, ids, args) -> dict[str, tuple[float, float]]:
    """Cho từng thẻ: cạnh pixel và conf cao nhất của hộp trúng thẻ, với nhiều cách đưa ảnh vào YOLO.

    Dùng ngưỡng 0,01 để thấy cả những hộp thấp hơn `detect_conf`. Các cách: cắt vùng bảng (như
    Vision.detect) hoặc cả ảnh, imgsz 640 hoặc 1280, và riêng vùng quanh thẻ (cận trên).
    Trả {cách: (cạnh trung bình, [conf mỗi thẻ])}.
    """
    weights = REPO_ROOT / vision.cfg["weights"]
    x0, y0, x1, y1 = vision.board_roi(frame.shape)
    confs: dict[str, list[float]] = {}
    sides = []
    for i in ids:
        c = scene.cards[i]
        r, y = c.size / 2, scene.board["plane_y"]
        corners = np.array(
            [project((c.x + a, y, c.z + b), scene.camera) for a in (-r, r) for b in (-r, r)]
        )
        side = float(np.ptp(corners, axis=0).mean())
        sides.append(side)
        cx, cy = project((c.x, y, c.z), scene.camera)
        px0, py0 = max(int(cx - side * 1.5), 0), max(int(cy - side * 1.5), 0)
        near = frame[py0 : int(cy + side * 1.5), px0 : int(cx + side * 1.5)]
        views = {
            "cắt bảng, imgsz 640": (frame[y0:y1, x0:x1], x0, y0, 640),
            "cắt bảng, imgsz 1280": (frame[y0:y1, x0:x1], x0, y0, 1280),
            "cả ảnh, imgsz 640": (frame, 0, 0, 640),
            "cả ảnh, imgsz 1280": (frame, 0, 0, 1280),
            "vùng quanh thẻ (3x cạnh)": (near, px0, py0, 640),
        }
        for name, (image, ox, oy, size) in views.items():
            found = detect(image, weights, conf=0.01, imgsz=size, device=args.device)
            hits = [
                d.conf for d in found if abs(d.u + ox - cx) < side and abs(d.v + oy - cy) < side
            ]
            confs.setdefault(name, []).append(max(hits, default=0.0))
    return {k: (float(np.mean(sides)), v) for k, v in confs.items()}


def stats(name: str, values: list[float], unit: str = "") -> str:
    if not values:
        return f"{name}: không có số liệu"
    a = np.array(values)
    return (
        f"{name}: trung bình {a.mean():.2f}{unit}, trung vị {np.median(a):.2f}{unit}, "
        f"lớn nhất {a.max():.2f}{unit} (n={len(a)})"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--show", action="store_true", help="hiện ảnh từng bước (phím bất kỳ để tiếp)")
    p.add_argument("--diagnose", action="store_true", help="in cỡ thẻ và conf thấp cho thẻ bị sót")
    p.add_argument("--cards", default="1,3", help="khoảng số thẻ mỗi lượt, ví dụ 1,3")
    p.add_argument("--bias-deg", type=float, default=2.0, help="sai lệch servo tối đa mỗi trục")
    p.add_argument("--focal", type=float, default=900.0, help="tiêu cự (pixel) ứng với rộng 1280")
    p.add_argument("--resolution", default="1280,720", help="độ phân giải ảnh, ví dụ 1920,1080")
    p.add_argument("--card-gain", type=float, default=0.8, help="độ sáng thẻ in so với ảnh gốc")
    p.add_argument("--noise", type=float, default=2.0, help="độ lệch chuẩn nhiễu gauss mỗi khung")
    p.add_argument("--imgsz", type=int, default=None, help="mặc định theo site.yaml")
    p.add_argument("--device", default=None, help="mặc định để ultralytics tự chọn (GPU nếu có)")
    p.add_argument("--out", default="runs/sim", help="thư mục lưu ảnh minh họa")
    p.add_argument("--save-n", type=int, default=3, help="số lượt đầu được lưu ảnh")
    args = p.parse_args()

    site = sim_site()
    cfg = site["vision"]
    args.imgsz = args.imgsz or cfg["imgsz"]
    rng = np.random.default_rng(args.seed)
    width, height = (int(v) for v in args.resolution.split(","))
    focal = args.focal * width / 1280  # cùng góc nhìn khi đổi độ phân giải
    intrinsics = default_intrinsics((width, height), focal)
    scene = Scene(
        site,
        intrinsics=intrinsics,
        seed=args.seed,
        noise_sigma=args.noise,
        card_gain=args.card_gain,
    )
    cap = SimCamera(scene)
    detector = partial(
        detect,
        weights=REPO_ROOT / cfg["weights"],
        conf=cfg["detect_conf"],
        imgsz=args.imgsz,
        device=args.device,
    )
    vision = Vision(site, scene.intrinsics, detector)
    out = REPO_ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    cam = scene.camera.position
    print(f"Camera ảo tại ({cam[0]:.2f}, {cam[1]:.2f}, {cam[2]:.2f}) m, f = {focal:g} px")
    state = vision.commission(read_frames(cap, cfg["commission_frames"]))
    print(
        f"Commissioning: sai số camera {np.linalg.norm(state.camera.position - cam) * 100:.2f} cm, "
        f"chiếu lại {state.reprojection_px:.2f} px"
        + "".join(
            f", {n} {np.linalg.norm(state.nodes[n].position - scene.nodes[n].position) * 100:.2f} cm"
            for n in sorted(state.nodes)
        )
    )
    print("Nạp YOLO...")
    vision.detect(read_fresh(cap))

    lo, hi = (int(v) for v in args.cards.split(","))
    seen, total, false_pos = 0, 0, 0
    loc_err, first_err, final_err, rounds, confs = [], [], [], [], []
    lost = 0
    diag: dict[str, list[float]] = {}
    sides: list[float] = []
    for run in range(1, args.runs + 1):
        sensor = str(rng.choice(sorted(scene.nodes)))
        n_cards = int(rng.integers(lo, hi + 1))
        ids = place_cards(scene, vision, rng, sensor, n_cards)
        truth = {i: scene.card_position(i) for i in ids}
        sx = vision.nodes[sensor].position[0]
        in_range = [
            i for i in ids if abs(truth[i][0] - sx) <= site["targeting"]["sensor_match_radius_x"]
        ]
        frame = read_fresh(cap)
        targets = vision.targets(frame, sensor=sensor)

        def nearest(pos, ids=ids, truth=truth):
            return min(ids, key=lambda j: np.linalg.norm((pos - truth[j])[[0, 2]]))

        matched = [
            t
            for t in targets
            if np.linalg.norm((t.position - truth[nearest(t.position)])[[0, 2]]) < MATCH_M
        ]
        found_ids = {nearest(t.position) for t in matched}
        seen += len(found_ids & set(in_range))
        total += len(in_range)
        false_pos += len(targets) - len(matched)
        print(
            f"\n[lượt {run}] cảnh báo từ {sensor}; {n_cards} thẻ, {len(in_range)} trong tầm; "
            f"YOLO báo {len(targets)} bia, đúng {len(matched)}"
        )
        if args.diagnose:
            for name, (side, c) in diagnose(scene, vision, frame, in_range, args).items():
                diag.setdefault(name, []).extend(c)
                sides.append(side)
        view = annotate(
            frame, vision, scene, targets, None, ids, None, f"run {run}: YOLO ({sensor})"
        )
        if args.show:
            cv2.imshow("sim_demo", view)
            cv2.waitKey(0)
        if not matched:
            if run <= args.save_n:
                cv2.imwrite(str(out / f"run{run:02d}_detect.png"), view)
            continue

        chosen = max(matched, key=lambda t: t.detection.conf)
        confs.append(chosen.detection.conf)
        loc = float(np.linalg.norm((chosen.position - truth[nearest(chosen.position)])[[0, 2]]))
        loc_err.append(loc * 100)
        real_target = truth[nearest(chosen.position)]

        bias = rng.uniform(-args.bias_deg, args.bias_deg, 2)
        scene.set_servo_bias(sensor, *bias)
        result = correct(
            cap,
            vision,
            sensor,
            chosen.position,
            site["targeting"]["correct_max_iters"],
            site["targeting"]["correct_done_m"],
        )
        if result.lost:
            lost += 1
            print("  không thấy vết laser")
            continue
        errs = [float(np.linalg.norm((s - real_target)[[0, 2]])) * 100 for s in result.truth]
        first_err.append(errs[0])
        final_err.append(errs[-1])
        rounds.append(result.rounds)
        print(
            f"  bia: conf {chosen.detection.conf:.2f}, sai số định vị {loc * 100:.2f} cm; "
            f"servo lệch ({bias[0]:+.1f}, {bias[1]:+.1f}) độ"
        )
        print(
            "  laser so với thẻ thật theo từng vòng (cm): "
            + ", ".join(f"{e:.2f}" for e in errs)
            + f"; {result.rounds} vòng"
        )
        scene.set_laser(sensor, True)
        spot_frame = read_fresh(cap)
        scene.set_laser(sensor, False)
        view = annotate(
            spot_frame,
            vision,
            scene,
            targets,
            chosen,
            ids,
            scene.spot(sensor),
            f"run {run}: {sensor} err {errs[0]:.1f} -> {errs[-1]:.1f} cm",
        )
        if run <= args.save_n:
            cv2.imwrite(str(out / f"run{run:02d}_laser.png"), view)
        if args.show:
            cv2.imshow("sim_demo", view)
            cv2.waitKey(0)

    print(f"\n=== Tổng kết {args.runs} lượt (seed {args.seed}, f = {focal:g} px) ===")
    print(f"YOLO thấy bia trong tầm sensor: {seen}/{total} = {seen / max(total, 1):.0%}")
    print(f"Báo nhầm (hộp không khớp thẻ nào): {false_pos}")
    print(stats("Sai số định vị bia", loc_err, " cm"))
    print(stats("Sai số laser trước CORRECT", first_err, " cm"))
    print(stats("Sai số laser sau CORRECT", final_err, " cm"))
    if rounds:
        done = sum(e < 1.0 for e in final_err)
        print(
            f"Số vòng lặp: trung bình {np.mean(rounds):.2f}, phân bố "
            f"{ {k: rounds.count(k) for k in sorted(set(rounds))} }; "
            f"sau CORRECT dưới 1 cm: {done}/{len(final_err)}; không thấy vết: {lost}"
        )
    if diag:
        print(f"Chẩn đoán: cạnh thẻ trung bình {np.mean(sides):.0f} px trên ảnh gốc")
        for name, confs_ in diag.items():
            hit = sum(c >= cfg["detect_conf"] for c in confs_)
            print(
                f"  {name}: thấy {hit}/{len(confs_)} (conf >= {cfg['detect_conf']}), "
                f"có hộp trúng thẻ ở ngưỡng 0,01: {sum(c > 0 for c in confs_)}/{len(confs_)}, "
                f"conf trung vị {np.median(confs_):.2f}"
            )
    cv2.destroyAllWindows()
    print(f"Ảnh minh họa: {Path(out).relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
