"""Các bài đo sai số của phần thị giác, ghi CSV vào runs/measure/<bài>.csv (ghi thêm dòng).

    uv run python scripts/measure.py click --label p1 --truth 0.30,0.20
    uv run python scripts/measure.py target --label p1 --truth 0.30,0.20 --repeat 3
    uv run python scripts/measure.py tag --node s1 --truth 0.40,0.58 --repeat 3
    uv run python scripts/measure.py spot --label p1 --target 0.30,0.20
    uv run python scripts/measure.py target --label p1 --truth 0.30,0.20 --headless   # trên Pi

Cần calibration/commissioning.yaml (commission.py). Mọi tọa độ tính bằng mét trong hệ sa bàn.
  click   Bài thước 5 điểm: click chuột lên điểm trên bảng, in tọa độ sa bàn. --truth là X,Z.
  target  Định vị mục tiêu: mỗi lượt chụp một ảnh, lấy bia gần --truth (X,Z) nhất. Không thấy bia
          thì ghi dòng với cột đo để trống. Có --headless thì tự chụp, cách nhau --interval giây.
  tag     Pose tag: mỗi lượt lấy commission_frames khung, ghi vị trí node (trung vị) so với
          --truth là X,Y của tâm tag, cùng tỷ lệ số khung thấy tag.
  spot    Đo laser bằng tay: chụp ảnh tắt rồi ảnh bật laser, tìm vết, so với --target (X,Z).
          Orchestrator thật sẽ bật/tắt laser qua CoAP thay cho hai lần bấm phím này.

Phím: SPACE chụp, Q thoát (click: bấm chuột để đo). Chế độ --headless dùng Enter thay SPACE
ở bài spot.
"""

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from nt532.config import REPO_ROOT, load_site
from nt532.measure import append_row, error_fields, nearest_target, next_run, parse_xy
from nt532.vision import Vision, read_frames, read_fresh
from nt532.vision.camera import open_camera
from nt532.vision.tags import median_corners
from nt532.vision.tags import tag_poses as solve_tag_poses

OUT_DIR = REPO_ROOT / "runs" / "measure"
ORANGE, YELLOW, GREEN = (0, 140, 255), (0, 220, 220), (0, 220, 0)
WINDOW = "NT532 - do sai so"


def show(vision: Vision, frame: np.ndarray, text: str) -> None:
    """Vẽ viền bảng và dòng hướng dẫn lên ảnh (không dấu: font OpenCV không có tiếng Việt)."""
    view = frame.copy()
    cv2.polylines(view, [vision.board_polygon().astype(np.int32)], True, ORANGE, 2)
    cv2.putText(view, text, (10, 25), 0, 0.7, YELLOW, 2)
    cv2.imshow(WINDOW, view)


def grab(cap, vision: Vision, text: str, headless: bool, wait: str = "space") -> np.ndarray | None:
    """Chờ người bấm SPACE (hoặc Enter nếu headless và wait == "enter"), rồi chụp ảnh mới.

    Trả None nếu bấm Q. Headless với wait == "auto" chụp ngay.
    """
    print(text)
    if headless:
        if wait == "enter":
            return (
                None if input("  Enter để chụp, q để thoát: ").strip() == "q" else read_fresh(cap)
            )
        return read_fresh(cap)
    while True:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("không đọc được khung hình")
        show(vision, frame, text)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            return None
        if key == ord(" "):
            return read_fresh(cap)


def need_commissioning(vision: Vision) -> None:
    if vision.state is None:
        raise SystemExit("chưa có calibration/commissioning.yaml, chạy commission.py trước")


def run_click(args, cap, vision: Vision, path: Path) -> None:
    truth = parse_xy(args.truth)
    clicks: list[tuple[int, int]] = []
    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(
        WINDOW, lambda ev, x, y, *_: clicks.append((x, y)) if ev == cv2.EVENT_LBUTTONDOWN else None
    )
    mark = None
    print(f"Click vào điểm {args.label} (thật X,Z = {truth}); Q thoát.")
    while True:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("không đọc được khung hình")
        while clicks:
            u, v = clicks.pop(0)
            point = vision.localize((u, v))
            if point is None:
                print(f"  ({u}, {v}) nằm ngoài bảng bia")
                continue
            run = next_run(path, args.label)
            append_row(path, args.label, run, error_fields(truth, (point[0], point[2])))
            ex, ez = (point[0] - truth[0]) * 100, (point[2] - truth[1]) * 100
            print(
                f"  lượt {run}: x={point[0]:.4f} z={point[2]:.4f} m, "
                f"sai số x={ex:+.1f} z={ez:+.1f} cm, tổng {np.hypot(ex, ez):.1f} cm"
            )
            mark = (u, v)
        view = frame.copy()
        if mark:
            cv2.drawMarker(view, mark, GREEN, cv2.MARKER_CROSS, 24, 2)
        show(vision, view, f"{args.label}: click vao diem do, Q thoat")
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break


def run_target(args, cap, vision: Vision, path: Path) -> None:
    truth = parse_xy(args.truth)
    first = next_run(path, args.label)
    for i in range(args.repeat):
        if args.headless and i:
            time.sleep(args.interval)
        frame = grab(
            cap, vision, f"{args.label} lượt {first + i}: SPACE để chụp", args.headless, "auto"
        )
        if frame is None:
            return
        found = vision.targets(frame)
        best = nearest_target(found, truth)
        measured = None if best is None else (best.position[0], best.position[2])
        fields = error_fields(truth, measured)
        fields |= {"conf": "" if best is None else round(best.detection.conf, 3)}
        fields |= {"n_targets": len(found)}
        append_row(path, args.label, first + i, fields)
        if best is None:
            print(f"  không thấy bia nào ({len(found)} bia trên bảng)")
        else:
            print(f"  sai số {fields['err'] * 100:.1f} cm (thấy {len(found)} bia)")


def run_tag(args, cap, vision: Vision, path: Path) -> None:
    node_ids = vision.site["tags"]["nodes"]["ids"]
    if args.node not in node_ids:
        raise SystemExit(f"không có node {args.node}, chọn một trong {sorted(node_ids)}")
    tag_id = node_ids[args.node]
    truth = parse_xy(args.truth)
    label = args.label or args.node
    first = next_run(path, label)
    n = vision.cfg["commission_frames"]
    for i in range(args.repeat):
        if args.headless and i:
            time.sleep(args.interval)
        if (
            grab(cap, vision, f"{label} lượt {first + i}: SPACE để đo", args.headless, "auto")
            is None
        ):
            return
        corners, rate = median_corners(read_frames(cap, n))
        pose = solve_tag_poses(corners, vision.camera, vision.site).get(tag_id)
        measured = None if pose is None else (pose.position[0], pose.position[1])
        fields = error_fields(truth, measured, axis2="y")
        fields |= {"meas_z": "" if pose is None else float(pose.position[2])}
        fields |= {"node": args.node, "tag_rate": round(rate.get(tag_id, 0.0), 3)}
        append_row(path, label, first + i, fields)
        if pose is None:
            print("  không thấy tag")
        else:
            print(f"  sai số {fields['err'] * 100:.1f} cm, thấy tag {rate[tag_id]:.0%} số khung")


def run_spot(args, cap, vision: Vision, path: Path) -> None:
    target = parse_xy(args.target)
    first = next_run(path, args.label)
    for i in range(args.repeat):
        run = first + i
        off = grab(
            cap, vision, f"{args.label} lượt {run}: TẮT laser rồi SPACE", args.headless, "enter"
        )
        if off is None:
            return
        on = grab(
            cap, vision, f"{args.label} lượt {run}: BẬT laser rồi SPACE", args.headless, "enter"
        )
        if on is None:
            return
        point = vision.find_spot(on, off)
        measured = None if point is None else (point[0], point[2])
        fields = error_fields(target, measured)
        append_row(path, args.label, run, fields)
        if point is None:
            print("  không thấy vết laser trên bảng")
        else:
            print(
                f"  vết tại x={point[0]:.4f} z={point[2]:.4f} m, "
                f"lệch so với target {fields['err'] * 100:.1f} cm"
            )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source", default=None, help="số thứ tự webcam, file video, ảnh hoặc sim")
    sub = p.add_subparsers(dest="task", required=True)

    def common(q, repeat: bool = True) -> None:
        q.add_argument("--label", default=None, help="nhãn vị trí, ví dụ p1")
        if repeat:
            q.add_argument("--repeat", type=int, default=1, help="số lượt đo")
        q.add_argument("--headless", action="store_true", help="không cửa sổ (chạy trên Pi)")
        q.add_argument("--interval", type=float, default=1.0, help="giây giữa hai lượt headless")

    q = sub.add_parser("click", help="click chuột lên điểm trên bảng")
    common(q, repeat=False)
    q.add_argument("--truth", required=True, help="X,Z thật trên bảng (m)")
    q = sub.add_parser("target", help="đo vị trí bia YOLO")
    common(q)
    q.add_argument("--truth", required=True, help="X,Z thật của bia (m)")
    q = sub.add_parser("tag", help="đo pose tag node")
    common(q)
    q.add_argument("--node", required=True, help="tên node, ví dụ s1")
    q.add_argument("--truth", required=True, help="X,Y thật của tâm tag trên bàn (m)")
    q = sub.add_parser("spot", help="đo vết laser bằng tay")
    common(q)
    q.add_argument("--target", required=True, help="X,Z điểm đang ngắm trên bảng (m)")
    args = p.parse_args()

    if args.task != "tag" and not args.label:
        p.error("cần --label")
    if args.task == "click" and args.headless:
        p.error("click cần cửa sổ, không dùng được với --headless")

    site = load_site(source=args.source)
    vision = Vision.from_site(site)
    need_commissioning(vision)
    try:
        cap = open_camera(site["camera"], args.source)
    except RuntimeError as e:
        raise SystemExit(str(e)) from e
    path = OUT_DIR / f"{args.task}.csv"
    try:
        {"click": run_click, "target": run_target, "tag": run_tag, "spot": run_spot}[args.task](
            args, cap, vision, path
        )
    except ValueError as e:
        raise SystemExit(str(e)) from e
    finally:
        cap.release()
        cv2.destroyAllWindows()
    print(f"Đã ghi {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
