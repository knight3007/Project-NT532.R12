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


def water_tilt(site: dict) -> float:
    """Góc cộng thêm vào tilt khi phun nước (tia nước rơi theo quỹ đạo); 0 cho tới khi đo."""
    return float(site.get("actuator", {}).get("water_tilt_deg") or 0.0)


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
