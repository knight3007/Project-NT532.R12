"""Train YOLO phát hiện lửa. Mặc định: yolo11n trên D-Fire.

uv run python scripts/train_detect.py --epochs 50 --name dfire-n
"""

import argparse

from ultralytics import YOLO

from nt532.config import REPO_ROOT


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default=str(REPO_ROOT / "data/raw/dfire/data.yaml"))
    p.add_argument("--weights", default=str(REPO_ROOT / "models/yolo11n.pt"))
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=16)
    # Trên Windows mỗi worker chiếm khoảng 0,8 GB RAM; máy 16 GB không chịu nổi 8 worker.
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--fraction", type=float, default=1.0, help="tỷ lệ tập train dùng để chạy thử")
    p.add_argument("--name", default="dfire-n")
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()

    model = YOLO(args.weights)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        workers=args.workers,
        fraction=args.fraction,
        project=str(REPO_ROOT / "runs/detect"),
        name=args.name,
        exist_ok=args.resume,
        resume=args.resume,
        device=0,
    )


if __name__ == "__main__":
    main()
