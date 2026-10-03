"""Xuất trọng số YOLO sang định dạng chạy nhanh trên CPU ARM (Raspberry Pi 5).

    uv run python scripts/export_model.py                          # NCNN 640
    uv run python scripts/export_model.py --format onnx --imgsz 480

Kết quả nằm cạnh file .pt, ví dụ models/fire-n_ncnn_model. Xuất NCNN cần mạng để tải pnnx.
Xuất trên laptop rồi chép thư mục sang Pi; imgsz lúc xuất phải khớp imgsz lúc chạy.
"""

import argparse
from pathlib import Path

from ultralytics import YOLO

from nt532.config import REPO_ROOT
from nt532.vision.detect import DEFAULT_WEIGHTS

FORMATS = ("ncnn", "onnx", "openvino")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    p.add_argument("--format", default="ncnn", choices=FORMATS)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--half", action="store_true", help="FP16; chỉ dùng khi chạy bằng GPU")
    args = p.parse_args()

    # FP16 trên CPU ARM không nhanh hơn, còn ONNX trên CPU thì không chạy được FP16.
    if args.half and args.format == "onnx":
        raise SystemExit("ONNX FP16 không chạy được trên CPU; bỏ --half")
    out = YOLO(args.weights).export(format=args.format, imgsz=args.imgsz, half=args.half)
    out = Path(out)
    if out.is_relative_to(REPO_ROOT):
        out = out.relative_to(REPO_ROOT)
    print(f"đã xuất {args.format} {args.imgsz} px: {out}")


if __name__ == "__main__":
    main()
