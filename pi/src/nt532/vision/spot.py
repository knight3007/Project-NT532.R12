import cv2
import numpy as np


def find_spot(
    on: np.ndarray,
    off: np.ndarray,
    min_rise: int = 40,
    blur: int = 5,
    mask: np.ndarray | None = None,
) -> tuple[float, float] | None:
    """Tìm vết laser đỏ bằng hiệu của ảnh bật và ảnh tắt (BGR, cùng kích thước).

    Trả pixel (u, v) là trọng tâm theo độ sáng của vệt sáng nhất, hoặc None nếu kênh đỏ
    không tăng quá `min_rise` ở đâu cả. `mask` (uint8, khác 0 là vùng xét) giới hạn chỗ tìm,
    ví dụ chỉ trong bảng bia, để người đi qua giữa hai ảnh không bị nhận là vết laser.

    Bảng màu sáng dễ làm kênh đỏ chạm trần 255 nên laser không còn chỗ để tăng: nếu không tìm
    thấy vết, giảm phơi sáng của camera trước khi hạ `min_rise`.
    """
    diff = cv2.subtract(on[:, :, 2], off[:, :, 2])
    diff = cv2.GaussianBlur(diff, (blur, blur), 0)
    if mask is not None:
        diff[mask == 0] = 0
    _, peak, _, peak_loc = cv2.minMaxLoc(diff)
    if peak < min_rise:
        return None
    # Chỉ giữ vùng liền quanh đỉnh để ánh phản xạ ở chỗ khác không kéo lệch trọng tâm.
    mask = (diff >= peak / 2).astype(np.uint8)
    _, labels = cv2.connectedComponents(mask)
    blob = np.where(labels == labels[peak_loc[1], peak_loc[0]], diff, 0).astype(np.float64)
    m = cv2.moments(blob)
    return m["m10"] / m["m00"], m["m01"] / m["m00"]
