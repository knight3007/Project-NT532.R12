"""Đầu chấm điểm: score_i = s^T W c_i, xác suất = softmax(score / T).

W khởi tạo bằng ma trận đơn vị nên W=I chính là cosine zero-shot. Một W dùng chung cho mọi câu
hỏi, mỗi câu hỏi có nhiệt độ T riêng (khớp trên tập val).
"""

from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np
import torch
import torch.nn.functional as F

T0 = 0.05  # nhiệt độ cố định lúc huấn luyện


@dataclass
class Question:
    """Một câu hỏi: tên các lựa chọn và embedding (đã chuẩn hóa L2) của từng lựa chọn."""

    choices: list[str]
    cand: torch.Tensor  # (k, d)
    T: float = T0


@dataclass
class Head:
    W: torch.Tensor  # (d, d)
    questions: dict[str, Question] = field(default_factory=dict)


def score(s: torch.Tensor, W: torch.Tensor, cand: torch.Tensor) -> torch.Tensor:
    """Điểm thô (n, k) = s W c^T. s: (n, d) hoặc (d,)."""
    return (s @ W) @ cand.T


def nll(logits: torch.Tensor, y: torch.Tensor, T: float = 1.0) -> float:
    return F.cross_entropy(logits / T, y).item()


def ece(probs: np.ndarray, y: np.ndarray, bins: int = 15) -> float:
    """Expected calibration error trên độ tin cậy = xác suất lớn nhất."""
    conf = probs.max(1)
    hit = (probs.argmax(1) == y).astype(float)
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in pairwise(edges):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            total += m.mean() * abs(hit[m].mean() - conf[m].mean())
    return float(total)


def fit_temperature(scores: torch.Tensor, y: torch.Tensor) -> float:
    """Tìm T làm NLL nhỏ nhất trên (scores, y); tối ưu log T bằng LBFGS."""
    scores, y = scores.detach().double(), y.detach()
    logt = torch.tensor(float(np.log(T0)), dtype=torch.float64, requires_grad=True)
    opt = torch.optim.LBFGS([logt], lr=0.5, max_iter=100, line_search_fn="strong_wolfe")

    def closure():
        opt.zero_grad()
        loss = F.cross_entropy(scores / logt.exp(), y)
        loss.backward()
        return loss

    opt.step(closure)
    return float(logt.detach().exp().clamp(1e-3, 10.0))


def balanced_weights(y: torch.Tensor, k: int) -> torch.Tensor:
    """Trọng số lớp tỉ lệ nghịch với tần suất (lớp vắng mặt nhận 0), trung bình 1."""
    counts = torch.bincount(y, minlength=k).float()
    w = torch.where(counts > 0, counts.sum() / (k * counts.clamp(min=1)), torch.zeros_like(counts))
    return w


def train_head(
    data: dict[str, tuple[torch.Tensor, torch.Tensor]],
    val: dict[str, tuple[torch.Tensor, torch.Tensor]],
    cands: dict[str, torch.Tensor],
    *,
    lam: float = 0.0,
    lr: float = 1e-3,
    epochs: int = 60,
    batch: int = 256,
    patience: int = 8,
    seed: int = 0,
    device: str = "cpu",
) -> tuple[torch.Tensor, list[dict]]:
    """Huấn luyện một W dùng chung cho mọi câu hỏi.

    Mất mát: cross-entropy cân bằng lớp từng câu hỏi (cộng lại) + lam * ||W - I||^2.
    Dừng sớm theo tổng NLL cân bằng lớp trên val. Trả về (W tốt nhất, lịch sử).
    """
    g = torch.Generator().manual_seed(seed)
    d = next(iter(cands.values())).shape[1]
    eye = torch.eye(d, device=device)
    W = eye.clone().requires_grad_(True)
    opt = torch.optim.Adam([W], lr=lr)
    data = {q: (x.to(device), y.to(device)) for q, (x, y) in data.items()}
    val = {q: (x.to(device), y.to(device)) for q, (x, y) in val.items()}
    cands = {q: c.to(device) for q, c in cands.items()}
    wts = {q: balanced_weights(y, cands[q].shape[0]) for q, (_, y) in data.items()}
    vwts = {q: balanced_weights(y, cands[q].shape[0]) for q, (_, y) in val.items()}

    def val_loss(Wm: torch.Tensor) -> float:
        with torch.no_grad():
            return sum(
                F.cross_entropy(score(x, Wm, cands[q]) / T0, y, weight=vwts[q]).item()
                for q, (x, y) in val.items()
            )

    best, best_W, bad, hist = val_loss(eye), eye.clone(), 0, []
    steps = max(len(y) for _, y in data.values()) // batch + 1
    for ep in range(epochs):
        for _ in range(steps):
            loss = 0.0
            for q, (x, y) in data.items():
                idx = torch.randint(len(y), (batch,), generator=g).to(device)
                logits = score(x[idx], W, cands[q]) / T0
                loss = loss + F.cross_entropy(logits, y[idx], weight=wts[q])
            loss = loss + lam * ((W - eye) ** 2).sum()
            opt.zero_grad()
            loss.backward()
            opt.step()
        v = val_loss(W.detach())
        hist.append({"epoch": ep + 1, "val": v})
        if v < best - 1e-4:
            best, best_W, bad = v, W.detach().clone(), 0
        else:
            bad += 1
            if bad >= patience:
                break
    return best_W.cpu(), hist


def decide(
    state: torch.Tensor | np.ndarray, head: Head, qids: list[str] | None = None
) -> dict[str, dict]:
    """state: embedding (d,) đã chuẩn hóa L2. Trả về {qid: {answer, probs, confidence}}."""
    s = torch.as_tensor(state, dtype=head.W.dtype).reshape(1, -1)
    out = {}
    for qid in qids or list(head.questions):
        q = head.questions[qid]
        p = torch.softmax(score(s, head.W, q.cand)[0] / q.T, dim=0)
        i = int(p.argmax())
        out[qid] = {
            "answer": q.choices[i],
            "probs": {c: float(v) for c, v in zip(q.choices, p)},
            "confidence": float(p[i]),
        }
    return out
