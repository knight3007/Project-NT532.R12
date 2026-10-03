"""Vòng CORRECT đơn giản trên sa bàn ảo, để thử và để kiểm tra phần thị giác.

Chỉ là bản tối thiểu cho mô phỏng: công thức ngắm và vòng chính thức do orchestrator (Hậu) viết.
"""

from dataclasses import dataclass, field

import numpy as np

from ..vision import Vision, read_fresh
from .camera import SimCamera
from .scene import aim_angles


@dataclass
class CorrectResult:
    measured: list[np.ndarray | None] = field(default_factory=list)  # find_spot mỗi vòng (x, y, z)
    errors_m: list[float] = field(default_factory=list)  # |target - vết đo được| theo (x, z), mét
    truth: list[np.ndarray | None] = field(default_factory=list)  # vết thật (ground truth) mỗi vòng
    lost: bool = False  # một vòng không thấy vết laser

    @property
    def rounds(self) -> int:
        return len(self.measured)


def correct(
    cap: SimCamera,
    vision: Vision,
    node: str,
    target: np.ndarray,
    max_iters: int = 3,
    done_m: float = 0.01,
    pivot_offset=(0.0, 0.0, 0.08),
) -> CorrectResult:
    """Ngắm `target` (x, y, z) bằng laser của `node`, đo vết bằng find_spot rồi bù độ lệch.

    Mỗi vòng: ngắm vào điểm đang nhắm, chụp ảnh tắt rồi bật laser, đo vết; dừng khi lệch dưới
    `done_m`, còn không thì dời điểm nhắm đúng bằng độ lệch đo được. Tâm quay lấy từ pose đế do
    commissioning đo (cộng `pivot_offset`), không phải giá trị thật của sim. Laser tắt khi xong.
    """
    scene = cap.scene
    pivot = vision.nodes[node].to_world(pivot_offset)
    aim_point = np.asarray(target, float).copy()
    result = CorrectResult()
    for i in range(max_iters):
        scene.aim(node, *aim_angles(pivot, aim_point))
        scene.set_laser(node, False)
        off = read_fresh(cap)
        scene.set_laser(node, True)
        on = read_fresh(cap)
        scene.set_laser(node, False)
        spot = vision.find_spot(on, off)
        result.measured.append(spot)
        result.truth.append(scene.spot(node))
        if spot is None:
            result.lost = True
            break
        miss = np.asarray(target, float) - spot
        result.errors_m.append(float(np.hypot(miss[0], miss[2])))
        if result.errors_m[-1] < done_m or i == max_iters - 1:
            break
        aim_point = aim_point + miss
    return result
