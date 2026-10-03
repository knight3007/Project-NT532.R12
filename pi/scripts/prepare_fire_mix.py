"""Gộp D-Fire với hai bộ lửa trong nhà về cùng thứ tự lớp của D-Fire (0 = smoke, 1 = fire).

    uv run python scripts/prepare_fire_mix.py

Home-fire và Indoor Fire Smoke dùng 0 = fire, 1 = smoke nên nhãn được viết lại; ảnh được tạo
hard link thay vì chép, không tốn thêm dung lượng. D-Fire đã đúng thứ tự nên dùng thẳng thư mục
gốc. Kết quả ở data/dataset/fire-mix/: data.yaml để train, và một file yaml cho từng bộ để
đánh giá riêng.
"""

import os
import shutil
from pathlib import Path

import yaml

from nt532.config import REPO_ROOT

RAW = REPO_ROOT / "data/raw"
DST = REPO_ROOT / "data/dataset/fire-mix"
NAMES = ["smoke", "fire"]
SWAP = {"0": "1", "1": "0"}

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

    def write(path: Path, data: dict) -> None:
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), "utf-8")

    def as_str(paths) -> list[str]:
        return [p.as_posix() for p in paths]

    indoor = ["home-fire", "indoor-fire-smoke"]
    write(
        DST / "data.yaml",
        {
            # Train trên cả ba bộ; chọn model theo val của hai bộ trong nhà vì đó là bối cảnh demo.
            "train": as_str(dirs[n]["train"] for n in SOURCES),
            "val": as_str(dirs[n]["val"] for n in indoor),
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
