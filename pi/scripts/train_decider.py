"""Huấn luyện và đánh giá mô hình quyết định trên embedding đã cache (EmbeddingGemma 2 đóng băng).

    uv run python scripts/embed_cache.py          # tạo cache ảnh trước
    uv run python scripts/train_decider.py        # chọn tiền tố, train W, khớp T, đo trên test

So sánh zero-shot (W=I), đầu đã train, và baseline YOLO trên cùng ảnh test.
Hai câu hỏi: fire_class (A/B/C/D/F/none, ClassesOfFire) và real_fire (yes/no, fire-mix).
"""

import argparse
import time

import numpy as np
import torch

from nt532.config import REPO_ROOT
from nt532.decider import (
    QUESTIONS,
    Head,
    Question,
    candidate_texts,
    decide,
    ece,
    fit_temperature,
    score,
    train_head,
)

CACHE = REPO_ROOT / "runs/decider/cache"
OUT = REPO_ROOT / "runs/decider"
DATASETS = {"fire_class": "cof", "real_fire": "mix"}
PREFIXES = {
    "none": "",
    "classification": "task: classification | query: ",
    "document": "title: none | text: ",
    "search": "task: search result | query: ",
}
YOLO_CLS = REPO_ROOT / "models/cof26-n-cls-20261004.pt"
YOLO_DET = {
    "fire-n": REPO_ROOT / "models/fire-n.pt",  # DEFAULT_WEIGHTS của nt532.vision.detect
    "mix26-neg": REPO_ROOT / "models/mix26-neg-20261003.pt",
}
DET_THRESHOLDS = (0.25, 0.35, 0.5)
SELECT = (0.6, 0.8, 0.9)


def load(qid: str, split: str) -> dict:
    z = np.load(CACHE / f"{DATASETS[qid]}_{split}.npz")
    return {k: z[k] for k in z.files}


def tensors(d: dict) -> tuple[torch.Tensor, torch.Tensor]:
    return torch.from_numpy(d["emb"]), torch.from_numpy(d["labels"]).long()


def cand_embeddings(prefix: str) -> dict[str, torch.Tensor]:
    """Nhúng các câu mô tả bằng phía văn bản; cache theo tiền tố."""
    path = CACHE / f"cand_{prefix}.npz"
    if not path.exists():
        from embed_cache import load_model

        model = load_model("cuda" if torch.cuda.is_available() else "cpu")
        arrays = {}
        for qid in QUESTIONS:
            _, texts = candidate_texts(qid, PREFIXES[prefix])
            e = model.encode(texts, normalize_embeddings=True)
            arrays[qid] = e / np.linalg.norm(e, axis=1, keepdims=True)
        np.savez(path, **arrays)
    z = np.load(path)
    return {q: torch.from_numpy(z[q]).float() for q in QUESTIONS}


def summary(pred: np.ndarray, y: np.ndarray, k: int) -> dict:
    cm = np.zeros((k, k), int)
    np.add.at(cm, (y, pred), 1)
    tp = np.diag(cm).astype(float)
    p = tp / np.maximum(cm.sum(0), 1)
    r = tp / np.maximum(cm.sum(1), 1)
    f = 2 * p * r / np.maximum(p + r, 1e-12)
    present = cm.sum(1) > 0
    return {
        "acc": float((pred == y).mean()),
        "f1": float(f[present].mean()),
        "bal_acc": float(r[present].mean()),
        "p": p,
        "r": r,
    }


def selective(probs: np.ndarray, y: np.ndarray) -> str:
    conf, hit = probs.max(1), probs.argmax(1) == y
    cells = []
    for t in SELECT:
        m = conf >= t
        acc = hit[m].mean() if m.any() else float("nan")
        cells.append(f">={t}: acc {acc:.3f} cov {m.mean():.2f}")
    return " | ".join(cells)


def yolo_cls_test(names: list[str]) -> np.ndarray:
    """Dự đoán của YOLO phân loại, theo đúng thứ tự ảnh trong cache test."""
    from ultralytics import YOLO

    d = load("fire_class", "test")
    model = YOLO(str(YOLO_CLS))
    remap = np.array([names.index(model.names[i]) for i in range(len(model.names))])
    files = [str(REPO_ROOT / p) for p in d["paths"]]
    pred = []
    for i in range(0, len(files), 64):
        pred += [r.probs.top1 for r in model.predict(files[i : i + 64], verbose=False)]
    return remap[np.array(pred)]


def yolo_det_test(weights) -> np.ndarray:
    """Độ tin cậy lớn nhất của ô lửa trên mỗi ảnh test của fire-mix."""
    from ultralytics import YOLO

    d = load("real_fire", "test")
    model = YOLO(str(weights))
    fire = next(i for i, n in model.names.items() if n == "fire")
    files = [str(REPO_ROOT / p) for p in d["paths"]]
    best = []
    for i in range(0, len(files), 32):
        for r in model.predict(files[i : i + 32], conf=min(DET_THRESHOLDS), verbose=False):
            s = [c for c, k in zip(r.boxes.conf.tolist(), r.boxes.cls.tolist()) if k == fire]
            best.append(max(s, default=0.0))
    return np.array(best)


def fa_by_source(pred: np.ndarray, d: dict) -> str:
    """Tỉ lệ báo nhầm (đoán yes) trên ảnh không có lửa, theo nguồn."""
    cells = []
    for s in np.unique(d["source"]):
        m = (d["source"] == s) & (d["labels"] == 0)
        if m.any():
            cells.append(f"{s} {pred[m].mean():.1%} (n={m.sum()})")
    return " | ".join(cells)


def print_cls(tag: str, s: dict, names: list[str]) -> None:
    pr = " ".join(f"{n}:{s['p'][i]:.2f}/{s['r'][i]:.2f}" for i, n in enumerate(names))
    print(f"{tag:14} acc {s['acc']:.3f} F1 {s['f1']:.3f} balAcc {s['bal_acc']:.3f} | P/R {pr}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lams", type=float, nargs="+", default=[0.0, 1e-3, 1e-2, 1e-1])
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--no-baseline", action="store_true")
    args = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    qids = list(QUESTIONS)
    data = {s: {q: load(q, s) for q in qids} for s in ("train", "val", "test")}
    names = {q: [str(n) for n in data["test"][q]["names"]] for q in qids}
    eye = torch.eye(768)

    # 1. Chọn tiền tố cho câu mô tả theo macro-F1 zero-shot trên val (trung bình hai câu hỏi).
    print("== Chọn tiền tố văn bản (zero-shot, val, macro-F1) ==")
    best_prefix, best_f1 = "none", -1.0
    for pre in PREFIXES:
        c = cand_embeddings(pre)
        f1s = []
        for q in qids:
            x, y = tensors(data["val"][q])
            pred = score(x, eye, c[q]).argmax(1).numpy()
            f1s.append(summary(pred, y.numpy(), len(names[q]))["f1"])
        print(f"  {pre:15} " + "  ".join(f"{q} {f:.3f}" for q, f in zip(qids, f1s, strict=True)))
        if np.mean(f1s) > best_f1:
            best_prefix, best_f1 = pre, float(np.mean(f1s))
    print(f"-> dùng tiền tố '{best_prefix}'")
    cand = cand_embeddings(best_prefix)

    # 2. Train W (chọn lam theo val), khớp T trên val.
    train = {q: tensors(data["train"][q]) for q in qids}
    val = {q: tensors(data["val"][q]) for q in qids}
    best = None
    print("\n== Chọn lam (weight decay về ma trận đơn vị) ==")
    for lam in args.lams:
        t0 = time.time()
        W, hist = train_head(train, val, cand, lam=lam, lr=args.lr, epochs=args.epochs, device=dev)
        v = min(h["val"] for h in hist)
        print(f"  lam {lam:g}: val loss {v:.4f}, {len(hist)} epoch, {time.time() - t0:.0f}s")
        if best is None or v < best[0]:
            best = (v, lam, W, hist)
    _, lam, W, hist = best
    print(f"-> lam {lam:g}, epoch tốt nhất {int(np.argmin([h['val'] for h in hist])) + 1}")

    heads = {}
    for tag, Wm in (("zero-shot", eye), ("trained", W)):
        qs = {}
        for q in qids:
            x, y = val[q]
            qs[q] = Question(names[q], cand[q], fit_temperature(score(x, Wm, cand[q]), y))
        heads[tag] = Head(Wm, qs)
    OUT.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"W": W, "T": {q: heads["trained"].questions[q].T for q in qids}, "prefix": best_prefix},
        OUT / "head.pt",
    )

    # 3. Đánh giá trên test.
    baseline = {}
    if not args.no_baseline:
        baseline["fire_class"] = yolo_cls_test(names["fire_class"])
        baseline["real_fire"] = {n: yolo_det_test(w) for n, w in YOLO_DET.items()}

    for q in qids:
        d = data["test"][q]
        x, y = tensors(d)
        yn, k = y.numpy(), len(names[q])
        print(
            f"\n===== {q}: test n={len(yn)}, đếm lớp {np.bincount(yn, minlength=k).tolist()} ====="
        )
        if q == "fire_class" and baseline:
            print_cls("YOLO26n-cls", summary(baseline[q], yn, k), names[q])
        if q == "real_fire" and baseline:
            for n, conf in baseline[q].items():
                for t in DET_THRESHOLDS:
                    pred = (conf >= t).astype(int)
                    print_cls(f"{n}@{t}", summary(pred, yn, k), names[q])
                    print(f"{'':14} báo nhầm: {fa_by_source(pred, d)}")
        for tag, head in heads.items():
            s = score(x, head.W, cand[q])
            T = head.questions[q].T
            p0, p1 = torch.softmax(s / 0.05, 1).numpy(), torch.softmax(s / T, 1).numpy()
            pred = p1.argmax(1)
            print_cls(tag, summary(pred, yn, k), names[q])
            print(
                f"{'':14} T {T:.4f} | ECE T=0.05 {ece(p0, yn):.3f} -> sau khớp T {ece(p1, yn):.3f}"
            )
            print(f"{'':14} chọn lọc: {selective(p1, yn)}")
            if q == "real_fire":
                print(f"{'':14} báo nhầm: {fa_by_source(pred, d)}")

    # 4. Độ trễ đầu chấm điểm (một ảnh, sau khởi động).
    x = data["test"]["fire_class"]["emb"][0]
    for device in ("cpu", "cuda") if torch.cuda.is_available() else ("cpu",):
        h0 = heads["trained"]
        qs = {q: Question(v.choices, v.cand.to(device), v.T) for q, v in h0.questions.items()}
        h = Head(h0.W.to(device), qs)
        s = torch.from_numpy(x).to(device)
        for _ in range(20):
            decide(s, h)
        if device == "cuda":
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(200):
            decide(s, h)
        if device == "cuda":
            torch.cuda.synchronize()
        ms = (time.perf_counter() - t0) / 200 * 1000
        print(f"\nđầu chấm điểm ({device}): {ms:.3f} ms cho cả hai câu hỏi")


if __name__ == "__main__":
    main()
