"""Xem trực tiếp AprilTag: vẽ viền và ID, in pose camera và pose từng node.

    uv run python scripts/live_tags.py                   # webcam trong site.yaml
    uv run python scripts/live_tags.py --source clip.mp4

Cần calibration/camera.yaml (calibrate_camera.py) và vị trí tag tham chiếu trong site.yaml để
giải pose; thiếu thì vẫn hiện tag và ID. Khi thấy từ 2 tag tham chiếu: vẽ viền bảng bia chiếu lên
ảnh, in vị trí camera, sai số chiếu lại, (x, y, z) và yaw của từng node.

Phím: C commissioning rồi lưu file, K kiểm tra xê dịch so với file đã lưu, Q thoát.
"""

import argparse
import time

import cv2
import numpy as np

from nt532.config import REPO_ROOT, load_site
from nt532.vision import Commissioning, Vision, detect_tags, read_frames
from nt532.vision.camera import open_camera
from nt532.vision.report import commissioning_report, health_report, pose_line
from nt532.vision.tags import reprojection_error, solve_camera
from nt532.vision.tags import tag_poses as solve_tag_poses

GREEN, ORANGE, YELLOW = (0, 220, 0), (0, 140, 255), (0, 220, 220)


def draw_tags(frame: np.ndarray, corners: dict[int, np.ndarray], names: dict[int, str]) -> None:
    for tag_id, c in corners.items():
        cv2.polylines(frame, [c.astype(np.int32)], True, GREEN, 2)
        label = f"{tag_id} {names[tag_id]}" if tag_id in names else str(tag_id)
        x, y = c.min(axis=0)
        cv2.putText(frame, label, (int(x), max(int(y) - 6, 14)), 0, 0.6, GREEN, 2)


def solve_live(vision: Vision, corners: dict[int, np.ndarray]) -> tuple[Vision | None, str]:
    """Giải pose camera trên một khung; trả (Vision tạm giữ pose đó, lý do nếu không giải được)."""
    site = vision.site
    try:
        camera = solve_camera(corners, vision.intrinsics, site)
    except ValueError as e:
        return None, str(e)
    node_of = {i: n for n, i in site["tags"]["nodes"]["ids"].items()}
    nodes = {node_of[i]: p for i, p in solve_tag_poses(corners, camera, site).items()}
    live = Vision(site, vision.intrinsics)
    live.state = Commissioning(camera, nodes, reprojection_error(corners, camera, site), {}, [])
    return live, ""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=None, help="số thứ tự webcam, file video, ảnh hoặc sim")
    args = p.parse_args()

    site = load_site(source=args.source)
    cfg = site["vision"]
    node_ids = site["tags"]["nodes"]["ids"]
    try:
        vision = Vision.from_site(site)
    except FileNotFoundError:
        vision = None
        print("Chưa có calibration/camera.yaml: chỉ phát hiện tag, chạy calibrate_camera.py trước.")
    try:
        cap = open_camera(site["camera"], args.source)
    except RuntimeError as e:
        raise SystemExit(str(e)) from e

    names = {i: n for n, i in node_ids.items()}
    last_print = 0.0
    while True:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("không đọc được khung hình; kiểm tra camera hoặc thử --source khác")
        corners = detect_tags(frame)
        draw_tags(frame, corners, names)
        # Chữ trên ảnh không dấu vì font của OpenCV không vẽ được tiếng Việt.
        lines = [f"{len(corners)} tag: {sorted(corners)}"]
        if vision is None:
            lines.append("chua co intrinsics: can hieu chuan camera")
        else:
            live, why = solve_live(vision, corners)
            if live is None:
                lines.append(why)
            else:
                cv2.polylines(frame, [live.board_polygon().astype(np.int32)], True, ORANGE, 2)
                state = live.state
                cx, cy, cz = state.camera.position
                lines.append(
                    f"camera ({cx:.3f}, {cy:.3f}, {cz:.3f}) m, "
                    f"chieu lai {state.reprojection_px:.2f} px"
                )
                poses = [pose_line(n, pose) for n, pose in state.nodes.items()]
                lines += [t.replace("độ", "do") for t in poses]
                if time.time() - last_print > 1.0:
                    last_print = time.time()
                    print(lines[1], *poses, sep="\n  ", end="\n\n")
        for i, line in enumerate(lines):
            cv2.putText(frame, line, (10, 25 + 24 * i), 0, 0.6, YELLOW, 2)
        cv2.imshow("NT532 - AprilTag (C commissioning, K kiem tra, Q thoat)", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key in (ord("c"), ord("k")) and vision is not None:
            images = read_frames(cap, cfg["commission_frames"])
            try:
                if key == ord("c"):
                    state = vision.commission(images)
                    print("\n".join(commissioning_report(state, node_ids, cfg["camera_shift_px"])))
                    print(f"Đã lưu {vision.save().relative_to(REPO_ROOT)}")
                elif vision.state is None:
                    print("Chưa commissioning, bấm C trước.")
                else:
                    health = vision.check(images)
                    shift, moved = cfg["camera_shift_px"], cfg["node_moved_m"]
                    print("\n".join(health_report(health, shift, moved)))
            except ValueError as e:
                print(f"Không làm được: {e}")
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
