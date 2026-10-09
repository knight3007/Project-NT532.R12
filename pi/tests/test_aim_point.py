import csv
import importlib.util
from argparse import Namespace
from pathlib import Path

import pytest

from nt532.station import build_sim

_path = Path(__file__).resolve().parents[1] / "scripts" / "aim_point.py"
_spec = importlib.util.spec_from_file_location("aim_point", _path)
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)

ARGS = Namespace(repeat=1, water=False, no_laser=False, gate_cm=5.0)


def rows_of(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def run_on(link, tmp_path, points):
    st = build_sim(link=link, fps=12)
    try:
        st.start(orchestrate=False)
        sent = []
        orig = st.link.aim
        st.link.aim = lambda *a, **k: (sent.append(a), orig(*a, **k))[1]
        out = tmp_path / "aim.csv"
        rows = ap.run_points(st, points, ARGS, out)
        return st, rows, rows_of(out), sent
    finally:
        st.stop()
        # st.world còn đọc được sau stop: trạng thái laser cuối cùng


@pytest.mark.parametrize("link", ["mem", "coap"])
def test_point_near_centre_is_measured_and_laser_ends_off(tmp_path, link):
    st, _, csv_rows, sent = run_on(link, tmp_path, [("s1", 0.45, 0.30)])
    assert len(csv_rows) == 1 and len(sent) >= 1
    miss = float(csv_rows[0]["miss_cm"])
    assert 0 <= miss < 15
    assert csv_rows[0]["pass"] in ("True", "False")
    assert not any(st.world.truth()["lasers"].values())


def test_point_outside_limits_is_skipped_without_aim(tmp_path):
    st, _, csv_rows, sent = run_on("mem", tmp_path, [("s1", 1.15, 0.30)])
    assert sent == []
    assert "ngoài giới hạn" in csv_rows[0]["note"] and csv_rows[0]["miss_cm"] == ""
    assert not any(st.world.truth()["lasers"].values())


def test_grid_points_inside_board():
    site = {"board": {"width": 1.2, "height": 0.6}}
    pts = ap.grid_points(site)
    assert len(pts) == 9 and all(0 < x < 1.2 and 0 < z < 0.6 for x, z in pts)
