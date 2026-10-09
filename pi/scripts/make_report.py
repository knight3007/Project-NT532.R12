"""Gom số đo trong runs/ thành một báo cáo HTML (tuần 5-6): sai số định vị và ngắm, hội tụ CORRECT,
độ trễ cảnh báo-phun, kết cục các lượt, chất lượng bộ quyết định, tải Pi, độ trễ stream.

    uv run python scripts/make_report.py
    uv run python scripts/make_report.py --since 2026-10-09 --out runs/report/thu_nghiem

Ghi runs/report/<thời điểm>/index.html (tự đủ, mở thẳng bằng trình duyệt). Mục nào không có dữ
liệu thì bỏ qua; mỗi mục ghi nguồn file và số mẫu.
"""

import argparse
import sys
import time
from pathlib import Path

from nt532.config import REPO_ROOT
from nt532.report import build_report, collect
from nt532.report.build import parse_since


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--runs-dir", type=Path, default=REPO_ROOT / "runs", help="thư mục chứa measure/, station/, decider/")
    p.add_argument("--out", type=Path, default=None, help="thư mục ra; mặc định runs/report/<thời điểm>")
    p.add_argument("--since", default=None, help="chỉ lấy số đo từ mốc này (2026-10-09 hoặc 20261009-080000)")
    p.add_argument("--gate-cm", type=float, default=5.0, help="ngưỡng qua cổng ngắm")
    args = p.parse_args()

    try:
        since = parse_since(args.since)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    runs_dir = args.runs_dir if args.runs_dir.is_absolute() else Path.cwd() / args.runs_dir
    out = args.out or runs_dir / "report" / time.strftime("%Y%m%d-%H%M%S")
    html = build_report(collect(runs_dir, since), args.gate_cm)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"Đã ghi {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
