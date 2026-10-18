"""Bộ quyết định cho orchestrator: luật, mô hình Jev, hoặc lai (mô hình khi đủ tự tin).

Mọi bộ nhận (obs, questions) và trả cùng một dạng `Decision`, nên orchestrator và dashboard
không cần biết bên trong là gì. Các chặn an toàn cứng không nằm ở đây mà ở orchestrator.
"""

import time
from dataclasses import dataclass, field

from ..config import REPO_ROOT
from ..decider.rules import rule_answers, rule_nozzle
from ..decider.scenario import load_geometry
from ..decider.state_text import BOTH, NOZZLES, nozzle_healthy, render_state
from .fusion import candidate_id

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

    def __init__(self, run: str = "jev2", device: str = "cpu", model=None) -> None:
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


STAGES = ("decide", "verify")
QUESTION_IDS = ("real_fire", "action", "target", "nozzle", "after_verify")


def parse_stages(text: str) -> frozenset[str]:
    """"verify" hoặc "decide,verify" thành tập giai đoạn; sai tên thì ValueError."""
    stages = frozenset(s.strip() for s in text.split(",") if s.strip())
    bad = stages - set(STAGES)
    if bad or not stages:
        raise ValueError(f"giai đoạn mô hình phải thuộc {STAGES}, nhận {text!r}")
    return stages


def stage_of(obs: dict, questions: dict) -> str:
    """`verify` cho câu after_verify, `decide` cho real_fire, action, target, nozzle."""
    return "verify" if obs.get("stage") == "verify" or "after_verify" in questions else "decide"


def hybrid_nozzle(obs: dict, candidates: list, target, rule_ans: str, model_ans=None,
                  model_conf: float = 0.0, both_tau: float | None = None) -> tuple[str, str]:
    """Đáp án `nozzle` của chế độ lai khi câu này theo luật; trả (đáp án, nguồn).

    Hàm thuần dùng chung cho trạm (HybridDecider) và chấm điểm (eval_jev.py). `target` là đáp án
    `target` cuối cùng (chuỗi ứng viên, hoặc None nếu không có câu target): vòi được chọn cho đúng bia
    đó chứ không cho bia mà luật chọn ("rules@target"). Không có bia hợp lệ hoặc không vòi nào đủ điều
    kiện thì giữ `rule_ans` ("rules"). Có `both_tau`: mô hình trả BOTH với conf >= both_tau, BOTH nằm
    trong `candidates`, cả hai vòi khỏe và với tới bia cuối thì dùng BOTH ("model").
    """
    tid = candidate_id(target) if isinstance(target, str) else None
    if tid is None or tid not in {t["id"] for t in obs["targets"]}:
        return rule_ans, "rules"
    if (both_tau is not None and model_ans == BOTH and model_conf >= both_tau and BOTH in candidates
            and all(nozzle_healthy(obs["nozzles"][n]) and obs["nozzles"][n]["reach"][tid] for n in NOZZLES)):
        return BOTH, "model"
    n = rule_nozzle(obs, tid)
    if n is None:
        return rule_ans, "rules"
    return n, "rules" if n == rule_ans else "rules@target"


class HybridDecider:
    """Từng câu hỏi: dùng đáp án mô hình nếu độ tin cậy (đã hiệu chỉnh) >= tau, ngược lại luật.

    Cùng chính sách với phần `hybrid` của eval_jev.py. Mô hình lỗi (chưa có trọng số, hết bộ
    nhớ...) thì cả lần đó dùng luật. Câu hỏi thuộc giai đoạn không nằm trong `stages` được luật trả
    lời và không gọi mô hình (không tốn độ trễ). Mặc định chỉ hỏi mô hình ở `verify`: DECIDE cần
    nhanh, mô hình Jev mất cỡ 8 s mỗi lần trên CPU, còn luật yếu nhất ở after_verify (62,8% so với
    89,4% của Jev trên tập val).

    `rule_questions`: các câu luôn lấy đáp án luật, bất kể giai đoạn và độ tin cậy (như
    `--rule-questions` của eval_jev.py, vd {"nozzle"}). Lần gọi chỉ gồm các câu đó thì không gọi mô hình.
    Riêng `nozzle`, vòi được chọn theo bia cuối cùng (sau khi lai) chứ không theo bia của luật
    (hybrid_nozzle). `both_tau` (mặc định tắt): bật thì vẫn hỏi mô hình câu nozzle, và nhận BOTH khi
    mô hình trả BOTH với conf >= both_tau và cả hai vòi khỏe, với tới bia cuối.
    """

    def __init__(self, model: JevDecider, rules: RuleDecider | None = None, tau: float = 0.8,
                 stages: frozenset[str] | set[str] | str = frozenset({"verify"}),
                 rule_questions: frozenset[str] | set[str] | tuple | list = frozenset(),
                 both_tau: float | None = None):
        self.both_tau = both_tau
        self.stages = parse_stages(stages) if isinstance(stages, str) else frozenset(stages)
        self.rule_questions = frozenset(rule_questions)
        if bad := self.rule_questions - set(QUESTION_IDS):
            raise ValueError(f"câu hỏi phải thuộc {QUESTION_IDS}, nhận {sorted(bad)}")
        self.model_decider, self.rules, self.tau = model, rules or RuleDecider(), tau
        self.name = f"hybrid(tau {tau:g}, mô hình ở {','.join(s for s in STAGES if s in self.stages)}"
        if self.rule_questions:
            self.name += f", luật cho {','.join(q for q in QUESTION_IDS if q in self.rule_questions)}"
        if both_tau is not None and "nozzle" in self.rule_questions:
            self.name += f", both khi conf >= {both_tau:g}"
        self.name += ")"
        self.last_error: str | None = None

    def decide(self, obs: dict, questions: dict) -> Decision:
        t0 = time.perf_counter()
        rules = self.rules.answers(obs, questions)
        model = {}
        ask_both = self.both_tau is not None and "nozzle" in self.rule_questions
        asked = {q: v for q, v in questions.items()
                 if q not in self.rule_questions or (q == "nozzle" and ask_both)}
        if asked and stage_of(obs, questions) in self.stages:
            try:
                model = self.model_decider.answers(obs, asked)
                self.last_error = None
            except Exception as e:  # noqa: BLE001 - mô hình hỏng thì vẫn phải quyết định được
                self.last_error = f"{type(e).__name__}: {e}"
        out = {}
        for q, r in rules.items():
            if q in self.rule_questions:
                out[q] = r  # nozzle chỉnh lại bên dưới theo bia cuối cùng
                continue
            m = model.get(q)
            out[q] = m if m is not None and m.confidence >= self.tau else r
        if "nozzle" in self.rule_questions and "nozzle" in rules:
            tgt, m = out.get("target"), model.get("nozzle")
            ans, src = hybrid_nozzle(obs, questions["nozzle"]["candidates"], tgt.answer if tgt else None,
                                     rules["nozzle"].answer, m.answer if m else None,
                                     m.confidence if m else 0.0, self.both_tau)
            if src == "model":
                out["nozzle"] = Answer(ans, m.confidence, m.probs, "model")
            elif src != "rules":
                out["nozzle"] = Answer(ans, 1.0, {ans: 1.0}, src)
        return Decision(out, render_state(obs), (time.perf_counter() - t0) * 1000, self.name)


def make_decider(kind: str, run: str = "jev2", tau: float = 0.8, device: str = "cpu",
                 stages: str | frozenset[str] = "verify", url: str | None = None,
                 timeout_s: float = 3.0, rule_questions: frozenset[str] | set[str] | tuple | list = (),
                 both_tau: float | None = None):
    if kind == "remote":
        if not url:
            raise ValueError("--decider remote cần --decider-url")
        from .remote import RemoteDecider

        return RemoteDecider(url, timeout_s)
    if kind == "rules":
        return RuleDecider()
    if kind == "jev":
        return JevDecider(run, device)
    if kind == "hybrid":
        return HybridDecider(JevDecider(run, device), tau=tau, stages=stages, rule_questions=rule_questions,
                             both_tau=both_tau)
    raise ValueError(f"không biết bộ quyết định {kind!r} (rules, jev, hybrid, remote)")
