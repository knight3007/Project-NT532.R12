"""Xem trực tiếp model phát hiện lửa trên webcam, video hoặc ảnh.

    uv run python scripts/live_detect.py                  # webcam trong site.yaml
    uv run python scripts/live_detect.py --source 1       # webcam khác
    uv run python scripts/live_detect.py --source clip.mp4 --smoke

Phím: Q thoát, S lưu khung hình hiện tại vào runs/live/.
"""

import argparse
import sys
import time

import cv2
from ultralytics import YOLO

from nt532.config import REPO_ROOT, load_site
from nt532.vision.detect import DEFAULT_WEIGHTS

COLORS = {"fire": (0, 80, 255), "smoke": (200, 200, 200)}


def open_source(source: str | None) -> cv2.VideoCapture:
    if source is None or source.isdigit():
        cam = load_site()["camera"]
        # Trên Windows backend mặc định (MSMF) hay không lấy được khung hình; DirectShow ổn định hơn.
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(int(source) if source else cam["index"], backend)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam["height"])
        return cap
    return cv2.VideoCapture(source)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=None, help="số thứ tự webcam, file video hoặc ảnh")
    p.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--smoke", action="store_true", help="hiện cả khói")
    args = p.parse_args()

    model = YOLO(args.weights)
    wanted = [i for i, n in model.names.items() if n == "fire" or (args.smoke and n == "smoke")]
    cap = open_source(args.source)
    if not cap.isOpened():
        raise SystemExit(f"không mở được nguồn {args.source}")
    out_dir = REPO_ROOT / "runs" / "live"
    fps = 0.0
    frames = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            if frames == 0:
                raise SystemExit(
                    f"mở được nguồn {args.source} nhưng không đọc được khung hình nào; "
                    "kiểm tra quyền camera của Windows hoặc thử --source khác"
                )
            break
        frames += 1
        t0 = time.perf_counter()
        result = model.predict(frame, conf=args.conf, classes=wanted, verbose=False)[0]
        dt = time.perf_counter() - t0
        fps = 0.9 * fps + 0.1 / dt if fps else 1 / dt

        for (x0, y0, x1, y1), c, k in zip(
            result.boxes.xyxy.int().tolist(),
            result.boxes.conf.tolist(),
            result.boxes.cls.int().tolist(),
        ):
            name = model.names[k]
            cv2.rectangle(frame, (x0, y0), (x1, y1), COLORS[name], 2)
            cv2.putText(
                frame,
                f"{name} {c:.2f}",
                (x0, max(y0 - 6, 14)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                COLORS[name],
                2,
            )
        cv2.putText(
            frame,
            f"{fps:5.1f} FPS  {dt * 1000:4.0f} ms",
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )
        cv2.imshow("NT532 - phat hien lua (Q thoat, S luu)", frame)

        single_image = cap.get(cv2.CAP_PROP_FRAME_COUNT) == 1
        key = cv2.waitKey(0 if single_image else 1) & 0xFF
        if key == ord("s"):
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.jpg"
            cv2.imwrite(str(path), frame)
            print(f"đã lưu {path.relative_to(REPO_ROOT)}")
        if key == ord("q") or single_image:
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
