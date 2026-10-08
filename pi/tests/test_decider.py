import numpy as np
import torch

from nt532.decider import (
    Head,
    Question,
    decide,
    ece,
    fit_temperature,
    nll,
    score,
    train_head,
)


def unit(x):
    return x / x.norm(dim=-1, keepdim=True)


def test_score_identity_is_cosine():
    torch.manual_seed(0)
    s, c = unit(torch.randn(5, 16)), unit(torch.randn(3, 16))
    out = score(s, torch.eye(16), c)
    assert out.shape == (5, 3)
    assert torch.allclose(out, s @ c.T, atol=1e-6)


def test_fit_temperature_lowers_nll():
    torch.manual_seed(0)
    y = torch.randint(0, 3, (500,))
    scores = torch.randn(500, 3) * 0.5
    scores[torch.arange(500), y] += 1.0  # đúng phần lớn nhưng quá tự tin ở T=0.05
    t = fit_temperature(scores, y)
    assert nll(scores, y, t) < nll(scores, y, 0.05)
    assert 0.1 < t < 5


def test_ece_toy():
    # luôn tự tin 0.9 nhưng chỉ đúng một nửa -> ECE khoảng 0.4
    probs = np.tile([0.9, 0.1], (100, 1))
    y = np.array([0, 1] * 50)
    assert abs(ece(probs, y) - 0.4) < 1e-6
    # hiệu chỉnh hoàn hảo
    probs = np.tile([1.0, 0.0], (10, 1))
    assert ece(probs, np.zeros(10, int)) == 0.0


def test_train_head_learns_and_decide_shape():
    torch.manual_seed(0)
    d, k = 16, 3
    cand = unit(torch.randn(k, d))
    y = torch.randint(0, k, (300,))
    # ảnh nằm lệch khỏi lời mô tả: cần W khác ma trận đơn vị
    rot = torch.linalg.qr(torch.randn(d, d))[0]
    x = unit(cand[y] @ rot + 0.1 * torch.randn(300, d))
    W, _ = train_head(
        {"q": (x[:200], y[:200])}, {"q": (x[200:], y[200:])}, {"q": cand}, epochs=40, lr=5e-3
    )
    acc0 = (score(x[200:], torch.eye(d), cand).argmax(1) == y[200:]).float().mean()
    acc1 = (score(x[200:], W, cand).argmax(1) == y[200:]).float().mean()
    assert acc1 > acc0

    head = Head(W, {"q": Question(["a", "b", "c"], cand, 0.05)})
    out = decide(x[0], head)
    assert set(out) == {"q"}
    assert out["q"]["answer"] in "abc"
    assert abs(sum(out["q"]["probs"].values()) - 1) < 1e-5
    assert out["q"]["confidence"] == max(out["q"]["probs"].values())
