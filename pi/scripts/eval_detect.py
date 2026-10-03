"""Đánh giá trọng số YOLO trên một phần của dataset.

uv run python scripts/eval_detect.py --split test
"""

import argparse

from ultralytics import YOLO

from nt532.config import REPO_ROOT
from nt532.vision.detect import DEFAULT_WEIGHTS


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default=str(REPO_ROOT / "data/raw/dfire/data.yaml"))
    p.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    p.add_argument("--split", default="test", choices=["val", "test"])
    p.add_argument("--workers", type=int, default=2)
    args = p.parse_args()

    metrics = YOLO(args.weights).val(
        data=args.data,
        split=args.split,
        workers=args.workers,
        project=str(REPO_ROOT / "runs/detect"),
        name=f"eval-{args.split}",
        exist_ok=True,
        plots=False,
    )
    names = metrics.names
    print(f"{'lớp':8}{'P':>7}{'R':>7}{'mAP50':>8}{'mAP50-95':>10}")
    p_, r_, m50, m = metrics.box.mean_results()
    print(f"{'tất cả':8}{p_:7.3f}{r_:7.3f}{m50:8.3f}{m:10.3f}")
    for i, c in enumerate(metrics.box.ap_class_index):
        p_, r_, m50, m = metrics.box.class_result(i)
        print(f"{names[c]:8}{p_:7.3f}{r_:7.3f}{m50:8.3f}{m:10.3f}")


if __name__ == "__main__":
    main()
