"""Sinh bộ dữ liệu kịch bản cho mô hình quyết định (cần cache từ cache_yolo_obs.py).

    uv run python scripts/make_scenarios.py
    uv run python scripts/make_scenarios.py --sizes 2000 200 200 200 --seed 1
    uv run python scripts/make_scenarios.py --sensor-model ../runs/decider/sensor_fit.yaml
    uv run python scripts/make_scenarios.py --out ../runs/decider/scenarios_3d --tilt-min -70
    uv run python scripts/make_scenarios.py --out ../runs/decider/scenarios_3d_corners \
        --nodes "s1=0,0,0.33@0.6,0.8;s2=1.2,0,0.33@-0.6,0.8"

Ghi runs/decider/scenarios/{train,val,test,test_shift}.jsonl (đổi bằng --out). Bia nằm trên bảng
hoặc trên nền (p_floor: 0,5 ở train/val/test, 0,6 ở test_shift); tầm với tính 3D từ từng node
với pan +-70 (đo từ hướng nhìn của node) và tilt từ --tilt-min (mặc định -35) tới 45. train/val/test dùng quan sát của
fire-n.pt và hồ sơ nhiễu thường; test_shift dùng mix26-neg-20261003.pt, cảm biến nhiễu hơn,
nhiều vật gây nhiễu hơn, khung rơi nhiều hơn (ảnh lấy từ split test).
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from nt532.config import REPO_ROOT
from nt532.decider.scenario import (
    NORMAL,
    SHIFT,
    TILT_LIM,
    generate,
    load_geometry,
    load_pool,
    load_sensor_model,
)

OUT = REPO_ROOT / "runs/decider"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sizes", type=int, nargs=4, default=[20000, 2000, 2000, 2000],
                   metavar=("TRAIN", "VAL", "TEST", "SHIFT"))
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--obs", default=str(OUT / "yolo_obs_fire-n_cap.jsonl"))
    p.add_argument("--obs-shift", default=str(OUT / "yolo_obs_mix26-neg_test.jsonl"))
    p.add_argument("--sensor-model", default=None, metavar="YAML",
                   help="ghi đè SensorModel mặc định (placeholder) bằng file từ fit_sensor_model.py, "
                        "vd ../runs/decider/sensor_fit.yaml")
    p.add_argument("--out", default=str(OUT / "scenarios"), metavar="DIR",
                   help="thư mục ghi 4 file jsonl (mặc định runs/decider/scenarios)")
    p.add_argument("--tilt-min", type=float, default=TILT_LIM[0],
                   help="tilt thấp nhất của vòi, độ (giả định -35; cơ khí đang cân nhắc -70)")
    p.add_argument("--tilt-max", type=float, default=TILT_LIM[1])
    p.add_argument("--nodes", default=None, metavar="SPEC",
                   help='đặt lại trục quay và hướng pan = 0: "s1=x,y,z@fx,fy;s2=..." (@fx,fy bỏ = +Y)')
    args = p.parse_args()
    sm = load_sensor_model(args.sensor_model)
    geo = load_geometry(tilt_lim=(args.tilt_min, args.tilt_max), nodes=args.nodes)
    print("node:", {n: (tuple(round(v, 2) for v in xyz), geo.facing.get(n, (0, 1)))
                    for n, xyz in geo.nodes.items()}, f"tilt {geo.tilt_lim}")
    out = Path(args.out)

    jobs = [
        ("train", "train", args.obs, NORMAL, args.sizes[0]),
        ("val", "val", args.obs, NORMAL, args.sizes[1]),
        ("test", "test", args.obs, NORMAL, args.sizes[2]),
        ("test_shift", "test", args.obs_shift, SHIFT, args.sizes[3]),
    ]
    out.mkdir(parents=True, exist_ok=True)
    for name, img_split, obs_path, prof, n in jobs:
        pool = load_pool(obs_path, img_split)
        records = generate(n, pool, prof, name, args.seed, sm, geo)
        path = out / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        kinds = Counter(r["truth"]["kind"] for r in records if r["id"].endswith("decide"))
        print(f"{name:11}{n:6} kịch bản, {len(records):6} bản ghi  {dict(kinds)}")
        fires = [r["truth"] for r in records if r["id"].endswith("decide") and r["truth"]["real_fire"]]
        seen = [t for t in fires if t["true_target"]]  # lửa thật có bia nhìn thấy
        if fires:
            surf = Counter(t["surface"] for t in seen)
            far = Counter(t["surface"] for t in seen if not t["reach_any"])
            print(f"    lửa thật nhìn thấy: {dict(surf)}  ngoài tầm với của cả hai vòi: {dict(far)}")
            d = np.array([t["fire_dist_min"] for t in fires])
            print(f"    khoảng cách 3D lửa tới sensor gần nhất: TB {d.mean():.2f} m, "
                  f"p90 {np.percentile(d, 90):.2f}, max {d.max():.2f}")
        for q in ("real_fire", "action", "target", "nozzle", "after_verify"):
            c = Counter(
                ("a visible target" if q == "target" and not r["labels"][q].startswith("none")
                 else str(r["labels"][q]))
                for r in records if q in r["labels"]
            )
            if c:
                print(f"    {q:13}{dict(c)}")


if __name__ == "__main__":
    main()
