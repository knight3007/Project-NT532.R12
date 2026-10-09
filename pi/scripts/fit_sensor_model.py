"""Ước lượng SensorModel (phân phối cảm biến của bộ kịch bản) từ số đo thật.

    uv run python scripts/fit_sensor_model.py --logs ../runs/station/*.jsonl
    uv run python scripts/fit_sensor_model.py --csv ../runs/decider/do_tuan2.csv --write
    uv run python scripts/make_scenarios.py --sensor-model ../runs/decider/sensor_fit.yaml

Nguồn: nhật ký trạm (dòng `tel`, chỉ cho phần NỀN) và/hoặc CSV nhóm ghi tay lúc đo
(`t_s,node,temp,gas,hum,label,dist_m`, label là baseline|fire|steam|spike|lamp; xem
nt532/decider/sensor_fit.py). In bảng "giả định hiện tại <-> đo được"; đại lượng không đủ dữ liệu
giữ mặc định. Ngưỡng báo động (temp_thr, gas_thr) do nhóm đặt, không ước lượng. `lamp` chưa dùng:
kịch bản đèn dùng chung fire_gain.
"""

import argparse
import glob
from pathlib import Path

import yaml

from nt532.config import REPO_ROOT, read_site
from nt532.decider.scenario import SENSOR
from nt532.decider.sensor_fit import (
    LABELS,
    NA,
    fit_sensor_model,
    load_csv,
    load_log,
    quiet_from_log,
    to_yaml_dict,
)
from nt532.orchestrator.fusion import alarm_limits

OUT = REPO_ROOT / "runs/decider/sensor_fit.yaml"


def expand(patterns: list[str]) -> list[str]:
    return sorted({f for p in patterns for f in (glob.glob(p) or [p])})


def fmt(v) -> str:
    if v is None:
        return NA
    if isinstance(v, (tuple, list)):
        return f"[{v[0]:g}, {v[1]:g}]"
    return f"{v:g}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--logs", nargs="*", default=[], help="nhật ký trạm runs/station/*.jsonl")
    p.add_argument("--csv", nargs="*", default=[], help="CSV số đo có nhãn")
    p.add_argument("--write", action="store_true", help=f"ghi {OUT.relative_to(REPO_ROOT)}")
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args()
    logs, csvs = expand(args.logs), expand(args.csv)
    if not logs and not csvs:
        p.error("cần ít nhất một --logs hoặc --csv")

    limits = alarm_limits(read_site())
    samples = []
    for i, path in enumerate(csvs):
        samples += load_csv(path, i)
    for j, path in enumerate(logs):
        raw, alerts = load_log(path, len(csvs) + j)
        samples += quiet_from_log(raw, alerts, limits)
    counts = {lab: sum(s.label == lab for s in samples) for lab in LABELS}
    print(f"{len(csvs)} CSV, {len(logs)} log; số mẫu theo nhãn: {counts}")

    fits = fit_sensor_model(samples, limits)
    print(f"{'đại lượng':<16}{'giả định hiện tại':<22}{'đo được':<26}{'n':>6}  ghi chú")
    for name, f in fits.items():
        print(f"{name:<16}{fmt(getattr(SENSOR, name)):<22}{fmt(f.value):<26}{f.n:>6}  {f.note}")

    out = to_yaml_dict(fits)
    if args.write:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {"csv": csvs, "logs": logs, "counts": counts,
                "n": {k: f.n for k, f in fits.items() if f.value is not None}}
        path.write_text("# Tạo bởi scripts/fit_sensor_model.py; chỉ chứa đại lượng đã ước lượng được.\n"
                        + yaml.safe_dump({"sensor_model": out, "meta": meta}, allow_unicode=True,
                                         sort_keys=False), encoding="utf-8")
        print(f"Đã ghi {path} ({len(out)} đại lượng)")
    elif not out:
        print("Không ước lượng được đại lượng nào.")


if __name__ == "__main__":
    main()
