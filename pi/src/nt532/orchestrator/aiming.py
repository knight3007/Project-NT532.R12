"""Tính góc pan/tilt từ pose đế vòi và kiểm tra giới hạn.

Quy ước góc giống `Node.direction` của sa bàn ảo: pan = 0, tilt = 0 là hướng đầu của node (yaw);
với yaw = 0 đó là +Y. pan dương quay về phía bên trái node (với yaw = 0 là -X); tilt dương ngẩng lên +Z.
Yaw (độ) là góc quay ngược chiều kim đồng hồ nhìn từ trên xuống của hướng đầu so với +Y, tức
yaw = atan2(-hx, hy) với h là hướng đầu trong mặt phẳng XY. Hướng đầu lấy từ trục +Y của tag đế (chiếu
xuống XY), hoặc `actuator.nodes.<n>.yaw_deg` nếu có. Bảng hiệu chỉnh servo (góc lệnh -> góc thật) để Hiếu đo ở
tuần 3; ở đây chỉ có offset tâm quay, giới hạn và góc bù tilt cho tia nước từ site.yaml.
"""

import math
from dataclasses import dataclass

import numpy as np

# Giả định khi site.yaml còn để null, cùng số với bộ kịch bản của mô hình quyết định (PAN_LIM = 70:
# node ở X 0,30 và 0,90 cách bảng 0,24 m, với ±50 không có vùng nào cả hai vòi cùng với tới).
DEFAULT_PAN = (-70.0, 70.0)
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


def pivot_offset(site: dict, node: str | None = None) -> tuple[float, float, float]:
    """Tâm quay trong hệ tag: `actuator.nodes.<node>.pivot_offset` nếu có, không thì `actuator.pivot_offset`
    chung, không thì mặc định. Chỉ là số giả định ban đầu: trạm tự hiệu chuẩn lại (orchestrator/selfcal.py)."""
    act = site.get("actuator", {})
    cfg = (act.get("nodes", {}) or {}).get(node) or {} if node else {}
    return tuple(cfg.get("pivot_offset") or act.get("pivot_offset") or DEFAULT_PIVOT_OFFSET)


def water_tilt(site: dict, node: str | None = None, distance_m: float | None = None) -> float:
    """Góc cộng thêm vào tilt khi phun nước (tia nước rơi theo quỹ đạo); 0 cho tới khi đo.

    Thứ tự: `water_tilt_table` của node nội suy theo `distance_m` (khoảng cách ngang tâm quay -> bia,
    tức trong mặt phẳng XY, dùng chung cho bia trên bảng và trên nền; ngoài đầu bảng thì giữ giá trị đầu), rồi `nodes.<node>.water_tilt_deg`, rồi `actuator.water_tilt_deg`
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
    """Khoảng cách ngang (m) từ tâm quay tới bia, trong mặt phẳng XY (bỏ chênh cao Z), dùng tra
    `water_tilt_table`. Bia ở bảng đứng hay trên nền đều tính cùng một cách."""
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


def heading_yaw_deg(pose) -> float:
    """Yaw (độ, (-180, 180]) của node từ pose tag đế: trục +Y của tag (cột 2 của ma trận xoay) chiếu
    xuống mặt phẳng XY. Tag không xoay quanh Z thì ra 0 (hướng +Y)."""
    h = np.asarray(pose.rotation, float)[:, 1]
    return math.degrees(math.atan2(-h[0], h[1]))


def node_yaw(site: dict, node: str, pose=None) -> float:
    """Yaw của `node`: `actuator.nodes.<node>.yaw_deg` nếu đặt (thắng), không thì từ `pose` tag, không
    có pose thì 0."""
    cfg = (site.get("actuator", {}).get("nodes", {}) or {}).get(node) or {}
    if cfg.get("yaw_deg") is not None:
        return float(cfg["yaw_deg"])
    return 0.0 if pose is None else heading_yaw_deg(pose)


def _wrap(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0 if not -180.0 < deg <= 180.0 else deg


def angles(pivot, target, yaw_deg: float = 0.0) -> tuple[float, float]:
    """(pan, tilt) độ để tia từ `pivot` đi qua `target`; pan đo so với hướng đầu `yaw_deg` của node
    (0 = +Y như cũ), tilt so với mặt phẳng ngang."""
    v = np.asarray(target, float) - np.asarray(pivot, float)
    pan = _wrap(math.degrees(math.atan2(-v[0], v[1])) - yaw_deg)
    tilt = math.degrees(math.atan2(v[2], math.hypot(v[0], v[1])))
    return pan, tilt


def reachable_point(site: dict, node: str, pivot, point, yaw_deg: float = 0.0) -> bool:
    """Điểm 3D (x, y, z) bất kỳ (bảng đứng hay nền) có nằm trong giới hạn pan/tilt của `node` không."""
    pan, tilt = angles(pivot, point, yaw_deg)
    return limits(site, node).ok(pan, tilt)


def reachable(site: dict, node: str, pivot, x: float, z: float, y: float | None = None,
              yaw_deg: float = 0.0) -> bool:
    """Bia (x, z) trên mặt bảng (y = plane_y) hoặc, nếu có `y`, điểm (x, y, z) bất kỳ có với tới được
    không. Chữ ký cũ (x, z) vẫn dùng được cho bia trên bảng."""
    y = site["board"]["plane_y"] if y is None else y
    return reachable_point(site, node, pivot, (x, y, z), yaw_deg)
