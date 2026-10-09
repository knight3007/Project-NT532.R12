"""Ước lượng các đại lượng của `SensorModel` từ số đo thật (log trạm và/hoặc CSV có nhãn).

Chỉ dùng thống kê bền (trung vị, MAD, phân vị) và chỉ trả số khi đủ dữ liệu; đại lượng nào không
ước lượng được thì giữ nguyên giá trị mặc định (không có trong kết quả). Không cần torch.

Định dạng CSV (cột `dist_m` là tùy chọn, các cột còn lại bắt buộc; `hum` có thể để trống):

    t_s,node,temp,gas,hum,label,dist_m
    12.0,s1,27.4,181,52,baseline,
    80.0,s1,41.0,420,50,fire,0.15

`label` thuộc baseline|fire|steam|spike|lamp, do nhóm ghi tay lúc đo ở tuần 2; `t_s` là giây
trên đồng hồ của riêng từng file; `dist_m` là khoảng cách (m) từ node tới nguồn, chỉ cần để ước
lượng `decay_m`. Mỗi đợt có nhãn nên mở đầu bằng >= 3 mẫu còn ở mức nền (chưa bật nguồn): đó là
mức nền riêng của đợt, vì nhiệt độ phòng mỗi lần đo một khác. Mẫu trong log trạm (dòng `"kind": "tel"`) không có nhãn: chỉ dùng làm nền khi
không dính tới cảnh báo nào (cách cảnh báo gần nhất >= 60 s, dưới ngưỡng).
"""

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .scenario import SENSOR, SensorModel

LABELS = ("baseline", "fire", "steam", "spike", "lamp")
GAP_S = 10.0  # khoảng trống lớn hơn chừng này cắt một đoạn liên tục
QUIET_S = 60.0  # mẫu trong log phải cách cảnh báo chừng này mới coi là nền
MIN_BASE = 20  # số mẫu nền tối thiểu cho một đại lượng nền
MIN_EP = 3  # số đợt tối thiểu để ước lượng một khoảng [min, max]
MIN_EP_LEN = 8  # một đợt ngắn hơn chừng này mẫu thì bỏ
LEAD = 3  # mỗi đợt nên mở đầu bằng chừng này mẫu còn ở mức nền (trước khi bật nguồn)
NA = "không đủ dữ liệu"


@dataclass(frozen=True)
class Sample:
    src: int  # chỉ số file nguồn (đồng hồ riêng)
    t: float
    node: str
    temp: float
    gas: float
    hum: float | None
    label: str | None = None
    dist: float | None = None


@dataclass
class Fit:
    value: object  # float, (lo, hi) hoặc None khi không ước lượng được
    n: int  # số mẫu hoặc số đợt đã dùng
    note: str = ""


# --- đọc dữ liệu ------------------------------------------------------------------------------

def load_log(path: str | Path, src: int = 0) -> tuple[list[Sample], list[float]]:
    """(mẫu telemetry chưa nhãn, thời điểm các cảnh báo) từ một file nhật ký JSONL của trạm."""
    samples, alerts = [], []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == "tel":
                samples.append(Sample(src, e["time"], e["n"], e["t"], e["g"], e.get("h")))
            elif e.get("kind") in ("alert", "run", "fault"):
                alerts.append(e["time"])
    return samples, alerts


def load_csv(path: str | Path, src: int = 0) -> list[Sample]:
    out = []
    with Path(path).open(encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), 2):
            label = (row.get("label") or "").strip().lower()
            if label not in LABELS:
                raise ValueError(f"{path}:{i}: nhãn {label!r} không thuộc {LABELS}")
            hum, dist = (row.get("hum") or "").strip(), (row.get("dist_m") or "").strip()
            out.append(Sample(src, float(row["t_s"]), row["node"].strip(), float(row["temp"]),
                              float(row["gas"]), float(hum) if hum else None, label,
                              float(dist) if dist else None))
    return out


def quiet_from_log(samples: list[Sample], alerts: list[float], limits: dict) -> list[Sample]:
    """Mẫu log dùng được làm nền: dưới ngưỡng và xa mọi cảnh báo."""
    out = []
    for s in samples:
        if s.temp > limits["temp"] or s.gas > limits["gas"]:
            continue
        if any(abs(s.t - a) < QUIET_S for a in alerts):
            continue
        out.append(Sample(s.src, s.t, s.node, s.temp, s.gas, s.hum, "baseline", None))
    return out


# --- thống kê ---------------------------------------------------------------------------------

def _range(vals) -> tuple[float, float] | None:
    """[min, max] khi ít mẫu, [p10, p90] khi nhiều; None nếu dưới MIN_EP."""
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)], float)
    if len(v) < MIN_EP:
        return None
    lo, hi = (v.min(), v.max()) if len(v) < 8 else np.percentile(v, [10, 90])
    return round(float(lo), 3), round(float(hi), 3)


def _segments(samples: list[Sample]) -> list[list[Sample]]:
    segs: list[list[Sample]] = []
    for s in sorted(samples, key=lambda s: (s.src, s.node, s.t)):
        last = segs[-1][-1] if segs else None
        if last and (last.src, last.node) == (s.src, s.node) and s.t - last.t <= GAP_S:
            segs[-1].append(s)
        else:
            segs.append([s])
    return segs


def _robust_sigma_diff(segs: list[list[Sample]], key: str) -> tuple[float | None, int]:
    """Độ lệch chuẩn nhiễu từ sai phân bậc một (loại trôi chậm): 1,4826 * MAD(diff) / sqrt(2)."""
    diffs = []
    for seg in segs:
        v = [getattr(s, key) for s in seg]
        if any(x is None for x in v):
            continue
        diffs.extend(np.diff(v))
    if len(diffs) < MIN_BASE:
        return None, len(diffs)
    d = np.asarray(diffs)
    return float(1.4826 * np.median(np.abs(d - np.median(d))) / math.sqrt(2)), len(d) + 1


def _rolling_mean3(v: np.ndarray) -> np.ndarray:
    return np.convolve(v, np.ones(3) / 3, mode="valid") if len(v) >= 3 else v


@dataclass
class Episode:
    label: str
    src: int
    node: str
    t: np.ndarray
    ch: dict  # kênh -> mảng (hum có thể None)
    dist: float | None

    def base(self, key: str) -> float:
        """Mức nền của chính đợt này: trung vị LEAD mẫu đầu (ambient mỗi lần đo một khác)."""
        return float(np.median(self.ch[key][:LEAD]))


def episodes(samples: list[Sample]) -> list[Episode]:
    """Đợt đo liên tục cùng nhãn (khác baseline) trên một node."""
    out = []
    labeled = [s for s in samples if s.label and s.label != "baseline"]
    for (_src, _node, label), group in _group(labeled).items():
        for seg in _split(sorted(group, key=lambda s: s.t)):
            if len(seg) < MIN_EP_LEN:
                continue
            hum = [s.hum for s in seg]
            dist = next((s.dist for s in seg if s.dist is not None), None)
            out.append(Episode(label, seg[0].src, seg[0].node, np.array([s.t for s in seg]),
                               {"temp": np.array([s.temp for s in seg]),
                                "gas": np.array([s.gas for s in seg]),
                                "hum": None if any(h is None for h in hum) else np.array(hum)},
                               dist))
    return out


def _group(samples: list[Sample]) -> dict:
    g: dict = {}
    for s in samples:
        g.setdefault((s.src, s.node, s.label), []).append(s)
    return g


def _split(seg: list[Sample]) -> list[list[Sample]]:
    out: list[list[Sample]] = []
    for s in seg:
        if out and s.t - out[-1][-1].t <= GAP_S:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def _peak(v: np.ndarray, base: float) -> float:
    """Biên độ có dấu của đỉnh so với nền, làm mượt 3 mẫu để nhiễu không thổi phồng."""
    sm = _rolling_mean3(v) - base
    return float(sm[np.argmax(np.abs(sm))])


def _overlap_best(eps: list[Episode], score) -> list[Episode]:
    """Mỗi nhóm đợt chồng thời gian (cùng file, cùng nhãn: nhiều node đo một lần cháy) chỉ giữ đợt có
    `score` lớn nhất, tức node gần nguồn nhất."""
    keep: list[Episode] = []
    for e in sorted(eps, key=lambda e: (e.src, e.label, e.t[0])):
        if keep and (keep[-1].src, keep[-1].label) == (e.src, e.label) and e.t[0] <= keep[-1].t[-1]:
            if score(e) > score(keep[-1]):
                keep[-1] = e
        else:
            keep.append(e)
    return keep


def _groups(eps: list[Episode]) -> list[list[Episode]]:
    out: list[list[Episode]] = []
    for e in sorted(eps, key=lambda e: (e.src, e.label, e.t[0])):
        if out and (out[-1][0].src, out[-1][0].label) == (e.src, e.label) and e.t[0] <= max(
                x.t[-1] for x in out[-1]):
            out[-1].append(e)
        else:
            out.append([e])
    return out


# --- ước lượng --------------------------------------------------------------------------------

def fit_sensor_model(samples: list[Sample], limits: dict | None = None,
                     default: SensorModel = SENSOR) -> dict[str, Fit]:
    """Trả {tên trường SensorModel: Fit}. `Fit.value is None` nghĩa là không ước lượng được."""
    lim = limits or {"temp": default.temp_thr, "gas": default.gas_thr}
    res: dict[str, Fit] = {}
    base_s = [s for s in samples if s.label == "baseline"]
    for key in ("temp", "gas", "hum"):
        vals = [getattr(s, key) for s in base_s if getattr(s, key) is not None]
        if len(vals) >= MIN_BASE:
            lo, hi = np.percentile(vals, [5, 95])
            res[f"{key}_amb"] = Fit((round(float(lo), 2), round(float(hi), 2)), len(vals),
                                    "p5-p95 của mẫu nền (gồm cả nhiễu)")
        else:
            res[f"{key}_amb"] = Fit(None, len(vals), NA)
        sig, n = _robust_sigma_diff(_segments(base_s), key)
        res[f"{key}_noise"] = Fit(None if sig is None else round(sig, 3), n,
                                  "" if sig is not None else NA)
    res["temp_thr"] = Fit(None, 0, NA + " (ngưỡng đặt bằng tay, site.yaml)")
    res["gas_thr"] = Fit(None, 0, NA + " (ngưỡng đặt bằng tay, site.yaml)")

    eps = episodes(samples)
    by = {lab: [e for e in eps if e.label == lab] for lab in LABELS[1:]}

    def gains(e: Episode) -> dict:
        out = {}
        for key, thr in (("temp", lim["temp"]), ("gas", lim["gas"])):
            b = e.base(key)
            if thr > b:
                out[key] = _peak(e.ch[key], b) / (thr - b)
        return out

    def best(lab):
        return _overlap_best(by[lab], lambda e: sum(max(g, 0) for g in gains(e).values()))

    # fire_gain / quiet_gain: kênh vượt ngưỡng là "gây báo động", kênh còn lại là "yên"
    drive, quiet = [], []
    for e in best("fire"):
        for g in gains(e).values():
            (drive if g >= 1.0 else quiet).append(g)
    res["fire_gain"] = _fit_range(drive, "đợt-kênh gây báo động")
    res["quiet_gain"] = _fit_range([max(g, 0.0) for g in quiet], "đợt-kênh không gây báo động")
    res["steam_gain"] = _fit_range(
        [g for e in best("steam") for g in gains(e).values() if g >= 1.0], "đợt-kênh hơi nước")
    res["spike_gain"] = _fit_range(
        [max(gains(e).values()) for e in best("spike") if gains(e)], "đợt xung")
    for lab, name in (("steam", "steam_hum_rise"), ("fire", "fire_hum_shift")):
        res[name] = _fit_range(
            [_peak(e.ch["hum"], e.base("hum")) for e in best(lab) if e.ch["hum"] is not None],
            "đợt có độ ẩm")

    # onset, tau từ kênh mạnh nhất của mỗi đợt cháy: onset = lúc vượt 5% đỉnh, tau = thêm tới 63%
    onsets, taus = [], []
    for e in best("fire"):
        g = gains(e)
        if not g:
            continue
        key = max(g, key=g.get)
        if g[key] < 1.0:
            continue
        b = e.base(key)
        y = (_rolling_mean3(e.ch[key]) - b) / max(_peak(e.ch[key], b), 1e-9)
        t = e.t[1:-1] - e.t[0] if len(y) == len(e.t) - 2 else e.t - e.t[0]
        i5, i63 = _first(y, 0.05), _first(y, 0.632)
        if i5 is not None and i63 is not None and i63 > i5:
            onsets.append(float(t[i5]))
            taus.append(float(t[i63] - t[i5]))
    res["onset"] = _fit_range(onsets, "đợt cháy")
    res["tau"] = _fit_range(taus, "đợt cháy")

    # decay_m: tỉ số đỉnh giữa node xa và gần trong cùng một lần cháy (cần dist_m ở >= 2 node)
    decays = []
    for grp in _groups(by["fire"]):
        pts = [(e.dist, e) for e in grp if e.dist is not None]
        if len({d for d, _ in pts}) < 2:
            continue
        near, far = min(pts, key=lambda p: p[0]), max(pts, key=lambda p: p[0])
        g_near, g_far = gains(near[1]), gains(far[1])
        if not g_near:
            continue
        key = max(g_near, key=g_near.get)
        if g_near[key] <= 0 or g_far.get(key, 0) <= 0:
            continue
        ratio = g_far[key] / g_near[key]
        if 0 < ratio < 1:
            decays.append((far[0] - near[0]) / -math.log(ratio))
    res["decay_m"] = (Fit(round(float(np.median(decays)), 3), len(decays), "trung vị")
                      if len(decays) >= 2 else Fit(None, len(decays), NA + " (cần dist_m, >= 2 lần cháy)"))
    return res


def _first(y: np.ndarray, level: float) -> int | None:
    idx = np.nonzero(y >= level)[0]
    return int(idx[0]) if len(idx) else None


def _fit_range(vals, what: str) -> Fit:
    r = _range(vals)
    return Fit(r, len(vals), what if r else NA)


def to_yaml_dict(fits: dict[str, Fit]) -> dict:
    """Chỉ các đại lượng đã ước lượng, dạng đọc được bởi `load_sensor_model`."""
    return {k: (list(f.value) if isinstance(f.value, tuple) else f.value)
            for k, f in fits.items() if f.value is not None}
