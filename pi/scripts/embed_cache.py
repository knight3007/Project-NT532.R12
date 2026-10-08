"""Nhúng ảnh bằng EmbeddingGemma 2 (đóng băng) rồi lưu cache để train_decider.py dùng lại.

    uv run python scripts/embed_cache.py                 # cả hai bộ dữ liệu, cả ba split
    uv run python scripts/embed_cache.py --bench         # đo thời gian nhúng từng ảnh

Cache ở runs/decider/cache/<bộ>_<split>.npz gồm paths, labels (chỉ số), names, source, emb (L2).
Bộ "cof": ClassesOfFire theo thư mục lớp. Bộ "mix": fire-mix, nhãn ảnh = có ít nhất một ô lớp 1
(fire) trong file nhãn YOLO; ảnh không có ô lửa (kể cả negatives/*) là âm tính.
"""

import argparse
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from nt532.config import REPO_ROOT

DATASET = REPO_ROOT / "data/dataset"
CACHE = REPO_ROOT / "runs/decider/cache"
MODEL = "google/embeddinggemma-2"
COF_NAMES = ["A", "B", "C", "D", "F", "none"]
MIX_NAMES = ["no", "yes"]
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def list_cof(split: str) -> list[tuple[Path, int, str]]:
    rows = []
    for i, name in enumerate(COF_NAMES):
        for f in sorted((DATASET / "classesoffire" / split / name).iterdir()):
            if f.suffix.lower() in IMG_EXT:
                rows.append((f, i, name))
    return rows


def has_fire(label: Path) -> bool:
    if not label.exists():
        return False
    return any(line.split()[:1] == ["1"] for line in label.read_text().splitlines())


def list_mix(split: str) -> list[tuple[Path, int, str]]:
    root = DATASET / "fire-mix"
    sources = [root / "home-fire", root / "indoor-fire-smoke"]
    sources += sorted(d for d in (root / "negatives").iterdir() if d.is_dir())
    rows = []
    for src in sources:
        for f in sorted((src / split / "images").iterdir()):
            if f.suffix.lower() in IMG_EXT:
                lab = src / split / "labels" / (f.stem + ".txt")
                rows.append((f, int(has_fire(lab)), src.name))
    return rows


def load_model(device: str):
    from sentence_transformers import SentenceTransformer

    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    # Chỉ cần văn bản và ảnh, bỏ bộ mã hóa âm thanh để đỡ tốn bộ nhớ.
    return SentenceTransformer(
        MODEL,
        device=device,
        model_kwargs={"torch_dtype": dtype},
        config_kwargs={"audio_config": None},
    )


def encode_images(model, files: list[Path], batch: int) -> np.ndarray:
    out = []
    for i in range(0, len(files), batch):
        imgs = [{"image": Image.open(f).convert("RGB")} for f in files[i : i + batch]]
        out.append(model.encode(imgs, batch_size=batch, normalize_embeddings=True))
        if (i // batch) % 20 == 0:
            print(f"  {i + len(imgs)}/{len(files)}", flush=True)
    emb = np.concatenate(out).astype(np.float32)
    return emb / np.linalg.norm(emb, axis=1, keepdims=True)  # bf16 làm chuẩn lệch ~0.5%


def build(model, name: str, split: str, batch: int) -> None:
    path = CACHE / f"{name}_{split}.npz"
    if path.exists():
        print(f"bỏ qua {path.name} (đã có)")
        return
    rows = (list_cof if name == "cof" else list_mix)(split)
    names = COF_NAMES if name == "cof" else MIX_NAMES
    print(f"{name}/{split}: {len(rows)} ảnh")
    t0 = time.time()
    emb = encode_images(model, [r[0] for r in rows], batch)
    print(f"  xong sau {time.time() - t0:.0f}s")
    np.savez_compressed(
        path,
        paths=np.array([str(r[0].relative_to(REPO_ROOT)) for r in rows]),
        labels=np.array([r[1] for r in rows]),
        names=np.array(names),
        source=np.array([r[2] for r in rows]),
        emb=emb,
    )


def bench(model, device: str) -> None:
    files = [r[0] for r in list_cof("test")[:30]]
    imgs = [{"image": Image.open(f).convert("RGB")} for f in files]
    for im in imgs[:5]:
        model.encode([im])
    if device == "cuda":
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    for im in imgs:
        model.encode([im], normalize_embeddings=True)
    if device == "cuda":
        torch.cuda.synchronize()
    dt = (time.perf_counter() - t0) / len(imgs)
    print(f"{device}: {dt * 1000:.1f} ms/ảnh (batch 1, gồm tiền xử lý ảnh)")
    if device == "cuda":
        print(f"VRAM cực đại {torch.cuda.max_memory_allocated() / 2**20:.0f} MiB")
        print(f"VRAM đã cấp {torch.cuda.memory_allocated() / 2**20:.0f} MiB (trọng số)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sets", nargs="+", default=["cof", "mix"])
    p.add_argument("--splits", nargs="+", default=["train", "val", "test"])
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--bench", action="store_true")
    args = p.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    model = load_model(args.device)
    if args.bench:
        bench(model, args.device)
        return
    for name in args.sets:
        for split in args.splits:
            build(model, name, split, args.batch)


if __name__ == "__main__":
    main()
