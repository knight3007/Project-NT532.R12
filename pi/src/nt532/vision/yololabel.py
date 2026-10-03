from .types import Detection

FIRE_CLASS_ID = 1  # khớp names: [smoke, fire] của mọi bộ dữ liệu trong repo


def yolo_label_lines(
    dets: list[Detection], width: int, height: int, class_id: int = FIRE_CLASS_ID
) -> list[str]:
    """Đổi hộp xywh theo pixel sang dòng nhãn YOLO (cx cy w h chuẩn hóa về 0..1).

    Hộp được cắt vào trong ảnh; hộp bị cắt hết (rộng hoặc cao bằng 0) thì bỏ.
    """
    lines = []
    for d in dets:
        x0, x1 = max(d.u - d.w / 2, 0.0), min(d.u + d.w / 2, float(width))
        y0, y1 = max(d.v - d.h / 2, 0.0), min(d.v + d.h / 2, float(height))
        if x1 <= x0 or y1 <= y0:
            continue
        cx, cy = (x0 + x1) / 2 / width, (y0 + y1) / 2 / height
        w, h = (x1 - x0) / width, (y1 - y0) / height
        lines.append(f"{class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    return lines
