"""Mô hình quyết định thử nghiệm: trạng thái (embedding) + câu hỏi có lựa chọn -> xác suất."""

from .head import (
    Head,
    Question,
    decide,
    ece,
    fit_temperature,
    nll,
    score,
    train_head,
)
from .questions import QUESTIONS, candidate_texts

__all__ = [
    "QUESTIONS",
    "Head",
    "Question",
    "candidate_texts",
    "decide",
    "ece",
    "fit_temperature",
    "nll",
    "score",
    "train_head",
]
