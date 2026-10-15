"""Dựng văn bản STATE từ quan sát có cấu trúc. Chỉ nhận quan sát, không bao giờ nhận truth."""

import re

NONE_OF_THESE = "none of these"
ACTIONS = ["spray", "alarm only", "ignore"]
NOZZLES = ["s1", "s2"]
BOTH = "both (s1 + s2)"  # ứng viên `nozzle`: phun cả hai vòi cùng lúc
HB_MAX_MS = 1500  # mất 3 nhịp heartbeat 500 ms thì vòi tự tắt
POSE_MAX_S = 60  # pose cũ hơn mức này bị chặn ngắm (ngưỡng đề xuất)
VERIFY = ["done (fire out)", "re-aim (spray missed)", "spray more (hit, still burning)",
          "call human (give up or fault)"]


def surface_of(t: dict) -> str:
    """Mặt chứa bia: "wall" (bảng đứng) hoặc "floor" (mặt bàn). Thiếu `surface` (log/bản ghi cũ)
    thì coi là bảng, với y = plane_y."""
    return "floor" if t.get("surface") == "floor" else "wall"


def pos_text(t: dict) -> str:
    """Tọa độ để in: bia trên bảng "wall x 0.88 z 0.20", bia trên nền "floor x 0.40 y 0.30"."""
    if surface_of(t) == "floor":
        return f"floor x {t['x']:.2f} y {t['y']:.2f}"
    return f"wall x {t['x']:.2f} z {t['z']:.2f}"


def target_candidates(targets: list[dict]) -> list[str]:
    """Chuỗi ứng viên cho câu hỏi `target`, cùng thứ tự với danh sách bia trong state:
    `T1 (floor x 0.40 y 0.30)` hoặc `T1 (wall x 0.88 z 0.20)`."""
    return [f"{t['id']} ({pos_text(t)})" for t in targets] + [NONE_OF_THESE]


def nozzle_healthy(n: dict) -> bool:
    return n["hb_ms"] <= HB_MAX_MS and n["pose_s"] <= POSE_MAX_S


def nozzle_candidates(obs: dict) -> list[str]:
    """Ứng viên cho câu hỏi `nozzle`: s1, s2, thêm BOTH khi quan sát cho thấy cả hai vòi khỏe và
    có một bia thấy mà cả hai với tới. Chỉ dùng quan sát (cùng ngưỡng với luật), không dùng truth."""
    noz = obs["nozzles"]
    cands = list(NOZZLES)
    if all(nozzle_healthy(noz[n]) for n in NOZZLES) and any(
        all(noz[n]["reach"].get(t["id"]) for n in NOZZLES) for t in obs["targets"]
    ):
        cands.append(BOTH)
    return cands


def nozzles_of(answer: str) -> list[str]:
    """Đáp án `nozzle` thành danh sách vòi cần phun: 's1' -> ['s1'], BOTH -> ['s1', 's2']."""
    return list(NOZZLES) if answer == BOTH else [answer]


def questions_from_state(state: str) -> dict:
    """Dựng lại dict `questions` từ văn bản STATE (đảo của render_state), để gõ tay một state.

    Ứng viên `target`/`nozzle` chỉ phụ thuộc quan sát nên khớp đúng với bộ sinh kịch bản. Riêng
    việc câu `nozzle` có xuất hiện hay không, bộ sinh quyết theo truth (có lửa thật và có vòi đủ
    điều kiện), không đọc được từ văn bản: với state gõ tay ta hỏi mỗi khi có ít nhất một bia."""
    if re.search(r"^stage:\s*verify\s*$", state, re.MULTILINE):
        return {"after_verify": {"type": "choice", "candidates": list(VERIFY)}}
    targets = []
    # `T1: floor x 0.40 y 0.30 ...` / `T1: wall x 0.88 z 0.20 ...`; dạng cũ `T1: x .. z ..` là wall
    for m in re.finditer(r"^(T\d+): (?:(wall|floor) )?x (-?[\d.]+) ([yz]) (-?[\d.]+)",
                         state, re.MULTILINE):
        t = {"id": m[1], "x": float(m[3]), "surface": "floor" if m[2] == "floor" else "wall"}
        t["y" if t["surface"] == "floor" else "z"] = float(m[5])
        targets.append(t)
    questions = {
        "real_fire": {"type": "boolean", "candidates": []},
        "action": {"type": "choice", "candidates": list(ACTIONS)},
    }
    if targets:
        questions["target"] = {"type": "choice", "candidates": target_candidates(targets)}
        noz = {}
        for m in re.finditer(
            r"^nozzle (s\d): heartbeat_age (-?[\d.]+)ms pose_age (-?[\d.]+)s(?: reach (.*))?$",
            state, re.MULTILINE,
        ):
            reach = re.findall(r"(T\d+) (yes|no)", m[4] or "")
            noz[m[1]] = {"hb_ms": float(m[2]), "pose_s": float(m[3]),
                         "reach": {k: v == "yes" for k, v in reach}}
        if all(n in noz for n in NOZZLES):
            cands = nozzle_candidates({"nozzles": noz, "targets": targets})
        else:  # thiếu dòng nozzle: không biết vòi nào khỏe nên không đề xuất both
            cands = list(NOZZLES)
        questions["nozzle"] = {"type": "choice", "candidates": cands}
    return questions


def _nums(values: list, fmt: str = "{:.0f}") -> str:
    return ",".join("-" if v is None else fmt.format(v) for v in values)


def _trend(values: list[float], step: float) -> str:
    delta = values[-1] - values[0]
    return "rising" if delta >= step else "falling" if delta <= -step else "flat"


def _sensor_line(node: str, s: dict) -> str:
    return (
        f"{node} temp_c {_nums(s['temp'])} ({_trend(s['temp'], 4)}) | "
        f"gas {_nums(s['gas'])} ({_trend(s['gas'], 60)}) | hum_pct {_nums(s['hum'])}"
    )


def render_state(obs: dict) -> str:
    lim = obs["limits"]
    lines = [f"stage: {obs['stage']}", f"alarm: {obs['alarm']}",
             f"alarm_limits: temp_c {lim['temp']:g} gas {lim['gas']:g}"]
    for node in ("s1", "s2"):
        lines.append(_sensor_line(node, obs["sensors"][node]))
    if obs["stage"] == "decide":
        lines.append(f"detections ({obs['frames']} frames):" + ("" if obs["targets"] else " none"))
        for t in obs["targets"]:
            lines.append(
                f"{t['id']}: {pos_text(t)} box {t['w']:.2f}x{t['h']:.2f} "
                f"conf {_nums(t['conf'], '{:.2f}')} dist_s1 {t['dist']['s1']:.2f}m "
                f"dist_s2 {t['dist']['s2']:.2f}m"
            )
        for node in ("s1", "s2"):
            n = obs["nozzles"][node]
            reach = " ".join(f"{k} {'yes' if v else 'no'}" for k, v in n["reach"].items())
            lines.append(
                f"nozzle {node}: heartbeat_age {n['hb_ms']:.0f}ms pose_age {n['pose_s']:.0f}s"
                + (f" reach {reach}" if reach else "")
            )
    else:
        v = obs["verify"]
        lines.append(f"sprayed: {v['target']} {pos_text(v)} with {v['nozzle']}")
        lines.append(f"attempt: {v['attempt']} of {v['max_attempts']}")
        lines.append(f"nozzle {v['nozzle']} status: {v['status']}")
        lines.append(f"post_spray {v['nozzle']} temp_c {_nums(v['temp'])} ({_trend(v['temp'], 3)})")
        lines.append(f"post_spray conf {_nums(v['conf'], '{:.2f}')}")
        mark = "not found" if v["mark_cm"] is None else f"{v['mark_cm']:.1f}cm"
        lines.append(f"water_mark offset: {mark} (tolerance {v['tol_cm']:g}cm)")
    return "\n".join(lines)
