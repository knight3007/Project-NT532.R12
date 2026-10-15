"""Bia trên bảng và trên nền (3D) trong bộ sinh kịch bản, STATE, parser và luật."""

import dataclasses

from test_scenario import decide_records, make

from nt532.decider.rules import rule_answers
from nt532.decider.scenario import (
    NORMAL,
    SHIFT,
    load_geometry,
    parse_nodes,
    reachable,
)
from nt532.decider.state_text import (
    NONE_OF_THESE,
    nozzle_candidates,
    questions_from_state,
    render_state,
    target_candidates,
)


def test_floor_and_wall_sampling():
    surfaces = [t["surface"] for r in decide_records(600) for t in r["obs"]["targets"]]
    share = surfaces.count("floor") / len(surfaces)
    assert 0.4 < share < 0.6
    geo = load_geometry(with_nodes=False)
    for r in decide_records(300):
        for t in r["obs"]["targets"]:
            if t["surface"] == "floor":
                assert t["z"] == 0.0 and 0.0 < t["y"] < geo.floor_depth
            else:
                assert t["y"] == geo.plane_y and 0.0 < t["z"] < geo.height
    shift = make(400, 0, dataclasses.replace(SHIFT, p_floor=1.0))
    assert all(t["surface"] == "floor" for r in shift if r["id"].endswith("decide")
               for t in r["obs"]["targets"])
    wall = make(200, 0, dataclasses.replace(NORMAL, p_floor=0.0))
    assert all(t["surface"] == "wall" for r in wall if r["id"].endswith("decide")
               for t in r["obs"]["targets"])


def test_truth_surface_and_dist3_use_y():
    geo = load_geometry()
    for r in decide_records(200):
        for t in r["obs"]["targets"]:
            for n in ("s1", "s2"):
                assert abs(t["dist3"][n] - _d3(geo, n, t)) < 0.011
                assert t["dist"][n] == round(abs(t["x"] - geo.nodes[n][0]), 2)
        tr = r["truth"]
        assert (tr["surface"] is not None) == bool(tr["true_target"])


def _d3(geo, n, t):
    import math
    return math.dist(geo.nodes[n], (t["x"], t["y"], t["z"]))


def test_state_lines_state_surface():
    seen = set()
    for r in decide_records(100):
        for t in r["obs"]["targets"]:
            if t["surface"] == "floor":
                line = f"{t['id']}: floor x {t['x']:.2f} y {t['y']:.2f} box"
            else:
                line = f"{t['id']}: wall x {t['x']:.2f} z {t['z']:.2f} box"
            assert line in r["state"]
            seen.add(t["surface"])
    assert seen == {"floor", "wall"}
    cands = target_candidates([
        {"id": "T1", "surface": "floor", "x": 0.4, "y": 0.3, "z": 0.0},
        {"id": "T2", "surface": "wall", "x": 0.88, "y": 0.8, "z": 0.2},
    ])
    assert cands == ["T1 (floor x 0.40 y 0.30)", "T2 (wall x 0.88 z 0.20)", NONE_OF_THESE]


def test_parser_roundtrip_floor_and_wall():
    for r in decide_records(300):
        got = questions_from_state(r["state"])
        for q, spec in r["questions"].items():
            assert got[q] == spec, (r["id"], q)


def test_missing_surface_is_wall():
    t = {"id": "T1", "x": 0.5, "z": 0.2}  # log cũ, không y/surface
    assert target_candidates([t])[0] == "T1 (wall x 0.50 z 0.20)"
    old = "stage: decide\nalarm: s1\ndetections (3 frames):\nT1: x 0.50 z 0.20 box 0.1x0.1 conf 0.9"
    assert questions_from_state(old)["target"]["candidates"][0] == "T1 (wall x 0.50 z 0.20)"
    assert questions_from_state(old.replace("x 0.50 z 0.20", "floor x 0.50 y 0.20"))[
        "target"]["candidates"][0] == "T1 (floor x 0.50 y 0.20)"


def _obs(targets, alarm="s1"):
    noz = {n: {"hb_ms": 200, "pose_s": 5, "reach": {t["id"]: True for t in targets}}
           for n in ("s1", "s2")}
    return {"stage": "decide", "alarm": alarm, "limits": {"temp": 45.0, "gas": 600.0}, "frames": 3,
            "sensors": {n: {"temp": [30.0] * 5, "gas": [200] * 5, "hum": [50] * 5} for n in noz},
            "targets": targets, "nozzles": noz}


def test_rules_on_floor_case():
    geo = load_geometry(with_nodes=False)
    floor = {"id": "T1", "x": 0.35, "y": 0.30, "z": 0.0, "surface": "floor", "w": 0.2, "h": 0.2,
             "conf": [0.9, 0.9, 0.9], "dist": {"s1": 0.05, "s2": 0.55},
             "dist3": {"s1": 0.45, "s2": 0.80}}
    far = dict(floor, id="T2", x=1.1, dist={"s1": 0.8, "s2": 0.2}, dist3={"s1": 1.0, "s2": 0.5})
    obs = _obs([floor, far])
    q = {"target": {}, "nozzle": {"candidates": nozzle_candidates(obs)}}
    ans = rule_answers({"obs": obs, "questions": q}, geo)
    assert ans["real_fire"] and ans["action"] == "spray"
    assert ans["target"].startswith("T1 (floor x 0.35 y 0.30)")
    assert ans["nozzle"] == "s1"
    obs["nozzles"]["s1"]["reach"]["T1"] = False  # chỉ s2 với tới -> vẫn phun, bằng s2
    assert rule_answers({"obs": obs, "questions": q}, geo)["nozzle"] == "s2"
    obs["nozzles"]["s2"]["reach"]["T1"] = False  # không vòi nào với tới -> alarm only
    assert rule_answers({"obs": obs, "questions": q}, geo)["action"] == "alarm only"
    render_state(obs)  # không lỗi khi bia nền thiếu khóa phụ


def test_rules_without_y_surface_run():
    geo = load_geometry(with_nodes=False)
    t = {"id": "T1", "x": 0.35, "z": 0.2, "w": 0.2, "h": 0.2, "conf": [0.9] * 3,
         "dist": {"s1": 0.05, "s2": 0.55}}
    obs = _obs([t])
    ans = rule_answers({"obs": obs, "questions": {"target": {}}}, geo)
    assert ans["target"] == "T1 (wall x 0.35 z 0.20)"


def test_reach_tilt_knob_and_facing():
    # hai trụ sát bảng (bố cục posts), đặt tường minh để không phụ thuộc data/sim/commissioning.yaml
    base = load_geometry(nodes="s1=0.30,0.55,0.33;s2=0.90,0.55,0.33")
    floor_pt = (0.30, 0.70, 0.0)  # nền sát chân bảng, ngay trước s1: tilt khoảng -66 độ
    assert not reachable(base, "s1", floor_pt[0], floor_pt[2], floor_pt[1])
    deep = load_geometry(tilt_lim=(-80, 45), nodes="s1=0.30,0.55,0.33;s2=0.90,0.55,0.33")
    assert reachable(deep, "s1", floor_pt[0], floor_pt[2], floor_pt[1])
    # hai góc phía trước, quay vào giữa: nền và bảng đều với tới
    corners = load_geometry(nodes="s1=0,0,0.33@0.6,0.8;s2=1.2,0,0.33@-0.6,0.8")
    assert reachable(corners, "s1", 0.6, 0.0, 0.4)
    assert reachable(corners, "s1", 0.6, 0.0, 0.4) and reachable(corners, "s2", 0.6, 0.0, 0.4)
    assert reachable(corners, "s1", 1.1, 0.5, 0.78)  # bảng xa
    # mặc định facing +Y: điểm sau lưng (y < pivot) vượt pan
    assert not reachable(base, "s1", 0.3, 0.0, 0.0)
    assert parse_nodes("s1=0,0,0.33@0.6,0.8;s2=1,0,0.3") == (
        {"s1": (0.0, 0.0, 0.33), "s2": (1.0, 0.0, 0.3)}, {"s1": (0.6, 0.8)})


def test_facing_matches_aiming_yaw_convention():
    import math

    from nt532.orchestrator import aiming

    corners = load_geometry(nodes="s1=0,0,0.33@0.6,0.8;s2=1.2,0,0.33@-0.6,0.8")
    for n, h in (("s1", (0.6, 0.8)), ("s2", (-0.6, 0.8))):
        yaw = math.degrees(math.atan2(-h[0], h[1]))
        pivot = corners.nodes[n]
        for x, y, z in ((0.6, 0.4, 0.0), (1.1, 0.8, 0.5), (0.1, 0.1, 0.0), (0.6, -0.2, 0.2)):
            pan, tilt = aiming.angles(pivot, (x, y, z))
            pan = (pan - yaw + 180) % 360 - 180
            want = abs(pan) <= 70 and -35 <= tilt <= 45
            assert reachable(corners, n, x, z, y) == want, (n, x, y, z)
