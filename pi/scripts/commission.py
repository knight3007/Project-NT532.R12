"""Commissioning không cửa sổ cho Pi: đo pose camera và node từ tag, lưu file commissioning.

    uv run python scripts/commission.py                  # đọc commission_frames khung từ webcam
    uv run python scripts/commission.py --frames 30 --source 1
    uv run python scripts/commission.py --check          # so cảnh hiện tại với file đã lưu

Thoát mã 1 nếu thiếu node hoặc sai số chiếu lại vượt camera_shift_px (và không ghi đè file đã
lưu, trừ khi có --force); mã 2 nếu không thấy đủ tag tham chiếu. Với --check, mã 1 nếu có gì
bị xê dịch.
"""

import argparse
import sys

from nt532.config import REPO_ROOT, load_site
from nt532.vision import Vision, read_frames
from nt532.vision.camera import open_camera
from nt532.vision.report import commissioning_report, health_report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=None, help="số thứ tự webcam, file video hoặc ảnh")
    p.add_argument("--frames", type=int, default=None, help="số khung (mặc định commission_frames)")
    p.add_argument("--check", action="store_true", help="kiểm tra so với file đã lưu, không ghi")
    p.add_argument("--force", action="store_true", help="vẫn lưu dù kết quả không đạt")
    args = p.parse_args()

    site = load_site()
    cfg = site["vision"]
    frames = args.frames or cfg["commission_frames"]
    try:
        vision = Vision.from_site(site)
        cap = open_camera(site["camera"], args.source)
    except (RuntimeError, FileNotFoundError) as e:
        raise SystemExit(f"{e} (đã chạy calibrate_camera.py chưa?)") from e
    images = read_frames(cap, frames)
    cap.release()

    if args.check:
        if vision.state is None:
            raise SystemExit("chưa có file commissioning để so, chạy lại không kèm --check")
        health = vision.check(images)
        print("\n".join(health_report(health, cfg["camera_shift_px"], cfg["node_moved_m"])))
        sys.exit(1 if health.stale else 0)

    try:
        state = vision.commission(images)
    except ValueError as e:
        print(f"Không commissioning được: {e}")
        sys.exit(2)
    node_ids = site["tags"]["nodes"]["ids"]
    print("\n".join(commissioning_report(state, node_ids, cfg["camera_shift_px"])))
    ok = not state.missing and state.reprojection_px <= cfg["camera_shift_px"]
    if ok or args.force:
        print(f"Đã lưu {vision.save().relative_to(REPO_ROOT)}")
    else:
        print("Kết quả không đạt nên KHÔNG lưu (thêm --force để vẫn lưu).")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
