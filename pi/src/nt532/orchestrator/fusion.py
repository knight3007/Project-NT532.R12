"""Gộp dữ liệu sống (telemetry, phát hiện qua nhiều khung, sức khỏe vòi) thành `obs`.

`obs` có đúng dạng mà bộ kịch bản tổng hợp sinh ra (nt532.decider.scenario), nên cùng một bộ
quyết định (luật, Jev hoặc lai) chạy được cả trên kịch bản lẫn trên hệ thật. `render_state(obs)`
cho ra đoạn STATE mà mô hình Jev đọc.
"""

import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from ..decider.scenario import MAX_ATTEMPTS, SENSOR, TOL_CM, WINDOW
from ..decider.state_text import ACTIONS, NOZZLES, VERIFY, target_candidates
from ..net.protocol import Telemetry

JOIN_M = 0.05  # hai phát hiện ở hai khung cách nhau dưới chừng này (theo x, z) là cùng một bia
CAP_AGE = 9999  # tuổi heartbeat/pose quá lớn hoặc chưa biết được ghi bằng số này


@dataclass(frozen=True)
class Sample:
    t: float  # thời điểm Pi nhận, đồng hồ monotonic
    seq: int
    temp: float
    gas: float
    hum: float | None


class SensorHistory:
    """Các mẫu telemetry gần nhất của từng node, an toàn khi nhiều luồng cùng đọc ghi."""

    def __init__(self, nodes=NOZZLES, size: int = 600, clock=time.monotonic, recorder=None) -> None:
        self.clock = clock
        self.recorder = recorder  # gọi với mỗi Telemetry nhận được (để ghi log)
        self._lock = threading.Lock()
        self._data = {n: deque(maxlen=size) for n in nodes}

    def add(self, tel: Telemetry) -> None:
        with self._lock:
            if tel.n in self._data:
                self._data[tel.n].append(Sample(self.clock(), tel.s, tel.t, tel.g, tel.h))
        if self.recorder is not None:
            self.recorder(tel)

    def samples(self, node: str, n: int | None = None) -> list[Sample]:
        with self._lock:
            d = list(self._data.get(node, ()))
        return d if n is None else d[-n:]

    def since(self, node: str, t: float) -> list[Sample]:
        return [s for s in self.samples(node) if s.t > t]

    def window(self, node: str, n: int = WINDOW) -> dict:
        """n mẫu cuối dạng {temp, gas, hum}; thiếu mẫu thì lặp mẫu cũ nhất có được."""
        got = self.samples(node, n)
        if not got:
            return {"temp": [0.0] * n, "gas": [0] * n, "hum": [None] * n}
        got = [got[0]] * (n - len(got)) + got
        return {
            "temp": [round(s.temp, 1) for s in got],
            "gas": [round(s.gas) for s in got],
            "hum": [None if s.hum is None else round(s.hum) for s in got],
        }


def alarm_limits(site: dict) -> dict:
    s = site.get("sensors") or {}
    return {"temp": float(s.get("temp_alarm_c") or SENSOR.temp_thr),
            "gas": float(s.get("gas_alarm") or SENSOR.gas_thr)}


@dataclass
class Track:
    """Một bia theo dõi qua các khung: vị trí (x, y, z) trung bình, conf từng khung (None nếu mất)."""

    position: np.ndarray
    conf: list
    w: float
    h: float


def aggregate(frames: list[list], frame_size: tuple[int, int], join_m: float = JOIN_M) -> list[Track]:
    """Ghép `vision.targets` của nhiều khung thành từng bia. `frame_size` là (rộng, cao) để chuẩn
    hóa cỡ hộp về tỉ lệ ảnh như trong cache YOLO của bộ kịch bản."""
    W, H = frame_size
    tracks: list[Track] = []
    pts: list[list[np.ndarray]] = []
    for i, targets in enumerate(frames):
        used = set()
        for t in sorted(targets, key=lambda t: -t.detection.conf):
            best, dist = None, join_m
            for j, tr in enumerate(tracks):
                d = float(np.hypot(*(tr.position - t.position)[[0, 2]]))
                if j not in used and d <= dist:
                    best, dist = j, d
            if best is None:
                tracks.append(Track(t.position.copy(), [None] * len(frames),
                                    t.detection.w / W, t.detection.h / H))
                pts.append([])
                best = len(tracks) - 1
            used.add(best)
            tr = tracks[best]
            tr.conf[i] = round(float(t.detection.conf), 2)
            pts[best].append(t.position)
            tr.position = np.mean(pts[best], axis=0)
    return tracks


def decide_obs(alarm: str, sensors: SensorHistory, tracks: list[Track], nozzles: dict,
               node_x: dict, n_frames: int, limits: dict) -> tuple[dict, dict]:
    """(obs giai đoạn decide, {id bia: Track}). `nozzles[n]` có hb_ms, pose_s và reach(x, z)."""
    tracks = sorted(tracks, key=lambda tr: tr.position[0])
    targets, by_id = [], {}
    for i, tr in enumerate(tracks, 1):
        tid = f"T{i}"
        x, z = round(float(tr.position[0]), 2), round(float(tr.position[2]), 2)
        targets.append({
            "id": tid, "x": x, "z": z, "w": round(tr.w, 3), "h": round(tr.h, 3), "conf": tr.conf,
            "dist": {n: round(abs(x - node_x[n]), 2) for n in NOZZLES},
        })
        by_id[tid] = tr
    noz = {}
    for n in NOZZLES:
        z = nozzles[n]
        noz[n] = {"hb_ms": round(min(z["hb_ms"], CAP_AGE)), "pose_s": round(min(z["pose_s"], CAP_AGE)),
                  "reach": {t["id"]: bool(z["reach"](t["x"], t["z"])) for t in targets}}
    obs = {
        "stage": "decide", "alarm": alarm, "limits": limits,
        "sensors": {n: sensors.window(n) for n in NOZZLES},
        "frames": n_frames, "targets": targets, "nozzles": noz,
    }
    return obs, by_id


def verify_obs(d_obs: dict, target_id: str, nozzle: str, attempt: int, status: str,
               temp: list[float], conf: list, mark_cm: float | None, sensors: SensorHistory) -> dict:
    t = next(t for t in d_obs["targets"] if t["id"] == target_id)
    return {
        "stage": "verify", "alarm": d_obs["alarm"], "limits": d_obs["limits"],
        "sensors": {n: sensors.window(n) for n in NOZZLES},
        "verify": {"target": target_id, "x": t["x"], "z": t["z"], "nozzle": nozzle,
                   "attempt": attempt, "max_attempts": MAX_ATTEMPTS, "status": status,
                   "temp": temp, "conf": conf,
                   "mark_cm": None if mark_cm is None else round(float(mark_cm), 1),
                   "tol_cm": TOL_CM},
    }


def questions_for(obs: dict) -> dict:
    """Câu hỏi có kiểu cho một obs, cùng dạng `questions` của bộ kịch bản."""
    if obs["stage"] == "verify":
        return {"after_verify": {"type": "choice", "candidates": list(VERIFY)}}
    q = {"real_fire": {"type": "boolean", "candidates": []},
         "action": {"type": "choice", "candidates": list(ACTIONS)}}
    if obs["targets"]:
        q["target"] = {"type": "choice", "candidates": target_candidates(obs["targets"])}
        q["nozzle"] = {"type": "choice", "candidates": list(NOZZLES)}
    return q


def candidate_id(text: str) -> str | None:
    """'T2 (x 0.31 z 0.22)' -> 'T2'; 'none of these' -> None."""
    head = text.split(" ", 1)[0]
    return head if head.startswith("T") and head[1:].isdigit() else None
