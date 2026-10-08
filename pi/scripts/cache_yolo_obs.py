"""Chạy detector lửa một lần trên fire-mix và negatives rồi lưu kết quả để sinh kịch bản.

    uv run python scripts/cache_yolo_obs.py                          # fire-n.pt
    uv run python scripts/cache_yolo_obs.py --weights ../models/mix26-neg-20261003.pt
    uv run python scripts/cache_yolo_obs.py --device cpu --batch 8   # khi GPU đang bận

Kết quả: runs/decider/yolo_obs_<tên weights>.jsonl, mỗi dòng một ảnh:
{"src", "split", "name", "has_fire", "dets": [{"c": conf, "w": rộng, "h": cao, "hit": bool}]}
w, h chuẩn hóa theo cỡ ảnh; hit = ô dự đoán trùng ô lửa gán nhãn (IoU >= 0.3). Chạy ở conf thấp
(0.05) để về sau lọc bằng ngưỡng bất kỳ. Kịch bản của split X chỉ dùng ảnh của split X.
"""

import argparse
import json
from pathlib import Path

from ultralytics import YOLO

from nt532.config import REPO_ROOT

MIX = REPO_ROOT / "data/dataset/fire-mix"
OUT = REPO_ROOT / "runs/decider"
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLITS = ("train", "val", "test")


def sources() -> list[Path]:
    neg = sorted(d for d in (MIX / "negatives").iterdir() if d.is_dir())
    return [MIX / "home-fire", MIX / "indoor-fire-smoke", *neg]


def gt_fire_boxes(label: Path) -> list[tuple[float, float, float, float]]:
    """Ô lửa (lớp 1) dạng xyxy chuẩn hóa."""
    if not label.exists():
        return []
    boxes = []
    for line in label.read_text().splitlines():
        p = line.split()
        if len(p) >= 5 and p[0] == "1":
            x, y, w, h = map(float, p[1:5])
            boxes.append((x - w / 2, y - h / 2, x + w / 2, y + h / 2))
    return boxes


def iou(a, b) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--weights", default=str(REPO_ROOT / "models/fire-n.pt"))
    p.add_argument("--conf", type=float, default=0.05)
    p.add_argument("--batch", type=int, default=16)
    p.add_argument("--device", default=None)
    p.add_argument("--out", default=None, help="mặc định runs/decider/yolo_obs_<tên weights>.jsonl")
    p.add_argument("--splits", nargs="+", default=list(SPLITS))
    p.add_argument("--max-train", type=int, default=0,
                   help="giới hạn ảnh mỗi nguồn ở train (val/test: một phần ba), lấy cách đều")
    args = p.parse_args()

    model = YOLO(args.weights)
    fire = next(i for i, n in model.names.items() if n == "fire")
    out = Path(args.out) if args.out else OUT / f"yolo_obs_{Path(args.weights).stem}.jsonl"
    OUT.mkdir(parents=True, exist_ok=True)
    n_img = 0
    with out.open("w", encoding="utf-8") as f:
        for src in sources():
            for split in args.splits:
                imgs = sorted(
                    x for x in (src / split / "images").iterdir() if x.suffix.lower() in IMG_EXT
                )
                cap = args.max_train // (1 if split == "train" else 3)
                if cap and len(imgs) > cap:
                    imgs = imgs[:: -(-len(imgs) // cap)]
                for s in range(0, len(imgs), args.batch):
                    chunk = imgs[s : s + args.batch]
                    results = model.predict(
                        chunk, conf=args.conf, classes=[fire], device=args.device, verbose=False
                    )
                    for path, r in zip(chunk, results):
                        gts = gt_fire_boxes(src / split / "labels" / (path.stem + ".txt"))
                        dets = []
                        for xyxy, wh, c in zip(
                            r.boxes.xyxyn.tolist(), r.boxes.xywhn.tolist(), r.boxes.conf.tolist()
                        ):
                            hit = any(iou(xyxy, g) >= 0.3 for g in gts)
                            dets.append(
                                {"c": round(c, 3), "w": round(wh[2], 3), "h": round(wh[3], 3),
                                 "hit": hit}
                            )
                        rec = {"src": src.name, "split": split, "name": path.name,
                               "has_fire": bool(gts), "dets": dets}
                        f.write(json.dumps(rec) + "\n")
                    f.flush()
                n_img += len(imgs)
                print(f"{src.name:20}{split:6}{len(imgs):6} ảnh")
    print(f"Đã ghi {n_img} ảnh vào {out}")


if __name__ == "__main__":
    main()
