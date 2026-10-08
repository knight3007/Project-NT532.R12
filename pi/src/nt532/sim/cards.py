"""Ảnh thẻ bia tổng hợp cho sa bàn ảo khi chưa có thẻ in từ D-Fire (make_target_cards.py).

Vẽ bằng OpenCV: thẻ lửa, đèn vàng (vật gây nhiễu), vật màu cam (vật gây nhiễu) và thẻ đã tắt
(cháy sém). Chỉ dùng với detector oracle; YOLO thật không được train trên các hình này.
"""

from pathlib import Path

import cv2
import numpy as np

from ..config import REPO_ROOT

SYNTH_DIR = REPO_ROOT / "data/sim/cards_synth"
SIZE = 200
# Tên file quyết định thứ tự (Scene sắp theo tên) nên chỉ số ảnh cố định theo loại.
FILES = {"fire": ["a_fire0", "a_fire1", "a_fire2"], "lamp": ["b_lamp0"], "object": ["c_object0"],
         "burnt": ["d_burnt0"]}


def _fire(rng) -> np.ndarray:
    img = np.full((SIZE, SIZE, 3), (25, 22, 30), np.uint8)
    glow = np.zeros((SIZE, SIZE), np.float32)
    for _ in range(9):
        cx = rng.normal(SIZE / 2, 18)
        cy = rng.normal(SIZE * 0.62, 12)
        ax, ay = rng.uniform(14, 34), rng.uniform(40, 75)
        cv2.ellipse(glow, (int(cx), int(cy)), (int(ax), int(ay)), rng.uniform(-15, 15), 0, 360,
                    float(rng.uniform(0.5, 1.0)), -1)
    glow = cv2.GaussianBlur(glow, (0, 0), 9)
    glow = np.clip(glow / max(glow.max(), 1e-6), 0, 1)
    # đỏ -> cam -> vàng -> trắng theo cường độ
    b = np.clip((glow - 0.75) * 4, 0, 1) * 200
    g = np.clip(glow * 1.3 - 0.15, 0, 1) * 220
    r = np.clip(glow * 2.0, 0, 1) * 255
    flame = np.dstack([b, g, r])
    out = img.astype(np.float32) * (1 - glow[..., None]) + flame * glow[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def _lamp(rng) -> np.ndarray:
    img = np.full((SIZE, SIZE, 3), (40, 45, 50), np.uint8)
    halo = np.zeros((SIZE, SIZE), np.float32)
    cv2.circle(halo, (SIZE // 2, SIZE // 2), 55, 1.0, -1)
    halo = cv2.GaussianBlur(halo, (0, 0), 18)
    color = np.array([150, 230, 255], np.float32)
    out = img.astype(np.float32) * (1 - halo[..., None]) + color * halo[..., None]
    cv2.circle(out, (SIZE // 2, SIZE // 2), 30, (225, 250, 255), -1)
    return np.clip(out, 0, 255).astype(np.uint8)


def _object(rng) -> np.ndarray:
    img = np.full((SIZE, SIZE, 3), (170, 200, 215), np.uint8)
    cv2.circle(img, (SIZE // 2, SIZE // 2 + 10), 58, (20, 110, 235), -1)
    cv2.circle(img, (SIZE // 2 - 20, SIZE // 2 - 10), 14, (120, 190, 255), -1)
    return img


def _burnt(rng) -> np.ndarray:
    img = np.full((SIZE, SIZE, 3), (200, 205, 210), np.uint8)
    soot = np.zeros((SIZE, SIZE), np.float32)
    cv2.ellipse(soot, (SIZE // 2, int(SIZE * 0.6)), (45, 70), 0, 0, 360, 1.0, -1)
    soot = cv2.GaussianBlur(soot, (0, 0), 14)
    out = img.astype(np.float32) * (1 - 0.8 * soot[..., None])
    return np.clip(out, 0, 255).astype(np.uint8)


DRAW = {"fire": _fire, "lamp": _lamp, "object": _object, "burnt": _burnt}


def ensure_synth_cards(directory: str | Path = SYNTH_DIR, seed: int = 7) -> dict[str, list[int]]:
    """Ghi ảnh (nếu chưa có) và trả {loại: [chỉ số ảnh trong Scene]}."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    for kind, names in FILES.items():
        for name in names:
            path = directory / f"{name}.png"
            if not path.exists():
                cv2.imwrite(str(path), DRAW[kind](rng))
    order = sorted(p.stem for p in directory.glob("*.png"))
    return {kind: [order.index(n) for n in names] for kind, names in FILES.items()}
