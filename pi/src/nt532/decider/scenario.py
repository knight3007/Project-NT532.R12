"""Sinh kịch bản tổng hợp có sự thật ẩn (truth) cho mô hình quyết định.

Mô hình chỉ thấy QUAN SÁT có nhiễu trong state; nhãn lấy từ truth. Quan sát YOLO lấy từ cache
thật (scripts/cache_yolo_obs.py); số đọc cảm biến là phân phối GIẢ ĐỊNH (SensorModel), chưa đo.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from ..config import REPO_ROOT, read_site
from .state_text import ACTIONS, NOZZLES, VERIFY, render_state, target_candidates

VIS_MIN = 0.15  # conf thấp nhất mà một bia còn được coi là "nhìn thấy" (ứng viên)
N_SAMPLES = 70  # độ dài chuỗi cảm biến mô phỏng (1 Hz)
WINDOW = 5  # số mẫu cuối đưa vào state
MAX_ATTEMPTS = 3
TOL_CM = 3.0  # đúng với targeting.tolerance_m = 0.03 (đề xuất)
POS_NOISE_M = 0.015  # sai số vị trí bia do localize
# Giới hạn cơ khí GIẢ ĐỊNH (actuator.nodes.*_limits trong site.yaml còn null)
PAN_LIM = 50.0
TILT_LIM = (-35.0, 45.0)
KINDS = ("real_fire", "distractor", "steam", "spike")


@dataclass(frozen=True)
class SensorModel:
    """PLACEHOLDER: phân phối số đọc cảm biến, chưa đo thực. Sửa ở đây khi có dữ liệu thật."""

    temp_amb: tuple = (22.0, 32.0)  # nhiệt độ nền, độ C
    gas_amb: tuple = (120.0, 260.0)  # MQ-2 thô
    hum_amb: tuple = (40.0, 65.0)  # độ ẩm %
    temp_noise: float = 0.6
    gas_noise: float = 12.0
    hum_noise: float = 1.0
    temp_thr: float = 45.0  # ngưỡng báo động (đặt thật ở tuần 2)
    gas_thr: float = 600.0
    decay_m: float = 0.35  # tín hiệu giảm theo khoảng cách tới nguồn
    onset: tuple = (6.0, 14.0)  # giây bắt đầu tăng
    tau: tuple = (4.0, 14.0)  # hằng số thời gian tăng, giây
    fire_gain: tuple = (1.15, 3.0)  # đỉnh / (ngưỡng - nền) với tín hiệu gây báo động
    quiet_gain: tuple = (0.1, 0.7)  # đỉnh của tín hiệu không gây báo động
    spike_gain: tuple = (1.3, 2.6)
    steam_gain: tuple = (1.15, 2.2)  # như fire_gain nhưng nhỏ hơn
    steam_hum_rise: tuple = (15.0, 35.0)
    fire_hum_shift: tuple = (-5.0, 4.0)


SENSOR = SensorModel()


@dataclass(frozen=True)
class Profile:
    name: str = "normal"
    sensor_noise: float = 1.0
    conf_jitter: float = 0.04
    p_drop: float = 0.10
    kinds: tuple = (0.45, 0.25, 0.15, 0.15)  # theo KINDS
    p_unhealthy: float = 0.10  # xác suất mỗi vòi hỏng
    p_noisy_neg: float = 0.85  # lấy ảnh có phát hiện sai khi chọn vật gây nhiễu
    p_blind: float = 0.08  # sensor gần lửa nhất bị mù
    extra: tuple = (0.55, 0.30, 0.15)  # số bia gây nhiễu thêm: 0, 1, 2
    p_fault: float = 0.05  # vòi lỗi sau khi phun


NORMAL = Profile()
SHIFT = Profile(
    name="shift", sensor_noise=1.8, conf_jitter=0.08, p_drop=0.25,
    kinds=(0.35, 0.35, 0.15, 0.15), p_unhealthy=0.20, p_noisy_neg=0.97, p_blind=0.15,
    extra=(0.30, 0.40, 0.30), p_fault=0.10,
)


@dataclass(frozen=True)
class Geometry:
    nodes: dict  # tên -> (x, y, z)
    plane_y: float
    width: float
    height: float
    detect_conf: float
    match_radius: float


def load_geometry() -> Geometry:
    site = read_site()
    com = yaml.safe_load((REPO_ROOT / "data/sim/commissioning.yaml").read_text(encoding="utf-8"))
    nodes = {n: tuple(com["nodes"][n]["position"]) for n in NOZZLES}
    b = site["board"]
    return Geometry(
        nodes, b["plane_y"], b["width"], b["height"],
        site["vision"]["detect_conf"], site["targeting"]["sensor_match_radius_x"],
    )


def reachable(geo: Geometry, node: str, x: float, z: float) -> bool:
    nx, ny, nz = geo.nodes[node]
    dx, dy, dz = x - nx, geo.plane_y - ny, z - nz
    pan = math.degrees(math.atan2(dx, dy))
    tilt = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
    return abs(pan) <= PAN_LIM and TILT_LIM[0] <= tilt <= TILT_LIM[1]


# --- Cache quan sát YOLO -------------------------------------------------------------------

@dataclass
class Pool:
    """Ảnh của một split. Mỗi ảnh rút gọn thành phát hiện mạnh nhất (conf, w, h) hoặc None."""

    fire: list
    neg_noisy: list
    neg_quiet: list


def pool_from_records(records: list[dict], split: str) -> Pool:
    pool = Pool([], [], [])
    for r in records:
        if r["split"] != split:
            continue
        top = max(r["dets"], key=lambda d: d["c"], default=None)
        img = None if top is None else (top["c"], top["w"], top["h"])
        if r["has_fire"]:
            pool.fire.append(img)
        elif img is not None and img[0] >= VIS_MIN:
            pool.neg_noisy.append(img)
        else:
            pool.neg_quiet.append(img)
    return pool


def load_pool(path: str | Path, split: str) -> Pool:
    with Path(path).open(encoding="utf-8") as f:
        return pool_from_records([json.loads(line) for line in f], split)


def _pick(rng, items: list):
    return items[int(rng.integers(len(items)))]


def _frames(rng, img, n: int, prof: Profile) -> list:
    """Conf từng khung: ảnh gốc + nhiễu, có khung rơi (None)."""
    out = []
    for _ in range(n):
        if img is None or rng.random() < prof.p_drop:
            out.append(None)
            continue
        c = img[0] + rng.normal(0, prof.conf_jitter)
        out.append(None if c < 0.05 else round(min(c, 0.99), 2))
    return out


# --- Cảm biến ------------------------------------------------------------------------------

def _first_alarm(sm: SensorModel, s: dict) -> int | None:
    """Chỉ số mẫu đầu tiên mà một tín hiệu đã vượt ngưỡng 3 mẫu liên tiếp."""
    for i in range(2, N_SAMPLES):
        for key, thr in (("temp", sm.temp_thr), ("gas", sm.gas_thr)):
            if all(s[key][j] > thr for j in range(i - 2, i + 1)):
                return i
    return None


def _node_series(rng, sm, noise, amb, peaks, t0, tau, pulse):
    t = np.arange(N_SAMPLES)
    if pulse:
        shape = ((t >= t0) & (t < t0 + pulse)).astype(float)
    else:
        shape = np.where(t > t0, 1 - np.exp(-(t - t0) / tau), 0.0)
    noises = (sm.temp_noise, sm.gas_noise, sm.hum_noise)
    return {
        k: amb[i] + peaks[i] * shape + rng.normal(0, noises[i] * noise, N_SAMPLES)
        for i, k in enumerate(("temp", "gas", "hum"))
    }


def simulate_sensors(rng, kind: str, src_x: float, geo: Geometry, prof: Profile,
                     sm: SensorModel = SENSOR) -> tuple[dict, str, bool]:
    """Trả (WINDOW mẫu cuối mỗi node, node báo động trước, sensor gần nguồn có bị mù không)."""
    if kind == "distractor":  # vật gây nhiễu (đèn, vật cam) đi kèm báo động sai bất kỳ
        kind = str(rng.choice(["spike", "steam", "lamp"]))
    dist = {n: abs(geo.nodes[n][0] - src_x) for n in NOZZLES}
    near = min(dist, key=dist.get)
    blind = kind == "real_fire" and rng.random() < prof.p_blind
    for attempt in range(20):
        blind = blind and attempt < 10  # nếu mù mà không ai báo động thì bỏ mù
        amb = (rng.uniform(*sm.temp_amb), rng.uniform(*sm.gas_amb), rng.uniform(*sm.hum_amb))
        dt, dg = sm.temp_thr - amb[0], sm.gas_thr - amb[1]
        t0, tau = rng.uniform(*sm.onset), rng.uniform(*sm.tau)
        drive = "temp" if kind == "lamp" else rng.choice(["temp", "gas", "both"], p=[0.35, 0.30, 0.35])
        pulse = 0
        peaks = {n: (0.0, 0.0, 0.0) for n in NOZZLES}
        if kind == "spike":  # xung ngắn trên một node và một tín hiệu
            node = NOZZLES[int(rng.integers(2))]
            gain = rng.uniform(*sm.spike_gain)
            pulse = int(rng.integers(3, 6))
            peaks[node] = (gain * dt, 0.0, 0.0) if rng.random() < 0.5 else (0.0, gain * dg, 0.0)
        elif kind in ("real_fire", "steam", "lamp"):  # lamp: nóng như lửa nhưng không có khí
            gain = sm.steam_gain if kind == "steam" else sm.fire_gain
            gt = rng.uniform(*gain if drive != "gas" else sm.quiet_gain)
            gg = rng.uniform(*gain if drive != "temp" else sm.quiet_gain)
            hum = rng.uniform(*(sm.steam_hum_rise if kind == "steam" else sm.fire_hum_shift))
            for n in NOZZLES:
                d = math.exp(-(dist[n] - dist[near]) / sm.decay_m)
                if blind and n == near:
                    d *= 0.1
                peaks[n] = (gt * dt * d, gg * dg * d, hum * d)
        slow = 1.5 if kind == "steam" else 1.0
        series = {n: _node_series(rng, sm, prof.sensor_noise, amb, peaks[n], t0, tau * slow, pulse)
                  for n in NOZZLES}
        alarms = {n: _first_alarm(sm, series[n]) for n in NOZZLES}
        alarms = {n: i for n, i in alarms.items() if i is not None and i >= WINDOW}
        if not alarms:
            continue
        first = min(alarms, key=alarms.get)
        end = alarms[first]
        win = {}
        for n in NOZZLES:
            cut = {k: series[n][k][end - WINDOW + 1 : end + 1] for k in series[n]}
            win[n] = {
                "temp": [round(float(v), 1) for v in cut["temp"]],
                "gas": [round(float(v)) for v in cut["gas"]],
                "hum": [round(float(v)) for v in cut["hum"]],
            }
        return win, first, blind
    raise RuntimeError("không sinh được báo động cảm biến")


# --- Kịch bản ------------------------------------------------------------------------------

def _health(rng, prof: Profile) -> tuple[bool, float, float]:
    """(vòi khỏe thật, tuổi heartbeat ms, tuổi pose s). Vòi hỏng đôi khi trông còn ổn."""
    if rng.random() >= prof.p_unhealthy:
        hb = rng.gamma(4.0, 70.0)
        if rng.random() < 0.04:  # nhịp trễ thoáng qua nhưng vẫn khỏe
            hb = rng.uniform(900, 1400)
        return True, float(min(hb, 1450)), float(rng.uniform(1, 45))
    mode = rng.choice(["hb", "pose", "marginal"], p=[0.45, 0.35, 0.20])
    if mode == "hb":
        return False, float(rng.uniform(1600, 9000)), float(rng.uniform(1, 45))
    if mode == "pose":
        return False, float(rng.gamma(4.0, 70.0)), float(rng.uniform(65, 600))
    return False, float(rng.uniform(700, 1450)), float(rng.uniform(40, 59))


def _spread_x(rng, geo: Geometry, k: int) -> list[float]:
    for _ in range(100):
        xs = [float(rng.uniform(0.05, geo.width - 0.05)) for _ in range(k)]
        if all(abs(a - b) >= 0.12 for i, a in enumerate(xs) for b in xs[:i]):
            return xs
    return xs


def make_scenario(rng, geo: Geometry, pool: Pool, prof: Profile, sid: str) -> list[dict]:
    """Một kịch bản -> 1 bản ghi giai đoạn `decide`, thêm 1 bản `verify` nếu đã phun."""
    kind = KINDS[int(rng.choice(len(KINDS), p=prof.kinds))]
    n_frames = int(rng.integers(3, 6))
    real = kind == "real_fire"
    n_extra = int(rng.choice(3, p=prof.extra)) if real else int(rng.choice([1, 2], p=[0.6, 0.4]))
    if kind in ("steam", "spike"):
        n_extra = int(rng.choice(3, p=[0.5, 0.4, 0.1]))
    n_targets = n_extra + int(real)
    xs = _spread_x(rng, geo, n_targets) if n_targets else []
    zs = [float(rng.uniform(0.08, geo.height - 0.08)) for _ in xs]

    # bia: (x, z, ảnh, là lửa thật)
    things = []
    for i, (x, z) in enumerate(zip(xs, zs)):
        if real and i == 0:
            things.append((x, z, _pick(rng, pool.fire), True))
        else:
            noisy = rng.random() < prof.p_noisy_neg and pool.neg_noisy
            things.append((x, z, _pick(rng, pool.neg_noisy if noisy else pool.neg_quiet), False))
    src_x = xs[0] if real else float(rng.uniform(0.05, geo.width - 0.05))

    sensors, alarm, blind = simulate_sensors(rng, kind, src_x, geo, prof)

    # bia nhìn thấy, đánh số từ trái sang phải theo x đo được
    cands = []
    for x, z, img, is_fire in things:
        conf = _frames(rng, img, n_frames, prof)
        if not any(c is not None and c >= VIS_MIN for c in conf):
            continue
        ox = round(x + rng.normal(0, POS_NOISE_M), 2)
        oz = round(z + rng.normal(0, POS_NOISE_M), 2)
        cands.append({"x": ox, "z": oz, "w": img[1], "h": img[2], "conf": conf, "fire": is_fire,
                      "tx": x, "tz": z})
    cands.sort(key=lambda c: c["x"])
    targets = []
    for i, c in enumerate(cands, 1):
        targets.append({
            "id": f"T{i}", "x": c["x"], "z": c["z"], "w": c["w"], "h": c["h"], "conf": c["conf"],
            "dist": {n: round(abs(c["x"] - geo.nodes[n][0]), 2) for n in NOZZLES},
        })

    health = {n: _health(rng, prof) for n in NOZZLES}
    nozzles = {
        n: {
            "hb_ms": round(health[n][1]), "pose_s": round(health[n][2]),
            "reach": {t["id"]: reachable(geo, n, t["x"], t["z"]) for t in targets},
        }
        for n in NOZZLES
    }
    obs = {
        "stage": "decide", "alarm": alarm,
        "limits": {"temp": SENSOR.temp_thr, "gas": SENSOR.gas_thr},
        "sensors": sensors, "frames": n_frames, "targets": targets, "nozzles": nozzles,
    }

    # truth
    true_idx = next((i for i, c in enumerate(cands) if c["fire"]), None)
    true_t = cands[true_idx] if true_idx is not None else None
    can = {}
    if true_t is not None:
        for n in NOZZLES:
            can[n] = health[n][0] and reachable(geo, n, true_t["tx"], true_t["tz"])
    able = [n for n in NOZZLES if can.get(n)]
    best = min(able, key=lambda n: abs(true_t["tx"] - geo.nodes[n][0])) if able else None
    if real and able:
        action = ACTIONS[0]
    elif real:
        action = ACTIONS[1]
    else:
        action = ACTIONS[2]
    truth = {
        "kind": kind, "real_fire": real, "alarm_node": alarm, "sensor_blind": blind,
        "true_target": None if true_idx is None else f"T{true_idx + 1}",
        "nozzle_ok": {n: health[n][0] for n in NOZZLES}, "can_hit": can, "best_nozzle": best,
        "n_frames": n_frames,
    }

    cand_txt = target_candidates(targets)
    questions = {
        "real_fire": {"type": "boolean", "candidates": []},
        "action": {"type": "choice", "candidates": list(ACTIONS)},
    }
    labels = {"real_fire": real, "action": action}
    if targets:
        questions["target"] = {"type": "choice", "candidates": cand_txt}
        labels["target"] = cand_txt[true_idx if true_idx is not None else -1]
    if best is not None and true_idx is not None:
        questions["nozzle"] = {"type": "choice", "candidates": list(NOZZLES)}
        labels["nozzle"] = best
    records = [{"id": f"{sid}-decide", "state": render_state(obs), "questions": questions,
                "labels": labels, "truth": truth, "obs": obs}]

    if action == ACTIONS[0]:
        records.append(_verify_record(rng, geo, pool, prof, sid, obs, sensors, true_idx, true_t,
                                      best, truth))
    return records


def _verify_record(rng, geo, pool, prof, sid, d_obs, sensors, true_idx, true_t, nozzle, truth):
    attempt = int(rng.choice([1, 2, 3], p=[0.55, 0.30, 0.15]))
    miss_cm = abs(rng.normal(0, 1.0)) * (2.0 if rng.random() < 0.55 else 5.5)
    miss = miss_cm > TOL_CM
    out = rng.random() < (0.04 if miss else 0.65)
    fault = rng.random() < prof.p_fault
    n = 3
    if out:  # hết lửa: không còn phát hiện, hoặc chỉ còn phát hiện sai nhỏ
        img = _pick(rng, pool.neg_noisy) if rng.random() < 0.3 and pool.neg_noisy else None
    else:
        img = _pick(rng, pool.fire)
    conf = _frames(rng, img, n, prof)
    t_last = sensors[nozzle]["temp"][-1]
    step = rng.uniform(3, 9)
    sign = -1 if out else rng.choice([-0.2, 0.0, 0.4])
    temp = [round(t_last + sign * step * i + float(rng.normal(0, 0.6 * prof.sensor_noise)), 1)
            for i in range(n)]
    mark = None if rng.random() < 0.08 else round(
        max(0.0, miss_cm + float(rng.normal(0, 0.8 * prof.sensor_noise))), 1)
    status = "fault" if fault and rng.random() < 0.9 else "ok"

    if out:
        label = VERIFY[0]
    elif fault or attempt >= MAX_ATTEMPTS:
        label = VERIFY[3]
    elif miss:
        label = VERIFY[1]
    else:
        label = VERIFY[2]
    t = d_obs["targets"][true_idx]
    obs = {
        "stage": "verify", "alarm": d_obs["alarm"], "limits": d_obs["limits"], "sensors": sensors,
        "verify": {"target": t["id"], "x": t["x"], "z": t["z"], "nozzle": nozzle,
                   "attempt": attempt, "max_attempts": MAX_ATTEMPTS, "status": status,
                   "temp": temp, "conf": conf, "mark_cm": mark, "tol_cm": TOL_CM},
    }
    v_truth = dict(truth)
    v_truth.update({"fire_out": out, "miss_cm": round(miss_cm, 2), "miss": miss,
                    "attempt": attempt, "nozzle_fault": fault})
    return {
        "id": f"{sid}-verify", "state": render_state(obs),
        "questions": {"after_verify": {"type": "choice", "candidates": list(VERIFY)}},
        "labels": {"after_verify": label}, "truth": v_truth, "obs": obs,
    }


def generate(n: int, pool: Pool, prof: Profile, split: str, seed: int) -> list[dict]:
    """n kịch bản, tái lập được theo (seed, split)."""
    geo = load_geometry()
    rng = np.random.default_rng([seed, sum(map(ord, split))])
    out = []
    for i in range(n):
        out.extend(make_scenario(rng, geo, pool, prof, f"{split}-{i:05d}"))
    return out
