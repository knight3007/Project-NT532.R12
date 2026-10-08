"""Kiểm thử mô hình Jev bằng backbone giả (túi từ), không tải EmbeddingGemma."""

import zlib

import pytest
import torch
from torch import nn

from nt532.decider.jev import (
    JevModel,
    fit_temperature,
    format_sequence,
    gather,
    nll_logits,
)

DIM = 16


class FakeBackbone(nn.Module):
    """Băm từng từ vào bảng embedding rồi lấy trung bình; ghi lại các chuỗi nhận được."""

    dim = DIM

    def __init__(self):
        super().__init__()
        self.emb = nn.EmbeddingBag(64, DIM, mode="mean")
        self.seen: list[str] = []

    def encode(self, texts):
        self.seen += texts
        ids = [[zlib.crc32(w.encode()) % 64 for w in t.split()] for t in texts]
        flat = torch.tensor([i for x in ids for i in x])
        offs = torch.tensor([0] + [len(x) for x in ids[:-1]]).cumsum(0)
        return self.emb(flat, offs)

    def trainable_state(self):
        return {k: v.detach().clone() for k, v in self.state_dict().items()}

    def load_trainable_state(self, state):
        self.load_state_dict(state)


def rec(state="stage: decide", k=3, label=0, fire=True):
    cands = [f"T{i}" for i in range(k)]
    return {
        "state": state,
        "questions": {"real_fire": {"type": "boolean", "candidates": []},
                      "target": {"type": "choice", "candidates": cands}},
        "labels": {"real_fire": fire, "target": cands[label]},
    }


def make_model(seed=0):
    torch.manual_seed(seed)
    return JevModel(FakeBackbone(), hidden=16, heads=2)


def test_format_sequence():
    assert format_sequence("S", "nozzle", "s1").endswith("question: which nozzle should spray?\nanswer: s1")
    assert "answer" not in format_sequence("S", "real_fire", None)


def test_dynamic_candidates_in_one_batch_and_regrouping():
    m = make_model().eval()
    recs = [rec(k=2), rec(k=5), rec("stage: verify", k=3)]
    out = m.logits(recs)
    assert [o["target"].shape[0] for o in out] == [2, 5, 3]
    assert all(o["real_fire"].dim() == 0 for o in out)
    # một lần encode, mỗi ứng viên một chuỗi + 1 chuỗi boolean mỗi bản ghi
    assert len(m.backbone.seen) == (2 + 5 + 3) + 3
    assert m.backbone.seen[0] == format_sequence("stage: decide", "real_fire", None)
    # kết quả không phụ thuộc việc ghép lô (padding)
    single = m.logits([recs[1]])[0]["target"]
    assert torch.allclose(single, out[1]["target"], atol=1e-5)


def test_choice_head_permutation_invariant():
    m = make_model().eval()
    r = rec(k=4)
    base = m.logits([r])[0]["target"]
    perm = [2, 0, 3, 1]
    r2 = rec(k=4)
    r2["questions"]["target"]["candidates"] = [f"T{i}" for i in perm]
    shuffled = m.logits([r2])[0]["target"]
    assert torch.allclose(shuffled, base[perm], atol=1e-5)


def test_loss_backward_and_learns():
    m = make_model().eval()  # tắt dropout cho ổn định
    recs = [rec(k=3, label=i % 3, fire=bool(i % 2), state=f"stage: decide s{i}") for i in range(6)]
    opt = torch.optim.Adam(m.parameters(), lr=0.05)
    first, stats = m.loss(recs)
    assert stats["target"][1] == 6
    for _ in range(200):
        opt.zero_grad()
        loss, _ = m.loss(recs)
        loss.backward()
        opt.step()
    assert loss.item() < first.item() * 0.5


def test_temperature_fit_lowers_nll():
    g = torch.Generator().manual_seed(0)
    y = torch.randint(0, 3, (400,), generator=g)
    logits = torch.randn(400, 3, generator=g) * 4  # quá tự tin và nhiều nhiễu
    logits[torch.arange(400), y] += 3
    ls, ts = list(logits), y.tolist()
    t = fit_temperature(ls, ts)
    assert t > 1.0
    assert nll_logits(ls, ts, t) < nll_logits(ls, ts)
    # số ứng viên khác nhau trong cùng một tập
    mixed = [v[: 2 + i % 2] for i, v in enumerate(ls)]
    tm = fit_temperature(mixed, [min(t, len(v) - 1) for t, v in zip(ts, mixed, strict=True)])
    assert 0.05 <= tm <= 20.0
    # boolean
    yb = torch.randint(0, 2, (400,), generator=g)
    lb = (yb * 2 - 1).float() * 1.0 + torch.randn(400, generator=g) * 3
    lb = lb * 4
    tb = fit_temperature(list(lb), yb.tolist())
    assert tb > 1.0
    assert nll_logits(list(lb), yb.tolist(), tb) < nll_logits(list(lb), yb.tolist())


def test_save_load_roundtrip(tmp_path):
    m = make_model(1).eval()
    m.temps = {"target": 1.7, "real_fire": 0.8}
    r = rec(k=3)
    before = m.logits([r])[0]
    m.save(tmp_path / "m")
    m2 = JevModel.load(tmp_path / "m", backbone=FakeBackbone()).eval()
    after = m2.logits([r])[0]
    assert m2.temps == m.temps
    assert torch.allclose(before["target"], after["target"], atol=1e-6)
    assert torch.allclose(before["real_fire"], after["real_fire"], atol=1e-6)


def test_decide_output_shape():
    m = make_model().eval()
    m.temps = {"target": 2.0}
    r = rec(k=3)
    out = m.decide(r["state"], r["questions"])
    assert set(out) == {"real_fire", "target"}
    t = out["target"]
    assert t["answer"] in r["questions"]["target"]["candidates"]
    assert set(t["probs"]) == set(r["questions"]["target"]["candidates"])
    assert sum(t["probs"].values()) == pytest.approx(1.0)
    assert t["confidence"] == pytest.approx(max(t["probs"].values()))
    b = out["real_fire"]
    assert isinstance(b["answer"], bool)
    assert b["probs"]["true"] + b["probs"]["false"] == pytest.approx(1.0)


def test_gather_targets():
    m = make_model().eval()
    recs = [rec(k=3, label=2, fire=True), rec(k=2, label=1, fire=False)]
    g = gather(recs, m.logits(recs))
    assert g["target"][1] == [2, 1]
    assert g["real_fire"][1] == [1, 0]
