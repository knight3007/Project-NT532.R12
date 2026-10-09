"""Đo baseline luật (kế hoạch NT532 mục 4) trên các tập kịch bản.

    uv run python scripts/eval_rules.py
    uv run python scripts/eval_rules.py --splits test test_shift
    uv run python scripts/eval_rules.py --data ../runs/decider/scenarios/real.jsonl   # lượt thật

In độ chính xác từng câu hỏi và các lỗi mức hệ thống; ghi runs/decider/rules_eval.json.
`--data` chấm file jsonl bất kỳ cùng định dạng (vd lượt thật từ export_episodes.py; nhãn vắng thì bỏ
qua) và ghi rules_eval_data.json.
"""

import argparse
import json
from pathlib import Path

from nt532.config import REPO_ROOT
from nt532.decider.rules import rule_answers
from nt532.decider.scenario import load_geometry

DIR = REPO_ROOT / "runs/decider"
QUESTIONS = ("real_fire", "action", "target", "nozzle", "after_verify")


def evaluate(records: list[dict], geo, answers: list[dict] | None = None) -> dict:
    """`answers` (cùng thứ tự records) cho phép chấm đáp án của mô hình khác bằng đúng logic này."""
    correct = {q: 0 for q in QUESTIONS}
    asked = {q: 0 for q in QUESTIONS}
    sysc = {"false_spray": 0, "missed_spray": 0, "wrong_target": 0, "wrong_nozzle": 0,
            "no_fire": 0, "should_spray": 0, "sprayed_correctly": 0}
    for i, r in enumerate(records):
        ans = answers[i] if answers is not None else rule_answers(r, geo)
        lab = r["labels"]
        for q in lab:
            asked[q] += 1
            correct[q] += ans.get(q) == lab[q]
        if "action" not in lab or "real_fire" not in lab:
            continue
        spray = ans["action"] == "spray"
        if not lab["real_fire"]:
            sysc["no_fire"] += 1
            sysc["false_spray"] += spray
        elif lab["action"] == "spray":
            sysc["should_spray"] += 1
            sysc["missed_spray"] += not spray
            if spray:
                bad_t = "target" in lab and ans.get("target") != lab["target"]
                bad_n = "nozzle" in lab and ans.get("nozzle") != lab["nozzle"]
                sysc["wrong_target"] += bad_t
                sysc["wrong_nozzle"] += bad_n and not bad_t
                sysc["sprayed_correctly"] += not bad_t and not bad_n
    acc = {q: correct[q] / asked[q] for q in QUESTIONS if asked[q]}
    return {"n_records": len(records), "asked": asked, "accuracy": acc, "system": sysc}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--splits", nargs="+", default=["test", "test_shift"])
    p.add_argument("--data", nargs="+", default=None, metavar="JSONL",
                   help="chấm các file này thay cho --splits")
    args = p.parse_args()

    geo = load_geometry(with_nodes=False)
    result = {}
    paths = ({Path(d).stem: Path(d) for d in args.data} if args.data
             else {s: DIR / "scenarios" / f"{s}.jsonl" for s in args.splits})
    for split, path in paths.items():
        with path.open(encoding="utf-8") as f:
            result[split] = evaluate([json.loads(line) for line in f], geo)
    args.splits = list(paths)

    print(f"{'':36}" + "".join(f"{s:>14}" for s in args.splits))
    for q in QUESTIONS:
        row = "".join(f"{result[s]['accuracy'].get(q, float('nan')):14.1%}" for s in args.splits)
        print(f"{'acc ' + q:36}{row}")
    labels = {
        "false_spray": "phun khi không có lửa",
        "missed_spray": "lửa thật cần phun mà không phun",
        "wrong_target": "phun nhầm bia",
        "wrong_nozzle": "phun đúng bia, nhầm vòi",
        "sprayed_correctly": "phun đúng bia đúng vòi",
    }
    for key, text in labels.items():
        base = "no_fire" if key == "false_spray" else "should_spray"
        cells = ""
        for s in args.splits:
            c = result[s]["system"]
            cells += f"{c[key]:7d}/{c[base]:<6d}"
        print(f"{text:36}{cells}")
    out = DIR / ("rules_eval_data.json" if args.data else "rules_eval.json")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Đã ghi {out}")


if __name__ == "__main__":
    main()
