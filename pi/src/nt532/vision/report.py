"""Chuyển kết quả commissioning và kiểm tra thành các dòng chữ để in ra."""

import numpy as np

from .system import Commissioning, Health
from .types import TagPose


def yaw_deg(pose: TagPose) -> float:
    """Góc xoay quanh trục Z của tag so với trục X sa bàn, độ, trong (-180, 180]."""
    return float(np.degrees(np.arctan2(pose.rotation[1, 0], pose.rotation[0, 0])))


def pose_line(name: str, pose: TagPose) -> str:
    x, y, z = pose.position
    return (
        f"{name} (tag {pose.tag_id}): x={x:.3f} y={y:.3f} z={z:.3f} m, yaw={yaw_deg(pose):+.1f} độ"
    )


def commissioning_report(
    state: Commissioning, node_ids: dict[str, int], camera_shift_px: float
) -> list[str]:
    cx, cy, cz = state.camera.position
    verdict = "ĐẠT" if state.reprojection_px <= camera_shift_px else "QUÁ NGƯỠNG"
    lines = [
        f"Vị trí camera: x={cx:.3f} y={cy:.3f} z={cz:.3f} m",
        (
            f"Sai số chiếu lại tag tham chiếu: {state.reprojection_px:.2f} px "
            f"(ngưỡng {camera_shift_px:.1f}) {verdict}"
        ),
    ]
    for name, pose in state.nodes.items():
        lines.append(pose_line(name, pose))
    for name in state.missing:
        lines.append(f"{name} (tag {node_ids[name]}): THIẾU, không đủ khung thấy tag")
    rates = ", ".join(f"tag {i}: {r:.0%}" for i, r in sorted(state.tag_rate.items()))
    lines.append(f"Tỷ lệ số khung thấy tag: {rates or 'không thấy tag nào'}")
    return lines


def health_report(health: Health, camera_shift_px: float, node_moved_m: float) -> list[str]:
    if health.camera_shift_px is None:
        lines = ["Camera: không thấy đủ 2 tag tham chiếu, không kết luận được"]
    else:
        lines = [
            f"Camera: lệch {health.camera_shift_px:.2f} px (ngưỡng {camera_shift_px:.1f})",
        ]
    for name, moved in health.node_moved_m.items():
        lines.append(f"Node {name}: dời {moved * 100:.1f} cm (ngưỡng {node_moved_m * 100:.1f})")
    lines.append(
        "Đang hết hạn: " + ", ".join(health.stale) if health.stale else "Không có gì bị xê dịch"
    )
    return lines
