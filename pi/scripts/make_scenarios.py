"""Sinh bộ dữ liệu kịch bản cho mô hình quyết định (cần cache từ cache_yolo_obs.py).

    uv run python scripts/make_scenarios.py
    uv run python scripts/make_scenarios.py --sizes 2000 200 200 200 --seed 1
    uv run python scripts/make_scenarios.py --sensor-model ../runs/decider/sensor_fit.yaml

Ghi runs/decider/scenarios/{train,val,test,test_shift}.jsonl. train/val/test dùng quan sát của
fire-n.pt và hồ sơ nhiễu thường; test_shift dùng mix26-neg-20261003.pt, cảm biến nhiễu hơn,
nhiều vật gây nhiễu hơn, khung rơi nhiều hơn (ảnh lấy từ split test).
"""

import argparse
import json
from collections import Counter

from nt532.config import REPO_ROOT
from nt532.decider.scenario import NORMAL, SHIFT, generate, load_pool, load_sensor_model

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
    args = p.parse_args()
    sm = load_sensor_model(args.sensor_model)

    jobs = [
        ("train", "train", args.obs, NORMAL, args.sizes[0]),
        ("val", "val", args.obs, NORMAL, args.sizes[1]),
        ("test", "test", args.obs, NORMAL, args.sizes[2]),
        ("test_shift", "test", args.obs_shift, SHIFT, args.sizes[3]),
    ]
    (OUT / "scenarios").mkdir(parents=True, exist_ok=True)
    for name, img_split, obs_path, prof, n in jobs:
        pool = load_pool(obs_path, img_split)
        records = generate(n, pool, prof, name, args.seed, sm)
        path = OUT / "scenarios" / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        kinds = Counter(r["truth"]["kind"] for r in records if r["id"].endswith("decide"))
        print(f"{name:11}{n:6} kịch bản, {len(records):6} bản ghi  {dict(kinds)}")
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
