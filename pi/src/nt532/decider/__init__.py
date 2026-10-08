"""Mô hình quyết định thử nghiệm: trạng thái (embedding) + câu hỏi có lựa chọn -> xác suất.

Các tên dưới đây nạp lười (cần torch); `nt532.decider.rules`, `scenario`, `state_text` dùng được
mà không cần torch, để orchestrator chạy bằng luật trên Pi không phải cài nhóm `decider`.
"""

from importlib import import_module

_LAZY = {
    "Head": ".head", "Question": ".head", "decide": ".head", "ece": ".head",
    "fit_temperature": ".head", "nll": ".head", "score": ".head", "train_head": ".head",
    "QUESTIONS": ".questions", "candidate_texts": ".questions",
}

__all__ = ["QUESTIONS", "Head", "Question", "candidate_texts", "decide", "ece",
           "fit_temperature", "nll", "score", "train_head"]


def __getattr__(name: str):
    if name in _LAZY:
        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
