# Dynamic Reference Mapping (offline specification)

## Objective and coordinate contract

Opt-in `vision.reference_mode: auto` determines the current table-plane geometry from calibrated camera images. ID 2 is the origin; its printed right and top edges define +X and +Y, and the tag normal defines +Z. Units are metres. All reference markers must lie flat on the same plane and have the configured 0.08 m **black marker** side length. Their centres may form any nondegenerate arrangement and may rotate within the plane. The origin and axes move if ID 2 moves or rotates; no absolute table coordinate can be recovered without another surveyed landmark.

The default `fixed` mode retains the existing surveyed coordinates, board geometry, commissioning file and orchestration behaviour. Auto mode never reads `tags.reference.positions` to compute geometry and never writes calibration, commissioning, or site files. It does not load a fixed commissioning file. A fresh accepted image batch is required after startup and after any mapping failure.

## Algorithm and quality gate

Undistort marker corners using camera intrinsics. Solve the two IPPE square poses of ID 2, intersect rays from each other reference marker's four corners with the ID 2 plane, and score each candidate against the known marker side lengths, square shape and reprojection residual. Require ID 2 and at least two other reference markers in each accepted frame. Reject ambiguous candidate pairs when they imply materially different positions and score nearly equally. Require positive depth, table-facing camera, sufficient projected marker size and low metric/reprojection residual. Solve each frame independently, then use a median of accepted frame estimates; reject inconsistent inter-frame geometry. Never reuse a previous camera pose to make an invalid new map pass.

Use the accepted camera pose and existing node-tag PnP path to locate IDs 21/22. A pixel on the table is the forward intersection of its calibrated ray with Z=0. Auto mode's YOLO output is the **table contact-point estimate** `(X,Y,0)`; a single image cannot recover the height of an arbitrary flame. The position of a separate vertical board is also unobservable from reference tags alone, so auto mode does not claim wall coordinates. Limit extrapolation outside the convex hull of visible references.

## Commands, structure and style

- From `pi/`: `uv run pytest tests/test_dynamic_mapping.py`, then `uv run pytest tests`; `uv run ruff check src/nt532/vision scripts/dynamic_map.py tests/test_dynamic_mapping.py`.
- Core: `pi/src/nt532/vision/dynamic_mapping.py`; integration: `system.py`; headless Pi CLI: `pi/scripts/dynamic_map.py`; synthetic tests: `pi/tests/test_dynamic_mapping.py`.
- Keep existing NumPy arrays, `Camera`/`TagPose` types, snake_case names and explicit `ValueError` for bad geometry. Example: `mapping = estimate_reference_map(corners, intrinsics, size=0.08)`.

## Verification and boundaries

Synthetic ground truth is generated independently from the estimator. Test 60×40 cm, 80×50 cm and asymmetric layouts, changed camera pose, moved reference, missing anchor, table pixel, node poses, noisy/ambiguous observations, and fixed-mode regression. Aim for ≤2 cm position error on accepted scenes; report measured errors and rejected cases. Real camera tests must validate printed marker size, intrinsics at actual focus/resolution, motion blur, partial occlusion, lens distortion and millimetre-scale height differences.

Only Vision mapping, its CLI, tests and documentation may change. Do not change firmware, networks, control, calibration files, or fixed coordinates. No Pi connection, push or merge.
