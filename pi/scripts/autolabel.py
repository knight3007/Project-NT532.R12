"""Tạo nhãn nháp cho ảnh thẻ bia tự chụp trên sa bàn, để mở công cụ gán nhãn sửa lại.

    uv run python scripts/autolabel.py --images data/raw/sa-ban --out data/dataset/sa-ban

Kết quả: <out>/images/{train,val}, <out>/labels/{train,val}, <out>/data.yaml (smoke, fire).
Chỉ ghi lớp fire (id 1). Ảnh không có phát hiện vẫn được giữ với file nhãn rỗng.
Nhãn do model đoán nên có thể sai: phải mở từng ảnh trong công cụ gán nhãn để sửa trước khi train.
"""

import argparse
import random
import shutil

import cv2
import yaml

from nt532.config import REPO_ROOT
from nt532.vision.detect import DEFAULT_WEIGHTS, detect
from nt532.vision.yololabel import yolo_label_lines

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--images", required=True, help="thư mục ảnh, tính từ gốc repo")
    p.add_argument("--out", required=True, help="thư mục dataset đầu ra, tính từ gốc repo")
    p.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    p.add_argument("--conf", type=float, default=0.25, help="thấp để ít bỏ sót, sửa tay sau")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--val", type=float, default=0.2, help="tỷ lệ ảnh vào tập val")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--device", default=None, help="mặc định để ultralytics tự chọn")
    args = p.parse_args()

    src = REPO_ROOT / args.images
    out = REPO_ROOT / args.out
    paths = sorted(f for f in src.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES)
    if not paths:
        raise SystemExit(f"không có ảnh trong {src}")
    random.Random(args.seed).shuffle(paths)
    n_val = round(len(paths) * args.val)

    counts = {"co": 0, "khong": 0}
    for i, path in enumerate(paths):
        split = "val" if i < n_val else "train"
        img = cv2.imread(str(path))
        if img is None:
            print(f"bỏ qua {path.name}: không đọc được")
            continue
        h, w = img.shape[:2]
        dets = detect(img, args.weights, args.conf, args.imgsz, args.device)
        lines = yolo_label_lines(dets, w, h)
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out / "images" / split / path.name)
        (out / "labels" / split / f"{path.stem}.txt").write_text(
            "".join(line + "\n" for line in lines), encoding="utf-8"
        )
        counts["co" if lines else "khong"] += 1

    data = {
        "path": out.as_posix(),
        "train": "images/train",
        "val": "images/val",
        "nc": 2,
        "names": ["smoke", "fire"],
    }
    (out / "data.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    print(f"{counts['co']} ảnh có nhãn, {counts['khong']} ảnh không có nhãn, val {n_val} ảnh")
    print(f"đã ghi {out.relative_to(REPO_ROOT)}")
    print("Nhãn chỉ là bản nháp: mở công cụ gán nhãn để sửa từng ảnh trước khi train.")


if __name__ == "__main__":
    main()
