import pytest

from nt532.vision.types import Detection
from nt532.vision.yololabel import yolo_label_lines


def test_doi_xywh_pixel_sang_yolo():
    lines = yolo_label_lines([Detection(320, 240, 160, 120, 0.9)], 640, 480)
    assert lines == ["1 0.500000 0.500000 0.250000 0.250000"]


def test_hop_vuot_mep_bi_cat_vao_anh():
    # Hộp trải từ x = -20 đến 60, y = 90 đến 130 trên ảnh 100x100.
    (line,) = yolo_label_lines([Detection(20, 110, 80, 40, 0.8)], 100, 100)
    cls, cx, cy, w, h = line.split()
    assert cls == "1"
    assert float(cx) == pytest.approx(0.3)
    assert float(cy) == pytest.approx(0.95)
    assert float(w) == pytest.approx(0.6)
    assert float(h) == pytest.approx(0.1)


def test_hop_nam_ngoai_anh_bi_bo():
    assert yolo_label_lines([Detection(-50, 50, 20, 20, 0.9)], 100, 100) == []


def test_khong_phat_hien_cho_nhan_rong():
    assert yolo_label_lines([], 640, 480) == []
