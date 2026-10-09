"""Xuất các lượt xử lý thật trong nhật ký trạm thành bản ghi cùng định dạng với bộ kịch bản.

    uv run python scripts/export_episodes.py --logs ../runs/station/*.jsonl
    uv run python scripts/export_episodes.py --logs ../runs/station/2026*.jsonl \
        --annotate ../runs/decider/nhan_demo.csv --out ../runs/decider/scenarios/real.jsonl
    uv run python scripts/eval_rules.py --data ../runs/decider/scenarios/real.jsonl

Mỗi quyết định (decide và từng verify) của một lượt đã hoàn tất thành một bản ghi
{id, state, questions, labels, truth, obs, source: "real"}. `truth` chỉ chứa điều đã biết (kết cục
lượt, bia/vòi máy đã chọn, điều người chú thích); nhãn nào không biết thì vắng mặt, không đoán.
Chỉ xuất được lượt có `obs` trong log (log ghi từ bản trạm có ghi obs); lượt chưa kết thúc bị bỏ.

File chú thích (--annotate), mỗi lượt một dòng, ô trống là chưa biết:

    run_id,real_fire,fire_out,target,nozzle,action
    20261009-075812:1,1,1,T1,s1,

run_id = `<tên file log không đuôi>:<số lượt>` (chỉ số lượt là đủ khi chỉ có một log).
real_fire 1/0; fire_out 1/0 = lửa đã tắt thật sau lượt; target = T<n> thật sự cháy (hoặc `none`);
nozzle = vòi nên dùng; action = spray|alarm only|ignore. Nhãn suy ra:
  real_fire 0 -> action ignore, target none of these; real_fire 1 và máy đã phun -> action spray;
  target lấy từ chú thích, hoặc bia máy chọn nếu fire_out = 1; after_verify chỉ gán cho lần
  verify CUỐI của lượt khi biết fire_out (cùng quy tắc nhãn với bộ kịch bản).
"""

import argparse
import csv
import glob
import json
from pathlib import Path

from nt532.config import REPO_ROOT
from nt532.decider.scenario import MAX_ATTEMPTS
from nt532.decider.state_text import ACTIONS, NONE_OF_THESE, VERIFY, render_state, target_candidates

OUT = REPO_ROOT / "runs/decider/scenarios/real.jsonl"
TRUE, FALSE = {"1", "true", "yes", "y", "co", "có", "đúng"}, {"0", "false", "no", "n", "khong", "không", "sai"}


def parse_bool(text: str, where: str) -> bool | None:
    t = text.strip().lower()
    if not t:
        return None
    if t in TRUE:
        return True
    if t in FALSE:
        return False
    raise ValueError(f"{where}: không hiểu giá trị đúng/sai {text!r}")


def read_runs(path: Path) -> dict[int, dict]:
    """{số lượt: {"decisions": [event], "end": event}} chỉ gồm lượt có sự kiện kết thúc."""
    runs: dict[int, dict] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if "run" not in e:
                continue
            r = runs.setdefault(e["run"], {"decisions": [], "end": None})
            if e["kind"] == "decision" and "obs" in e:
                r["decisions"].append(e)
            elif e["kind"] == "run":
                r["end"] = e
    return {k: v for k, v in runs.items() if v["end"] and v["decisions"]}


def read_annotations(path: str | None, stems: list[str]) -> dict[str, dict]:
    if not path:
        return {}
    out = {}
    with Path(path).open(encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), 2):
            rid = (row.get("run_id") or "").strip()
            if ":" not in rid:
                if len(stems) != 1:
                    raise ValueError(f"{path}:{i}: có nhiều log, run_id phải dạng <log>:<lượt>")
                rid = f"{stems[0]}:{rid}"
            where = f"{path}:{i}"
            ann = {"real_fire": parse_bool(row.get("real_fire") or "", where),
                   "fire_out": parse_bool(row.get("fire_out") or "", where),
                   "target": (row.get("target") or "").strip() or None,
                   "nozzle": (row.get("nozzle") or "").strip() or None,
                   "action": (row.get("action") or "").strip() or None}
            out[rid] = {k: v for k, v in ann.items() if v is not None}
    return out


def verify_label(v: dict, fire_out: bool) -> str:
    if fire_out:
        return VERIFY[0]
    if v["status"] == "fault" or v["attempt"] >= MAX_ATTEMPTS:
        return VERIFY[3]
    if v["mark_cm"] is not None and v["mark_cm"] > v["tol_cm"]:
        return VERIFY[1]
    return VERIFY[2]


def decide_labels(obs: dict, questions: dict, ann: dict, end: dict) -> dict:
    labels = {}
    real = ann.get("real_fire")
    if real is not None:
        labels["real_fire"] = real
    if ann.get("action") in ACTIONS:
        labels["action"] = ann["action"]
    elif real is False:
        labels["action"] = ACTIONS[2]
    elif real and end.get("nozzle"):  # máy đã phun thật
        labels["action"] = ACTIONS[0]
    if "target" in questions:
        cands = target_candidates(obs["targets"])
        ids = [t["id"] for t in obs["targets"]]
        want = ann.get("target")
        if want and want.lower() == "none" or real is False:
            labels["target"] = NONE_OF_THESE
        elif want in ids:
            labels["target"] = cands[ids.index(want)]
        elif real and ann.get("fire_out") and (end.get("target") or {}).get("id") in ids:
            labels["target"] = cands[ids.index(end["target"]["id"])]
    if "nozzle" in questions and ann.get("nozzle") in ("s1", "s2"):
        labels["nozzle"] = ann["nozzle"]
    return labels


def episodes_of(stem: str, runs: dict[int, dict], ann: dict[str, dict]) -> list[dict]:
    records = []
    for run_id, r in sorted(runs.items()):
        a, end = ann.get(f"{stem}:{run_id}", {}), r["end"]
        last_attempt = max((e["obs"]["verify"]["attempt"] for e in r["decisions"]
                            if e["obs"]["stage"] == "verify"), default=None)
        for e in r["decisions"]:
            obs, questions = e["obs"], e["questions"]
            truth = {"outcome": end.get("outcome"), "alarm_node": obs["alarm"],
                     "run_target": (end.get("target") or {}).get("id"), "run_nozzle": end.get("nozzle"),
                     "attempts": end.get("attempts"), "annotation": a}
            if obs["stage"] == "verify":
                v = obs["verify"]
                rid = f"real-{stem}-r{run_id}-verify{v['attempt']}"
                labels = {}
                if v["attempt"] == last_attempt and "fire_out" in a:
                    labels["after_verify"] = verify_label(v, a["fire_out"])
            else:
                rid = f"real-{stem}-r{run_id}-decide"
                labels = decide_labels(obs, questions, a, end)
            records.append({"id": rid, "source": "real", "state": render_state(obs),
                            "questions": questions, "labels": labels, "truth": truth, "obs": obs})
    return records


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--logs", nargs="+", required=True, help="nhật ký trạm runs/station/*.jsonl")
    p.add_argument("--annotate", default=None, help="CSV chú thích: run_id,real_fire,fire_out,target,nozzle,action")
    p.add_argument("--out", default=str(OUT))
    p.add_argument("--labeled-only", action="store_true", help="chỉ xuất bản ghi đã có ít nhất một nhãn")
    args = p.parse_args()

    logs = sorted({f for pat in args.logs for f in (glob.glob(pat) or [pat])})
    ann = read_annotations(args.annotate, [Path(f).stem for f in logs])
    records, seen = [], set()
    for f in logs:
        runs = read_runs(Path(f))
        seen |= {f"{Path(f).stem}:{k}" for k in runs}
        recs = episodes_of(Path(f).stem, runs, ann)
        print(f"{Path(f).name}: {len(runs)} lượt hoàn tất, {len(recs)} bản ghi")
        records += recs
    for rid in sorted(set(ann) - seen):
        print(f"cảnh báo: chú thích {rid} không khớp lượt nào")
    if args.labeled_only:
        records = [r for r in records if r["labels"]]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_lab = sum(bool(r["labels"]) for r in records)
    print(f"Đã ghi {len(records)} bản ghi ({n_lab} có nhãn) vào {out}")


if __name__ == "__main__":
    main()
