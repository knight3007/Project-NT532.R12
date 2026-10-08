"""Huấn luyện mô hình quyết định kiểu Jev (backbone văn bản EmbeddingGemma 2 + LoRA + đầu Choice/Boolean).

    uv run python scripts/train_jev.py --device cuda --name jev1
    uv run python scripts/train_jev.py --device cpu --max-records 32 --epochs 1 --batch 4 --name smoke
    uv run python scripts/train_jev.py --freeze-backbone --name heads_only     # đối chứng: chỉ train đầu

Train trên scenarios/train.jsonl, đánh giá val định kỳ (mất mát + độ chính xác từng câu hỏi), giữ
bản tốt nhất, rồi khớp nhiệt độ từng câu hỏi trên val và lưu vào runs/decider/jev/<name>/.
Chỉ dùng `state`, `questions`, `labels`; không đọc truth/obs.
"""

import argparse
import json
import math
import random
import time

import numpy as np
import torch

from nt532.config import REPO_ROOT
from nt532.decider.jev import GemmaBackbone, JevModel, fit_temperature, gather, nll_logits

SCEN = REPO_ROOT / "runs/decider/scenarios"
OUT = REPO_ROOT / "runs/decider/jev"


def load_records(split: str, limit: int | None = None) -> list[dict]:
    keep = ("id", "state", "questions", "labels")
    recs = []
    with (SCEN / f"{split}.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            recs.append({k: r[k] for k in keep})
            if limit and len(recs) >= limit:
                break
    return recs


def evaluate(model: JevModel, records: list[dict], batch: int) -> tuple[float, dict]:
    """Mất mát trung bình mỗi bản ghi trên val và độ chính xác từng câu hỏi."""
    model.eval()
    tot, n = 0.0, 0
    acc: dict[str, list[int]] = {}
    with torch.no_grad():
        for i in range(0, len(records), batch):
            chunk = records[i:i + batch]
            loss, stats = model.loss(chunk)
            tot += loss.item() * len(chunk)
            n += len(chunk)
            for q, (_, c, hit) in stats.items():
                a = acc.setdefault(q, [0, 0])
                a[0] += hit
                a[1] += c
    model.train()
    return tot / n, {q: h / c for q, (h, c) in acc.items()}


def fmt_eta(sec: float) -> str:
    return f"{int(sec // 3600)}h{int(sec % 3600 // 60):02d}m"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--name", default="jev")
    p.add_argument("--epochs", type=float, default=1.0)
    p.add_argument("--batch", type=int, default=4, help="số bản ghi mỗi bước nhỏ")
    p.add_argument("--accum", type=int, default=4, help="số bước nhỏ gộp gradient")
    p.add_argument("--lr", type=float, default=2e-4, help="lr của LoRA")
    p.add_argument("--head-lr", type=float, default=1e-3)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--max-len", type=int, default=512)
    p.add_argument("--max-records", type=int, default=0, help="chỉ lấy N bản ghi train (chạy nhanh)")
    p.add_argument("--val-records", type=int, default=500, help="số bản ghi val dùng khi train")
    p.add_argument("--val-every", type=int, default=100, help="đánh giá val sau mỗi N bước tối ưu")
    p.add_argument("--fit-records", type=int, default=0, help="số bản ghi val để khớp T (0 = hết)")
    p.add_argument("--freeze-backbone", action="store_true", help="đối chứng: bỏ LoRA, chỉ train đầu")
    p.add_argument("--no-checkpointing", action="store_true")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    train = load_records("train", args.max_records or None)
    val_all = load_records("val", args.fit_records or None)
    val = val_all[:args.val_records]
    print(f"train {len(train)} bản ghi, val {len(val)} (khớp T trên {len(val_all)})", flush=True)

    lora_r = 0 if args.freeze_backbone else args.lora_r
    bb = GemmaBackbone(args.device, lora_r=lora_r, max_len=args.max_len,
                       checkpointing=not args.no_checkpointing)
    model = JevModel(bb)
    model.to(args.device).train()
    head_params = list(model.choice.parameters()) + list(model.boolean.parameters())
    lora = [x for x in bb.parameters() if x.requires_grad]
    groups = [{"params": head_params, "lr": args.head_lr, "base_lr": args.head_lr}]
    if lora:
        groups.append({"params": lora, "lr": args.lr, "base_lr": args.lr})
    print(f"tham số train: LoRA {sum(x.numel() for x in lora) / 1e6:.2f}M, "
          f"đầu {sum(x.numel() for x in head_params) / 1e6:.2f}M", flush=True)
    opt = torch.optim.AdamW(groups, weight_decay=0.01)
    all_params = head_params + lora

    per_step = args.batch * args.accum
    total = max(1, math.ceil(len(train) * args.epochs / per_step))
    warm = max(1, total // 20)
    order: list[int] = []
    while len(order) < total * per_step:
        perm = list(range(len(train)))
        random.shuffle(perm)
        order += perm

    best, best_state, hist = float("inf"), None, []
    t0, seen = time.time(), 0
    run_loss = 0.0
    for step in range(1, total + 1):
        if step <= warm:
            scale = step / warm
        else:
            scale = 0.5 * (1 + math.cos(math.pi * (step - warm) / max(1, total - warm)))
        for g in opt.param_groups:
            g["lr"] = g["base_lr"] * scale
        for a in range(args.accum):
            i0 = (step - 1) * per_step + a * args.batch
            chunk = [train[j] for j in order[i0:i0 + args.batch]]
            loss, _ = model.loss(chunk)
            (loss / args.accum).backward()
            run_loss += loss.item() / args.accum
            seen += len(chunk)
        torch.nn.utils.clip_grad_norm_(all_params, 1.0)
        opt.step()
        opt.zero_grad(set_to_none=True)

        rate = seen / (time.time() - t0)
        if step % 5 == 0 or step == total:
            eta = (total * per_step - seen) / rate
            print(f"bước {step}/{total} loss {run_loss / min(step, 5):.3f} "
                  f"{rate:.2f} bản ghi/s ETA {fmt_eta(eta)}", flush=True)
            run_loss = 0.0
        if step % args.val_every == 0 or step == total:
            vl, acc = evaluate(model, val, args.batch * 2)
            hist.append({"step": step, "val_loss": vl, "acc": acc})
            print(f"  val loss {vl:.3f} | " + " ".join(f"{q} {a:.1%}" for q, a in acc.items()),
                  flush=True)
            if vl < best:
                best = vl
                heads = {k: v.clone() for k, v in model.state_dict().items()
                         if not k.startswith("backbone.")}
                best_state = (heads, bb.trainable_state())

    if best_state is not None:
        model.load_state_dict(best_state[0], strict=False)
        bb.load_trainable_state(best_state[1])
        print(f"dùng bản tốt nhất, val loss {best:.3f}")

    print("khớp nhiệt độ trên val ...", flush=True)
    lg = model.predict_logits(val_all, args.batch * 2)
    model.temps = {}
    for qid, (ls, ts) in gather(val_all, lg).items():
        t = fit_temperature(ls, ts)
        print(f"  {qid}: T {t:.3f}  NLL {nll_logits(ls, ts):.4f} -> {nll_logits(ls, ts, t):.4f}")
        model.temps[qid] = t

    out = OUT / args.name
    elapsed = time.time() - t0
    peak_gb = torch.cuda.max_memory_allocated() / 1e9 if args.device.startswith("cuda") else None
    model.save(out, extra={"args": vars(args), "train_records": len(train), "history": hist,
                           "seconds": elapsed, "records_per_s": seen / elapsed,
                           "truncated": bb.n_truncated, "sequences": bb.n_seen})
    lens = np.array(bb.token_lengths)
    print(f"độ dài token: trung vị {np.median(lens):.0f}, p95 {np.percentile(lens, 95):.0f}, "
          f"max {lens.max()}; cắt {bb.n_truncated}/{bb.n_seen} chuỗi (max_len {args.max_len})")
    print(f"xong sau {elapsed:.0f}s, {seen / elapsed:.2f} bản ghi/s"
          + (f", VRAM đỉnh {peak_gb:.1f} GB" if peak_gb else ""))
    print(f"Đã lưu {out}")


if __name__ == "__main__":
    main()
