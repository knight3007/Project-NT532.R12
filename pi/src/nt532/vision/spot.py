import cv2
import numpy as np


def find_spot(
    on: np.ndarray, off: np.ndarray, min_rise: int = 40, blur: int = 5
) -> tuple[float, float] | None:
    """Tìm vết laser đỏ bằng hiệu của ảnh bật và ảnh tắt (BGR, cùng kích thước).

    Trả pixel (u, v) là trọng tâm theo độ sáng của vệt sáng nhất, hoặc None nếu kênh đỏ
    không tăng quá `min_rise` ở đâu cả.
    """
    diff = cv2.subtract(on[:, :, 2], off[:, :, 2])
    diff = cv2.GaussianBlur(diff, (blur, blur), 0)
    _, peak, _, peak_loc = cv2.minMaxLoc(diff)
    if peak < min_rise:
        return None
    # Chỉ giữ vùng liền quanh đỉnh để ánh phản xạ ở chỗ khác không kéo lệch trọng tâm.
    mask = (diff >= peak / 2).astype(np.uint8)
    _, labels = cv2.connectedComponents(mask)
    blob = np.where(labels == labels[peak_loc[1], peak_loc[0]], diff, 0).astype(np.float64)
    m = cv2.moments(blob)
    return m["m10"] / m["m00"], m["m01"] / m["m00"]
