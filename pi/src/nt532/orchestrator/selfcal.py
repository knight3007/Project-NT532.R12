"""Tự hiệu chuẩn cụm pan-tilt của từng node từ vết laser mà camera thấy.

Ngắm bằng pose tag đế cộng các số giả định (`actuator.pivot_offset`, hướng pan = 0, tilt = 0 nằm ngang,
hệ số servo 1) nên chỉ cần lệch vài độ là trượt bia 3 cm. Module này học lại các số đó: bắn laser tới
vài góc lệnh, đo vết trên bảng/nền bằng camera (đã biết mặt phẳng nên ra điểm 3D), rồi khớp bình phương
tối thiểu mô hình hình học của vòi.

Mô hình (cùng quy ước với `aiming.angles` và `sim.scene.Node.direction`): gốc tia là tâm quay
`tag.to_world(pivot)`; hướng ngang = yaw tag (hoặc `yaw_deg` của site) + `yaw0` + `pan_gain * pan_lệnh`;
độ cao = `tilt0 + tilt_gain * tilt_lệnh`. pan dương quay ngược chiều kim đồng hồ nhìn từ trên (với hướng
đầu +Y là về phía -X), tilt dương ngẩng lên +Z.

Giới hạn: cần vết laser nằm trên mặt phẳng đã biết (bảng hoặc nền); laser và vòi nước không được tách
(coi cùng một tia, `water_tilt` vẫn cộng riêng); pose tag sai một lượng nhất quán sẽ bị hấp thụ vào
`pivot` và `yaw0`, đúng ý nghĩa vì các số này đều đo trong hệ của tag.
"""

import math
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import yaml

from ..config import REPO_ROOT
from . import aiming

DEFAULT_FILE = "calibration/nodes.yaml"
SIGMA_PIVOT_M = 0.03  # độ tin của số pivot giả định (Tikhonov) ở ca suy biến; ca đủ trải dùng selfcal.pivot_prior_sigma_m
SIGMA_ANGLE_DEG = 10.0  # yaw0, tilt0
SIGMA_GAIN = 0.1  # hệ số servo
SIGMA_MEAS_M = 0.01  # sai số đo vết laser (camera + pose tag), để cân với các thành phần prior
HUBER_K = 3.0  # (đơn vị sigma) vượt ngưỡng này thì phần dư tăng tuyến tính, chống điểm ngoại lai
PARAMS = ("px", "py", "pz", "yaw0", "tilt0", "pan_gain", "tilt_gain")


@dataclass(frozen=True, eq=False)
class NodeCal:
    pivot: tuple  # (x, y, z) tâm quay trong hệ tag, mét
    yaw0_deg: float = 0.0  # hướng pan = 0 so với hướng đầu của tag
    tilt0_deg: float = 0.0  # độ cao của tia khi tilt lệnh = 0
    pan_gain: float = 1.0  # độ thật trên độ lệnh
    tilt_gain: float = 1.0
    n_samples: int = 0
    rms_cm: float | None = None
    fitted: bool = False
    time: float = 0.0
    heading_deg: float | None = None  # yaw_deg của site (thắng yaw tag) nếu có; không lưu, lấy từ site
    rms_before_cm: float | None = None  # chỉ để báo cáo kết quả của `fit`
    note: str = ""

    @classmethod
    def from_site(cls, site: dict, node: str) -> "NodeCal":
        """Giá trị giả định ban đầu: pivot_offset và yaw_deg của site, mọi sai lệch bằng 0."""
        cfg = (site.get("actuator", {}).get("nodes", {}) or {}).get(node) or {}
        yaw = cfg.get("yaw_deg")
        return cls(tuple(float(v) for v in aiming.pivot_offset(site, node)),
                   heading_deg=None if yaw is None else float(yaw))

    def heading(self, tag) -> float:
        return self.heading_deg if self.heading_deg is not None else aiming.heading_yaw_deg(tag)

    def pivot_world(self, tag) -> np.ndarray:
        return tag.to_world(self.pivot)

    def vector(self) -> np.ndarray:
        return np.array([*self.pivot, self.yaw0_deg, self.tilt0_deg, self.pan_gain, self.tilt_gain], float)

    def with_vector(self, v) -> "NodeCal":
        return replace(self, pivot=tuple(float(x) for x in v[:3]), yaw0_deg=float(v[3]),
                       tilt0_deg=float(v[4]), pan_gain=float(v[5]), tilt_gain=float(v[6]))

    def to_json(self) -> dict:
        return {"pivot": [round(float(x), 4) for x in self.pivot], "yaw0_deg": round(self.yaw0_deg, 3),
                "tilt0_deg": round(self.tilt0_deg, 3), "pan_gain": round(self.pan_gain, 4),
                "tilt_gain": round(self.tilt_gain, 4), "n_samples": self.n_samples,
                "rms_cm": None if self.rms_cm is None else round(self.rms_cm, 2),
                "fitted": self.fitted, "time": self.time}


# --- hình học ----------------------------------------------------------------------------------


def surfaces_of(site: dict) -> dict:
    """{"wall": {plane_y, width, height} | None, "floor": {width, depth} | None} từ site.yaml (cùng quy ước
    với `Vision._floor`)."""
    board = site["board"]
    floor = site.get("floor")
    fl = None
    if floor and floor.get("enabled", True):
        fl = {"width": floor.get("width", board["width"]), "depth": floor.get("depth", board["plane_y"])}
    return {"wall": {"plane_y": board["plane_y"], "width": board["width"], "height": board["height"]},
            "floor": fl}


def _plane_point(origin, direction, name: str, surfaces: dict) -> tuple[float, np.ndarray] | None:
    """Giao tia với mặt phẳng vô hạn của `name`; (tham số tia, điểm) hoặc None nếu song song."""
    if name == "wall":
        k, c = 1, surfaces["wall"]["plane_y"]
    else:
        k, c = 2, 0.0
    if abs(direction[k]) < 1e-9:
        return None
    s = (c - origin[k]) / direction[k]
    return float(s), origin + s * direction


def ray_hit(origin, direction, surfaces: dict, margin: float = 0.0) -> tuple[np.ndarray, str] | None:
    """Điểm đầu tiên tia chạm vùng bia (bảng đứng hoặc nền), kèm tên mặt; None nếu không chạm.
    `margin` > 0 nới vùng, < 0 thu vào trong."""
    origin, direction = np.asarray(origin, float), np.asarray(direction, float)
    hits = []
    wall, floor = surfaces.get("wall"), surfaces.get("floor")
    if wall is not None and (h := _plane_point(origin, direction, "wall", surfaces)):
        s, p = h
        if s > 0 and -margin <= p[0] <= wall["width"] + margin and -margin <= p[2] <= wall["height"] + margin:
            hits.append((s, p, "wall"))
    if floor is not None and (h := _plane_point(origin, direction, "floor", surfaces)):
        s, p = h
        if s > 0 and -margin <= p[0] <= floor["width"] + margin and -margin <= p[1] <= floor["depth"] + margin:
            hits.append((s, p, "floor"))
    if not hits:
        return None
    _, p, name = min(hits, key=lambda h: h[0])
    return p, name


def direction_of(heading_deg: float, pan_deg: float, tilt_deg: float) -> np.ndarray:
    """Vectơ đơn vị của tia: hướng ngang `heading + pan` (ngược chiều kim đồng hồ so với +Y), độ cao tilt."""
    psi, el = math.radians(heading_deg + pan_deg), math.radians(tilt_deg)
    return np.array([-math.sin(psi) * math.cos(el), math.cos(psi) * math.cos(el), math.sin(el)])


def _ray(cal: NodeCal, tag, pan_cmd: float, tilt_cmd: float) -> tuple[np.ndarray, np.ndarray]:
    d = direction_of(cal.heading(tag) + cal.yaw0_deg, cal.pan_gain * pan_cmd,
                     cal.tilt0_deg + cal.tilt_gain * tilt_cmd)
    return cal.pivot_world(tag), d


def predict_spot(cal: NodeCal, tag, pan_cmd: float, tilt_cmd: float, surfaces: dict,
                 margin: float = 0.0) -> np.ndarray | None:
    """Điểm 3D mà tia chạm khi ra lệnh (pan_cmd, tilt_cmd), None nếu ra ngoài vùng bia."""
    hit = ray_hit(*_ray(cal, tag, pan_cmd, tilt_cmd), surfaces, margin)
    return None if hit is None else hit[0]


def command_for(cal: NodeCal, tag, target, extra_tilt_deg: float = 0.0) -> tuple[float, float]:
    """(pan_lệnh, tilt_lệnh) để tia đi qua `target`. `extra_tilt_deg` (góc bù tia nước) cộng vào tilt hình
    học trước khi đổi sang lệnh."""
    pan, tilt = aiming.angles(cal.pivot_world(tag), target, cal.heading(tag) + cal.yaw0_deg)
    return pan / cal.pan_gain, (tilt + extra_tilt_deg - cal.tilt0_deg) / cal.tilt_gain


# --- khớp --------------------------------------------------------------------------------------


def _spot_surface(spot, surfaces: dict) -> str:
    """Vết ở bảng nếu cách mặt phẳng bảng dưới 3 cm, ngược lại ở nền."""
    return "wall" if abs(float(spot[1]) - surfaces["wall"]["plane_y"]) <= 0.03 else "floor"


def _errors(cal: NodeCal, samples, surfaces: dict) -> np.ndarray:
    """Vectơ lệch (dự đoán - đo) của từng mẫu, (n, 3); dự đoán lấy giao với mặt phẳng chứa vết đo (không
    giới hạn mép) để hàm trơn khi tham số đang lệch."""
    out = np.zeros((len(samples), 3))
    for i, (tag, pan, tilt, spot) in enumerate(samples):
        spot = np.asarray(spot, float)
        origin, d = _ray(cal, tag, pan, tilt)
        h = _plane_point(origin, d, _spot_surface(spot, surfaces), surfaces)
        pred = h[1] if h is not None else origin + 10.0 * d
        out[i] = pred - spot
    return out


def rms_cm(cal: NodeCal, samples, surfaces: dict) -> float:
    if not samples:
        return 0.0
    e = _errors(cal, samples, surfaces)
    return float(np.sqrt(np.mean(np.sum(e * e, axis=1))) * 100)


def spread(samples) -> tuple[float, float]:
    """(độ rộng pan, độ rộng tilt) của các góc lệnh trong mẫu."""
    if not samples:
        return 0.0, 0.0
    pan = [s[1] for s in samples]
    tilt = [s[2] for s in samples]
    return max(pan) - min(pan), max(tilt) - min(tilt)


def fit(samples, prior: NodeCal, site: dict, weights=None, min_samples: int | None = None,
        max_rms_cm: float | None = None, baseline: NodeCal | None = None) -> NodeCal:
    """Khớp `NodeCal` từ mẫu [(TagPose, pan_lệnh, tilt_lệnh, điểm_đo_xyz)] bằng Levenberg-Marquardt có
    Jacobian số và prior Tikhonov về `prior`. `baseline` (mặc định `prior`): cal đang dùng, để so rms
    trước và sau.

    Ít mẫu (< min_samples) hoặc góc lệnh trải hẹp (pan < 15 độ, tilt < 8 độ) thì chỉ khớp yaw0, tilt0 và
    giữ pivot, hệ số servo như prior (ghi ở `note`). Kết quả bị bác (trả `baseline`, fitted=False) nếu rms
    sau khớp quá `max_rms_cm` hoặc không tốt hơn trước."""
    sc = site.get("selfcal", {}) or {}
    min_samples = int(sc.get("min_samples", 6)) if min_samples is None else min_samples
    max_rms = float(sc.get("max_rms_cm", 3.0)) if max_rms_cm is None else max_rms_cm
    base = baseline or prior
    surfaces = surfaces_of(site)
    samples = list(samples)
    n = len(samples)
    before = rms_cm(base, samples, surfaces)
    if n < 2:
        return replace(base, rms_before_cm=before, note=f"quá ít mẫu ({n})")

    pan_sp, tilt_sp = spread(samples)
    full = n >= min_samples and pan_sp >= 15.0 and tilt_sp >= 8.0
    free = list(range(7)) if full else [3, 4]
    note = ("khớp đủ 7 tham số" if full else
            f"chỉ khớp yaw0, tilt0 (mẫu {n}, pan trải {pan_sp:.0f} độ, tilt trải {tilt_sp:.0f} độ: "
            "chưa đủ để tách pivot và hệ số servo)")
    w = np.ones(n) if weights is None else np.asarray(weights, float)
    x0 = prior.vector()
    # pivot chỉ được thả khi mẫu đủ trải (full): khi đó prior yếu để pivot lệch 10-20 cm vẫn tìm lại được;
    # ở ca suy biến pivot đứng yên ở prior (prior chặt SIGMA_PIVOT_M chỉ còn là tham chiếu)
    sig_pivot = float(sc.get("pivot_prior_sigma_m", 0.15)) if full else SIGMA_PIVOT_M
    sig = np.array([sig_pivot] * 3 + [SIGMA_ANGLE_DEG] * 2 + [SIGMA_GAIN] * 2)
    steps = np.array([1e-4] * 3 + [1e-3] * 2 + [1e-5] * 2)

    def resid(xf: np.ndarray) -> np.ndarray:
        x = x0.copy()
        x[free] = xf
        e = _errors(prior.with_vector(x), samples, surfaces) / SIGMA_MEAS_M
        nrm = np.linalg.norm(e, axis=1)
        scale = np.where(nrm > HUBER_K, np.sqrt(HUBER_K / np.maximum(nrm, 1e-12)), 1.0) * np.sqrt(w)
        return np.concatenate([(e * scale[:, None]).ravel(), (xf - x0[free]) / sig[free]])

    xf = _lm(resid, x0[free].copy(), steps[free])
    x = x0.copy()
    x[free] = xf
    cal = prior.with_vector(x)
    after = rms_cm(cal, samples, surfaces)
    ok = after <= max_rms and after <= before * (1 + 1e-6) + 1e-9
    if not ok:
        why = (f"rms {after:.2f} cm vượt {max_rms:g} cm" if after > max_rms
               else f"rms sau khớp {after:.2f} cm không tốt hơn trước {before:.2f} cm")
        return replace(base, rms_before_cm=before, note=f"bác kết quả: {why}")
    return replace(cal, n_samples=n, rms_cm=after, fitted=True, time=time.time(), rms_before_cm=before,
                   note=note)


def _lm(f, x0: np.ndarray, steps: np.ndarray, iters: int = 80) -> np.ndarray:
    """Levenberg-Marquardt tối thiểu cho f(x) -> vectơ phần dư; Jacobian sai phân trung tâm."""
    x = x0.copy()
    r = f(x)
    cost = float(r @ r)
    lam = 1e-2
    for _ in range(iters):
        J = np.empty((len(r), len(x)))
        for j, h in enumerate(steps):
            d = np.zeros_like(x)
            d[j] = h
            J[:, j] = (f(x + d) - f(x - d)) / (2 * h)
        A, g = J.T @ J, J.T @ r
        improved = False
        for _ in range(8):
            try:
                dx = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-9), -g)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            r_new = f(x + dx)
            c_new = float(r_new @ r_new)
            if c_new < cost:
                x, r, improved = x + dx, r_new, True
                done = cost - c_new < 1e-10 * max(cost, 1.0)
                cost, lam = c_new, max(lam * 0.3, 1e-9)
                break
            lam *= 5
        if not improved or done:
            break
    return x


# --- lưu và đọc --------------------------------------------------------------------------------


def cal_path(site: dict) -> Path:
    p = Path((site.get("selfcal", {}) or {}).get("file") or DEFAULT_FILE)
    return p if p.is_absolute() else REPO_ROOT / p


def save_cals(site: dict, cals: dict) -> Path:
    """Ghi các cal đã khớp ra file yaml của site (chỉ cal có `fitted`)."""
    path = cal_path(site)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {n: {k: v for k, v in c.to_json().items() if k != "fitted"} for n, c in cals.items() if c.fitted}
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=True)
    return path


def load_cals(site: dict) -> dict:
    """Đọc cal đã lưu, trả {node: NodeCal} (rỗng nếu chưa có file hoặc file hỏng). Hướng đầu `heading_deg`
    lấy lại từ site, không từ file."""
    path = cal_path(site)
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        out = {}
        for n, d in raw.items():
            prior = NodeCal.from_site(site, n)
            out[n] = replace(prior, pivot=tuple(float(v) for v in d["pivot"]), yaw0_deg=float(d["yaw0_deg"]),
                             tilt0_deg=float(d["tilt0_deg"]), pan_gain=float(d.get("pan_gain", 1.0)),
                             tilt_gain=float(d.get("tilt_gain", 1.0)), n_samples=int(d.get("n_samples", 0)),
                             rms_cm=d.get("rms_cm"), fitted=True, time=float(d.get("time", 0.0)))
        return out
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError):
        return {}


# --- chọn góc quét -----------------------------------------------------------------------------


def sweep_commands(cal: NodeCal, tag, site: dict, limits: "aiming.Limits", n: int = 8,
                   margin_m: float = 0.10) -> list[tuple[float, float]]:
    """n góc lệnh (pan, tilt) trải đều quanh góc hướng vào giữa vùng bia, chỉ giữ góc trong giới hạn của
    node mà vết dự đoán (theo `cal` hiện tại) nằm cách mép vùng bia ít nhất `margin_m` để camera còn thấy.
    Chọn theo khoảng cách xa nhất trong (pan, tilt) để cùng lúc trải rộng pan, tilt và khoảng cách."""
    surf = surfaces_of(site)
    wall = surf["wall"]
    centre = np.array([wall["width"] / 2, wall["plane_y"] / 2, 0.0])
    pan0, tilt0 = command_for(cal, tag, centre)
    cands = []
    for dp in np.linspace(-35, 35, 8):
        for dt in np.linspace(-15, 25, 9):
            pan, tilt = pan0 + float(dp), tilt0 + float(dt)
            if limits.ok(pan, tilt) and predict_spot(cal, tag, pan, tilt, surf, -margin_m) is not None:
                cands.append((pan, tilt))
    if len(cands) <= n:
        return cands
    pts = np.array(cands)
    scale = np.array([35.0, 20.0])
    chosen = [int(np.argmin(np.linalg.norm((pts - [pan0, tilt0]) / scale, axis=1)))]
    while len(chosen) < n:
        d = np.min(np.linalg.norm(((pts[:, None, :] - pts[chosen][None]) / scale), axis=2), axis=1)
        chosen.append(int(np.argmax(d)))
    return [cands[i] for i in chosen]
