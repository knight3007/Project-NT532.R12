"""Dựng văn bản STATE từ quan sát có cấu trúc. Chỉ nhận quan sát, không bao giờ nhận truth."""

NONE_OF_THESE = "none of these"
ACTIONS = ["spray", "alarm only", "ignore"]
NOZZLES = ["s1", "s2"]
VERIFY = ["done (fire out)", "re-aim (spray missed)", "spray more (hit, still burning)",
          "call human (give up or fault)"]


def target_candidates(targets: list[dict]) -> list[str]:
    """Chuỗi ứng viên cho câu hỏi `target`, cùng thứ tự với danh sách bia trong state."""
    return [f"{t['id']} (x {t['x']:.2f} z {t['z']:.2f})" for t in targets] + [NONE_OF_THESE]


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
                f"{t['id']}: x {t['x']:.2f} z {t['z']:.2f} box {t['w']:.2f}x{t['h']:.2f} "
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
        lines.append(f"sprayed: {v['target']} x {v['x']:.2f} z {v['z']:.2f} with {v['nozzle']}")
        lines.append(f"attempt: {v['attempt']} of {v['max_attempts']}")
        lines.append(f"nozzle {v['nozzle']} status: {v['status']}")
        lines.append(f"post_spray {v['nozzle']} temp_c {_nums(v['temp'])} ({_trend(v['temp'], 3)})")
        lines.append(f"post_spray conf {_nums(v['conf'], '{:.2f}')}")
        mark = "not found" if v["mark_cm"] is None else f"{v['mark_cm']:.1f}cm"
        lines.append(f"water_mark offset: {mark} (tolerance {v['tol_cm']:g}cm)")
    return "\n".join(lines)
