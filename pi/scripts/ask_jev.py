"""Gõ tay một STATE rồi xem mô hình Jev trả lời thế nào.

    uv run --no-sync python scripts/ask_jev.py                    # jev1, cuda nếu có
    uv run --no-sync python scripts/ask_jev.py --run jev1 --device cpu --split test
    uv run --no-sync python scripts/ask_jev.py --check            # chỉ kiểm tra dựng questions, không nạp mô hình
    uv run --no-sync python scripts/ask_jev.py --check --data-dir ../runs/decider/scenarios_3d

Trong vòng lặp: dán STATE nhiều dòng rồi gõ một dòng chỉ có `.`; hoặc dùng lệnh `:vd`, `:sua`,
`:file`, `:xem`, `:raw`, `:giup`, `:thoat`. Câu hỏi dựng từ văn bản bằng questions_from_state.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from nt532.config import REPO_ROOT
from nt532.decider.state_text import questions_from_state

OUT = REPO_ROOT / "runs/decider/jev"
SCEN = REPO_ROOT / "runs/decider/scenarios"  # đổi được bằng --data-dir

HELP = """Dán STATE (nhiều dòng), kết thúc bằng một dòng chỉ có `.` để hỏi mô hình.
  :vd [n|id]    nạp ví dụ n (chỉ số) hoặc id trong split, in STATE, trả lời, so nhãn đúng và luật
  :sua          sửa STATE hiện tại bằng trình soạn thảo ($EDITOR, notepad/nano) rồi trả lời lại
  :file <path>  đọc STATE từ file rồi trả lời
  :xem          in STATE hiện tại
  :raw          bật/tắt xác suất chưa hiệu chỉnh (nhiệt độ)
  :giup         bảng này
  :thoat        thoát (hoặc Ctrl+D / Ctrl+C)"""


def load_split(split: str) -> list[dict]:
    with (SCEN / f"{split}.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def check(splits: list[str]) -> int:
    """So questions_from_state với questions của từng bản ghi. Trả số lệch ứng viên."""
    bad = 0
    for split in splits:
        recs = load_split(split)
        cand_bad = missing = extra = 0
        for r in recs:
            got, want = questions_from_state(r["state"]), r["questions"]
            for q, spec in want.items():
                if q not in got:
                    missing += 1
                elif got[q] != spec:
                    cand_bad += 1
                    if cand_bad <= 3:
                        print(f"  lệch {r['id']} {q}: {got[q]} != {spec}")
            extra += sum(q not in want for q in got)
        print(f"{split}: {len(recs)} bản ghi | lệch ứng viên {cand_bad} | thiếu câu hỏi {missing} "
              f"| câu thừa {extra} (nozzle: bản ghi không có vòi đủ điều kiện)")
        bad += cand_bad + missing
    return bad


def bar(p: float, width: int = 10) -> str:
    n = round(p * width)
    return "█" * n + "░" * (width - n)


def rule_labels(rec: dict, geo) -> dict:
    from nt532.decider.rules import rule_answers

    return rule_answers(rec, geo)


def ask(model, state: str, calibrated: bool, rec: dict | None = None, geo=None) -> None:
    questions = questions_from_state(state)
    t0 = time.perf_counter()
    logits = model.predict_logits([{"state": state, "questions": questions}])[0]
    ms = (time.perf_counter() - t0) * 1000
    labels = rec["labels"] if rec else {}
    rules = rule_labels(rec, geo) if rec else {}
    for qid, lg in logits.items():
        p = model.probs(qid, lg.float().cpu(), calibrated)
        if lg.dim() == 0:
            names, answers = ["false", "true"], [False, True]
        else:
            names = answers = questions[qid]["candidates"]
        top = int(p.argmax())
        print(f"\n[{qid}] -> {answers[top]}  (tin cậy {p[top]:.2f})")
        for i in sorted(range(len(p)), key=lambda j: -p[j]):
            mark = "*" if i == top else " "
            print(f" {mark} {bar(p[i])} {p[i]:.2f}  {names[i]}")
        if qid in labels:
            ok = "đúng" if labels[qid] == answers[top] else "SAI"
            print(f"   nhãn đúng: {labels[qid]} ({ok}) | luật: {rules.get(qid)}")
    print(f"\n(decide {ms:.0f} ms, {'đã hiệu chỉnh' if calibrated else 'chưa hiệu chỉnh'})")


def read_state() -> str | None:
    """Đọc đến dòng `.`; trả None khi hết input trước khi có nội dung."""
    lines = []
    while True:
        try:
            line = input("> " if not lines else "")
        except EOFError:
            return "\n".join(lines) if lines else None
        s = line.strip()
        if not lines and s.startswith(":"):
            return s
        if s == ".":
            return "\n".join(lines)
        lines.append(line.rstrip())


def edit(state: str) -> str:
    editor = os.environ.get("EDITOR") or ("notepad" if sys.platform == "win32" else "nano")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(state + "\n")
        path = f.name
    try:
        subprocess.run([*editor.split(), path], check=False)
        return Path(path).read_text(encoding="utf-8").strip()
    finally:
        os.unlink(path)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="jev1")
    p.add_argument("--device", default=None)
    p.add_argument("--precision", choices=["auto", "bf16", "fp32"], default="fp32")
    p.add_argument("--split", default="val", help="split lấy ví dụ cho :vd")
    p.add_argument("--check", action="store_true",
                   help="chỉ so questions_from_state với val và test rồi thoát")
    p.add_argument("--data-dir", default=None, metavar="DIR",
                   help="thư mục {split}.jsonl thay cho runs/decider/scenarios")
    args = p.parse_args()
    if args.data_dir:
        global SCEN
        SCEN = Path(args.data_dir)
    if args.check:
        sys.exit(1 if check(["val", "test"]) else 0)

    import torch

    from nt532.decider.jev import JevModel
    from nt532.decider.scenario import load_geometry

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"đang nạp {args.run} trên {device} ({args.precision}) ...", flush=True)
    model = JevModel.load(OUT / args.run, device=device, precision=args.precision)
    model.eval()
    geo = load_geometry(with_nodes=False)
    examples = load_split(args.split) if (SCEN / f"{args.split}.jsonl").exists() else []
    print(f"sẵn sàng, {len(examples)} ví dụ trong {args.split}. Gõ :giup để xem lệnh.")

    state, calibrated = "", True
    while True:
        try:
            text = read_state()
        except KeyboardInterrupt:
            text = None
        if text is None:
            break
        rec = None
        if text.startswith(":"):
            cmd, _, arg = text.partition(" ")
            arg = arg.strip()
            if cmd == ":thoat":
                break
            elif cmd == ":giup":
                print(HELP)
                continue
            elif cmd == ":xem":
                print(state or "(chưa có STATE)")
                continue
            elif cmd == ":raw":
                calibrated = not calibrated
                print("xác suất:", "đã hiệu chỉnh" if calibrated else "chưa hiệu chỉnh (raw)")
                continue
            elif cmd == ":vd":
                if arg.isdigit() and int(arg) < len(examples):
                    rec = examples[int(arg)]
                else:
                    rec = next((r for r in examples if r["id"] == arg), None)
                    if rec is None and not arg and examples:
                        rec = examples[0]
                if rec is None:
                    print(f"không thấy ví dụ {arg!r} trong {args.split}")
                    continue
                state = rec["state"]
                print(f"# {rec['id']}\n{state}")
            elif cmd == ":sua":
                if not state:
                    print("chưa có STATE để sửa")
                    continue
                state = edit(state)
            elif cmd == ":file":
                try:
                    state = Path(arg).read_text(encoding="utf-8").strip()
                except OSError as e:
                    print(f"không đọc được file: {e}")
                    continue
            else:
                print("lệnh lạ, gõ :giup")
                continue
        else:
            state = text
        if not state.strip():
            continue
        try:
            ask(model, state, calibrated, rec, geo)
        except Exception as e:  # noqa: BLE001 - STATE gõ sai thì báo, không thoát vòng lặp
            print(f"lỗi: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
