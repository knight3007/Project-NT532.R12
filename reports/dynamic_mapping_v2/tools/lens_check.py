"""Image-position, distortion magnitude and free-focal self-consistency check.

Usage (from pi/): uv run --no-sync python ../build-output/origin22-geometry-v2/lens_check.py FRAMES INTR
"""

import sys
from pathlib import Path

import cv2
import numpy as np

from nt532.vision.calibration import load_intrinsics
from nt532.vision.dynamic_mapping import estimate_reference_map
from nt532.vision.tags import _square, detect_tags
from nt532.vision.types import Intrinsics

frames, intr_path = Path(sys.argv[1]), Path(sys.argv[2])
intr = load_intrinsics(intr_path)
paths = sorted(p for p in frames.iterdir() if p.suffix.lower() in (".png", ".jpg"))
img = cv2.imread(str(paths[-1]))
obs = detect_tags(img)
K, dist = intr.K, intr.dist
cx, cy = K[0, 2], K[1, 2]
rmax = np.hypot(max(cx, 1280 - cx), max(cy, 720 - cy))
print("tag  center_px        r/rmax  distortion_shift_px(max corner)")
for t in sorted(obs):
    q = obs[t]
    und = cv2.undistortPoints(q.reshape(-1, 1, 2), K, dist, P=K).reshape(-1, 2)
    c = q.mean(axis=0)
    print(f"{t:3d} ({c[0]:7.1f},{c[1]:6.1f})  {np.hypot(c[0]-cx, c[1]-cy)/rmax:5.3f}  "
          f"{np.linalg.norm(und - q, axis=1).max():6.2f}")

# Refit reference map with focal scaled; report joint reprojection and ID22/21 table sides.
from nt532.vision.dynamic_mapping import DynamicMap  # noqa: E402

def sides(t, cam):
    table = DynamicMap(cam, {}, 0, 0, 0)
    p = np.stack([table.table_point(tuple(x)) for x in obs[t]])
    e = np.linalg.norm(np.roll(p, -1, 0) - p, axis=1) * 1000
    d = np.linalg.norm(p[:2] - p[2:], axis=1) * 1000
    return e.round(2).tolist(), d.round(2).tolist()

print("\nfocal_scale  joint_rms_px  max_ref_corner_px  ID22 sides/diag   ID21 sides/diag")
for s in (0.96, 0.98, 1.0, 1.02, 1.04):
    K2 = K.copy()
    K2[0, 0] *= s
    K2[1, 1] *= s
    i2 = Intrinsics(K2, dist, intr.size)
    try:
        m = estimate_reference_map(obs, i2, 0.08)
    except ValueError as exc:
        print(s, exc)
        continue
    mx = max(q["max_corner_px"] for q in m.tag_quality.values())
    print(f"{s:5.2f}  {m.reprojection_px:6.3f}  {mx:6.3f}  {sides(22, m.camera)}  {sides(21, m.camera)}")

print("\nk3 scale (distortion), focal 1.0")
for s in (0.0, 0.5, 1.0, 1.5):
    d2 = dist.copy()
    d2[4] *= s
    i2 = Intrinsics(K, d2, intr.size)
    try:
        m = estimate_reference_map(obs, i2, 0.08)
    except ValueError as exc:
        print(s, exc)
        continue
    mx = max(q["max_corner_px"] for q in m.tag_quality.values())
    print(f"{s:4.1f}  {m.reprojection_px:6.3f}  {mx:6.3f}  {sides(22, m.camera)}  {sides(21, m.camera)}")
