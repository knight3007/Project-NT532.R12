import importlib.util
from pathlib import Path

import numpy as np

_path = Path(__file__).resolve().parents[1] / "scripts" / "stream_latency.py"
_spec = importlib.util.spec_from_file_location("stream_latency", _path)
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


def _frame(v=50, spot=None):
    f = np.full((60, 80, 3), v, np.uint8)
    if spot:
        f[20:30, 30:40] = spot
    return f


def test_first_change_finds_stamp_of_first_laser_frame():
    base = sl.prep(_frame())
    frames = [(_frame(), 1.0), (_frame(52), 1.1), (_frame(spot=255), 1.2), (_frame(spot=255), 1.3)]
    assert sl.first_change(base, frames, 40) == 1.2


def test_first_change_none_when_no_change():
    base = sl.prep(_frame())
    assert sl.first_change(base, [(_frame(), 1.0), (_frame(53), 1.1)], 40) is None
    assert sl.first_change(base, [], 40) is None


def test_suggest_rounds_up_plus_margin():
    assert sl.suggest([0.61, 0.62, 0.9]) == 0.75  # trung vị 0,62 -> 0,65 + 0,1
    assert sl.suggest([0.5]) == 0.6
