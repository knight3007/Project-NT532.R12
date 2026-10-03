"""Lọc và chia bộ ClassesOfFire thành train/val/test cho bài phân loại lớp đám cháy.

    uv run python scripts/prepare_classesoffire.py

Đọc data/raw/classesoffire/<lớp>/, bỏ ảnh hỏng và ảnh trùng nội dung, rồi chia theo từng lớp
vào data/dataset/classesoffire/{train,val,test}/<lớp>/. Ảnh trùng nằm ở hai lớp khác nhau thì
bỏ cả hai vì không biết nhãn nào đúng.
"""

import hashlib
import random
import shutil
from collections import defaultdict
from pathlib import Path

from PIL import Image

from nt532.config import REPO_ROOT

SRC = REPO_ROOT / "data/raw/classesoffire"
DST = REPO_ROOT / "data/dataset/classesoffire"
CLASSES = {
    "Class A": "A",
    "Class B": "B",
    "Class C": "C",
    "Class D": "D",
    "Class F": "F",
    "No Fire": "none",
}
SPLITS = (("train", 0.70), ("val", 0.15), ("test", 0.15))
SEED = 0


def is_readable(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except (OSError, SyntaxError):
        return False


def main() -> None:
    by_hash: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    broken = 0
    for folder, name in CLASSES.items():
        for path in sorted((SRC / folder).iterdir()):
            if not is_readable(path):
                broken += 1
                continue
            by_hash[hashlib.md5(path.read_bytes()).hexdigest()].append((name, path))

    kept: dict[str, list[Path]] = defaultdict(list)
    duplicates = conflicts = 0
    for items in by_hash.values():
        if len({name for name, _ in items}) > 1:
            conflicts += len(items)
            continue
        duplicates += len(items) - 1
        kept[items[0][0]].append(items[0][1])

    if DST.exists():
        shutil.rmtree(DST)
    rng = random.Random(SEED)
    print(f"{'lớp':6}{'train':>7}{'val':>7}{'test':>7}")
    for name in CLASSES.values():
        paths = sorted(kept[name])
        rng.shuffle(paths)
        n_val, n_test = round(len(paths) * SPLITS[1][1]), round(len(paths) * SPLITS[2][1])
        parts = {
            "train": paths[n_val + n_test :],
            "val": paths[:n_val],
            "test": paths[n_val : n_val + n_test],
        }
        for split, items in parts.items():
            out = DST / split / name
            out.mkdir(parents=True)
            for i, path in enumerate(items):
                shutil.copy2(path, out / f"{name}_{i:04d}{path.suffix.lower()}")
        print(f"{name:6}{len(parts['train']):7}{len(parts['val']):7}{len(parts['test']):7}")
    print(f"bỏ: {broken} ảnh hỏng, {duplicates} ảnh trùng, {conflicts} ảnh trùng khác lớp")


if __name__ == "__main__":
    main()
