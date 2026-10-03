"""Trích ảnh không có lửa từ các bộ tải về, làm mẫu âm tính cho YOLO.

    uv run python scripts/prepare_negatives.py

- COCO val2017: giữ ảnh có đồ vật trong nhà, bỏ ảnh mà chú thích nhắc tới nến, lửa, lò sưởi...
- Fire Recognition Image Dataset (Mendeley): lớp Artificial Fire (đèn, lửa giả).

Kết quả: data/raw/negatives/<nguồn>/*.jpg. Ảnh tự chụp trong phòng đặt cùng chỗ, ví dụ
data/raw/negatives/phong/, sẽ được prepare_fire_mix.py dùng như nhau.
"""

import json
import re
import shutil
import zipfile

from nt532.config import REPO_ROOT

DOWNLOADS = REPO_ROOT / "data/downloads"
OUT = REPO_ROOT / "data/raw/negatives"
INDOOR = {
    "bed",
    "couch",
    "dining table",
    "tv",
    "oven",
    "microwave",
    "refrigerator",
    "sink",
    "toilet",
    "toaster",
}
FIRE_WORDS = re.compile(
    r"candle|fire|flame|burn|smok|torch|lantern|blaze|ember|match|lighter|grill|bbq|barbecue"
)


def coco_indoor() -> int:
    with zipfile.ZipFile(DOWNLOADS / "coco-annotations_trainval2017.zip") as z:
        instances = json.loads(z.read("annotations/instances_val2017.json"))
        captions = json.loads(z.read("annotations/captions_val2017.json"))
    indoor_ids = {c["id"] for c in instances["categories"] if c["name"] in INDOOR}
    has_indoor = {a["image_id"] for a in instances["annotations"] if a["category_id"] in indoor_ids}
    firey = {
        c["image_id"] for c in captions["annotations"] if FIRE_WORDS.search(c["caption"].lower())
    }
    keep = {i["file_name"] for i in instances["images"] if i["id"] in has_indoor - firey}

    out = OUT / "coco-indoor"
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(DOWNLOADS / "coco-val2017.zip") as z:
        for name in keep:
            with z.open(f"val2017/{name}") as src, open(out / name, "wb") as dst:
                shutil.copyfileobj(src, dst)
    print(
        f"coco-indoor: {len(keep)} ảnh (bỏ {len(has_indoor & firey)} ảnh có chú thích liên quan lửa)"
    )
    return len(keep)


def artificial_fire() -> int:
    out = OUT / "artificial-fire"
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(DOWNLOADS / "fire-recognition-original.zip") as z:
        for info in z.infolist():
            parts = info.filename.lower().split("/")
            if info.is_dir() or not any("artificial" in p for p in parts[:-1]):
                continue
            if not parts[-1].endswith((".jpg", ".jpeg", ".png")):
                continue
            with z.open(info) as src, open(out / f"af_{n:04d}_{parts[-1]}", "wb") as dst:
                shutil.copyfileobj(src, dst)
            n += 1
    print(f"artificial-fire: {n} ảnh")
    return n


def main() -> None:
    coco_indoor()
    artificial_fire()


if __name__ == "__main__":
    main()
