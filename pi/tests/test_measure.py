import csv

import numpy as np
import pytest

from nt532.measure import (
    append_row,
    error_fields,
    nearest_target,
    next_run,
    parse_xy,
    read_rows,
    summarize,
)
from nt532.vision import Detection, Target


def target(x: float, z: float, conf: float = 0.9) -> Target:
    return Target(Detection(0, 0, 10, 10, conf), np.array([x, 0.80, z]))


def test_parse_xy():
    assert parse_xy("0.30,0.20") == (0.30, 0.20)
    with pytest.raises(ValueError):
        parse_xy("0.30")


def test_nearest_target_uses_board_plane_only():
    found = [target(0.50, 0.10), target(0.31, 0.22), target(0.90, 0.50)]
    assert nearest_target(found, (0.30, 0.20)) is found[1]
    assert nearest_target([], (0.30, 0.20)) is None


def test_error_fields():
    f = error_fields((0.30, 0.20), (0.33, 0.24))
    assert f["err_x"] == pytest.approx(0.03) and f["err_z"] == pytest.approx(0.04)
    assert f["err"] == pytest.approx(0.05)
    assert error_fields((0.3, 0.2), None)["err"] == ""
    assert "truth_y" in error_fields((0.4, 0.58), (0.4, 0.6), axis2="y")


def test_csv_roundtrip_and_stats(tmp_path):
    path = tmp_path / "runs" / "target.csv"
    assert next_run(path, "p1") == 1
    for run, dx in enumerate([0.01, 0.03, -0.02], start=1):
        append_row(path, "p1", run, error_fields((0.30, 0.20), (0.30 + dx, 0.20)))
    append_row(path, "p1", 4, error_fields((0.30, 0.20), None))
    assert next_run(path, "p1") == 5 and next_run(path, "p2") == 1
    rows, axis2 = read_rows(path)
    assert axis2 == "z" and len(rows) == 4 and rows[3]["err"] is None
    s = summarize(rows)
    assert (s["n"], s["found"]) == (4, 3)
    assert s["err"]["mean"] == pytest.approx(2.0) and s["err"]["median"] == pytest.approx(2.0)
    assert s["err"]["max"] == pytest.approx(3.0)
    assert s["err_x"]["std"] == pytest.approx(np.std([1, 3, -2], ddof=1))
    assert s["err_z"]["max"] == pytest.approx(0.0)


def test_append_row_rejects_other_columns(tmp_path):
    path = tmp_path / "a.csv"
    append_row(path, "p1", 1, error_fields((0.3, 0.2), (0.3, 0.2)))
    with pytest.raises(ValueError):
        append_row(path, "p1", 2, error_fields((0.3, 0.2), (0.3, 0.2), axis2="y"))
    with path.open(encoding="utf-8") as f:
        assert len(list(csv.reader(f))) == 2


def test_summarize_all_missing():
    s = summarize([{"err": None, "err_x": None, "err_z": None}])
    assert s["found"] == 0 and np.isnan(s["err"]["mean"])
