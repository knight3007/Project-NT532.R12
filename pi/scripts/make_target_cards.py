"""Sinh PDF A4 thẻ bia in hình lửa (cắt từ D-Fire, CC0) để dán lên bảng bia của sa bàn.

    uv run python scripts/make_target_cards.py                    # 2 trang, thẻ 70 mm
    uv run python scripts/make_target_cards.py --size-mm 80 --pages 3

Chỉ giữ ảnh cắt mà model hiện tại báo lửa với độ tin cậy cao, mỗi ảnh gốc một thẻ, các thẻ lấy
từ ảnh khác nhau. In ở tỷ lệ 100%, cắt theo nét xám rồi ép plastic. Danh sách ảnh nguồn ghi vào
docs/print/target-cards.txt (để ghi nguồn trong báo cáo).
"""

import argparse
import random
import re
from pathlib import Path

import cv2
import numpy as np
import yaml
from make_print_sheets import A4_MM, DPI, OUT_DIR, draw_ruler, px, text
from PIL import Image

from nt532.config import REPO_ROOT
from nt532.vision.detect import DEFAULT_WEIGHTS, detect

DFIRE = REPO_ROOT / "data" / "raw" / "dfire"
MARGIN_MM = 3  # viền trắng quanh thẻ, nét cắt nằm ở mép ngoài viền
MIN_CROP_PX = 224
SEQ_GAP = 30  # hai khung cùng chuỗi video cách nhau ít hơn thế thì coi là trùng cảnh


def fire_class_id() -> int:
    names = yaml.safe_load((DFIRE / "data.yaml").read_text(encoding="utf-8"))["names"]
    names = dict(enumerate(names)) if isinstance(names, list) else names
    return next(i for i, n in names.items() if n == "fire")


def largest_fire_box(label_path: Path, fire: int) -> tuple[float, float, float, float] | None:
    """Hộp lửa lớn nhất trong file nhãn YOLO, dạng (cx, cy, w, h) chuẩn hóa."""
    boxes = []
    for line in label_path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 5 and int(parts[0]) == fire:
            boxes.append(tuple(float(v) for v in parts[1:]))
    return max(boxes, key=lambda b: b[2] * b[3]) if boxes else None


def square_crop(
    shape: tuple[int, int], box: tuple[float, float, float, float], context: float
) -> tuple[int, int, int] | None:
    """Vùng cắt vuông (x0, y0, cạnh) quanh hộp, nới `context` lần; None nếu không đủ lớn."""
    h, w = shape
    cx, cy, bw, bh = box[0] * w, box[1] * h, box[2] * w, box[3] * h
    side = round(max(bw, bh) * context)
    side = min(side, h, w)
    if side < MIN_CROP_PX or side < max(bw, bh):
        return None
    x0 = min(max(round(cx - side / 2), 0), w - side)
    y0 = min(max(round(cy - side / 2), 0), h - side)
    return x0, y0, side


def seq_key(stem: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"(.*?)(\d+)", stem)
    return (m.group(1), int(m.group(2))) if m else None


def pick_cards(n: int, weights: Path, min_conf: float, context: float, seed: int) -> list[dict]:
    """Duyệt ngẫu nhiên ảnh D-Fire có lửa, giữ ảnh cắt mà model báo lửa >= min_conf."""
    fire = fire_class_id()
    candidates = []
    for split in ("test", "val", "train"):
        for lab in sorted((DFIRE / split / "labels").glob("*.txt")):
            box = largest_fire_box(lab, fire)
            if box:
                candidates.append((split, lab.stem, box))
    random.Random(seed).shuffle(candidates)
    print(f"{len(candidates)} ảnh D-Fire có lửa; cần {n} thẻ")

    cards: list[dict] = []
    seen: list[tuple[str, int]] = []
    for split, stem, box in candidates:
        key = seq_key(stem)
        if key and any(k[0] == key[0] and abs(k[1] - key[1]) < SEQ_GAP for k in seen):
            continue
        path = next((DFIRE / split / "images").glob(f"{stem}.*"), None)
        img = cv2.imread(str(path)) if path else None
        if img is None:
            continue
        crop = square_crop(img.shape[:2], box, context)
        if crop is None:
            continue
        x0, y0, side = crop
        patch = img[y0 : y0 + side, x0 : x0 + side]
        found = detect(patch, weights, conf=0.25, device="cpu")
        if not found or found[0].conf < min_conf:
            continue
        cards.append(
            {"split": split, "file": path.name, "crop": (x0, y0, side), "conf": found[0].conf}
            | {"image": patch}
        )
        if key:
            seen.append(key)
        if len(cards) == n:
            break
    return cards


def layout(size_mm: float) -> tuple[int, int, float, float]:
    """Số cột, số hàng, bước ngang và bước dọc (mm) của lưới thẻ trên một trang A4."""
    cell = size_mm + 2 * MARGIN_MM
    cols = int((A4_MM[0] - 10) // cell)
    rows = int((A4_MM[1] - 10 - 22) // (cell + 6))
    if cols < 1 or rows < 1:
        raise SystemExit(f"thẻ {size_mm:g} mm quá lớn so với trang A4")
    return cols, rows, (A4_MM[0] - cols * cell) / (cols + 1) + cell, cell + 6


def render_page(cards: list[dict], first_no: int, size_mm: float) -> np.ndarray:
    cols, _, pitch_x, pitch_y = layout(size_mm)
    page = np.full((px(A4_MM[1]), px(A4_MM[0]), 3), 255, np.uint8)
    cell = size_mm + 2 * MARGIN_MM
    gap = pitch_x - cell
    side = px(size_mm)
    for i, card in enumerate(cards):
        row, col = divmod(i, cols)
        x = gap + col * pitch_x
        y = 10 + row * pitch_y
        patch = cv2.resize(card["image"], (side, side), interpolation=cv2.INTER_CUBIC)
        tx, ty = px(x + MARGIN_MM), px(y + MARGIN_MM)
        page[ty : ty + side, tx : tx + side] = patch
        cv2.rectangle(page, (px(x), px(y)), (px(x + cell), px(y + cell)), (160, 160, 160), px(0.15))
        text(page, f"the {first_no + i:02d}  {size_mm:g} mm", px(x), px(y + cell + 4), 3.0)
    draw_ruler(page)
    return page


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--size-mm", type=float, default=70, help="cạnh thẻ")
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    p.add_argument("--min-conf", type=float, default=0.7)
    p.add_argument("--context", type=float, default=1.3, help="nới vùng cắt so với hộp lửa")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    per_page = layout(args.size_mm)[0] * layout(args.size_mm)[1]
    cards = pick_cards(
        per_page * args.pages, Path(args.weights), args.min_conf, args.context, args.seed
    )
    if not cards:
        raise SystemExit("không chọn được ảnh nào; hạ --min-conf thử")
    pages = [
        render_page(cards[i : i + per_page], i + 1, args.size_mm)
        for i in range(0, len(cards), per_page)
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf = OUT_DIR / "target-cards.pdf"
    images = [Image.fromarray(cv2.cvtColor(pg, cv2.COLOR_BGR2RGB)) for pg in pages]
    images[0].save(pdf, save_all=True, append_images=images[1:], resolution=DPI, quality=95)

    lines = [
        "Thẻ bia in hình lửa, cắt từ ảnh D-Fire (CC0), bản chia sẵn của sayedgamal99 trên Kaggle.",
        f"Cạnh thẻ {args.size_mm:g} mm, chỉ giữ ảnh cắt model báo lửa với conf >= {args.min_conf}.",
        "Cột: số thẻ, ảnh nguồn (tập/tên), vùng cắt x0,y0,cạnh (px), conf của model.",
        "",
    ]
    for i, c in enumerate(cards, 1):
        x0, y0, s = c["crop"]
        lines.append(f"{i:02d}\t{c['split']}/{c['file']}\t{x0},{y0},{s}\t{c['conf']:.2f}")
    (OUT_DIR / "target-cards.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(cards)} thẻ trên {len(pages)} trang: {pdf.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
