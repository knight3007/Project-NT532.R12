"""Đo tỷ lệ báo nhầm: phần trăm ảnh không có lửa mà model vẫn thấy lửa.

    uv run python scripts/eval_false_alarms.py
    uv run python scripts/eval_false_alarms.py --weights ../runs/detect/x/weights/best.pt

Mặc định chạy trên phần test của mẫu âm tính trong data/dataset/fire-mix/negatives/.
"""

import argparse
from pathlib import Path

from ultralytics import YOLO

from nt532.config import REPO_ROOT
from nt532.vision.detect import DEFAULT_WEIGHTS

THRESHOLDS = (0.25, 0.35, 0.5, 0.7)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    p.add_argument("--split", default="test")
    args = p.parse_args()

    model = YOLO(args.weights)
    fire = next(i for i, n in model.names.items() if n == "fire")
    root = REPO_ROOT / "data/dataset/fire-mix/negatives"
    print(f"{'nguồn':16}{'ảnh':>6}" + "".join(f"{f'conf>={t}':>11}" for t in THRESHOLDS))
    for source in sorted(d for d in root.iterdir() if d.is_dir()):
        images = sorted(Path(source / args.split / "images").iterdir())
        best = []
        for start in range(0, len(images), 32):
            for r in model.predict(images[start : start + 32], conf=min(THRESHOLDS), verbose=False):
                scores = [
                    c for c, k in zip(r.boxes.conf.tolist(), r.boxes.cls.tolist()) if k == fire
                ]
                best.append(max(scores, default=0.0))
        rates = "".join(f"{sum(b >= t for b in best) / len(best):11.1%}" for t in THRESHOLDS)
        print(f"{source.name:16}{len(best):6}{rates}")


if __name__ == "__main__":
    main()
