"""Hàm thuần cho các bài đo: ghi CSV, tìm bia gần điểm thật nhất, thống kê sai số."""

import csv
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from .vision.types import Target

# Các cột sai số tính bằng mét; trục thứ hai là z (mặt bảng) hoặc y (vị trí node trên bàn).
STAT_NAMES = ("mean", "median", "std", "max")


def parse_xy(text: str) -> tuple[float, float]:
    """Đọc "0.30,0.20" thành (0.30, 0.20)."""
    try:
        a, b = text.split(",")
        return float(a), float(b)
    except ValueError as e:
        raise ValueError(f"cần dạng X,Z (mét), ví dụ 0.30,0.20; nhận được {text!r}") from e


def nearest_target(targets: Sequence[Target], truth_xz: tuple[float, float]) -> Target | None:
    """Bia có vị trí trên bảng gần điểm thật (x, z) nhất, hoặc None nếu danh sách rỗng."""
    if not targets:
        return None
    goal = np.asarray(truth_xz, dtype=np.float64)
    return min(targets, key=lambda t: float(np.linalg.norm(t.position[[0, 2]] - goal)))


def error_fields(
    truth: tuple[float, float],
    measured: tuple[float, float] | None,
    axis2: str = "z",
) -> dict[str, float | str]:
    """Các cột truth/meas/err; để trống phần đo khi không đo được (ví dụ không thấy bia)."""
    fields: dict[str, float | str] = {"truth_x": truth[0], f"truth_{axis2}": truth[1]}
    if measured is None:
        fields.update(
            {"meas_x": "", f"meas_{axis2}": "", "err_x": "", f"err_{axis2}": "", "err": ""}
        )
        return fields
    ex, e2 = measured[0] - truth[0], measured[1] - truth[1]
    fields.update(
        {
            "meas_x": measured[0],
            f"meas_{axis2}": measured[1],
            "err_x": ex,
            f"err_{axis2}": e2,
            "err": float(np.hypot(ex, e2)),
        }
    )
    return fields


def append_row(path: Path, label: str, run: int, fields: dict[str, float | str]) -> None:
    """Ghi thêm một dòng vào CSV (tạo file và thư mục nếu chưa có), thêm cột thời gian, nhãn, lượt."""
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "label": label, "run": run}
    row |= {k: round(v, 5) if isinstance(v, float) else v for k, v in fields.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    if exists:
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f))
        if header != list(row):
            raise ValueError(f"{path.name} có cột khác ({header}), dùng file CSV khác cho bài này")
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def next_run(path: Path, label: str) -> int:
    """Số lượt kế tiếp của một nhãn (đếm các dòng đã ghi có cùng nhãn)."""
    if not path.exists():
        return 1
    with path.open(encoding="utf-8", newline="") as f:
        return 1 + sum(row["label"] == label for row in csv.DictReader(f))


def read_rows(path: Path) -> tuple[list[dict[str, float | str | None]], str]:
    """Đọc CSV; ô số thành float, ô trống thành None. Trả (các dòng, tên trục thứ hai)."""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        axis2 = "z" if "truth_z" in fields else "y"
        rows = []
        for raw in reader:
            row: dict[str, float | str | None] = {}
            for key, value in raw.items():
                if key in ("time", "label", "node"):
                    row[key] = value
                else:
                    row[key] = float(value) if value not in ("", None) else None
            rows.append(row)
    return rows, axis2


def summarize(rows: Sequence[dict], axis2: str = "z") -> dict:
    """Thống kê sai số của một nhóm dòng, đơn vị cm.

    `n` là số lượt, `found` là số lượt đo được. Với sai số tổng: trung bình, trung vị, độ lệch
    chuẩn, max. Với từng trục: trung bình, trung vị và max của |sai số|, độ lệch chuẩn của sai số
    có dấu (đo độ phân tán, không lẫn với độ lệch trung bình).
    """
    found = [r for r in rows if r.get("err") is not None]
    out: dict = {"n": len(rows), "found": len(found)}
    for col in ("err", "err_x", f"err_{axis2}"):
        values = np.array([r[col] for r in found], dtype=np.float64) * 100
        if values.size == 0:
            out[col] = dict.fromkeys(STAT_NAMES, float("nan"))
            continue
        absolute = np.abs(values)
        out[col] = {
            "mean": float(absolute.mean()),
            "median": float(np.median(absolute)),
            "std": float(values.std(ddof=1)) if values.size > 1 else 0.0,
            "max": float(absolute.max()),
        }
    return out
