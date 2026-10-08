"""Nhật ký sự kiện dùng chung cho orchestrator, mạng và dashboard; tùy chọn ghi ra file JSONL."""

import json
import threading
import time
from collections import deque
from pathlib import Path


class EventLog:
    def __init__(self, size: int = 500, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._items: deque = deque(maxlen=size)
        self._seq = 0
        self._file = None
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._file = open(path, "a", encoding="utf-8")  # noqa: SIM115 - mở suốt phiên chạy

    def emit(self, kind: str, text: str, level: str = "info", **data) -> dict:
        """kind: alert, phase, decision, cmd, status, sim, fault...; level: info, warn, error."""
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "time": time.time(), "kind": kind, "level": level,
                  "text": text, **data}
            self._items.append(ev)
            if self._file is not None:
                self._file.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                self._file.flush()
        return ev

    def since(self, seq: int = 0, limit: int = 200) -> list[dict]:
        with self._lock:
            out = [e for e in self._items if e["seq"] > seq]
        return out[-limit:]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq
