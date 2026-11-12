# NT532 — A/B Pan–Tilt Geometry Audit & 2D vs 3D Aiming Simulation

**Evaluation base:** `53c3f05e7b184125ba0680e70e3dd3f2392f134c` on `feature/robust-multitag-mapping`; isolated branch `evaluation/aiming-2d-vs-3d`. **Scope:** offline code audit, tests and synthetic geometry. No Raspberry Pi, ESP32, actuator, laser or pump was contacted. The accepted dynamic mapping, YOLO model, calibration and runtime code were not changed.

## A. Executive Summary

| Question | Verdict | Evidence |
|---|---|---|
| Current 2D floor projection aims a floor point | **PASS in simulator, conditional** | 16/24 nominal node-cases at Z=0 hit a 3 cm assumed target; 8/24 violate assumed servo limits. |
| Current 2D floor projection aims elevated points | **FAIL in simulator** | 0 hits at every tested Z=5–30 cm. At Z=5 cm, 8 commanded cases miss by median 9.45 cm; 8 more are rejected by the floor-map region. |
| Image feedback alone proves a 3D hit | **FAIL as a general claim** | 30 nominal B cases align background spot and target to ≤2 px yet are labeled `UNKNOWN`; their median shortest ray miss is 19.92 cm. Target-surface observations are essential. |
| Exact XYZ and calibrated mechanics can aim both nodes | **PASS only as an ideal benchmark** | Oracle C hits all 128/128 reachable nominal node-cases. The exact-zero nominal error is built into the ideal model, not measured hardware accuracy. |
| Real multi-height accuracy, HOME axes and actual depth | **NOT VERIFIED** | No physical 3D ground truth, servo angle measurements, actual calibration file, or target-surface laser observations were supplied. |

**Executive verdict:** A single calibrated RGB camera plus AprilTags gives a world ray and a reliable *chosen plane* intersection. It does not identify an arbitrary point's depth along that ray. The present floor-only auto mode cannot reliably aim at freely elevated targets. A target-visible laser spot could support local image feedback once acquired, but a spot on the table or rear surface cannot certify 3D alignment. Geometry and servo calibration are prerequisites even for an ideal depth estimate.

## B. Current Implementation Audit

| Path | Observed behavior and consequence |
|---|---|
| [`detect.py`](../pi/src/nt532/vision/detect.py#L21), [`types.py`](../pi/src/nt532/vision/types.py#L7) | YOLO returns image `xywh` and confidence for fire; no metric depth. The accepted detector was not run here. |
| [`dynamic_mapping.py`](../pi/src/nt532/vision/dynamic_mapping.py#L16) | `DynamicMap.table_point()` intersects a pixel ray with the ID 2 table plane Z=0; it rejects near-horizontal/downward-invalid rays. This is table localization, not free-space XYZ. |
| [`system.py`](../pi/src/nt532/vision/system.py#L246) | `Vision.localize_surface()` in `auto` mode returns only `(localize_table(pixel), "floor")`. Fixed mode supports the configured floor and wall and chooses the first surface hit; neither infers a hovering target's depth. |
| [`system.py`](../pi/src/nt532/vision/system.py#L268) | `localize_detection()` tests the bottom-center of the box first and uses it if it intersects the floor; otherwise it uses the box center. A floating flame's box bottom is not a known floor contact point. The point-target baseline used box height 0 to isolate parallax; a separate 12 px box scenario tests anchor bias. |
| [`system.py`](../pi/src/nt532/vision/system.py#L286) | `find_spot()` finds a red image spot and calls `localize(pixel)`. In auto mode every returned spot is placed at Z=0, even if the actual laser reflection were on an elevated object. It does not identify the reflecting surface. |
| [`types.py`](../pi/src/nt532/vision/types.py#L40) | `TagPose.to_world(offset)` is correctly `tag.position + tag.rotation @ offset` for a **specified vector in tag coordinates**. The requested 0.10 m scalar is insufficient to choose that vector. A projected vertical-tag corner test recovered the pose and pivot with the correct vector. |
| [`aiming.py`](../pi/src/nt532/orchestrator/aiming.py#L38), [`site.yaml`](../config/site.yaml#L102) | `pivot_offset()` reads the site value `[0,0,0.08]` m, otherwise the same 8 cm default. This disagrees with the proposed 10 cm mounting distance; neither value is a verified 3D vector for both tag orientations. |
| [`aiming.py`](../pi/src/nt532/orchestrator/aiming.py#L100) | `heading_yaw_deg()` projects tag +Y onto world XY without checking its norm. A vertical tag whose top edge points up has zero XY projection, so the computed yaw is undefined yet the function can return 0°. `node_yaw()` uses explicit `yaw_deg` if present; both values are `null` in `site.yaml`. |
| [`aiming.py`](../pi/src/nt532/orchestrator/aiming.py#L120) | `angles()` treats yaw 0 as world +Y; positive pan turns toward world −X, positive tilt means elevation above horizontal. Tilt is an absolute elevation, with no measured TILT HOME elevation offset. `reachable_point()` only checks configured command-angle limits (defaults pan ±70°, tilt −35°…+45°). It does not validate physical servo travel, a target surface, or visibility. |
| [`machine.py`](../pi/src/nt532/orchestrator/machine.py#L351) | Runtime follows detection → `Vision.targets()` → pivot from tag pose/`pivot_offset()` → `angles()` → `_aim()`. `_aim()` checks angle limits/heartbeat/status but those checks cannot correct a wrong pivot or HOME. [`_plane_mask()`](../pi/src/nt532/orchestrator/machine.py#L603) keeps Z fixed for floor corrections and Y fixed for wall corrections; the CORRECT loop cannot recover an unknown elevated Z. [`_blocked()`](../pi/src/nt532/orchestrator/machine.py#L616) rejects missing node pose and out-of-area targets. |
| [`sim/scene.py`](../pi/src/nt532/sim/scene.py#L130), [`aim_point.py`](../pi/scripts/aim_point.py#L1) | Existing simulator provides `Node.direction`, bias, floor/wall ray hits and horizontal tag rendering. Its ordinary `Node.pose`/`tag_quad` do not model a vertically mounted tag. The new offline harness reuses the ray convention and camera functions and tests a separate vertical `TagPose`; it never invokes the hardware-capable `aim_point.py` CLI. |
| [`hal_esp.c`](../firmware/sensor-h2/main/hal_esp.c#L48), [`node_cfg.c`](../firmware/sensor-h2/main/node_cfg.c#L58) | Read-only inspection: active HAL nominally maps command −90°…+90° linearly across configured pulse min/max, clamps angles, and supports per-node offset/inversion from Kconfig/NVS. This proves a command conversion exists, **not** that 1500 µs is physically level/forward or that actual travel matches the nominal map. The separate `servo_map.c` tests do not establish actual mounted servo calibration. |

## C. Mechanical Coordinate Model

World axes: X right, Y toward the board, Z up. Tag local axes: +X printed right, +Y toward printed top, +Z outward normal. For each node, **pivot = tag center + R(world←tag) × measured tag-to-pivot vector**. PAN HOME has an independent world heading ψ₀; TILT HOME has an independent elevation φ₀. They cannot be derived from a 10 cm scalar or PWM 1500 µs.

```text
Horizontal tag: local +Y lies on world XY;  offset [0, 0, +0.10] → pivot +Z.
Vertical tag:   local +Y points world +Z;  offset [0, +0.10, 0] → pivot +Z.
                local +Z points toward camera (−Y); [0, 0, +0.10] moves the
                estimated pivot toward camera and leaves it 10 cm too low.

             +Z world / vertical tag +Y
                    ↑ pivot P
                    │ 0.10 m, measured vector required
             tag T  ●──→ +Y world / board
                   ╱
             +X world
```

For target **Q**, the production geometric convention is `pan = atan2(−(Qx−Px), Qy−Py) − ψ₀` (wrapped to ±180°), `tilt = atan2(Qz−Pz, hypot(Qx−Px,Qy−Py))`. A physical servo requires an additional verified map from command angle to PWM and from PWM to shaft angle, including sign, gain, offset and φ₀. The harness labels these as assumptions. The simulation nodes have symmetric actual HOME headings −35° (s1) and +35° (s2), while their tag top-edge yaw is −10°/+10°: treating tag yaw as HOME creates a 25° error even for horizontal tags. The vertical tag top-edge yaw is undefined in XY.

## D. Existing Tests

Initial HEAD/branch/status: expected commit, `feature/robust-multitag-mapping`, clean working tree. The separate evaluation worktree began at exactly that commit.

| Run | Result | Classification |
|---|---:|---|
| Seven requested existing files, full baseline | **90 passed, 1 skipped, 7 failed** | Six `test_scenario_3d.py` failures require absent `data/sim/commissioning.yaml`; one `test_aim_point.py[coap]` fails because `CoreLib.build()` invokes absent Windows `make`. These are environment/data failures, retained in [`baseline_tests.txt`](aiming_evaluation/baseline_tests.txt). The skip is the pre-existing optional external 24-image dataset test. |
| New isolated harness tests | **18 passed** | Offset and PnP for both mounts, vertical yaw degeneracy, symmetric HOME, elevation/sign, same-pixel/different-depth ambiguity, A/B/C/D, limits, lost spot, missing pose/reference, wrong offset and servo gain. See [`harness_tests.txt`](aiming_evaluation/harness_tests.txt). |
| Existing regression files plus new tests, with the unavailable CoAP case deselected | **105 passed, 1 skipped, 1 deselected** | Includes dynamic mapping, yaw, 3D targets, station sim3D, aim point memory-link, sim floor and new audit tests. `test_scenario_3d.py` remains represented by the full baseline failure, not silently removed from the verdict. See [`regression_tests.txt`](aiming_evaluation/regression_tests.txt). |
| Ruff on new script/test | **PASS** | No lint errors. |

The first attempt using system Python lacked `cv2`; project `uv` environment was used. An initial pytest temp-directory permission error was resolved by setting `--basetemp` inside the writable worktree. Neither was a behavioral failure. No failed test was edited, skipped or deleted to make the report green.

## E. Simulation Methodology

The offline harness is [`aiming_geometry_audit.py`](../pi/scripts/aiming_geometry_audit.py). It calls project `look_at`, `default_intrinsics`, `project`, `pixel_ray`, `Vision.localize_detection()`/auto map, `TagPose.to_world()`, `aiming.angles()`/limits and `sim.scene.Node.direction`. The evaluator owns true XYZ. A and B receive only the rendered target pixel/bounding box; B's controller receives a target pixel and a sensor callback returning a noisy observed spot pixel and target/background label. C receives true XYZ. D receives a deliberately perturbed **true camera range** to model a hypothetical metric-depth prediction. No depth model was loaded.

**ASSUMPTIONS, not measured NT532 parameters:** 1280×720 synthetic intrinsics (`f=900 px`, mild synthetic distortion); fixed camera `(0.60, −0.75, 0.45)` m looking at `(0.60, 0.45, 0)` m (about 20.6° down); fixed four synthetic reference centers; exact nominal camera/tag pose; s1/s2 tags at `(0.24/0.96, 0.10, 0.20)` m, pivots 10 cm above; 3 cm target radius; camera-facing circular target patch; nominal ±70° PAN, −35°…+45° TILT; HOME command PWM 1500 µs, nominal 2000/180 µs per degree, assumed PAN range 500–2500 µs and TILT range 600–2500 µs; perfect command-to-angle nominal mechanics. Pixel and spot measurement Gaussian σ=0.6 px each, spot loss 5%, one-step observation delay 10%, small reversal backlash 0.3°. A uses a point target with zero box height to isolate parallax; one extra scenario uses a 12 px box. The script uses fixed seeds.

Each mount has 2 nodes × 6 Z heights (0, 5, 10, 15, 20, 30 cm) × 6 XY positions (near A, near B, overlap, far/near camera, edge). Camera and reference tags stay fixed as Z changes. Ten alternative mechanical/camera/box scenarios plus nominal and special missing-ID/unsafe-observation cases give **4,547 A/B/C rows**. D adds **1,296** rows for signed camera-range errors 0, ±1, ±2, ±5 and ±10 cm. Full per-case data and grouped median/P95/worst are in [`metrics.csv`](aiming_evaluation/metrics.csv), [`summary.csv`](aiming_evaluation/summary.csv), [`depth_sensitivity.csv`](aiming_evaluation/depth_sensitivity.csv), and [`depth_sensitivity_summary.csv`](aiming_evaluation/depth_sensitivity_summary.csv).

`aim_position_error_cm` compares the ray point at the true target's pivot range with the true target. `ray_miss_cm` is the shortest distance from the **forward** ray to the target point. The 3 cm `hit` is the evaluator's assumed spherical-radius criterion. `PASS` for B additionally requires an observed target-surface spot; background alignment is `UNKNOWN` even when pixel error is small. `BLOCKED`/`OUT_OF_VIEW`/`OUT_OF_RANGE` do not enter error quantiles; their counts remain in the denominator of coverage. Real YOLO box shape, fire reflectance, occlusion, latency distribution, recoil/water trajectory, camera/AprilTag estimation noise and device physics are not represented. The target patch surface label is an optimistic observation assumption; a real classifier for it has not been demonstrated.

## F. 2D vs 3D Results

**Nominal geometry (per strategy 144 node-cases; med/P95/worst over cases that produced a ray):**

| Strategy | Commanded/reachable | Geometric hits | Verified PASS | Miss med / P95 / worst (cm) | Interpretation |
|---|---:|---:|---:|---:|---|
| A — current floor projection | 48/144 | 16/144 | 16/144 simulated hits | 10.16 / 31.42 / 31.42 | Z=0 works where servo reaches; raised targets miss or are rejected by the floor map. A itself cannot certify true XYZ. |
| B — image feedback | 48/144 | 16/144 | 16/144 target-surface observations | 10.16 / 31.21 / 31.27 | Only the floor cases acquired a target-surface spot. At height, background alignment stayed `UNKNOWN` or failed to converge. |
| C — exact-XYZ oracle | 128/144 | 128/144 | 128/144 ideal hits | 0 / 0 / 0 | Ideal reference; 16 command-limit cases remain. |

Absolute angular error med/P95/worst over the same commanded cases: A PAN **13.17/60.13/60.13°**, TILT **0.92/9.78/9.78°**; B PAN **12.90/59.50/59.69°**, TILT **0.92/9.78/9.78°**; C both 0° under ideal mechanics. The corresponding aim-position error medians are 10.24 cm (A/B) and 0 cm (C). These are aiming errors against true XYZ, separate from the accepted table-plane mapping error.

| Z (cm) | A hit / commanded | A miss med / P95 / worst (cm) | B hit / commanded | C hit / commanded |
|---:|---:|---:|---:|---:|
| 0 | 16/16 | 0 / 0 / 0 | 16/16 | 16/16 |
| 5 | 0/8 | 9.45 / 10.26 / 10.26 | 0/8 | 20/20 |
| 10 | 0/16 | 17.76 / 19.92 / 19.92 | 0/16 | 20/20 |
| 15 | 0/4 | 27.09 / 27.09 / 27.09 | 0/4 | 24/24 |
| 20 | 0/4 | 31.42 / 31.42 / 31.42 | 0/4 | 24/24 |
| 30 | 0/0 | — (24 blocked) | 0/0 | 24/24 |

Example: s1, target `(0.88, 0.34, 0.05)` m, B reports a background spot only **1.04 px** from the target pixel while the emitter ray misses the true target by **10.26 cm**. Across 30 such nominal `UNKNOWN` cases, residual median/P95/worst is 0.87/1.73/1.96 px and ray miss is 19.92/31.24/31.27 cm. This is the same-camera-ray/different-depth ambiguity, not a localization error on the table plane.

**D, hypothetical metric depth only:**

| Absolute signed camera-range error (cm) | Geometric hits / reachable | Miss med / P95 / worst (cm) |
|---:|---:|---:|
| 0 | 128/128 | 0 / 0 / 0 |
| 1 | 252/252 | 0.65 / 0.99 / 1.00 |
| 2 | 252/252 | 1.28 / 1.98 / 2.00 |
| 5 | 108/252 | 3.16 / 4.97 / 5.00 |
| 10 | 24/260 | 6.09 / 9.92 / 10.00 |

The ±2 cm range-error result assumes perfect mechanics and camera pose. It is a **simulation design target**, not a measured acceptance threshold for a Depth model. A real model would need an independent metric-depth validation set at the actual camera angles, distances and flame appearances.

Plots: [miss versus Z](aiming_evaluation/error_by_z.svg), [absolute PAN error versus Z](aiming_evaluation/pan_error_by_z.svg), [absolute TILT error versus Z](aiming_evaluation/tilt_error_by_z.svg), [dual-node coverage versus Z](aiming_evaluation/dual_coverage_by_z.svg). The node curves overlap in nominal symmetric geometry; the CSV retains both separately.

## G. A/B Targeting Feasibility

For each height there are 12 `(mount, XY)` pairs, each evaluated for both nodes. At Z=0, A/B hit with **at least one** node in 10/12 pairs and with **both** nodes in 6/12; C has the same floor limit because near-camera floor points need a TILT below the assumed −35°. At Z=5 and 10 cm, C reaches at least one node in 12/12 and both in 8/12; from 15 to 30 cm, C reaches both in 12/12. A/B hit neither node above Z=0 in this grid. Each nominal node separately has A/B **8/72 hits** and C **64/72 hits**. In the overlap XY position across all six heights, A/B have 2/12 node-hits per mount; C has 12/12.

These are grid coverage counts, not continuous-area guarantees. The physical blind zones depend on true HOME headings, tilt elevation at HOME, pivot vectors, individual command and PWM limits, target radius, occluders and whether the target surface can return an observable spot. Missing ID 21/22 pose, untrusted reference map, out-of-frame target, command limit and lost spot are explicitly `BLOCKED`, `OUT_OF_VIEW`, `OUT_OF_RANGE` or `LOST_SPOT` in the CSV. A visible background spot alone leaves 3D hit status `UNKNOWN`.

## H. Critical Findings

1. **Critical: target height is unobservable from one RGB pixel without a surface/depth constraint.** The accepted dynamic table map answers where a camera ray meets Z=0. Reproduction: [`test_same_pixel_has_many_depths_but_one_floor_intersection`](../pi/tests/test_aiming_geometry_audit.py) and 5–30 cm A results above. Impact: false XYZ and large pan/tilt miss. Fix direction: establish target surface geometry, independent range/second view, or validated metric depth; do not reinterpret the table hit as flame XYZ.
2. **Critical: 10 cm pivot and HOME orientation are not commissioned.** Current site is 8 cm along tag +Z and both yaw overrides are null. A vertical tag can make tag +Y yaw undefined; a wrong 10 cm axis yields oracle median **12.36 cm** miss (0/72 hits). Tag-axis yaw instead of mechanical HOME gives oracle median **29.85 cm** miss (0/108 reachable hits). Reproduction: new PnP, offset and yaw tests. Fix direction: measure a 3-vector and physical HOME heading per node and mount, reject degenerate tag heading rather than silently using 0°.
3. **Critical: TILT HOME and servo transfer are not verified physically.** A +5° unknown HOME elevation reduces oracle hits to **12/128** and yields median **5.96 cm** miss. A 2° axis bias gives 56/128 hits, a +10% gain gives 52/128, and reversing PAN sign gives 0/128. The HAL has configurable offset/inversion and a nominal linear PWM map, but no device readings were taken. Fix direction: per-node physical angle-vs-command calibration and safe limits.
4. **High: pixel coincidence on a rear surface is not 3D contact.** Thirty B cases end at a ≤2 px residual on the table/background with large true misses. The existing auto `find_spot()` reports all spots at Z=0 and `_plane_mask()` holds Z fixed. Fix direction: require verified target-surface spot or independent depth/contact evidence; stop with `UNKNOWN` otherwise.
5. **High: current simulator/test fixtures hide the new mount geometry.** Default scene renders horizontal tags and its node ray applies only a simple angle bias. No existing test establishes vertical-tag yaw, physical HOME elevation, 10 cm direction or actual servo sign. The new harness is intentionally separate and synthetic; real multi-height commissioning remains necessary.

**Sensitivity under alternative assumptions (oracle C, same 3 cm assumed radius):** pivot estimate ±1 cm: 0.96/0.97 cm median miss, 124/124 and 128/128 reachable hits; +5° TILT HOME: 5.96 cm, 12/128; 2° servo bias: 3.32 cm, 56/128; +10% gain: 3.33 cm, 52/128; wrong vertical offset: 12.36 cm, 0/72; tag-axis yaw: 29.85 cm, 0/108; reversed PAN: 36.74 cm, 0/128. On Z=0, an assumed +1° camera pose error gives A median/P95 miss 1.98/2.73 cm; an assumed 12 px box bottom gives 1.99/2.39 cm. Neither is a measured camera or YOLO error.

## I. Recommendation

**Choose direction 3 first: add a geometric constraint or independent range/second-view observation, and commission both pan–tilt assemblies.** The actual question is freely elevated targets at multiple unknown heights. Current floor projection fails; background-only image servo cannot certify contact; an unverified Depth model would still inherit pivot/HOME/servo errors. A controlled target-surface spot with safe acquisition and reliable surface labeling could make direction 1 viable for reachable, optically cooperative targets. A metric Depth model becomes worth integrating only after independent data show roughly **≤2 cm P95 camera-range error in the working volume under these simulation assumptions**, and measured actuator/camera errors fit within the remaining 3 cm target budget. The threshold must be re-evaluated against the real target radius and safe operating limits. No model training or integration is proposed in this audit.

## J. Presentation Defense

**Bản 30 giây.** YOLO 2D giúp tìm vùng có lửa trên ảnh. Sau khi hiệu chuẩn camera và dùng AprilTag dựng hệ tọa độ, ta có thể biến pixel thành tia nhìn và tính điểm tia cắt mặt bàn để ngắm các mục tiêu nằm trên bàn. Với ngọn lửa ở độ cao chưa biết, cùng một pixel có nhiều vị trí 3D khả dĩ. Muốn xác nhận ngắm trúng cần thấy vết ngắm trên chính bề mặt mục tiêu hoặc có thêm thông tin khoảng cách; vết trên nền trùng pixel chưa đủ.

**Bản 60 giây.** ChArUco cho nội tại camera; AprilTag cho pose camera và các đế pan–tilt trong cùng hệ tọa độ. Từ pixel YOLO, ray–plane intersection cho tọa độ trên mặt bàn Z=0 nếu mục tiêu thật sự nằm trên mặt ấy. Mục tiêu nâng cao tạo parallax: tia vẫn như cũ nhưng điểm cắt bàn nằm xa hơn mục tiêu thật, nên góc PAN/TILT tính từ điểm bàn có thể lệch lớn. Hai cụm A/B còn cần đo vector từ tag tới tâm quay, hướng PAN HOME, góc TILT HOME và chiều/gain servo; PWM 1500 µs tự nó không xác nhận các góc đó. Phản hồi ảnh có thể chỉnh khi vết laser thật sự thấy trên mục tiêu, còn vết trên bàn trùng pixel chỉ là trùng theo đường nhìn camera. Nếu không quan sát được bề mặt đó, phải bổ sung ràng buộc hình học, góc nhìn/range khác hoặc metric depth đã được kiểm chứng; không thể gọi một ảnh RGB là định vị XYZ tự do.

## K. Next Steps and Reproduction

1. Đo và lưu **vector** tag→pivot cho từng cách gắn và từng node, pose tag, PAN HOME world heading, TILT HOME physical elevation, command/PWM→angle curve, dấu, backlash và giới hạn an toàn. Kiểm tra bằng cơ cấu không tải/thiết bị phù hợp trước mọi thử nghiệm ngắm thật.
2. Thu một bộ mục tiêu có ground-truth XYZ độc lập ở Z=0–30 cm, nhiều XY và hai cụm; lưu calibration thực, tag visibility, ảnh target/spot và độ trễ. Đo riêng lỗi localization mặt bàn và lỗi XYZ.
3. Chứng minh có thể quan sát và phân loại vết **trên chính bề mặt mục tiêu**; nếu không, giữ kết quả visual servo là `UNKNOWN` và thử một ràng buộc khoảng cách/second view. Chỉ sau đó so sánh metric depth theo error budget đã đo.
4. Khi có dữ liệu, thiết kế bản vá production và regression test riêng cho yaw suy biến, offset/HOME/servo calibration và logic surface feedback. Đợt audit này không triển khai các bản vá đó.

Run offline from the evaluation worktree's `pi` directory in a Python environment with project dependencies:

```powershell
$env:PYTHONPATH = 'src'
python -m pytest -q --basetemp=../reports/aiming_evaluation/recheck_tmp tests/test_aiming_geometry_audit.py
python scripts/aiming_geometry_audit.py --out ../reports/aiming_evaluation
python -m pytest -q --basetemp=../reports/aiming_evaluation/regression_recheck_tmp tests/test_dynamic_mapping.py tests/test_yaw.py tests/test_3d_targets.py tests/test_station_sim3d.py tests/test_aim_point.py tests/test_sim_floor.py tests/test_aiming_geometry_audit.py -k 'not coap'
```

To reproduce the **unfiltered** existing baseline and its documented Windows/data failures, run the seven original test files including `test_scenario_3d.py` and the CoAP case as recorded in `baseline_tests.txt`. `python scripts/aiming_geometry_audit.py` only writes offline CSV/SVG; `scripts/aim_point.py` is a different, hardware-capable tool and is not part of this procedure.
