"""Ruler-vs-mapping accuracy metrics; inputs are hand-written, never mapping-derived."""

import importlib.util
import json
import math
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "physical_accuracy.py"
    spec = importlib.util.spec_from_file_location("physical_accuracy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _estimate(**overrides):
    result = {"status": "PASS", "coordinate_frame": "tag_22_table",
              "operational_origin_id": 22, "unit": "m",
              "references": {"2": [0.30, 0.20, 0.0]},
              "nodes": {"s1": [0.80, 0.0, 0.0], "s2": [0.0, 0.0, 0.0]},
              "fire_targets": [{"xyz": [0.40, 0.50, 0.0]}]}
    result.update(overrides)
    return result


def _row(target, x, y, position="pos01", surface="table"):
    return {"position": position, "target": target, "x_true_cm": str(x),
            "y_true_cm": str(y), "surface": surface}


def test_metrics_follow_definitions():
    m = _module()
    truth = [_row("ID2", 29.0, 20.0), _row("ID21", 80.0, 3.0),
             _row("fire1", 43.0, 46.0, surface="flat_on_table")]
    report = m.evaluate(truth, {"pos01": _estimate()})
    errors = [1.0, 3.0, 5.0]  # (1,0), (0,-3), (-3,4)
    assert [s["xy_error_cm"] for s in report["samples"]] == pytest.approx(errors)
    o = report["overall"]
    assert o["mae_xy_cm"] == pytest.approx(3.0)
    assert o["median_xy_cm"] == pytest.approx(3.0)
    assert o["max_xy_cm"] == pytest.approx(5.0)
    assert o["rmse_xy_cm"] == pytest.approx(math.sqrt(35 / 3), abs=1e-3)
    assert o["mae_x_cm"] == pytest.approx(4 / 3, abs=1e-3)
    assert report["per_target"]["fire1"]["n"] == 1
    assert report["physical_accuracy"] == "MEASURED"  # never PASS without an explicit limit


def test_limit_gives_pass_or_fail_from_measured_errors_only():
    m = _module()
    truth = [_row("ID2", 29.0, 20.0)]
    assert m.evaluate(truth, {"pos01": _estimate()}, 1.5)["physical_accuracy"] == "PASS"
    assert m.evaluate(truth, {"pos01": _estimate()}, 0.5)["physical_accuracy"] == "FAIL"


def test_upright_fire_and_origin_are_excluded():
    m = _module()
    truth = [_row("ID22", 0, 0), _row("fire1", 40, 50, surface="phone_upright")]
    report = m.evaluate(truth, {"pos01": _estimate()})
    assert report["samples"] == [] and len(report["excluded"]) == 2
    assert report["physical_accuracy"] == "NOT_VERIFIED"


@pytest.mark.parametrize("bad", [{"status": "BLOCKED", "reason": "ID 22 square geometry"},
                                 {"coordinate_frame": "tag_2_table"},
                                 {"operational_origin_id": 2}])
def test_untrusted_mapping_is_rejected(bad):
    m = _module()
    with pytest.raises(ValueError):
        m.evaluate([_row("ID2", 30, 20)], {"pos01": _estimate(**bad)})


def test_cli_reports_not_verified_for_blocked_mapping(tmp_path, capsys):
    m = _module()
    truth = tmp_path / "truth.csv"
    truth.write_text("position,target,x_true_cm,y_true_cm,surface,measured_by,notes\n"
                     "pos01,ID21,80,0,table,op,\n", encoding="utf-8")
    estimate = tmp_path / "origin22.json"
    estimate.write_text(json.dumps(_estimate(status="BLOCKED")), encoding="utf-8")
    assert m.main(["--ground-truth", str(truth), "--estimate", f"pos01={estimate}"]) == 1
    assert json.loads(capsys.readouterr().out)["physical_accuracy"] == "NOT_VERIFIED"
