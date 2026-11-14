# NT532 CP-ORIGIN22 — tabletop localization and pan–tilt geometry

**Date:** 2026-11-14 (Asia/Ho_Chi_Minh)

**Branch:** `feature/nt532-origin22-tabletop`

**Base:** local `origin/main` at `64ffa12`, containing known-good mapping `53c3f05` and audit `cfbe62e`
**Evidence level:** software verified offline for the focused implementation; physical accuracy and servo reach **NOT VERIFIED**.

## A. Implementation summary

| File | Change |
|---|---|
| `pi/src/nt532/vision/table_frame.py` | Convert points, camera extrinsics, and node `TagPose` from solver ID 2 to tabletop ID 22. Validate the tag and batch stability. |
| `pi/scripts/dynamic_map.py` | Add opt-in `--world-origin 2\|22` and `--target-anchor`; convert all displayed coordinates and debug labels at the output boundary. ID 22 failure returns `BLOCKED` without coordinate fields. |
| `pi/src/nt532/vision/system.py` | Select the bbox centre for tabletop auto mode when configured; keep the legacy bottom-centre default and fixed/wall behavior. |
| `pi/src/nt532/orchestrator/laser_geometry.py` | Offline model with separate pivot and emitter, HOME angles, signs/gains and angular limits. It never sends a device command. |
| `pi/simulations/origin22_tabletop.yaml`, `pi/scripts/origin22_demo.py` | Explicit synthetic camera and A/B mount profile; 16 camera candidates; reproducible CLI JSON, debug image and coverage grid. |
| `pi/tests/test_origin22.py`, `test_tabletop_anchor.py`, `test_laser_geometry.py` | New synthetic ground-truth regression checks. |

No optimizer, firmware, CoAP/Thread code, YOLO weights, camera calibration, or production `config/site.yaml` was changed. The live orchestrator continues calculating in its existing frame. The ID 22 frame is opt-in at the Dynamic Mapping CLI output boundary.

## B. Coordinate frames

`solver_anchor_id = 2` remains the optimizer anchor. `operational_origin_id = 22` is the optional displayed origin. The ID 22 centre defines `(0,0,0)` on the tabletop; its printed +X edge defines +X, +Y is `+Z × +X`, and +Z points upward. The observed tag orientation contributes **yaw only**. A small PnP tilt or height error cannot redefine the table plane.

```text
                       +Z (up)
                        │
  ID 2 solver frame     │       ID 22 tabletop frame
  origin at tag 2       │       origin at tag 22, near camera
  reference IDs 0–3 ────┴────── node A / ID 21 and node B / ID 22

  P_22 = R_22ᵀ (P_2 − t_22)       P_2 = R_22 P_22 + t_22
  R_camera,22 = R_camera,2 R_22
  t_camera,22 = R_camera,2 t_22 + t_camera,2
```

The output contains `anchor_id: 2`, `operational_origin_id`, `coordinate_frame`, `unit: m`, and `target_anchor`. `references`, `nodes`, `camera.position/R/t`, `pixel.xyz`, `fire_targets[].xyz`, and debug labels use the selected frame together. Solver diagnostics still describe the ID 2 fitting process and are named as diagnostics. `s2` is reported exactly `(0,0,0)` in ID 22 mode after the ID 22 pose passes validation; height noise is discarded by the tabletop definition.

## C. Geometry validation and failure behavior

The rigid transform preserves pairwise distances and has an inverse. Camera projection and pixel-ray/table intersection were checked after converting the camera and point. ID 22 may rotate 90° in the plane. The direct `TagPose` API rejects invalid ID, non-finite/degenerate rotation, estimated height above 2 cm, and normal tilted beyond 10°. For the CLI, each observed ID 22 corner is intersected with the already fitted table plane; the reconstructed square must have edges within 5 mm and diagonals within 7 mm of the known tag size, image edge at least 12 px, reprojection within 3 px, configured detection rate, and batch drift at most 2.5 cm / 5°. This avoids the wrong-height solution of a single ambiguous planar PnP. Synthetic tests include a moving camera with a stationary ID 22 and a deliberately tilted ID 22. These are software quality gates, **not measured accuracy guarantees**.

If ID 2 fitting fails, the CLI retains `FAIL`. If fitting passes but ID 22 is absent or untrusted, it returns `BLOCKED` with no valid-looking coordinates. There is no silent fallback to ID 2. ID 22 batch validation uses each frame's independently estimated camera pose, so moving the camera alone need not look like tag movement.

## D. YOLO tabletop localization

The legacy anchor is `(u, v + h/2)`. Tabletop `center` uses `(u, v)` before the intrinsics ray intersects `Z=0`; `--world-origin 22` chooses centre unless overridden. Fixed/wall behavior is unchanged. Confidence threshold and `fire-n.pt` are unchanged.

In the synthetic flat-target demo, a 30×24 px bbox centred on the true table point produced **1.267 cm** bottom-centre position error and **0.000 cm** centre error relative to the same synthetic ground truth. This demonstrates the geometry for that fixture only. An actual YOLO box centre may be displaced from the physical fire marker centre; that must be checked from real images. Tests also cover edge targets, multiple/no detections, unusual bbox dimensions, and points outside the mapped region.

## E. Camera and layout study

The separate profile tests heights **0.45, 0.60, 0.80, 1.00 m** against downward angles **35°, 45°, 55°, 65°** from horizontal at 1280×720. Analytic projection found all six tags fully in-frame in **11/16** candidates. The reproducible example uses **0.80 m, 55°**, with all six tags visible and a minimum projected tag edge of **70.4 px**. This is a candidate, not an installed camera pose. The baseline reference arrangement is held in the synthetic profile, and IDs 21/22 are placed nearer the camera only in that profile. Occlusion by pan–tilt housings is **NOT VERIFIED** because enclosure dimensions and final mounts are unknown.

Moving the Android/RTSP camera requires recomputing extrinsics from AprilTags. Intrinsics remain tied to the unchanged resolution, zoom and focus. The demo does not alter or overwrite `calibration/camera.yaml` or `config/site.yaml`.

## F. Pan–tilt and 90° laser geometry

Each `LaserMount` has its own tag-to-pivot offset, pivot-to-emitter offset, HOME yaw/elevation, servo signs and gains, and pan/tilt angular limits. The synthetic A/B entries are separate. None is a measured production value; 1500 µs HOME and the 500–2500 / 600–2500 µs ranges do **not** establish a 360° or any other angular travel.

For emitter `E` and unit direction `d`, the tabletop intersection is `E − E_z d/d_z` when `E_z > 0` and `d_z < 0`. At exactly vertical down, `d=(0,0,-1)` and the hit is `(E_x,E_y,0)`. A tag with a different X/Y cannot be hit vertically from that fixed emitter. `vertical_calibration` returns `UNREACHABLE` if the commanded angles exceed limits, the ray is not within 0.5° of vertical, or its table hit misses the tag centre by more than 1 mm. Targeting iterates from the **moving emitter** and rejects angle-limit or ray-miss violations; it does not clamp commands into a false PASS. Own-tag/cross-tag feasibility depends on final geometry. No actual servo or laser was actuated.

The synthetic 63-point tabletop grid has **47/63** reachable points for A and **43/63** for B under the profile's assumed angular limits. These figures are design-study results, not hardware coverage.

## G. Tests and evidence

Environment: Windows, Python 3.13.9, offline `uv` environment with OpenCV 5.0.0, NumPy 2.5.3, pytest 9.1.1. A workspace-local `--basetemp=.pytest-tmp` was required because the default Windows pytest temp directory was denied.

| Run | Result |
|---|---|
| Before edits: `uv run --offline --group dev pytest tests/test_dynamic_mapping.py tests/test_aiming_geometry_audit.py -q --basetemp=.pytest-tmp` | **44 passed, 1 skipped** |
| Final mapping, tabletop, laser, vision, system and orchestrator regression | **107 passed, 1 skipped** in 160.79 s |
| `test_origin22.py` after adding explicit ID 2 parity check | **13 passed** in 1.04 s |
| Focused Ruff check on all changed Python files | **PASS**, no findings |
| `uv run --offline --group dev python scripts/origin22_demo.py --out ../reports/origin22_demo` | **PASS**; generated ID 2/22 JSON, annotated image, grid CSV, summary |
| Full `pytest -q` | Collection **ERROR**: optional `torch` absent (`test_decider.py`, `test_jev.py`) |
| Broad run excluding those two modules | **345 passed, 2 skipped, 18 failed, 19 errors** in 358.90 s. All listed failures/errors are in tests requiring absent `make` or absent `data/sim/commissioning.yaml`; the focused changed-path tests passed. |

The broad run does not establish whole-repository PASS. The missing generated simulation file and C build tool were not invented or changed to force a green result. No Raspberry Pi run occurred.

## H. Backward compatibility

The default CLI remains ID 2, with bottom-centre anchoring. `--world-origin 2` selects it explicitly. The added metadata names the frame and anchor choice. Existing mapping, fixed/wall, and orchestrator tests passed in the focused run. The production `reference_mode: fixed` default is unchanged. The new laser module is offline-only and is not connected to the firmware or orchestrator.

## I. Raspberry Pi verification for the operator

The branch is local and was **not pushed**. A local Git bundle is at `build-output/nt532-origin22-tabletop.bundle` in the Windows workspace and requires base `64ffa12`. Copy that bundle to the Pi as `~/nt532-origin22-tabletop.bundle`, then prepare the isolated clone:

```bash
git clone https://github.com/knight3007/Project-NT532.R12.git ~/nt532-dynamic-test
git -C ~/nt532-dynamic-test fetch ~/nt532-origin22-tabletop.bundle \
  refs/heads/feature/nt532-origin22-tabletop:refs/heads/feature/nt532-origin22-tabletop
git -C ~/nt532-dynamic-test switch feature/nt532-origin22-tabletop
```

Use new filenames so previous PASS artifacts remain intact. The following commands only read the camera and write diagnostic files; they do not command servos, laser or pump.

```bash
cd ~/nt532-dynamic-test/pi
uv sync --extra yolo
uv run python scripts/dynamic_map.py \
  --source rtsp://127.0.0.1:8554/cam/stream --frames 15 \
  --intrinsics ~/nt532/calibration/camera.yaml \
  --yolo --weights ~/nt532/models/fire-n.pt \
  --world-origin 2 --target-anchor bottom_center \
  --json ../reports/pi-origin2-tabletop-new.json \
  --debug ../reports/pi-origin2-tabletop-new.jpg
uv run python scripts/dynamic_map.py \
  --source rtsp://127.0.0.1:8554/cam/stream --frames 15 \
  --intrinsics ~/nt532/calibration/camera.yaml \
  --yolo --weights ~/nt532/models/fire-n.pt \
  --world-origin 22 --target-anchor center \
  --json ../reports/pi-origin22-tabletop-new.json \
  --debug ../reports/pi-origin22-tabletop-new.jpg
```

Verify `anchor_id=2`, `operational_origin_id=22`, `nodes.s2=[0,0,0]`, and `nodes.s1` relative to ID 22. Mark printed +X/+Y directions on the table and compare several ruler-measured X/Y offsets and pairwise distances against the JSON in **metres**. Place the flat marker at the table centre and near edges; compare manual pixel and YOLO target positions, ensure target `Z=0`, and record centre versus bottom-centre error from actual boxes. Repeat with ID 22 deliberately hidden while reference tags remain visible: the ID 22 output must say `BLOCKED` with no coordinate fields. Re-measure camera extrinsics after any camera movement. Keep focus, zoom and 1280×720 unchanged when reusing intrinsics. If the real tag pose is blocked, inspect tag size, flatness, print orientation, image reprojection, detection rate and batch drift; do not lower quality gates solely to obtain PASS.

## J. Outstanding risks / measurements

- **NOT VERIFIED:** installed camera height/angle, final ID 21/22 coordinates and rotations, real tag pixel sizes, enclosure occlusion, and Android RTSP latency or image stability.
- **NOT VERIFIED:** A/B tag-to-pivot and emitter offsets, HOME optical directions, mechanical angular limits, servo sign/gain/backlash, and actual PWM-to-angle curves. The nominal 10 cm is a study input only. Measure each axis independently before any physical laser calibration.
- **NOT VERIFIED:** actual `fire-n.pt` centre-anchor bias, real target localization error, and real A/B laser reach. The offline detector in the demo is a known synthetic bbox, not YOLO inference.
- **NOT VERIFIED:** full repository regression until `torch`, `make`, and the expected simulation fixture are available in an appropriate environment.

### Generated offline examples

`reports/origin22_demo/mapping_id2.json`, `mapping_id22.json`, `debug_id22.png`, `camera_layout_grid.csv`, `summary.json`, `scene_synthetic.png`, and `camera_synthetic.yaml` are reproducible outputs of the demo. Example ID 22 values:

```json
{
  "anchor_id": 2,
  "operational_origin_id": 22,
  "coordinate_frame": "tag_22_table",
  "nodes": {"s2": [0.0, 0.0, 0.0], "s1": [-0.00043, 0.27047, 0.00011]},
  "fire_targets": [{"xyz": [0.12027, 0.15046, 0.0], "surface": "floor"}]
}
```

Those coordinates and all study metrics are synthetic and must not be interpreted as measured hardware accuracy.
