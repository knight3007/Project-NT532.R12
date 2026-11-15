"""Lọc vết laser sáng liên tục: video lửa chập chờn và đèn relay không được thắng chấm laser thật."""

import numpy as np

from nt532.vision.spot import find_spot_steady

LASER = (950, 200)
RELAY = (921, 970)
FIRE = (920, 0, 1100, 80)  # x0, y0, x1, y1 vùng video lửa


def frames(seed=0):
    rng = np.random.default_rng(seed)
    off = np.full((1080, 1920, 3), 40, np.uint8)

    def on():
        f = off.copy()
        x0, y0, x1, y1 = FIRE  # video: đổi ngẫu nhiên mỗi khung, rise lớn
        f[y0:y1, x0:x1, 2] = rng.integers(150, 255, (y1 - y0, x1 - x0))
        f[LASER[1] - 4:LASER[1] + 5, LASER[0] - 4:LASER[0] + 5, 2] = 150  # chấm laser nhỏ, rise 110
        f[RELAY[1] - 6:RELAY[1] + 7, RELAY[0] - 6:RELAY[0] + 7, 2] = 255  # đèn relay, sáng hơn laser
        return f

    return on(), on(), off


def test_picks_laser_not_video_or_relay():
    a, b, off = frames()
    mask = np.full(off.shape[:2], 255, np.uint8)
    cv_mask = mask.copy()
    cv_mask[950:1000, 880:960] = 0  # che chân trụ như Vision.find_spot
    u, v = find_spot_steady([a, b], off, min_rise=40, mask=cv_mask, near=(930, 150, 150))
    assert abs(u - LASER[0]) < 2 and abs(v - LASER[1]) < 2


def test_falls_back_to_whole_area_when_nothing_near():
    a, b, off = frames(1)
    mask = np.full(off.shape[:2], 255, np.uint8)
    mask[950:1000, 880:960] = 0
    u, v = find_spot_steady([a, b], off, min_rise=40, mask=mask, near=(300, 800, 60))
    assert abs(u - LASER[0]) < 2 and abs(v - LASER[1]) < 2


def test_single_frame_would_choose_video():
    a, _, off = frames(2)
    from nt532.vision.spot import find_spot

    u, v = find_spot(a, off, min_rise=40)
    assert not (abs(u - LASER[0]) < 5 and abs(v - LASER[1]) < 5)  # xác nhận lỗi gốc: không lọc thì sai


def test_big_blob_dropped_and_none_when_empty():
    off = np.full((1080, 1920, 3), 40, np.uint8)
    on = off.copy()
    on[100:200, 100:200, 2] = 250  # vết 100x100: quá lớn
    assert find_spot_steady([on, on], off, min_rise=40) is None
