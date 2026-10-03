"""Đo thời gian suy luận của một hoặc nhiều trọng số, chỉ bằng CPU.

    uv run python scripts/bench_detect.py --weights models/fire-n.pt models/fire-n_ncnn_model
    uv run python scripts/bench_detect.py --weights models/fire-n_ncnn_model --imgsz 640 --n 100
    uv run python scripts/bench_detect.py --source 0     # ảnh từ webcam

Thời gian gồm tiền xử lý, suy luận và hậu xử lý (cả hàm predict), không gồm đọc ảnh.
Bản NCNN/ONNX có kích thước cố định lúc xuất; imgsz khác với lúc xuất thì cho kết quả không đáng tin.
Kết quả ghi thêm vào runs/bench/bench-<tên máy>.csv.
"""

import argparse
import csv
import platform
import statistics
import time
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from nt532.config import REPO_ROOT, load_site
from nt532.vision.camera import open_camera, read_fresh
from nt532.vision.detect import DEFAULT_WEIGHTS

DEFAULT_SOURCE = REPO_ROOT / "data" / "dataset" / "fire-mix" / "home-fire" / "test" / "images"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def load_frames(source: str, n: int) -> list[np.ndarray]:
    """Lấy tối đa n ảnh từ thư mục (chọn đều) hoặc từ webcam."""
    if source.isdigit() or source == "sim":
        cap = open_camera(load_site(source=source)["camera"], source)
        try:
            return [read_fresh(cap) for _ in range(min(n, 30))]
        finally:
            cap.release()
    paths = sorted(p for p in Path(source).iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not paths:
        raise SystemExit(f"không có ảnh trong {source}")
    step = max(len(paths) // n, 1)
    return [cv2.imread(str(p)) for p in paths[::step][:n]]


def summarize(times_ms: list[float]) -> dict[str, float]:
    ordered = sorted(times_ms)
    p95 = ordered[min(int(0.95 * len(ordered)), len(ordered) - 1)]
    mean = statistics.fmean(times_ms)
    return {
        "mean": mean,
        "median": statistics.median(times_ms),
        "p95": p95,
        "fps": 1000 / mean,
    }


def bench(model: YOLO, frames: list[np.ndarray], imgsz: int, n: int, warmup: int) -> list[float]:
    def run(frame: np.ndarray) -> float:
        t0 = time.perf_counter()
        model.predict(frame, imgsz=imgsz, conf=0.25, device="cpu", verbose=False)
        return (time.perf_counter() - t0) * 1000

    for i in range(warmup):
        run(frames[i % len(frames)])
    return [run(frames[i % len(frames)]) for i in range(n)]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--weights", nargs="+", default=[str(DEFAULT_WEIGHTS)])
    p.add_argument("--imgsz", nargs="+", type=int, default=[640])
    p.add_argument("--n", type=int, default=50, help="số lượt đo mỗi cấu hình")
    p.add_argument("--warmup", type=int, default=5)
    p.add_argument("--source", default=str(DEFAULT_SOURCE), help="thư mục ảnh, số webcam hoặc sim")
    args = p.parse_args()

    frames = load_frames(args.source, args.n)
    node, machine = platform.node(), platform.machine()
    print(f"máy {node} ({machine}), {len(frames)} ảnh, {args.n} lượt đo, chỉ CPU")
    header = f"{'trọng số':32}{'imgsz':>6}{'TB ms':>9}{'trung vị':>10}{'p95':>8}{'FPS':>7}"
    print(header)
    rows = []
    for w in args.weights:
        model = YOLO(w)
        for size in args.imgsz:
            try:
                r = summarize(bench(model, frames, size, args.n, args.warmup))
            except Exception as e:  # noqa: BLE001 - báo lỗi rồi đo tiếp cấu hình khác
                print(f"{Path(w).name:32}{size:6}  lỗi: {e}")
                continue
            name = Path(w).name
            print(
                f"{name:32}{size:6}{r['mean']:9.1f}{r['median']:10.1f}{r['p95']:8.1f}{r['fps']:7.1f}"
            )
            rows.append(
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "node": node,
                    "machine": machine,
                    "weights": name,
                    "imgsz": size,
                    "n": args.n,
                    "mean_ms": f"{r['mean']:.2f}",
                    "median_ms": f"{r['median']:.2f}",
                    "p95_ms": f"{r['p95']:.2f}",
                    "fps": f"{r['fps']:.2f}",
                }
            )
    if not rows:
        return
    out = REPO_ROOT / "runs" / "bench" / f"bench-{node}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    with out.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f"đã ghi {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
