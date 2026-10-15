"""Đo mô hình Jev trên test và test_shift, so với baseline luật và chính sách lai.

    uv run python scripts/eval_jev.py --name jev1 --device cuda
    uv run python scripts/eval_jev.py --name smoke --device cpu --max-records 20

Dùng lại logic của eval_rules.py nên số liệu so sánh trực tiếp được. Chính sách lai: dùng đáp án
mô hình khi độ tin cậy >= tau, ngược lại dùng đáp án luật (từng câu hỏi tách riêng). Đo thêm độ trễ
decide() mỗi bản ghi. Ghi runs/decider/jev/<name>/eval.json.

Chọn bộ dữ liệu: --data-dir ../runs/decider/scenarios_3d_corners. Chính sách lai theo câu hỏi:
--rule-questions nozzle luôn lấy đáp án luật cho các câu đó, các câu còn lại dùng mô hình (thêm
biến thể + tau). Khi dùng nó thì kết quả ghi eval_perq.json thay cho eval.json (--out đổi tên).
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np
import torch
from eval_rules import QUESTIONS, evaluate
from train_jev import OUT, SCEN

from nt532.decider.head import ece
from nt532.decider.jev import JevModel, gather
from nt532.decider.rules import rule_answers
from nt532.decider.scenario import load_geometry

TAUS = (0.6, 0.8, 0.9)


def load_full(split: str, limit: int, data_dir: Path = SCEN) -> list[dict]:
    with (data_dir / f"{split}.jsonl").open(encoding="utf-8") as f:
        recs = [json.loads(line) for line in f]
    return recs[:limit] if limit else recs


def model_answers(model: JevModel, records: list[dict], logits: list[dict]):
    """Đáp án và độ tin cậy (đã hiệu chỉnh) của mô hình cho từng bản ghi."""
    answers, confs = [], []
    for r, o in zip(records, logits, strict=True):
        a, c = {}, {}
        for qid, lg in o.items():
            p = model.probs(qid, lg)
            i = int(p.argmax())
            a[qid] = bool(i) if lg.dim() == 0 else r["questions"][qid]["candidates"][i]
            c[qid] = float(p[i])
        answers.append(a)
        confs.append(c)
    return answers, confs


def calibration(model: JevModel, records: list[dict], logits: list[dict]) -> dict:
    out = {}
    for qid, (ls, ts) in gather(records, logits).items():
        y = np.array(ts)
        res = {}
        for tag, cal in (("raw", False), ("cal", True)):
            probs = [model.probs(qid, v, cal) for v in ls]
            k = max(len(p) for p in probs)
            arr = np.array([np.pad(p, (0, k - len(p))) for p in probs])
            res[f"ece_{tag}"] = ece(arr, y)
        res["T"] = model.temps.get(qid, 1.0)
        out[qid] = res
    return out


def latency(model: JevModel, records: list[dict], n: int, device: str) -> dict:
    sample = records[:n]
    for r in sample[:2]:
        model.decide(r["state"], r["questions"])
    ts = []
    for r in sample:
        if device.startswith("cuda"):
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        model.decide(r["state"], r["questions"])
        if device.startswith("cuda"):
            torch.cuda.synchronize()
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return {"n": len(ts), "median_ms": statistics.median(ts),
            "p95_ms": ts[min(len(ts) - 1, int(0.95 * len(ts)))]}


def hybrid(records, rules, ans, conf, tau):
    mixed, used, total = [], 0, 0
    for r_ans, m_ans, m_conf in zip(rules, ans, conf, strict=True):
        h = {}
        for qid, a in m_ans.items():
            total += 1
            if m_conf[qid] >= tau or qid not in r_ans:
                h[qid] = a
                used += 1
            else:
                h[qid] = r_ans[qid]
        mixed.append(h)
    return mixed, used / max(total, 1)


def per_question(rules, ans, conf, rule_qs, tau=None):
    """Câu trong rule_qs luôn theo luật; câu khác theo mô hình (nếu có tau thì lùi về luật khi conf < tau)."""
    mixed = []
    for r_ans, m_ans, m_conf in zip(rules, ans, conf, strict=True):
        h = {}
        for qid, a in m_ans.items():
            keep = qid not in rule_qs and (tau is None or m_conf[qid] >= tau)
            h[qid] = a if keep or qid not in r_ans else r_ans[qid]
        mixed.append(h)
    return mixed


def print_table(res: dict, splits: list[str]) -> None:
    print(f"{'':34}" + "".join(f"{s + ' ' + k:>22}" for s in splits for k in ("luật", "mô hình")))
    for q in QUESTIONS:
        row = ""
        for s in splits:
            for k in ("rules", "model"):
                row += f"{res[s][k]['accuracy'].get(q, float('nan')):22.1%}"
        print(f"{'acc ' + q:34}{row}")
    labels = {"false_spray": ("phun khi không có lửa", "no_fire"),
              "missed_spray": ("lửa thật cần phun mà không phun", "should_spray"),
              "wrong_target": ("phun nhầm bia", "should_spray"),
              "wrong_nozzle": ("phun đúng bia, nhầm vòi", "should_spray"),
              "one_when_two": ("chọn một vòi khi cần hai", "should_spray"),
              "two_when_one": ("chọn hai vòi khi một là đủ", "should_spray"),
              "sprayed_correctly": ("phun đúng bia đúng vòi", "should_spray")}
    for key, (text, base) in labels.items():
        row = ""
        for s in splits:
            for k in ("rules", "model"):
                c = res[s][k]["system"]
                row += f"{c[key]:15d}/{c[base]:<6d}"
        print(f"{text:34}{row}")
    for size in ("small", "large"):
        row = ""
        for s in splits:
            for k in ("rules", "model"):
                c = res[s][k]["nozzle_by_size"][size]
                row += f"{c['correct'] / c['asked'] if c['asked'] else float('nan'):22.1%}"
        print(f"{'acc nozzle, lửa ' + size:34}{row}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="jev")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--precision", choices=["auto", "bf16", "fp32"], default="auto")
    p.add_argument("--splits", nargs="+", default=["test", "test_shift"])
    p.add_argument("--max-records", type=int, default=0, help="chỉ đo N bản ghi đầu mỗi split")
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--data-dir", type=Path, default=SCEN,
                   help="thư mục chứa {split}.jsonl (mặc định runs/decider/scenarios)")
    p.add_argument("--rule-questions", nargs="+", default=[], choices=QUESTIONS,
                   help="các câu hỏi luôn dùng đáp án luật (chính sách lai theo câu hỏi)")
    p.add_argument("--out", default="", help="tên file kết quả (mặc định eval.json, hoặc eval_perq.json)")
    p.add_argument("--lat-n", type=int, default=100, help="số bản ghi đo độ trễ decide()")
    args = p.parse_args()

    geo = load_geometry(with_nodes=False)
    model = JevModel.load(OUT / args.name, device=args.device, precision=args.precision)
    model.eval()
    rule_qs = set(args.rule_questions)
    result: dict = {}
    for split in args.splits:
        recs = load_full(split, args.max_records, args.data_dir)
        # mô hình chỉ nhận state + questions; obs chỉ để chạy luật
        slim = [{"state": r["state"], "questions": r["questions"], "labels": r["labels"]}
                for r in recs]
        t0 = time.time()
        logits = model.predict_logits(slim, args.batch)
        print(f"{split}: {len(recs)} bản ghi, {time.time() - t0:.0f}s", flush=True)
        ans, conf = model_answers(model, slim, logits)
        rules = [rule_answers(r, geo) for r in recs]
        res = {"n_records": len(recs),
               "rules": evaluate(recs, geo, rules),
               "model": evaluate(recs, geo, ans),
               "calibration": calibration(model, slim, logits), "hybrid": {}}
        for tau in TAUS:
            mixed, cov = hybrid(recs, rules, ans, conf, tau)
            res["hybrid"][str(tau)] = {**evaluate(recs, geo, mixed), "coverage": cov}
        if rule_qs:
            res["per_question"] = {}
            for tau in (None, *TAUS):
                mixed = per_question(rules, ans, conf, rule_qs, tau)
                res["per_question"]["rule_only" if tau is None else str(tau)] = evaluate(
                    recs, geo, mixed)
        res["latency"] = latency(model, slim, args.lat_n, args.device)
        result[split] = res

    print("\n== Độ chính xác và lỗi hệ thống ==")
    print_table(result, args.splits)
    print("\n== ECE (trước -> sau nhiệt độ) ==")
    for s in args.splits:
        for q, c in result[s]["calibration"].items():
            print(f"{s:12}{q:14} T {c['T']:.2f}  ECE {c['ece_raw']:.3f} -> {c['ece_cal']:.3f}")
    print("\n== Chính sách lai (mô hình khi conf >= tau, ngược lại luật) ==")
    for s in args.splits:
        for tau, h in result[s]["hybrid"].items():
            sy = h["system"]
            print(f"{s:12}tau {tau}: phủ mô hình {h['coverage']:.1%} | phun nhầm "
                  f"{sy['false_spray']}/{sy['no_fire']} | bỏ sót {sy['missed_spray']}/"
                  f"{sy['should_spray']} | đúng bia+vòi {sy['sprayed_correctly']}/{sy['should_spray']}"
                  f" | acc " + " ".join(f"{q} {a:.1%}" for q, a in h["accuracy"].items()))
    if rule_qs:
        print(f"\n== Lai theo câu hỏi (luật cho {sorted(rule_qs)}, còn lại mô hình; +tau) ==")
        for s in args.splits:
            for tau, h in result[s]["per_question"].items():
                sy = h["system"]
                name = "không tau" if tau == "rule_only" else f"tau {tau}"
                print(f"{s:12}{name:10}: phun nhầm {sy['false_spray']}/{sy['no_fire']} | bỏ sót "
                      f"{sy['missed_spray']}/{sy['should_spray']} | nhầm bia {sy['wrong_target']}"
                      f" | nhầm vòi {sy['wrong_nozzle']} | một khi cần hai {sy['one_when_two']}"
                      f" | hai khi một đủ {sy['two_when_one']} | đúng bia+vòi "
                      f"{sy['sprayed_correctly']}/{sy['should_spray']} | acc "
                      + " ".join(f"{q} {a:.1%}" for q, a in h["accuracy"].items()))
                for size in ("small", "large"):
                    c = h["nozzle_by_size"][size]
                    print(f"{'':22}nozzle lửa {size}: "
                          f"{c['correct'] / c['asked'] if c['asked'] else float('nan'):.1%}")
    print("\n== Độ trễ decide() ==")
    for s in args.splits:
        lt = result[s]["latency"]
        print(f"{s:12}trung vị {lt['median_ms']:.0f} ms, p95 {lt['p95_ms']:.0f} ms "
              f"({lt['n']} bản ghi, {args.device})")
    out = OUT / args.name / (args.out or ("eval_perq.json" if rule_qs else "eval.json"))
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Đã ghi {out}")


if __name__ == "__main__":
    main()
