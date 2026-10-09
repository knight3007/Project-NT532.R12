"""Baseline luật theo kế hoạch NT532 (mục 4): đọc quan sát có cấu trúc, trả đáp án như mô hình.

Không đọc truth. Ngưỡng lấy từ config/site.yaml (detect_conf, sensor_match_radius_x).
"""

from .scenario import MAX_ATTEMPTS, Geometry
from .state_text import (  # noqa: F401 - HB_MAX_MS, POSE_MAX_S, nozzle_healthy được nơi khác import từ đây
    ACTIONS,
    HB_MAX_MS,
    NONE_OF_THESE,
    NOZZLES,
    POSE_MAX_S,
    VERIFY,
    nozzle_healthy,
    target_candidates,
)


def _last_conf(conf: list) -> float:
    """Conf của khung gần nhất còn phát hiện; luật thật chỉ chụp một ảnh lúc LOCALIZE."""
    return next((c for c in reversed(conf) if c is not None), 0.0)


def rule_decide(obs: dict, geo: Geometry) -> dict:
    alarm = obs["alarm"]
    matched = [
        t for t in obs["targets"]
        if _last_conf(t["conf"]) >= geo.detect_conf and t["dist"][alarm] <= geo.match_radius
    ]
    if not matched:
        return {"real_fire": False, "action": ACTIONS[2], "target": None, "nozzle": alarm}
    best = max(matched, key=lambda t: _last_conf(t["conf"]))
    # vòi khỏe, với tới được và gần bia nhất (khoảng cách 3D từ trục quay; thiếu thì theo X). Không bao giờ `both`
    able = [n for n in NOZZLES if nozzle_healthy(obs["nozzles"][n]) and obs["nozzles"][n]["reach"][best["id"]]]
    d3 = best.get("dist3") or best["dist"]
    nozzle = min(able, key=lambda n: d3[n]) if able else None
    action = ACTIONS[0] if nozzle else ACTIONS[1]
    return {"real_fire": True, "action": action, "target": best["id"], "nozzle": nozzle or alarm}


def rule_verify(obs: dict) -> str:
    v = obs["verify"]
    if v["status"] == "fault":
        return VERIFY[3]
    if v["mark_cm"] is None or v["mark_cm"] <= v["tol_cm"]:
        return VERIFY[0]  # trúng thì kết thúc, kế hoạch không có bước phun thêm
    return VERIFY[1] if v["attempt"] < MAX_ATTEMPTS else VERIFY[3]


def rule_answers(record: dict, geo: Geometry) -> dict:
    """Đáp án của baseline cho mọi câu hỏi có trong bản ghi, cùng dạng với record['labels']."""
    obs = record["obs"]
    if obs["stage"] == "verify":
        return {"after_verify": rule_verify(obs)}
    d = rule_decide(obs, geo)
    ans = {"real_fire": d["real_fire"], "action": d["action"]}
    if "target" in record["questions"]:
        cands = target_candidates(obs["targets"])
        ids = [t["id"] for t in obs["targets"]]
        ans["target"] = NONE_OF_THESE if d["target"] is None else cands[ids.index(d["target"])]
    if "nozzle" in record["questions"]:
        ans["nozzle"] = d["nozzle"]
    return ans
