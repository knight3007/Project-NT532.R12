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


def find_spot_steady(
    ons: list[np.ndarray],
    off: np.ndarray,
    min_rise: int = 40,
    blur: int = 5,
    mask: np.ndarray | None = None,
    near: tuple[float, float, float] | None = None,
    max_area: int = 400,
    min_aspect: float = 0.3,
) -> tuple[float, float] | None:
    """Vết laser sáng liên tục, lọc theo độ ổn định: chỉ giữ pixel mà kênh đỏ của MỌI khung bật đều tăng quá
    `min_rise` so với khung nền `off` (AND các mặt nạ hiệu). Video lửa, người đi lại đổi giữa các khung nên bị
    loại; chấm laser đứng yên thì còn.

    Trong các thành phần liền còn lại bỏ vết lớn hơn `max_area` pixel và vết dẹt (cạnh ngắn / cạnh dài của hộp
    bao dưới `min_aspect`). `near` = (u, v, bán kính pixel): chọn vết có trọng tâm gần `(u, v)` nhất trong bán
    kính đó; không có vết hợp lệ trong bán kính (hoặc không có `near`) thì chọn vết sáng và gọn nhất cả vùng
    `mask`. Trả pixel (u, v) theo trọng tâm có trọng số độ sáng, None nếu không còn vết nào."""
    diffs = [cv2.GaussianBlur(cv2.subtract(on[:, :, 2], off[:, :, 2]), (blur, blur), 0) for on in ons]
    steady = diffs[0]
    for d in diffs[1:]:
        steady = np.minimum(steady, d)
    binary = (steady >= min_rise).astype(np.uint8)
    if mask is not None:
        binary[mask == 0] = 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    cands = []  # (u, v, độ sáng trung bình)
    for k in range(1, n):
        area = int(stats[k, cv2.CC_STAT_AREA])
        w, h = int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT])
        if area > max_area or min(w, h) < min_aspect * max(w, h):
            continue
        blob = np.where(labels == k, steady, 0).astype(np.float64)
        m = cv2.moments(blob)
        if m["m00"] <= 0:
            continue
        cands.append((m["m10"] / m["m00"], m["m01"] / m["m00"], m["m00"] / area))
    if not cands:
        return None
    if near is not None:
        close = [c for c in cands if np.hypot(c[0] - near[0], c[1] - near[1]) <= near[2]]
        if close:
            u, v, _ = min(close, key=lambda c: np.hypot(c[0] - near[0], c[1] - near[1]))
            return u, v
    u, v, _ = max(cands, key=lambda c: c[2])
    return u, v
