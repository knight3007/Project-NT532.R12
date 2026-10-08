"""Mô hình quyết định kiểu Jev (theo thiết kế NanoJev): STATE + câu hỏi có kiểu -> xác suất.

Mỗi ứng viên được mã hóa CÙNG state và câu hỏi trong một chuỗi bởi một backbone dùng chung
(phần văn bản của EmbeddingGemma 2, tinh chỉnh bằng LoRA). Vector gộp trung bình của từng chuỗi đi
qua đầu Choice (một lớp self-attention trên tập k ứng viên, không mã hóa vị trí nên không phụ thuộc
thứ tự, rồi linear ra một logit mỗi ứng viên, softmax) hoặc đầu Boolean (MLP, sigmoid). Mỗi câu hỏi
có một nhiệt độ T khớp sau huấn luyện. Không sinh văn bản.

    model = JevModel.load("runs/decider/jev/<name>", device="cuda")
    out = model.decide(state, questions)   # {qid: {answer, probs, confidence}}

Chỉ đọc `state` và `questions` của bản ghi, không bao giờ đọc truth hay obs.
"""

import json
import math
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

MODEL_ID = "google/embeddinggemma-2"
TASK_PREFIX = "task: classification | query: "  # tiền tố đối xứng theo model card
QUESTION_TEXT = {
    "real_fire": "is there a real fire that needs action?",
    "action": "what should the system do?",
    "target": "which detected target is the real fire?",
    "nozzle": "which nozzle should spray?",
    "after_verify": "what should happen after this spray?",
}
LORA_TARGETS = r".*layers\.\d+\.(self_attn\.(q|k|v|o)_proj|mlp\.(gate|up|down)_proj)"


def resolve_precision(device: str, precision: str | None = None) -> str:
    """'bf16' hoặc 'fp32'. Mặc định: bf16 nếu GPU hỗ trợ, ngược lại fp32. Không bao giờ fp16."""
    if precision in (None, "auto"):
        ok = torch.device(device).type == "cuda" and torch.cuda.is_bf16_supported()
        return "bf16" if ok else "fp32"
    if precision not in ("bf16", "fp32"):
        raise ValueError(f"precision phải là bf16 hoặc fp32 (không dùng fp16), nhận {precision!r}")
    return "fp32" if torch.device(device).type != "cuda" else precision


def format_sequence(state: str, qid: str, candidate: str | None) -> str:
    """Chuỗi đưa vào backbone; câu hỏi boolean không có dòng answer."""
    text = f"{state}\nquestion: {QUESTION_TEXT.get(qid, qid)}"
    return text if candidate is None else f"{text}\nanswer: {candidate}"


class GemmaBackbone(nn.Module):
    """Phần văn bản của EmbeddingGemma 2: mã hóa hai chiều, gộp trung bình, chuẩn hóa L2 (768 chiều).

    Tải bằng SentenceTransformer (bỏ audio_config) rồi lấy `model.language_model` và tokenizer; gộp
    trung bình thủ công trùng với module Pooling (cosine với st.encode = 1.0).
    """

    dim = 768

    def __init__(self, device: str = "cpu", lora_r: int = 16, lora_alpha: int | None = None,
                 lora_dropout: float = 0.05, max_len: int = 512, checkpointing: bool = False,
                 prefix: str = TASK_PREFIX, model_id: str = MODEL_ID,
                 precision: str | None = None):
        super().__init__()
        from sentence_transformers import SentenceTransformer

        self.device_type = torch.device(device).type
        self.precision = resolve_precision(device, precision)
        dtype = torch.bfloat16 if self.precision == "bf16" else torch.float32
        st = SentenceTransformer(model_id, device="cpu", model_kwargs={"torch_dtype": dtype},
                                 config_kwargs={"audio_config": None})
        self.tok = st.tokenizer
        lm = st[0].model.language_model
        del st
        for p in lm.parameters():
            p.requires_grad_(False)
        if lora_r > 0:
            from peft import LoraConfig, get_peft_model

            if checkpointing:
                lm.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
            cfg = LoraConfig(r=lora_r, lora_alpha=lora_alpha or 2 * lora_r,
                             lora_dropout=lora_dropout, target_modules=LORA_TARGETS)
            lm = get_peft_model(lm, cfg)
        self.lm = lm
        self.lora_r = lora_r
        self.max_len = max_len
        self.prefix = prefix
        self.n_truncated = 0
        self.n_seen = 0
        self.token_lengths: list[int] = []
        self.to(device)

    def _tokenize(self, texts: list[str]) -> tuple[torch.Tensor, torch.Tensor]:
        ids = self.tok([self.prefix + t for t in texts])["input_ids"]
        out = []
        for x in ids:
            self.n_seen += 1
            self.token_lengths.append(len(x))
            if len(x) > self.max_len:
                # cắt bên trái (phần đầu của state), giữ BOS và đuôi câu hỏi + ứng viên
                x = x[:1] + x[-(self.max_len - 1):]
                self.n_truncated += 1
            out.append(x)
        n = max(len(x) for x in out)
        pad = self.tok.pad_token_id
        ids_t = torch.tensor([x + [pad] * (n - len(x)) for x in out])
        mask = torch.tensor([[1] * len(x) + [0] * (n - len(x)) for x in out])
        return ids_t, mask

    def encode(self, texts: list[str]) -> torch.Tensor:
        ids, mask = self._tokenize(texts)
        dev = next(self.lm.parameters()).device
        ids, mask = ids.to(dev), mask.to(dev)
        use_grad = torch.is_grad_enabled() and any(p.requires_grad for p in self.parameters())
        with torch.set_grad_enabled(use_grad), torch.autocast(
            self.device_type, dtype=torch.bfloat16, enabled=self.precision == "bf16"
        ):
            h = self.lm(input_ids=ids, attention_mask=mask).last_hidden_state
        m = mask.unsqueeze(-1).float()
        pooled = (h.float() * m).sum(1) / m.sum(1)
        return F.normalize(pooled, dim=-1)

    def trainable_state(self) -> dict[str, torch.Tensor]:
        return {k: p.detach().cpu().clone() for k, p in self.named_parameters() if p.requires_grad}

    def load_trainable_state(self, state: dict[str, torch.Tensor]) -> None:
        params = dict(self.named_parameters())
        for k, v in state.items():
            params[k].data.copy_(v)


class ChoiceHead(nn.Module):
    """Một lớp self-attention trên tập ứng viên (không mã hóa vị trí) rồi linear ra logit."""

    def __init__(self, dim: int, hidden: int = 256, heads: int = 4):
        super().__init__()
        self.inp = nn.Sequential(nn.LayerNorm(dim), nn.Linear(dim, hidden))
        self.attn = nn.TransformerEncoderLayer(hidden, heads, 2 * hidden, dropout=0.1,
                                               batch_first=True, norm_first=True)
        self.out = nn.Sequential(nn.LayerNorm(hidden), nn.Linear(hidden, 1))

    def forward(self, x: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
        """x: (G, k, d), valid: (G, k) True ở ứng viên có thật. Trả (G, k), chỗ đệm là -inf."""
        h = self.attn(self.inp(x), src_key_padding_mask=~valid)
        return self.out(h).squeeze(-1).masked_fill(~valid, float("-inf"))


class BooleanHead(nn.Module):
    def __init__(self, dim: int, hidden: int = 256):
        super().__init__()
        self.net = nn.Sequential(nn.LayerNorm(dim), nn.Linear(dim, hidden), nn.GELU(),
                                 nn.Linear(hidden, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def _label_index(q: dict, label) -> int:
    return q["candidates"].index(label)


class JevModel(nn.Module):
    def __init__(self, backbone: nn.Module, dim: int | None = None, hidden: int = 256,
                 heads: int = 4):
        super().__init__()
        self.backbone = backbone
        self.dim = dim or backbone.dim
        self.hidden, self.n_heads = hidden, heads
        self.choice = ChoiceHead(self.dim, hidden, heads)
        self.boolean = BooleanHead(self.dim, hidden)
        self.temps: dict[str, float] = {}

    @property
    def device(self) -> torch.device:
        return next(self.choice.parameters()).device

    def logits(self, records: list[dict]) -> list[dict[str, torch.Tensor]]:
        """Một lần chạy backbone cho mọi chuỗi của lô, rồi gom lại theo (bản ghi, câu hỏi).

        Trả về cho mỗi bản ghi {qid: logit}: boolean là scalar, choice là vector (k,).
        """
        texts, slots = [], []  # slots: (chỉ số bản ghi, qid, số chuỗi)
        for ri, r in enumerate(records):
            for qid, q in r["questions"].items():
                if q["type"] == "boolean":
                    texts.append(format_sequence(r["state"], qid, None))
                    slots.append((ri, qid, 1))
                else:
                    texts += [format_sequence(r["state"], qid, c) for c in q["candidates"]]
                    slots.append((ri, qid, len(q["candidates"])))
        vecs = self.backbone.encode(texts).to(self.device)

        out: list[dict[str, torch.Tensor]] = [{} for _ in records]
        choice_groups, pos = [], 0
        for (ri, qid, n) in slots:
            v = vecs[pos:pos + n]
            pos += n
            if records[ri]["questions"][qid]["type"] == "boolean":
                out[ri][qid] = self.boolean(v)[0]
            else:
                choice_groups.append((ri, qid, v))
        if choice_groups:
            k = max(v.shape[0] for _, _, v in choice_groups)
            x = vecs.new_zeros(len(choice_groups), k, vecs.shape[1])
            valid = torch.zeros(len(choice_groups), k, dtype=torch.bool, device=vecs.device)
            for g, (_, _, v) in enumerate(choice_groups):
                x[g, :v.shape[0]] = v
                valid[g, :v.shape[0]] = True
            lg = self.choice(x, valid)
            for g, (ri, qid, v) in enumerate(choice_groups):
                out[ri][qid] = lg[g, :v.shape[0]]
        return out

    def loss(self, records: list[dict]) -> tuple[torch.Tensor, dict]:
        """Trung bình trên bản ghi của tổng (CE choice + BCE boolean). Kèm thống kê từng câu hỏi."""
        outs = self.logits(records)
        total = 0.0
        stats: dict[str, list[float]] = {}  # qid -> [tổng loss, số câu, số đúng]
        for r, o in zip(records, outs, strict=True):
            for qid, lg in o.items():
                q, y = r["questions"][qid], r["labels"][qid]
                if q["type"] == "boolean":
                    li = F.binary_cross_entropy_with_logits(lg, torch.tensor(float(y), device=lg.device))
                    hit = (lg > 0).item() == bool(y)
                else:
                    t = _label_index(q, y)
                    li = F.cross_entropy(lg.unsqueeze(0), torch.tensor([t], device=lg.device))
                    hit = int(lg.argmax()) == t
                total = total + li
                s = stats.setdefault(qid, [0.0, 0, 0])
                s[0] += li.item()
                s[1] += 1
                s[2] += hit
        return total / len(records), stats

    @torch.no_grad()
    def predict_logits(self, records: list[dict], batch: int = 8) -> list[dict[str, torch.Tensor]]:
        """Logit thô (CPU, float32) cho nhiều bản ghi, ở chế độ eval."""
        was = self.training
        self.eval()
        res = []
        for i in range(0, len(records), batch):
            res += [{q: v.float().cpu() for q, v in o.items()}
                    for o in self.logits(records[i:i + batch])]
        self.train(was)
        return res

    def probs(self, qid: str, logit: torch.Tensor, calibrated: bool = True) -> np.ndarray:
        """Xác suất từ logit. Choice: vector k. Boolean: [P(sai), P(đúng)]."""
        t = self.temps.get(qid, 1.0) if calibrated else 1.0
        if logit.dim() == 0:
            p = torch.sigmoid(logit / t).item()
            return np.array([1 - p, p])
        return torch.softmax(logit / t, dim=0).numpy()

    def decide(self, state: str, questions: dict) -> dict[str, dict]:
        """{qid: {answer, probs: {ứng viên: p}, confidence}}; boolean trả answer bool, probs true/false."""
        lg = self.predict_logits([{"state": state, "questions": questions}])[0]
        out = {}
        for qid, v in lg.items():
            p = self.probs(qid, v)
            if v.dim() == 0:
                names, answers = ["false", "true"], [False, True]
            else:
                names = answers = questions[qid]["candidates"]
            i = int(p.argmax())
            out[qid] = {"answer": answers[i], "probs": {n: float(x) for n, x in zip(names, p)},
                        "confidence": float(p[i])}
        return out

    def save(self, path: str | Path, extra: dict | None = None) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        bb = self.backbone
        cfg = {
            "dim": self.dim, "hidden": self.hidden, "heads": self.n_heads, "temps": self.temps,
            "backbone": {"model_id": MODEL_ID, "lora_r": getattr(bb, "lora_r", 0),
                         "max_len": getattr(bb, "max_len", None),
                         "prefix": getattr(bb, "prefix", None)},
            "question_text": QUESTION_TEXT, **(extra or {}),
        }
        (path / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
        heads = {"choice": self.choice.state_dict(), "boolean": self.boolean.state_dict()}
        torch.save(heads, path / "heads.pt")
        torch.save(bb.trainable_state() if hasattr(bb, "trainable_state") else {}, path / "lora.pt")

    @classmethod
    def load(cls, path: str | Path, device: str = "cpu", backbone: nn.Module | None = None,
             **backbone_kwargs) -> "JevModel":
        """Tải mô hình đã lưu. `backbone` truyền sẵn dùng cho kiểm thử (đã đúng kiến trúc)."""
        path = Path(path)
        cfg = json.loads((path / "config.json").read_text(encoding="utf-8"))
        if backbone is None:
            b = cfg["backbone"]
            backbone = GemmaBackbone(device=device, lora_r=b["lora_r"], max_len=b["max_len"],
                                     prefix=b["prefix"], model_id=b["model_id"], **backbone_kwargs)
        model = cls(backbone, cfg["dim"], cfg["hidden"], cfg["heads"])
        heads = torch.load(path / "heads.pt", map_location="cpu")
        model.choice.load_state_dict(heads["choice"])
        model.boolean.load_state_dict(heads["boolean"])
        lora = torch.load(path / "lora.pt", map_location="cpu")
        if lora:
            backbone.load_trainable_state(lora)
        model.temps = dict(cfg["temps"])
        return model.to(device)


def _pad_choice(logits: list[torch.Tensor]) -> torch.Tensor:
    k = max(len(v) for v in logits)
    out = torch.full((len(logits), k), -1e9, dtype=torch.float64)  # -inf làm gradient nan
    for i, v in enumerate(logits):
        out[i, :len(v)] = v.double()
    return out


def nll_logits(logits: list[torch.Tensor], targets: list, t: float = 1.0) -> float:
    """NLL trung bình. Choice: targets là chỉ số; boolean: logit scalar, targets là 0/1."""
    y = torch.tensor(targets)
    if logits[0].dim() == 0:
        return F.binary_cross_entropy_with_logits(torch.stack(logits).double() / t,
                                                  y.double()).item()
    return F.cross_entropy(_pad_choice(logits) / t, y.long()).item()


def fit_temperature(logits: list[torch.Tensor], targets: list) -> float:
    """Tìm T làm NLL nhỏ nhất (tối ưu log T bằng LBFGS), kẹp trong [0.05, 20]."""
    logt = torch.zeros((), dtype=torch.float64, requires_grad=True)
    opt = torch.optim.LBFGS([logt], lr=0.5, max_iter=100, line_search_fn="strong_wolfe")
    y = torch.tensor(targets)
    if logits[0].dim() == 0:
        z = torch.stack(logits).double()
        loss_fn = lambda t: F.binary_cross_entropy_with_logits(z / t, y.double())
    else:
        z = _pad_choice(logits)
        loss_fn = lambda t: F.cross_entropy(z / t, y.long())

    def closure():
        opt.zero_grad()
        loss = loss_fn(logt.exp())
        loss.backward()
        return loss

    opt.step(closure)
    return float(min(max(math.exp(logt.item()), 0.05), 20.0))


def gather(records: list[dict], logits: list[dict[str, torch.Tensor]]) -> dict[str, tuple]:
    """Gom theo câu hỏi: {qid: (danh sách logit, danh sách đích)}; đích là chỉ số hoặc 0/1."""
    res: dict[str, tuple[list, list]] = {}
    for r, o in zip(records, logits, strict=True):
        for qid, lg in o.items():
            q, y = r["questions"][qid], r["labels"][qid]
            ls, ts = res.setdefault(qid, ([], []))
            ls.append(lg)
            ts.append(int(bool(y)) if q["type"] == "boolean" else _label_index(q, y))
    return res
