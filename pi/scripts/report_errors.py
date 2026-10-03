"""Thống kê và vẽ sai số từ CSV của measure.py.

    uv run python scripts/report_errors.py --csv runs/measure/target.csv

In bảng theo từng nhãn và tổng (cm): số lượt, số lượt đo được, sai số tổng (trung bình, trung vị,
độ lệch chuẩn, max) và từng trục (trung bình, trung vị, max của giá trị tuyệt đối, độ lệch chuẩn
của sai số có dấu). Lưu hình PNG cạnh file CSV: vị trí thật (chấm đen) và vị trí đo (chấm màu)
trên mặt bảng, trục X ngang và Z dọc, kèm mũi tên sai số phóng to --scale lần cho dễ thấy.
"""

import argparse
from collections import defaultdict
from pathlib import Path

from nt532.config import REPO_ROOT, load_site
from nt532.measure import read_rows, summarize


def format_table(groups: dict[str, dict], axis2: str) -> str:
    head = (
        f"{'nhãn':<10}{'lượt':>5}{'đo được':>8}  "
        f"{'tổng (tb/trung vị/std/max)':<30}{'x (tb/tv/std/max)':<26}{axis2 + ' (tb/tv/std/max)'}"
    )
    lines = [head, "-" * len(head)]

    def cell(s: dict, width: int) -> str:
        text = f"{s['mean']:.2f}/{s['median']:.2f}/{s['std']:.2f}/{s['max']:.2f}"
        return f"{text:<{width}}"

    for name, s in groups.items():
        lines.append(
            f"{name:<10}{s['n']:>5}{s['found']:>8}  {cell(s['err'], 30)}"
            f"{cell(s['err_x'], 26)}{cell(s[f'err_{axis2}'], 26)}"
        )
    return "\n".join(lines)


def plot(rows: list[dict], axis2: str, out: Path, scale: float, site: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if axis2 == "z":
        width, height = site["board"]["width"], site["board"]["height"]
        title = "Mặt bảng bia"
    else:
        width, height = site["table"]["width"], site["table"]["depth"]
        title = "Mặt bàn"
    fig, ax = plt.subplots(figsize=(9, 9 * max(height / width, 0.4) + 1))
    ax.plot([0, width, width, 0, 0], [0, 0, height, height, 0], color="gray", lw=1.5)
    labels = sorted({r["label"] for r in rows})
    colors = plt.get_cmap("tab10")
    for k, label in enumerate(labels):
        mine = [r for r in rows if r["label"] == label]
        done = [r for r in mine if r.get("err") is not None]
        ax.scatter(
            [r["truth_x"] for r in mine],
            [r[f"truth_{axis2}"] for r in mine],
            c="k",
            marker="+",
            s=80,
        )
        if not done:
            continue
        color = colors(k % 10)
        ax.scatter(
            [r["meas_x"] for r in done],
            [r[f"meas_{axis2}"] for r in done],
            color=color,
            s=18,
            label=label,
        )
        for r in done:
            ax.annotate(
                "",
                xy=(
                    r["truth_x"] + scale * r["err_x"],
                    r[f"truth_{axis2}"] + scale * r[f"err_{axis2}"],
                ),
                xytext=(r["truth_x"], r[f"truth_{axis2}"]),
                arrowprops={"arrowstyle": "->", "color": color, "lw": 1},
            )
    ax.set_aspect("equal")
    ax.set_xlabel("X (m)")
    ax.set_ylabel(f"{axis2.upper()} (m)")
    ax.set_title(f"{title}: vị trí thật (+) và vị trí đo (chấm); mũi tên sai số phóng ×{scale:g}")
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True, help="file CSV, ví dụ runs/measure/target.csv")
    p.add_argument("--scale", type=float, default=10.0, help="độ phóng mũi tên sai số")
    args = p.parse_args()

    path = Path(args.csv)
    if not path.is_absolute() and not path.exists():
        path = REPO_ROOT / path
    if not path.exists():
        raise SystemExit(f"không thấy {args.csv}")
    rows, axis2 = read_rows(path)
    if not rows:
        raise SystemExit(f"{path.name} chưa có dòng nào")

    by_label = defaultdict(list)
    for r in rows:
        by_label[r["label"]].append(r)
    groups = {label: summarize(rs, axis2) for label, rs in sorted(by_label.items())}
    groups["TỔNG"] = summarize(rows, axis2)
    print(format_table(groups, axis2))

    out = path.with_suffix(".png")
    plot(rows, axis2, out, args.scale, load_site())
    print(f"Đã lưu {out}")


if __name__ == "__main__":
    main()
