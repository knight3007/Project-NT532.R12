"""Dữ liệu thật quay lại pipeline: ước lượng SensorModel, ghi đè bằng yaml, xuất lượt thật."""

import importlib.util
import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml
from test_scenario import fake_records

from nt532.decider.rules import rule_answers
from nt532.decider.scenario import (
    NORMAL,
    SENSOR,
    SensorModel,
    generate,
    load_geometry,
    load_sensor_model,
    pool_from_records,
)
from nt532.decider.sensor_fit import (
    Sample,
    fit_sensor_model,
    load_log,
    quiet_from_log,
    to_yaml_dict,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- ước lượng SensorModel ----------------------------------------------------------------------

TRUE = replace(
    SENSOR, temp_amb=(20.0, 30.0), gas_amb=(150.0, 250.0), hum_amb=(45.0, 60.0), temp_noise=0.4,
    gas_noise=20.0, hum_noise=1.5, decay_m=0.5, onset=(5.0, 10.0), tau=(3.0, 8.0),
    fire_gain=(1.3, 2.2), quiet_gain=(0.2, 0.5), steam_gain=(1.2, 1.8), spike_gain=(1.5, 2.5),
    steam_hum_rise=(20.0, 30.0), fire_hum_shift=(-4.0, 2.0),
)
LIM = {"temp": TRUE.temp_thr, "gas": TRUE.gas_thr}


def synth(sm: SensorModel, seed: int = 0, n_fire: int = 24) -> list[Sample]:
    """Số đo giả từ một SensorModel biết trước, theo cùng cấu trúc với bộ sinh kịch bản."""
    rng = np.random.default_rng(seed)
    out, t_clock = [], 0.0
    nodes = {"s1": 0.1, "s2": 0.5}  # khoảng cách tới nguồn

    def emit(label, series, node, t0, dist=None):
        for i in range(len(series["temp"])):
            out.append(Sample(0, t0 + i, node, float(series["temp"][i]), float(series["gas"][i]),
                              float(series["hum"][i]), label, dist))

    def series(amb, peaks, t0, tau, n, pulse=0):
        t = np.arange(n)
        shape = (((t >= t0) & (t < t0 + pulse)).astype(float) if pulse
                 else np.where(t > t0, 1 - np.exp(-(t - t0) / tau), 0.0))
        noises = (sm.temp_noise, sm.gas_noise, sm.hum_noise)
        return {k: amb[i] + peaks[i] * shape + rng.normal(0, noises[i], n)
                for i, k in enumerate(("temp", "gas", "hum"))}

    for _ in range(12):  # đoạn nền
        amb = (rng.uniform(*sm.temp_amb), rng.uniform(*sm.gas_amb), rng.uniform(*sm.hum_amb))
        for node in nodes:
            emit("baseline", series(amb, (0, 0, 0), 0, 1, 60), node, t_clock)
        t_clock += 200
    for k in range(n_fire):
        label = ["fire", "fire", "steam", "spike"][k % 4]
        amb = (rng.uniform(*sm.temp_amb), rng.uniform(*sm.gas_amb), rng.uniform(*sm.hum_amb))
        dt, dg = sm.temp_thr - amb[0], sm.gas_thr - amb[1]
        t0, tau = rng.uniform(*sm.onset), rng.uniform(*sm.tau)
        drive = rng.choice(["temp", "gas", "both"])
        gain = sm.steam_gain if label == "steam" else sm.fire_gain
        gt = rng.uniform(*(gain if drive != "gas" else sm.quiet_gain))
        gg = rng.uniform(*(gain if drive != "temp" else sm.quiet_gain))
        hum = rng.uniform(*(sm.steam_hum_rise if label == "steam" else sm.fire_hum_shift))
        spike = rng.uniform(*sm.spike_gain)
        for node, dist in nodes.items():
            d = np.exp(-(dist - 0.1) / sm.decay_m)
            if label == "spike":
                peaks = (spike * dt, 0.0, 0.0) if node == "s1" else (0.0, 0.0, 0.0)
                s = series(amb, peaks, t0, tau, 70, pulse=4)
            else:
                s = series(amb, (gt * dt * d, gg * dg * d, hum * d), t0, tau, 70)
            emit(label, s, node, t_clock, dist if label == "fire" else None)
        t_clock += 300
    return out


def test_fit_recovers_known_sensor_model():
    fits = fit_sensor_model(synth(TRUE), LIM)

    def val(name):
        assert fits[name].value is not None, (name, fits[name])
        return fits[name].value

    for key, noise in (("temp", 0.4), ("gas", 20.0), ("hum", 1.5)):
        assert val(f"{key}_noise") == pytest.approx(noise, rel=0.15)
    assert val("temp_amb")[0] == pytest.approx(20.0, abs=2.5)
    assert val("temp_amb")[1] == pytest.approx(30.0, abs=2.5)
    assert val("gas_amb")[0] == pytest.approx(150.0, abs=25)
    assert val("gas_amb")[1] == pytest.approx(250.0, abs=25)
    assert val("hum_amb")[0] == pytest.approx(45.0, abs=3)
    assert val("decay_m") == pytest.approx(0.5, rel=0.3)
    for name, true in (("fire_gain", TRUE.fire_gain), ("quiet_gain", TRUE.quiet_gain),
                       ("steam_gain", TRUE.steam_gain), ("spike_gain", TRUE.spike_gain),
                       ("steam_hum_rise", TRUE.steam_hum_rise), ("onset", TRUE.onset),
                       ("tau", TRUE.tau)):
        lo, hi = val(name)
        assert lo <= hi
        assert (lo + hi) / 2 == pytest.approx(sum(true) / 2, rel=0.25), name
    assert val("fire_hum_shift")[0] < 0 < val("fire_hum_shift")[1] + 3
    assert fits["temp_thr"].value is None and fits["gas_thr"].value is None


def test_fit_reports_not_enough_data():
    s = [x for x in synth(TRUE, n_fire=0)][:10]  # chỉ 10 mẫu nền
    fits = fit_sensor_model(s, LIM)
    assert all(f.value is None for f in fits.values())
    assert to_yaml_dict(fits) == {}
    # không có dist_m thì không ước lượng được decay_m, các đại lượng khác vẫn có
    no_dist = [replace(x, dist=None) for x in synth(TRUE)]
    fits = fit_sensor_model(no_dist, LIM)
    assert fits["decay_m"].value is None and fits["fire_gain"].value is not None


def test_log_baseline_skips_samples_near_alerts(tmp_path):
    lines = [{"kind": "tel", "time": 1000.0 + i, "n": "s1", "s": i, "t": 27.0, "g": 200.0, "h": 50.0}
             for i in range(200)]
    lines.append({"kind": "alert", "time": 1100.0, "text": "x"})
    path = tmp_path / "a.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    raw, alerts = load_log(path)
    quiet = quiet_from_log(raw, alerts, LIM)
    assert len(raw) == 200 and alerts == [1100.0]
    assert all(abs(s.t - 1100.0) >= 60 for s in quiet) and 0 < len(quiet) < 100


# --- ghi đè bằng yaml ---------------------------------------------------------------------------

def test_sensor_model_override_changes_generated_values(tmp_path):
    pool = pool_from_records(fake_records(), "train")
    default = generate(30, pool, NORMAL, "train", 1)
    assert json.dumps(generate(30, pool, NORMAL, "train", 1, load_sensor_model(None))) == json.dumps(default)

    path = tmp_path / "fit.yaml"
    path.write_text(yaml.safe_dump({"sensor_model": {"hum_amb": [80.0, 81.0], "temp_thr": 50.0}}),
                    encoding="utf-8")
    sm = load_sensor_model(path)
    assert sm.hum_amb == (80.0, 81.0) and sm.gas_amb == SENSOR.gas_amb
    over = generate(30, pool, NORMAL, "train", 1, sm)

    def hums(recs):
        return [h for r in recs for node in r["obs"]["sensors"].values() for h in node["hum"]]

    assert np.median(hums(default)) < 65 and np.median(hums(over)) > 78
    assert over[0]["obs"]["limits"]["temp"] == 50.0

    bad = tmp_path / "bad.yaml"
    bad.write_text("sensor_model: {hum_ambb: [1, 2]}", encoding="utf-8")
    with pytest.raises(ValueError):
        load_sensor_model(bad)


# --- xuất lượt thật -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def station_log(tmp_path_factory):
    from nt532.orchestrator.machine import Settings
    from nt532.station import build_sim

    path = tmp_path_factory.mktemp("log") / "demo.jsonl"
    st = build_sim(settings=Settings(frame_gap_s=0.05, verify_wait_s=1.0), fps=12, log_path=path)
    st.start()
    try:
        st.world.ignite(0.32, 0.30)
        end = time.time() + 60
        while not st.orch.runs and time.time() < end:
            time.sleep(0.1)
        assert st.orch.runs, "không có lượt nào hoàn tất"
    finally:
        st.stop()
    return path


def test_station_logs_telemetry_and_fit_reads_it(station_log):
    raw, _ = load_log(station_log)
    assert raw and {s.node for s in raw} <= {"s1", "s2"}
    assert raw[0].label is None and raw[0].gas > 0


def test_export_episodes_are_scorable_and_annotated(station_log):
    exp, ev = script("export_episodes"), script("eval_rules")
    runs = exp.read_runs(station_log)
    assert runs, "log không có lượt hoàn tất kèm obs"
    recs = exp.episodes_of("demo", runs, {})
    assert recs and all(r["source"] == "real" for r in recs)
    assert all(json.loads(json.dumps(r)) == r for r in recs)
    assert recs[0]["id"] == "real-demo-r1-decide" and recs[0]["labels"] == {}
    assert recs[0]["truth"]["outcome"] in ("extinguished", "human", "ignored", "alarm_only")
    geo = load_geometry(with_nodes=False)
    assert all(rule_answers(r, geo) for r in recs)
    assert ev.evaluate(recs, geo)["n_records"] == len(recs)  # không nhãn: chấm được, không câu nào

    ann = {"demo:1": {"real_fire": True, "fire_out": True, "target": "T1"}}
    recs = exp.episodes_of("demo", runs, ann)
    labels = recs[0]["labels"]
    assert labels["real_fire"] is True and labels["target"].startswith("T1 (")
    assert labels["action"] == "spray"
    last = [r for r in recs if "verify" in r["id"]][-1]
    assert last["labels"] == {"after_verify": "done (fire out)"}
    res = ev.evaluate(recs, geo)
    assert res["asked"]["real_fire"] == 1 and res["asked"]["after_verify"] == 1
    assert res["accuracy"]["real_fire"] == 1.0

    ann = {"demo:1": {"real_fire": False}}
    labels = exp.episodes_of("demo", runs, ann)[0]["labels"]
    assert labels == {"real_fire": False, "action": "ignore", "target": "none of these"}
