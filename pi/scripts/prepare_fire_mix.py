"""Gộp D-Fire với hai bộ lửa trong nhà về cùng thứ tự lớp của D-Fire (0 = smoke, 1 = fire).

    uv run python scripts/prepare_fire_mix.py

Home-fire và Indoor Fire Smoke dùng 0 = fire, 1 = smoke nên nhãn được viết lại; ảnh được tạo
hard link thay vì chép, không tốn thêm dung lượng. D-Fire đã đúng thứ tự nên dùng thẳng thư mục
gốc. Kết quả ở data/dataset/fire-mix/: data.yaml để train, và một file yaml cho từng bộ để
đánh giá riêng.

Ảnh không có lửa trong data/raw/negatives/<nguồn>/ (xem prepare_negatives.py) được chia
70/15/15 theo tên file và thêm vào train và val dưới dạng ảnh không nhãn. Tập test giữ nguyên
chỉ gồm hai bộ trong nhà để so sánh được với các lần đo trước; báo nhầm trên phần test của
mẫu âm tính đo riêng bằng eval_false_alarms.py.
"""

import hashlib
import os
import shutil
from pathlib import Path

import yaml

from nt532.config import REPO_ROOT

RAW = REPO_ROOT / "data/raw"
DST = REPO_ROOT / "data/dataset/fire-mix"
NAMES = ["smoke", "fire"]
SWAP = {"0": "1", "1": "0"}
SPLITS = ("train", "val", "test")

# nguồn: (thư mục gốc, {phần chuẩn: tên thư mục phần trong bộ gốc}, cần đổi lớp)
SOURCES = {
    "home-fire": (RAW / "home-fire", {"train": "train", "val": "val", "test": "test"}, True),
    "indoor-fire-smoke": (
        RAW / "indoor-fire-smoke",
        {"train": "train", "val": "valid", "test": "test"},
        True,
    ),
    "dfire": (RAW / "dfire", {"train": "train", "val": "val", "test": "test"}, False),
}


def remap(src: Path, dst: Path) -> None:
    """Hard link ảnh, viết lại nhãn với chỉ số lớp đã đổi."""
    (dst / "images").mkdir(parents=True)
    (dst / "labels").mkdir(parents=True)
    for image in (src / "images").iterdir():
        os.link(image, dst / "images" / image.name)
    for label in (src / "labels").iterdir():
        lines = []
        for line in label.read_text().splitlines():
            parts = line.split()
            if parts:
                lines.append(" ".join([SWAP[parts[0]], *parts[1:]]))
        (dst / "labels" / label.name).write_text("\n".join(lines) + ("\n" if lines else ""))


def split_of(name: str) -> str:
    """Chia cố định theo tên file, nên thêm ảnh mới không làm xáo trộn các ảnh đã chia."""
    bucket = int(hashlib.md5(name.encode()).hexdigest(), 16) % 100
    return "train" if bucket < 70 else "val" if bucket < 85 else "test"


def link_negatives() -> dict[str, dict[str, Path]]:
    """Hard link ảnh âm tính vào negatives/<nguồn>/<phần>/images; không cần file nhãn."""
    dirs: dict[str, dict[str, Path]] = {}
    root = RAW / "negatives"
    for source in sorted(d for d in root.iterdir() if d.is_dir()) if root.exists() else []:
        dirs[source.name] = {s: DST / "negatives" / source.name / s / "images" for s in SPLITS}
        for d in dirs[source.name].values():
            d.mkdir(parents=True)
        counts = dict.fromkeys(SPLITS, 0)
        for image in source.iterdir():
            if image.suffix.lower() in (".jpg", ".jpeg", ".png"):
                split = split_of(image.name)
                os.link(image, dirs[source.name][split] / image.name)
                counts[split] += 1
        print(f"negatives/{source.name}: {counts}")
    return dirs


def main() -> None:
    if DST.exists():
        shutil.rmtree(DST)
    dirs: dict[str, dict[str, Path]] = {}
    for name, (root, splits, swap) in SOURCES.items():
        dirs[name] = {}
        for split, folder in splits.items():
            if swap:
                remap(root / folder, DST / name / split)
                dirs[name][split] = DST / name / split / "images"
            else:
                dirs[name][split] = root / folder / "images"
        print(f"{name}: xong")
    negatives = link_negatives()

    def write(path: Path, data: dict) -> None:
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), "utf-8")

    def as_str(paths) -> list[str]:
        return [p.as_posix() for p in paths]

    indoor = ["home-fire", "indoor-fire-smoke"]
    write(
        DST / "data.yaml",
        {
            # Train trên cả ba bộ; chọn model theo val của hai bộ trong nhà vì đó là bối cảnh demo.
            "train": as_str([dirs[n]["train"] for n in SOURCES])
            + as_str(d["train"] for d in negatives.values()),
            "val": as_str([dirs[n]["val"] for n in indoor])
            + as_str(d["val"] for d in negatives.values()),
            "test": as_str(dirs[n]["test"] for n in indoor),
            "nc": 2,
            "names": NAMES,
        },
    )
    for name in SOURCES:
        write(
            DST / f"{name}.yaml",
            {
                "train": dirs[name]["train"].as_posix(),
                "val": dirs[name]["val"].as_posix(),
                "test": dirs[name]["test"].as_posix(),
                "nc": 2,
                "names": NAMES,
            },
        )
    print(f"đã ghi {(DST / 'data.yaml').relative_to(REPO_ROOT)} và yaml riêng cho từng bộ")


if __name__ == "__main__":
    main()
