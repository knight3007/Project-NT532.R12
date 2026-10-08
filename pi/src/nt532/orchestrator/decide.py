"""Bộ quyết định cho orchestrator: luật, mô hình Jev, hoặc lai (mô hình khi đủ tự tin).

Mọi bộ nhận (obs, questions) và trả cùng một dạng `Decision`, nên orchestrator và dashboard
không cần biết bên trong là gì. Các chặn an toàn cứng không nằm ở đây mà ở orchestrator.
"""

import time
from dataclasses import dataclass, field

from ..config import REPO_ROOT
from ..decider.rules import rule_answers
from ..decider.scenario import load_geometry
from ..decider.state_text import render_state

JEV_DIR = REPO_ROOT / "runs/decider/jev"


@dataclass
class Answer:
    answer: object  # bool cho câu boolean, chuỗi ứng viên cho câu choice
    confidence: float
    probs: dict = field(default_factory=dict)  # {ứng viên: xác suất}
    source: str = "rules"  # rules hoặc model


@dataclass
class Decision:
    answers: dict[str, Answer]
    state: str  # đoạn STATE đã đưa vào mô hình (để hiển thị và ghi log)
    latency_ms: float
    decider: str

    def get(self, qid: str, default=None):
        a = self.answers.get(qid)
        return default if a is None else a.answer

    def to_json(self) -> dict:
        return {"decider": self.decider, "latency_ms": round(self.latency_ms, 1), "state": self.state,
                "answers": {q: {"answer": a.answer, "confidence": round(a.confidence, 3),
                                "probs": {k: round(v, 3) for k, v in a.probs.items()},
                                "source": a.source} for q, a in self.answers.items()}}


class RuleDecider:
    """Baseline luật của kế hoạch (nt532.decider.rules): đáp án chắc chắn, xác suất 1."""

    name = "rules"

    def __init__(self) -> None:
        self.geo = load_geometry(with_nodes=False)

    def answers(self, obs: dict, questions: dict) -> dict[str, Answer]:
        raw = rule_answers({"obs": obs, "questions": questions}, self.geo)
        return {q: Answer(a, 1.0, {str(a).lower() if isinstance(a, bool) else a: 1.0}, "rules")
                for q, a in raw.items()}

    def decide(self, obs: dict, questions: dict) -> Decision:
        t0 = time.perf_counter()
        ans = self.answers(obs, questions)
        return Decision(ans, render_state(obs), (time.perf_counter() - t0) * 1000, self.name)


class JevDecider:
    """Mô hình Jev đã train (runs/decider/jev/<name>). Nạp lười ở lần gọi đầu vì mất vài giây."""

    name = "jev"

    def __init__(self, run: str = "jev1", device: str = "cpu", model=None) -> None:
        self.path = JEV_DIR / run
        self.device = device
        self._model = model

    @property
    def model(self):
        if self._model is None:
            from ..decider.jev import JevModel  # kéo torch, transformers: chỉ khi thật sự dùng

            if not (self.path / "config.json").exists():
                raise FileNotFoundError(f"chưa có mô hình Jev ở {self.path}")
            self._model = JevModel.load(self.path, device=self.device)
            self._model.eval()
        return self._model

    def answers(self, obs: dict, questions: dict) -> dict[str, Answer]:
        out = self.model.decide(render_state(obs), questions)
        return {q: Answer(o["answer"], o["confidence"], o["probs"], "model") for q, o in out.items()}

    def decide(self, obs: dict, questions: dict) -> Decision:
        t0 = time.perf_counter()
        ans = self.answers(obs, questions)
        return Decision(ans, render_state(obs), (time.perf_counter() - t0) * 1000, self.name)


class HybridDecider:
    """Từng câu hỏi: dùng đáp án mô hình nếu độ tin cậy (đã hiệu chỉnh) >= tau, ngược lại luật.

    Cùng chính sách với phần `hybrid` của eval_jev.py. Mô hình lỗi (chưa có trọng số, hết bộ
    nhớ...) thì cả lần đó dùng luật.
    """

    def __init__(self, model: JevDecider, rules: RuleDecider | None = None, tau: float = 0.8):
        self.model_decider, self.rules, self.tau = model, rules or RuleDecider(), tau
        self.name = f"hybrid(tau {tau:g})"
        self.last_error: str | None = None

    def decide(self, obs: dict, questions: dict) -> Decision:
        t0 = time.perf_counter()
        rules = self.rules.answers(obs, questions)
        try:
            model = self.model_decider.answers(obs, questions)
            self.last_error = None
        except Exception as e:  # noqa: BLE001 - mô hình hỏng thì vẫn phải quyết định được
            self.last_error = f"{type(e).__name__}: {e}"
            model = {}
        out = {}
        for q, r in rules.items():
            m = model.get(q)
            out[q] = m if m is not None and m.confidence >= self.tau else r
        return Decision(out, render_state(obs), (time.perf_counter() - t0) * 1000, self.name)


def make_decider(kind: str, run: str = "jev1", tau: float = 0.8, device: str = "cpu"):
    if kind == "rules":
        return RuleDecider()
    if kind == "jev":
        return JevDecider(run, device)
    if kind == "hybrid":
        return HybridDecider(JevDecider(run, device), tau=tau)
    raise ValueError(f"không biết bộ quyết định {kind!r} (rules, jev, hybrid)")
