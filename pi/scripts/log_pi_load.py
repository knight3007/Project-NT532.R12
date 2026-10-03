"""Ghi tải của Raspberry Pi theo chu kỳ vào CSV: CPU %, RAM, nhiệt độ, cờ throttling.

    uv run python scripts/log_pi_load.py --interval 1 --out runs/pi_load.csv --duration 600

Chạy song song với live_detect.py để đo tải khi suy luận. Chỉ chạy trên Linux (Pi).
Cột throttled là mặt nạ bit của `vcgencmd get_throttled`: bit 0-3 đang xảy ra (thiếu điện,
giới hạn xung, giảm xung, quá nhiệt), bit 16-19 đã từng xảy ra từ lúc bật máy.
"""

import argparse
import csv
import shutil
import subprocess
import sys
import time
from pathlib import Path

from nt532.config import REPO_ROOT
from nt532.sysload import (
    cpu_percent,
    parse_meminfo_used_mb,
    parse_proc_stat,
    parse_temp_c,
    parse_throttled,
)

THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")


def read_throttled() -> int | None:
    if shutil.which("vcgencmd") is None:
        return None
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2, check=True
        ).stdout
        return parse_throttled(out)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def read_temp() -> float | None:
    try:
        return parse_temp_c(THERMAL.read_text())
    except (OSError, ValueError):
        return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=1.0, help="giây giữa hai lần ghi")
    p.add_argument("--out", default="runs/pi_load.csv", help="tính từ gốc repo")
    p.add_argument(
        "--duration", type=float, default=None, help="giây; bỏ trống thì chạy đến Ctrl+C"
    )
    args = p.parse_args()

    if not sys.platform.startswith("linux"):
        raise SystemExit("log_pi_load.py chỉ hỗ trợ Linux (Raspberry Pi); không chạy trên Windows")

    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    prev = parse_proc_stat(Path("/proc/stat").read_text())
    start = time.monotonic()
    rows = 0
    with out.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "elapsed_s", "cpu_pct", "ram_used_mb", "temp_c", "throttled"])
        try:
            while args.duration is None or time.monotonic() - start < args.duration:
                time.sleep(args.interval)
                cur = parse_proc_stat(Path("/proc/stat").read_text())
                cpu = cpu_percent(prev, cur)
                prev = cur
                ram = parse_meminfo_used_mb(Path("/proc/meminfo").read_text())
                temp, thr = read_temp(), read_throttled()
                w.writerow(
                    [
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{time.monotonic() - start:.1f}",
                        f"{cpu:.1f}",
                        f"{ram:.0f}",
                        "" if temp is None else f"{temp:.1f}",
                        "" if thr is None else f"{thr:#x}",
                    ]
                )
                f.flush()
                rows += 1
        except KeyboardInterrupt:
            pass
    print(f"đã ghi {rows} dòng vào {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
