"""Physical accuracy from ruler ground truth vs Dynamic Mapping output in the ID 22 frame.

Ground truth is only what the operator measured with a ruler; nothing here derives it
from mapping output. Estimates must come from a PASS `dynamic_map.py --world-origin 22`
JSON (coordinate_frame tag_22_table, metres).

    uv run python scripts/physical_accuracy.py --ground-truth truth.csv \
        --estimate pos01=runs/pos01/evaluation/origin22.json \
        --estimate pos02=runs/pos02/evaluation/origin22.json --json accuracy.json

CSV columns: position,target,x_true_cm,y_true_cm,surface,measured_by,notes
  position  must match an --estimate label
  target    ID2 | ID21 | ID22 | fire1, fire2, ... (fire index in fire_targets, 1-based)
  surface   "table" for tags; fire rows count only with "flat_on_table"
Axes: origin at the ID 22 centre, +X along its printed bottom edge to the right,
+Y towards its printed top edge, centimetres.
"""

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import median

FIRE_SURFACE = "flat_on_table"


def _target_xy(result: dict, target: str) -> tuple[float, float] | None:
    name = target.strip()
    if name == "ID2":
        point = result.get("references", {}).get("2")
    elif name == "ID21":
        point = result.get("nodes", {}).get("s1")
    elif name == "ID22":
        point = result.get("nodes", {}).get("s2")
    elif name.startswith("fire") and name[4:].isdigit():
        index = int(name[4:]) - 1
        fires = result.get("fire_targets", [])
        point = fires[index]["xyz"] if 0 <= index < len(fires) else None
    else:
        raise ValueError(f"unknown target {target!r}")
    return None if point is None else (point[0] * 100, point[1] * 100)


def validate_estimate(result: dict, label: str) -> None:
    """Accept only a trusted ID 22 tabletop result."""
    if result.get("status") != "PASS":
        raise ValueError(f"{label}: mapping status {result.get('status')} "
                         f"({result.get('reason')}); physical accuracy cannot be measured")
    if (result.get("coordinate_frame") != "tag_22_table"
            or result.get("operational_origin_id") != 22 or result.get("unit") != "m"):
        raise ValueError(f"{label}: estimate must be ID 22 tabletop output in metres")


def _stats(rows: list[dict]) -> dict:
    xy = [r["xy_error_cm"] for r in rows]
    if not xy:
        return {"n": 0}
    return {"n": len(xy),
            "mae_x_cm": round(sum(abs(r["x_error_cm"]) for r in rows) / len(rows), 3),
            "mae_y_cm": round(sum(abs(r["y_error_cm"]) for r in rows) / len(rows), 3),
            "mae_xy_cm": round(sum(xy) / len(xy), 3),
            "median_xy_cm": round(median(xy), 3),
            "max_xy_cm": round(max(xy), 3),
            "rmse_xy_cm": round(math.sqrt(sum(e * e for e in xy) / len(xy)), 3)}


def evaluate(truth_rows: list[dict], estimates: dict[str, dict],
             max_error_cm: float | None = None) -> dict:
    for label, result in estimates.items():
        validate_estimate(result, label)
    rows, excluded = [], []
    for line, truth in enumerate(truth_rows, 2):
        position, target = truth["position"].strip(), truth["target"].strip()
        surface = (truth.get("surface") or "").strip()
        if position not in estimates:
            raise ValueError(f"CSV line {line}: no --estimate for position {position!r}")
        x_true, y_true = float(truth["x_true_cm"]), float(truth["y_true_cm"])
        record = {"position": position, "target": target, "x_true_cm": x_true,
                  "y_true_cm": y_true, "surface": surface}
        if target == "ID22":
            excluded.append({**record, "reason": "origin by definition (0,0); not an error sample"})
            continue
        if target.startswith("fire") and surface != FIRE_SURFACE:
            excluded.append({**record, "reason": f"fire surface must be {FIRE_SURFACE}; "
                                                 "Z=0 ray intersection is invalid otherwise"})
            continue
        estimate = _target_xy(estimates[position], target)
        if estimate is None:
            excluded.append({**record, "reason": "target not present in mapping output"})
            continue
        dx, dy = estimate[0] - x_true, estimate[1] - y_true
        rows.append({**record, "x_est_cm": round(estimate[0], 3), "y_est_cm": round(estimate[1], 3),
                     "x_error_cm": round(dx, 3), "y_error_cm": round(dy, 3),
                     "xy_error_cm": round(math.hypot(dx, dy), 3)})
    by = lambda key: {value: _stats([r for r in rows if r[key] == value])
                      for value in sorted({r[key] for r in rows})}
    overall = _stats(rows)
    status = "NOT_VERIFIED" if not rows else "MEASURED"
    if rows and max_error_cm is not None:
        status = "PASS" if overall["max_xy_cm"] <= max_error_cm else "FAIL"
    return {"physical_accuracy": status, "max_error_cm": max_error_cm,
            "ground_truth": "ruler measurements supplied by operator",
            "coordinate_frame": "tag_22_table", "unit": "cm",
            "overall": overall, "per_target": by("target"), "per_position": by("position"),
            "samples": rows, "excluded": excluded}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--estimate", action="append", required=True, metavar="LABEL=JSON")
    parser.add_argument("--max-error-cm", type=float,
                        help="optional acceptance limit on max XY error; omitted -> MEASURED")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    estimates = {}
    for item in args.estimate:
        label, sep, path = item.partition("=")
        if not sep:
            parser.error("--estimate must be LABEL=path/to/origin22.json")
        estimates[label] = json.loads(Path(path).read_text(encoding="utf-8"))
    with args.ground_truth.open(newline="", encoding="utf-8") as handle:
        truth = list(csv.DictReader(handle))
    try:
        report = evaluate(truth, estimates, args.max_error_cm)
    except ValueError as exc:
        print(json.dumps({"physical_accuracy": "NOT_VERIFIED", "reason": str(exc)}, indent=2))
        return 1
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.json is not None:
        args.json.write_text(text + "\n", encoding="utf-8")
    return 0 if report["physical_accuracy"] in ("MEASURED", "PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
