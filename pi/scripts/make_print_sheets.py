"""Sinh các trang A4 để in: AprilTag 36h11 và bảng ChArUco hiệu chuẩn camera.

    uv run python scripts/make_print_sheets.py

In ở tỷ lệ 100% (tắt "fit to page"), rồi đo thước 100 mm trên mỗi trang để kiểm tra.
"""

import cv2
import numpy as np
from PIL import Image

from nt532.config import REPO_ROOT, load_site

DPI = 600
A4_MM = (210, 297)
OUT_DIR = REPO_ROOT / "docs" / "print"


def px(mm: float) -> int:
    return round(mm * DPI / 25.4)


def blank_page() -> np.ndarray:
    return np.full((px(A4_MM[1]), px(A4_MM[0])), 255, np.uint8)


def draw_ruler(page: np.ndarray) -> None:
    """Thước 100 mm ở cuối trang để kiểm tra tỷ lệ in."""
    x0, y = px(20), px(A4_MM[1] - 12)
    cv2.line(page, (x0, y), (x0 + px(100), y), 0, px(0.3))
    for mm in range(0, 101, 10):
        cv2.line(page, (x0 + px(mm), y - px(2)), (x0 + px(mm), y + px(2)), 0, px(0.3))
    text(page, "100 mm - in ty le 100%", x0 + px(104), y + px(1.5), 3.5)


def text(page: np.ndarray, s: str, x: int, y: int, height_mm: float) -> None:
    scale = cv2.getFontScaleFromHeight(cv2.FONT_HERSHEY_SIMPLEX, px(height_mm), px(0.35))
    cv2.putText(page, s, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, 0, px(0.35), cv2.LINE_AA)


def tag_page(tags: list[tuple[int, float, str]], cols: int) -> np.ndarray:
    """tags: (id, cạnh tính bằng mét, nhãn). Xếp lưới, mỗi tag có viền trắng và nét cắt."""
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    page = blank_page()
    size_mm = tags[0][1] * 1000
    quiet = size_mm / 8  # viền trắng rộng một ô
    cell_w = size_mm + 2 * quiet
    cell_h = cell_w + 8
    gap = (A4_MM[0] - cols * cell_w) / (cols + 1)
    for i, (tag_id, size, label) in enumerate(tags):
        row, col = divmod(i, cols)
        x = gap + col * (cell_w + gap)
        y = 12 + row * (cell_h + 6)
        side = px(size * 1000)
        marker = cv2.aruco.generateImageMarker(dictionary, tag_id, side)
        tx, ty = px(x + quiet), px(y + quiet)
        page[ty : ty + side, tx : tx + side] = marker
        cv2.rectangle(page, (px(x), px(y)), (px(x + cell_w), px(y + cell_w)), 160, px(0.15))
        text(
            page, f"36h11 id {tag_id}  {size * 1000:g} mm  {label}", px(x), px(y + cell_w + 5), 3.5
        )
    draw_ruler(page)
    return page


def charuco_page(cfg: dict) -> np.ndarray:
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, f"DICT_{cfg['dictionary']}"))
    nx, ny = cfg["squares_x"], cfg["squares_y"]
    board = cv2.aruco.CharucoBoard((nx, ny), cfg["square"], cfg["marker"], dictionary)
    w, h = px(nx * cfg["square"] * 1000), px(ny * cfg["square"] * 1000)
    img = board.generateImage((w, h), marginSize=0)
    page = blank_page()
    x, y = (page.shape[1] - w) // 2, px(8)
    page[y : y + h, x : x + w] = img
    draw_ruler(page)
    return page


def save(page: np.ndarray, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.pdf"
    Image.fromarray(page).convert("1").save(path, resolution=DPI)
    print(path.relative_to(REPO_ROOT))


def main() -> None:
    site = load_site()
    tags = site["tags"]
    ref = [(i, tags["reference"]["size"], "tham chieu") for i in tags["reference"]["positions"]]
    nodes = [(i, tags["nodes"]["size"], f"node {name}") for name, i in tags["nodes"]["ids"].items()]

    # Tag 8 cm: hai cột, tối đa bốn tag mỗi trang.
    save(tag_page(ref, cols=2), "tags-reference")
    save(tag_page(nodes, cols=2), "tags-nodes")
    save(charuco_page(site["camera"]["calib_board"]), "charuco-a4")


if __name__ == "__main__":
    main()
