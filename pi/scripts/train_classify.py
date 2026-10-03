"""Train YOLO phân loại lớp đám cháy theo chất liệu trên ClassesOfFire, rồi đánh giá trên tập test.

uv run python scripts/train_classify.py --epochs 40 --name cof26-n-cls

Lớp: A chất rắn, B chất lỏng và khí, C điện, D kim loại, F dầu ăn, none không cháy.
Chạy scripts/prepare_classesoffire.py trước để có data/dataset/classesoffire/.
"""

import argparse

import numpy as np
from ultralytics import YOLO

from nt532.config import REPO_ROOT

DATA = REPO_ROOT / "data/dataset/classesoffire"


def report(weights: str, imgsz: int) -> None:
    """Ma trận nhầm lẫn, precision và recall từng lớp trên tập test."""
    model = YOLO(weights)
    names = model.names
    index = {n: i for i, n in names.items()}
    k = len(names)
    confusion = np.zeros((k, k), int)
    for cls_dir in sorted((DATA / "test").iterdir()):
        files = [str(f) for f in cls_dir.iterdir()]
        for i in range(0, len(files), 64):
            for r in model.predict(files[i : i + 64], imgsz=imgsz, verbose=False):
                confusion[index[cls_dir.name], r.probs.top1] += 1
    print("hàng: nhãn thật, cột: dự đoán")
    print("      " + "".join(f"{names[j]:>6}" for j in range(k)))
    for i in range(k):
        print(f"{names[i]:>6}" + "".join(f"{confusion[i, j]:6d}" for j in range(k)))
    print(f"\n{'lớp':6}{'số ảnh':>8}{'P':>7}{'R':>7}")
    for i in range(k):
        p = confusion[i, i] / max(confusion[:, i].sum(), 1)
        r = confusion[i, i] / max(confusion[i].sum(), 1)
        print(f"{names[i]:6}{confusion[i].sum():8d}{p:7.3f}{r:7.3f}")
    print(f"độ chính xác chung {np.trace(confusion) / confusion.sum():.3f}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--weights", default=str(REPO_ROOT / "models/yolo26n-cls.pt"))
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--imgsz", type=int, default=224)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--name", default="cof26-n-cls")
    p.add_argument("--eval-only", default=None, help="chỉ đánh giá trọng số này trên tập test")
    args = p.parse_args()

    if args.eval_only:
        report(args.eval_only, args.imgsz)
        return
    model = YOLO(args.weights)
    model.train(
        data=str(DATA),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        workers=args.workers,
        project=str(REPO_ROOT / "runs/classify"),
        name=args.name,
        device=0,
    )
    report(str(REPO_ROOT / "runs/classify" / args.name / "weights/best.pt"), args.imgsz)


if __name__ == "__main__":
    main()
