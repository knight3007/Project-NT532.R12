"""Sinh thẻ bia in hình lửa (cắt từ D-Fire, CC0) để dán lên bảng bia, kèm bộ thẻ train cho sa bàn ảo.

    uv run --no-sync python scripts/make_target_cards.py          # 12 thẻ in 100 mm + bộ thẻ train
    uv run --no-sync python scripts/make_target_cards.py --no-pdf # chỉ PNG, không ghi PDF

Hai bộ tách biệt: không trùng ảnh gốc, không cùng chuỗi video (SEQ_GAP).
  - Bộ in (mặc định 12 thẻ), lấy từ D-Fire test và val (model chưa từng train trên các ảnh này), tiêu
    chí chặt: ban ngày, ngọn lửa sáng chiếm >= 60% khung, ít pixel đen, model báo lửa conf >= 0,8.
    PNG vào data/sim/cards/, PDF vào docs/print/target-cards.pdf, nguồn vào target-cards.txt.
    In ở tỷ lệ 100%, cắt theo nét xám rồi ép plastic. Bộ này chỉ để ĐÁNH GIÁ, không đưa vào train.
  - Bộ train (mặc định 240 thẻ), lấy từ D-Fire train, tiêu chí nới hơn để đa dạng. PNG vào
    data/sim/cards-train/, nguồn vào sources.txt cùng thư mục. Chỉ dùng để sinh dữ liệu train sa bàn.
"""

import argparse
import random
import re
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Criteria:
    """Tiêu chí chọn ảnh cắt: lửa sáng, chiếm gần hết khung, ban ngày, model báo lửa conf cao."""

    context: float  # nới vùng cắt so với hộp lửa (nhỏ = ít ngữ cảnh)
    min_fill: float  # diện tích hộp lửa / diện tích vùng cắt tối thiểu
    min_gray: float  # độ sáng xám trung bình tối thiểu (0-255)
    max_dark: float  # tỷ lệ pixel xám < DARK_LEVEL tối đa (loại cảnh đêm nền đen)
    min_conf: float  # conf của model hiện tại trên vùng cắt


PRINT = Criteria(context=1.12, min_fill=0.60, min_gray=95, max_dark=0.12, min_conf=0.80)
TRAIN = Criteria(context=1.25, min_fill=0.40, min_gray=70, max_dark=0.30, min_conf=0.50)
DARK_LEVEL = 35


def candidates_for(splits: tuple[str, ...], seed: int) -> list[tuple[str, str, tuple]]:
    fire = fire_class_id()
    out = []
    for split in splits:
        for lab in sorted((DFIRE / split / "labels").glob("*.txt")):
            box = largest_fire_box(lab, fire)
            if box:
                out.append((split, lab.stem, box))
    random.Random(seed).shuffle(out)
    return out


def pick_cards(
    n: int,
    weights: Path,
    crit: Criteria,
    splits: tuple[str, ...],
    seed: int,
    seen: list[tuple[str, int]],
    used_files: set[str],
) -> list[dict]:
    """Duyệt ngẫu nhiên ảnh D-Fire có lửa, giữ ảnh cắt đạt `crit`.

    `seen` (khóa chuỗi video) và `used_files` được cập nhật tại chỗ, để bộ thẻ chọn sau tránh ảnh
    và chuỗi video của bộ chọn trước.
    """
    candidates = candidates_for(splits, seed)
    print(f"{len(candidates)} ảnh D-Fire có lửa ({'+'.join(splits)}); cần {n} thẻ")

    cards: list[dict] = []
    for split, stem, box in candidates:
        key = seq_key(stem)
        if key and any(k[0] == key[0] and abs(k[1] - key[1]) < SEQ_GAP for k in seen):
            continue
        path = next((DFIRE / split / "images").glob(f"{stem}.*"), None)
        if path is None or path.name in used_files or box[2] * box[3] < 0.01:
            continue
        img = cv2.imread(str(path))
        if img is None:
            continue
        crop = square_crop(img.shape[:2], box, crit.context)
        if crop is None:
            continue
        x0, y0, side = crop
        patch = img[y0 : y0 + side, x0 : x0 + side]
        h, w = img.shape[:2]
        if box[2] * w * box[3] * h / side**2 < crit.min_fill:
            continue
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        if gray.mean() < crit.min_gray or (gray < DARK_LEVEL).mean() > crit.max_dark:
            continue
        found = detect(patch, weights, conf=0.25)
        if not found or found[0].conf < crit.min_conf:
            continue
        cards.append(
            {"split": split, "file": path.name, "crop": (x0, y0, side), "conf": found[0].conf}
            | {"image": patch, "gray": float(gray.mean())}
        )
        used_files.add(path.name)
        if key:
            seen.append(key)
        if len(cards) == n:
            break
    return cards


def write_pngs(cards: list[dict], png_dir: Path) -> None:
    png_dir.mkdir(parents=True, exist_ok=True)
    for old in png_dir.glob("card_*.png"):
        old.unlink()
    for i, card in enumerate(cards, 1):
        patch = cv2.resize(card["image"], (256, 256), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(png_dir / f"card_{i:03d}.png"), patch)


def source_lines(cards: list[dict]) -> list[str]:
    lines = []
    for i, c in enumerate(cards, 1):
        x0, y0, s = c["crop"]
        lines.append(
            f"{i:03d}\t{c['split']}/{c['file']}\t{x0},{y0},{s}\t{c['conf']:.2f}\t{c['gray']:.0f}"
        )
    return lines


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
    p.add_argument("--size-mm", type=float, default=100, help="cạnh thẻ in")
    p.add_argument("--count", type=int, default=12, help="số thẻ in")
    p.add_argument("--train-count", type=int, default=240, help="số thẻ train (0 = bỏ bộ train)")
    p.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--png-dir", default=str(REPO_ROOT / "data" / "sim" / "cards"))
    p.add_argument("--train-dir", default=str(REPO_ROOT / "data" / "sim" / "cards-train"))
    p.add_argument("--no-pdf", action="store_true", help="chỉ lưu PNG, không ghi PDF và danh sách")
    args = p.parse_args()
    weights = Path(args.weights)

    seen: list[tuple[str, int]] = []
    used: set[str] = set()
    cards = pick_cards(args.count, weights, PRINT, ("test", "val"), args.seed, seen, used)
    if len(cards) < args.count:
        raise SystemExit(f"chỉ chọn được {len(cards)}/{args.count} thẻ in; nới PRINT")
    write_pngs(cards, Path(args.png_dir))
    print(f"{len(cards)} thẻ in PNG: {args.png_dir}")

    if args.train_count:
        train = pick_cards(args.train_count, weights, TRAIN, ("train",), args.seed, seen, used)
        write_pngs(train, Path(args.train_dir))
        head = [
            "Thẻ train cho sa bàn ảo, cắt từ D-Fire train. KHÔNG dùng để in hay đánh giá.",
            "Cột: số thẻ, ảnh nguồn, vùng cắt x0,y0,cạnh (px), conf của model, độ sáng xám TB.",
            "",
        ]
        (Path(args.train_dir) / "sources.txt").write_text(
            "\n".join(head + source_lines(train)) + "\n", encoding="utf-8"
        )
        print(f"{len(train)} thẻ train PNG: {args.train_dir}")
    if args.no_pdf:
        return

    per_page = layout(args.size_mm)[0] * layout(args.size_mm)[1]
    pages = [
        render_page(cards[i : i + per_page], i + 1, args.size_mm)
        for i in range(0, len(cards), per_page)
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf = OUT_DIR / "target-cards.pdf"
    images = [Image.fromarray(cv2.cvtColor(pg, cv2.COLOR_BGR2RGB)) for pg in pages]
    images[0].save(pdf, save_all=True, append_images=images[1:], resolution=DPI, quality=95)

    splits = sorted({c["split"] for c in cards})
    lines = [
        "Thẻ bia in hình lửa, cắt từ ảnh D-Fire (CC0), bản chia sẵn của sayedgamal99 trên Kaggle.",
        f"Cạnh thẻ {args.size_mm:g} mm, mỗi thẻ có viền trắng {MARGIN_MM} mm quanh phần ảnh.",
        f"Ảnh nguồn nằm ở split D-Fire: {', '.join(splits)} (model chưa train trên các ảnh này).",
        (
            f"Tiêu chí: ban ngày, lửa chiếm >= {PRINT.min_fill:.0%} khung, "
            f"xám TB >= {PRINT.min_gray:g}, pixel đen <= {PRINT.max_dark:.0%}, "
            f"conf model >= {PRINT.min_conf}."
        ),
        "Cột: số thẻ, ảnh nguồn (tập/tên), vùng cắt x0,y0,cạnh (px), conf của model, xám TB.",
        "",
        *source_lines(cards),
    ]
    (OUT_DIR / "target-cards.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(cards)} thẻ trên {len(pages)} trang: {pdf.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
