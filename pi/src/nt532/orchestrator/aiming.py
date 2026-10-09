"""Tính góc pan/tilt từ pose đế vòi và kiểm tra giới hạn.

Quy ước góc giống `Node.direction` của sa bàn ảo: pan = 0, tilt = 0 là hướng +Y; pan dương quay
về phía -X; tilt dương ngẩng lên +Z. Bảng hiệu chỉnh servo (góc lệnh -> góc thật) để Hiếu đo ở
tuần 3; ở đây chỉ có offset tâm quay, giới hạn và góc bù tilt cho tia nước từ site.yaml.
"""

import math
from dataclasses import dataclass

import numpy as np

# Giả định khi site.yaml còn để null, cùng số với bộ kịch bản của mô hình quyết định.
DEFAULT_PAN = (-50.0, 50.0)
DEFAULT_TILT = (-35.0, 45.0)
DEFAULT_PIVOT_OFFSET = (0.0, 0.0, 0.08)


@dataclass(frozen=True)
class Limits:
    pan: tuple[float, float]
    tilt: tuple[float, float]

    def ok(self, pan: float, tilt: float) -> bool:
        return self.pan[0] <= pan <= self.pan[1] and self.tilt[0] <= tilt <= self.tilt[1]


def limits(site: dict, node: str) -> Limits:
    cfg = (site.get("actuator", {}).get("nodes", {}) or {}).get(node) or {}
    return Limits(tuple(cfg.get("pan_limits") or DEFAULT_PAN),
                  tuple(cfg.get("tilt_limits") or DEFAULT_TILT))


def pivot_offset(site: dict) -> tuple[float, float, float]:
    return tuple(site.get("actuator", {}).get("pivot_offset") or DEFAULT_PIVOT_OFFSET)


def water_tilt(site: dict, node: str | None = None, distance_m: float | None = None) -> float:
    """Góc cộng thêm vào tilt khi phun nước (tia nước rơi theo quỹ đạo); 0 cho tới khi đo.

    Thứ tự: `water_tilt_table` của node nội suy theo `distance_m` (khoảng cách ngang tâm quay -> bia,
    ngoài đầu bảng thì giữ giá trị đầu), rồi `nodes.<node>.water_tilt_deg`, rồi `actuator.water_tilt_deg`
    chung. Gọi một đối số `water_tilt(site)` vẫn trả giá trị chung như trước.
    """
    act = site.get("actuator", {})
    cfg = (act.get("nodes", {}) or {}).get(node) or {} if node else {}
    table = cfg.get("water_tilt_table")
    if table and distance_m is not None:
        pts = sorted((float(d), float(a)) for d, a in table)
        return float(np.interp(distance_m, [d for d, _ in pts], [a for _, a in pts]))
    per_node = cfg.get("water_tilt_deg")
    if per_node is not None:
        return float(per_node)
    return float(act.get("water_tilt_deg") or 0.0)


def horizontal_distance(pivot, target) -> float:
    """Khoảng cách ngang (m) từ tâm quay tới bia, dùng tra `water_tilt_table`."""
    v = np.asarray(target, float) - np.asarray(pivot, float)
    return float(math.hypot(v[0], v[1]))


def check_info(site: dict, node: str, info) -> list[str]:
    """So `/info` của node với site.yaml; trả danh sách chênh lệch (rỗng nếu khớp)."""
    out = []
    if info.n != node:
        out.append(f"node tự nhận là {info.n!r}")
    lim = limits(site, node)
    for name, theirs, mine in (("pan", info.pan, lim.pan), ("tilt", info.tilt, lim.tilt)):
        if tuple(theirs) != tuple(mine):
            out.append(f"{name} node {list(theirs)} khác site.yaml {list(mine)}")
    hb = int(site.get("actuator", {}).get("heartbeat_ms", 500))
    if info.hb < 3 * hb:
        out.append(f"node tắt vòi sau {info.hb} ms, ngắn hơn 3 nhịp heartbeat ({3 * hb} ms)")
    fire = site.get("actuator", {}).get("fire_ms")
    if fire and fire > info.fire:
        out.append(f"fire_ms site.yaml {fire} lớn hơn tối đa của node {info.fire} ms")
    return out


def narrow_limits(site: dict, node: str, info) -> bool:
    """Thu giới hạn góc của `node` trong `site` về phần chung với giới hạn node tự báo (node sẽ từ chối
    phần ngoài, Pi không cần thử). Trả True nếu có thu hẹp."""
    lim = limits(site, node)
    pan = (max(lim.pan[0], info.pan[0]), min(lim.pan[1], info.pan[1]))
    tilt = (max(lim.tilt[0], info.tilt[0]), min(lim.tilt[1], info.tilt[1]))
    if pan == lim.pan and tilt == lim.tilt:
        return False
    nodes = site.setdefault("actuator", {}).setdefault("nodes", {})
    cfg = nodes.get(node) or {}
    nodes[node] = {**cfg, "pan_limits": list(pan), "tilt_limits": list(tilt)}
    return True


def angles(pivot, target) -> tuple[float, float]:
    """(pan, tilt) độ để tia từ `pivot` đi qua `target`, cả hai trong hệ sa bàn."""
    v = np.asarray(target, float) - np.asarray(pivot, float)
    pan = math.degrees(math.atan2(-v[0], v[1]))
    tilt = math.degrees(math.atan2(v[2], math.hypot(v[0], v[1])))
    return pan, tilt


def reachable(site: dict, node: str, pivot, x: float, z: float) -> bool:
    """Bia (x, z) trên mặt bảng có nằm trong giới hạn góc của `node` không."""
    pan, tilt = angles(pivot, (x, site["board"]["plane_y"], z))
    return limits(site, node).ok(pan, tilt)
