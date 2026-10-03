"""Chụp khung hình từ webcam vào một thư mục, tự động hoặc bằng phím.

    uv run python scripts/capture_frames.py --out data/raw/negatives/phong --every 0.5
    uv run python scripts/capture_frames.py --out data/raw/sa-ban                 # SPACE để chụp

Dùng để thu ảnh không có lửa (mẫu âm tính) và ảnh thẻ bia trên sa bàn.
Phím: SPACE chụp một ảnh, A bật/tắt chụp tự động, Q thoát.
"""

import argparse
import time

import cv2

from nt532.config import REPO_ROOT, load_site
from nt532.vision.camera import open_camera


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="thư mục lưu, tính từ gốc repo")
    p.add_argument("--source", type=int, default=None)
    p.add_argument("--every", type=float, default=0.0, help="giây giữa hai ảnh khi chụp tự động")
    args = p.parse_args()

    try:
        cap = open_camera(load_site()["camera"], args.source)
    except RuntimeError as e:
        raise SystemExit(str(e)) from e
    out = REPO_ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    prefix = time.strftime("%Y%m%d-%H%M%S")
    n, auto, last = 0, args.every > 0, 0.0
    while True:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("không đọc được webcam")
        now = time.monotonic()
        key = cv2.waitKey(1) & 0xFF
        if key == ord("a"):
            auto = not auto and args.every > 0
        if key == ord(" ") or (auto and now - last >= args.every):
            cv2.imwrite(str(out / f"{prefix}_{n:04d}.jpg"), frame)
            n, last = n + 1, now
        view = frame.copy()
        state = "AUTO" if auto else "tay"
        cv2.putText(
            view, f"{n} anh  [{state}]", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2
        )
        cv2.imshow("capture (SPACE chup, A tu dong, Q thoat)", view)
        if key == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    print(f"đã lưu {n} ảnh vào {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
