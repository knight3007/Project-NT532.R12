# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

NT532 is a course project: a simulated fire-detection and suppression station over Thread. ESP32-H2 nodes read sensors and drive a pan–tilt head (pump + aiming laser), and they raise alarms over Thread/CoAP. A Raspberry Pi 5 (OpenThread border router with an H2 DevKit as RCP) uses a fixed camera and AprilTags to verify and locate the fire target, decides what to do, and sends pan/tilt angles back to the node.

Docs, comments, commit messages and most READMEs are in **Vietnamese**; match that when editing them. The master plan is `docs/ke-hoach-trien-khai-NT532.md`: section 3 covers coordinates and section 4 the CoAP contract. The switch from one S3 nozzle to two H2 nozzles is recorded in `docs/thay-doi-hai-voi.md`. Dated handover notes (`docs/ban-giao-*.md`) and `reports/` record the latest hardware state and open items, so read the newest one before touching hardware-facing code.

## Commands

### Pi / station (Python, `pi/`)

Run these from `pi/`. You need Python ≥3.11 and `uv`; CI uses 3.13.

```bash
uv sync
uv run python scripts/commission.py --source sim   # first run: builds the virtual-table commissioning that test_scenario needs (data/ is gitignored)
uv run ruff check src tests scripts
uv run pytest -q --ignore=tests/test_decider.py --ignore=tests/test_jev.py   # what CI runs without torch
uv run pytest tests/test_selfcal.py::test_name -q                          # single test
```

- Optional extras: `--extra yolo` (ultralytics/torch), `--extra decider` (Jev model: transformers, peft, sentence-transformers), `--extra mqtt` (paho, for Home Assistant). **After installing an extra, keep passing it (`uv run --extra yolo ...`) or use `uv run --no-sync`**, because a plain `uv run` re-syncs and removes torch. On Windows, torch comes from the CUDA 12.6 index.
- `tests/test_decider.py` and `tests/test_jev.py` need the decider extra. `tests/test_mqtt.py` needs a broker at `NT532_MQTT_BROKER=localhost:1883`.
- Run the full station on the virtual table: `uv run python scripts/run_station.py --source sim` → http://localhost:8080/. Useful flags: `--decider rules|jev|hybrid|remote`, `--detector oracle|yolo`, `--link mem|coap`, `--mqtt`.
- The fixed eight-scene end-to-end demo with scoring: `uv run python scripts/demo_station.py --step` (see `docs/demo.md`).
- Any script with `--source` accepts `sim`, which gives a virtual camera, 4 reference tags, two nodes, target cards and a laser that follows pan/tilt. With no hardware, use `sim`.

### Firmware (ESP-IDF; each `firmware/*/` dir is its own project)

- `sensor-h2` C core host tests: `make -C firmware/sensor-h2/test` (ASan locally: `SAN='-fsanitize=address,undefined'`). `make ... libcore.so` builds the shared lib that `pi/src/nt532/net/fakenode.py` loads, so the Pi tests run against the real firmware logic.
- `sensor-h2` build (CI uses the `espressif/idf:release-v5.4` container): `idf.py set-target esp32h2 && idf.py build`.
- Node A/B incremental firmware host tests (Windows, MSYS2 gcc at `C:\msys64\ucrt64\bin\gcc.exe`): `powershell -ExecutionPolicy Bypass -File firmware/node-a-integrated/test/run_host_tests.ps1`. The other `node-*` dirs have `test/test_*.c` files that compile the same way, with the `fake_*` header shims on the include path.
- Real builds use ESP-IDF v5.4.4 with output outside the source tree, e.g. `idf.py -B ../../build-output/node-a-integrated-0.1.1 build`.

### Homelab control panel (`homelab/control/`)

FastAPI + SQLite. It proxies the Pi dashboard over Tailscale.

```bash
uv sync
uv run pytest
NT532_ADMIN_PASSWORD=... uv run uvicorn app.main:app --port 8800   # with a sim station running on :8080
```

Deployment (Docker Compose + Caddy in an LXC) is in `homelab/deploy/` and `docs/homelab-deploy.md`.

## Architecture

### Station pipeline (`pi/src/nt532/`)

`station.py` wires everything together, via `build_sim()` or `build_real()`. The orchestrator code is identical in both modes; only the camera and the node link change. Flow:

sensor `/t` `/a` → `net.coap.CoapLink` (dedupe by `(n, s)`) → `orchestrator.machine` state machine: **ALERT → LOCALIZE → DECIDE → safety gate → AIM → CORRECT → FIRE → VERIFY**. Every terminal path sends `/stop` to all nodes.

- `vision/`: the `Vision` facade (see `docs/vision-api.md`) handles camera calibration, AprilTag poses relative to the reference tags (commissioning / move detection), YOLO or oracle fire detection, and laser-spot finding by on/off frame differencing.
- `orchestrator/fusion.py` builds an `obs` dict in **exactly the same shape as the synthetic scenario set** in `decider/scenario.py`. That is how the same decider can be trained offline and run live. Don't change one shape without changing the other.
- `orchestrator/decide.py` has `RuleDecider`, `JevDecider`, `HybridDecider` and a remote decider, all returning `Decision`. The station currently runs **rules**; Jev (EmbeddingGemma + LoRA, trained on Kaggle/Colab) is on hold.
- The safety gate is **always rule-based**, whatever the decider: not commissioned or node moved, pose older than 60 s, heartbeat older than 1.5 s, target off the board, or angle out of limits. A blocked nozzle falls back to the other one, then to ALARM.
- `orchestrator/aiming.py`, `laser_geometry.py` and `selfcal.py` compute pan/tilt from the node base pose plus pivot offset. `selfcal` learns 7 parameters per node from laser hits (LM + Huber) and writes `calibration/nodes.yaml`.
- `net/link.py` provides `NodeLink` (command IDs, waiting on `/status`) and `HeartbeatSender` (`/hb` every 500 ms). `net/discover.py` finds nodes via the OTBR SRP server and falls back to `network.nodes` in `site.yaml`.
- `sim/` is a real-time virtual world (fires, distractors, sensors, `SimLink`, `OracleDetector`). Its numbers are demo assumptions, not measurements.
- `dashboard/` uses the stdlib HTTP server only. It reads `Station.snapshot()`, serves annotated MJPEG and accepts `POST /api/cmd`. `integrations/mqtt.py` bridges to Home Assistant.

### CoAP contract (single interface between Pi and nodes)

Node → Pi: `/t` (telemetry), `/a` (alarm), `/status`. Pi → node: `/aim`, `/fire`, `/stop`, `/hb`. Node ↔ node: `/alarm` multicast `ff03::1`. Payloads are JSON with short keys, under ~60 bytes, defined in `net/protocol.py` and `firmware/sensor-h2/main/core/proto.c`. Invariants that firmware, `SimLink` and `fakenode` must all keep:
- Outputs are **off by default**: pump and laser go low first, and a heartbeat timeout of 1.5 s turns them off. The Pi is not the only safety holder.
- Command IDs increase monotonically. A node remembers the highest `/stop` ID it has received and rejects any `/aim` or `/fire` with a lower ID, so a reordered UDP packet can't re-enable outputs.

### Two firmware lines

- `firmware/sensor-h2/` is the target design (full contract, pure-C `main/core/` tested on host, NVS overrides). Parts not yet verified on hardware are marked `CHƯA KIỂM`.
- `firmware/node-{a,b}-*` are the incremental bring-up apps actually flashed on the physical nodes. Each step is a separate project: probe → thread-autostart → dht11 → integrated. They expose a different, hardware-validated endpoint set (`/info`, `/status`, `/pantilt`, `/laser`, `/hb`, `/stop`, `/mq2`) with ARM/HOME gating, and their READMEs are in English. Rules for them: flash app-only at `0x10000`, **never erase NVS or reflash the bootloader or partition table**, never put the Thread dataset or Network Key in source, build logs or args (it is provisioned over USB serial with `START`/`TLV:`), and don't drive pins whose wiring hasn't been measured.

## Conventions

- Units are metres and degrees. The origin is the front-left corner of the table: X right, Y toward the target board, Z up. Timestamps use the Pi's clock.
- All site measurements (tag IDs and sizes, pivot offsets, angle limits, thresholds, `selfcal` settings, nodes) live in `config/site.yaml`; never hardcode them. Camera and servo calibration results go in `calibration/`.
- `data/`, `models/` and `runs/` are gitignored and shared via Drive. Script outputs go under `runs/<tool>/<timestamp>/`.
- Ruff line length is 100.
