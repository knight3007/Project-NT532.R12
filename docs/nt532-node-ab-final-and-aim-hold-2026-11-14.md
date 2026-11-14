# NT532 node A/B HOME and manual tabletop aiming handover — 2026-11-14

## Scope and evidence

Worktree: `build-output/node-ab-aim-hold-worktree`, branch
`feature/node-ab-aim-hold-20261114`, based on `origin/main` at
`d115e5d192621d98bebca83566f2219b3ddebd28` (PR #10). No commit, push,
merge, flash, or physical node command was made in this task. The original dirty
checkout, its calibration, and partner files remain untouched. GitHub's main
branch was checked again at handover and still pointed to the same commit.

| Deliverable | Code evidence | Hardware/e2e status |
|---|---|---|
| Firmware A | Host tests and ESP-IDF v5.4.4 build PASS | HARDWARE PENDING |
| Firmware B | Host tests and ESP-IDF v5.4.4 build PASS | HARDWARE PENDING |
| Pi integrated adapter and manual CLI | Fake-node/manual tests and Ruff PASS | HARDWARE PENDING |
| Legacy station (`--profile station`) | Still uses `/aim`/`/fire`, selfcal sweep and pump phases | Do **not** run against A/B |
| `run_station.py --profile vision-aim-only` | 24 fake-node tests + fake-node demo PASS; Ruff PASS | **STATION SOFTWARE E2E PASS**, **PHYSICAL HARDWARE E2E PENDING** |

The old checkout's untracked A/B sources differ from all 33 corresponding files
on the new main. Inspection showed the old copies lack the merged PR #6
heartbeat race and 20 ms ramp fixes. They were not copied into this worktree.
PR #7's simulated adapter and its temporary 0.09 degree/µs map were not used.

| Artifact | App version | Bytes | SHA-256 |
|---|---|---:|---|
| `build-output/node-a-integrated-0.1.2/nt532_node_a_integrated.bin` | `a-integrated-0.1.2` | 784176 | `87231B61725C952F11CAD814C0B251A980F21BAFA480FD00FA1CB184D5D2E8F6` |
| `build-output/node-b-integrated-0.2.5/nt532_node_b_integrated.bin` | `b-integrated-0.2.5` | 772672 | `5E9C607FFD9EF4C7229CC15516E9A16A76230E4CE7628843FCA04EC57F861230` |

Build logs: `build-output/node-a-integrated-0.1.2-build.log` and
`build-output/node-b-integrated-0.2.5-build.log`. ESP-IDF v5.4.4 builds
completed, with no compiler warnings/errors in those logs. The version strings
were checked inside each app binary. The 11 host executables in
`firmware/node-a-integrated/test/run_host_tests.ps1` passed, covering both
control/servo models, sensor logic, responses, and Thread auto-start. These are
host/build results, **HARDWARE PENDING**.

## Firmware and Pi contract

Node A: MQ-2 AO GPIO1; DO GPIO12 remains disabled; Pan GPIO4; Tilt GPIO5;
active-HIGH relay GPIO10. Node B: DHT11 GPIO11; Pan GPIO4; Tilt GPIO5;
relay GPIO10. The relay reports `relay_led_simulation`; the firmware does not
prove a physical laser emitter or spot. Pump power must stay disconnected.

Both nodes now start and HOME at Pan **1500 µs**, Tilt **1700 µs**. Legal
commands remain Pan 500–2500 µs, Tilt 600–2500 µs; PWM 50 Hz; ramp at most
20 µs per axis per elapsed 20 ms; heartbeat watchdog 1500 ms. Boot, STOP,
timeout, and Thread detach de-energize PWM/relay. ARM alone does not turn on
PWM. A fresh heartbeat, ARM, and HOME are needed after STOP. No NVS, Thread
dataset, sensor thresholds, or pin assignments were changed.

The new Pi manual path maps `s1` to A `a-integrated-0.1.2`, `s2` to B
`b-integrated-0.2.5` via `GET /info` before motion. It sends `POST /hb`,
`POST /pantilt` with `ARM`, `HOME`, then `pan_us,tilt_us`; it polls
`GET /pantilt` until both actual and target pulses agree. It uses `GET/POST
/laser` for the relay and `POST /stop` in cleanup. `fire(pump)` is rejected.
It never sends `/aim`, `/fire`, or a pump request to integrated firmware.
The separate heartbeat thread sends every 400 ms; loss, STOP, relay OFF, or
network error aborts hold. OFF and STOP are attempted even after Ctrl+C or
camera failure; final `/laser` and `/status` are read back. If this readback
fails, cut physical emitter power manually.

## Measurements required before moving

Copy `calibration/manual_aim_measured.example.yaml` to
`calibration/manual_aim_measured.yaml` and fill **both nodes separately** using
measurements. The template deliberately contains nulls. Measure tag-to-pivot
translation in the node tag frame, emitter offset in local HOME axes, HOME
yaw/elevation in `tag_2_table`, signs/gains, safe angular limits, and signed
µs/degree pulse gains around Pan 1500/Tilt 1700. The old
`calibration/pantilt_calibration.json` and `servo_measurements.csv` only contain
old HOME/visual estimates and cannot supply this geometry. Do not substitute
the generic `actuator.pivot_offset` or PR #7's pulse factor. Until measurements
are filled, `CALIBRATION_REQUIRED` is the expected outcome and the tool sends
no target/relay commands. If a node or tag is rotated or remounted after these
measurements, repeat the HOME direction and mount measurements before execution.

The CLI accepts only `(x,y,0)` metres in `tag_2_table`. Dynamic Mapping uses
ID 2 as anchor and a **fresh 15-frame** batch. It checks the current node tag,
visibility, map quality and target area. `tag_22_table` is refused until a
tested transform is provided. Computed poses and predicted laser hit are
plans, **not physical ground truth**. A tilted phone screen or a target raised
above the tabletop violates Z=0; place the physical target/spot on the actual
table plane before measuring.

## Pi dry run and controlled trial

On the Pi, install base Pi dependencies in `pi/` with `uv sync`, provide the
actual camera intrinsics at `calibration/camera.yaml`, and put each node's
current reachable IPv6 address in a **local copy** of `config/site.yaml`
under `network.nodes.s1/s2`. Point `NT532_CONFIG` to that copy if desired.
Keep any Thread credentials out of logs. Verify the camera feed and current
tag layout first. Example dry run for A:

```bash
cd ~/nt532/pi
uv run --no-sync python scripts/aim_point.py \
  --source rtsp://127.0.0.1:8554/cam/stream \
  --node s1 --surface floor --frame tag_2_table \
  --x 0.30 --y 0.20 --hold-seconds 30 \
  --calibration ../calibration/manual_aim_measured.yaml
```

Repeat with `--node s2` and a separately measured B mount. Dry run is the
default and performs no CoAP motion/relay command. It prints plan/mapping or a
blocker, and writes JSON/CSV under `runs/measure/`. Only after measured
calibration, correct identity, safe wiring, and a visible table spot should
the operator append `--execute`. `--execute` requires an explicit real camera
source. It asks for `OK` immediately before relay ON. Hold defaults to 30 s,
hard maximum 120 s per point. Ctrl+C aborts and triggers OFF/STOP. No startup
sweep or water compensation is used in real manual mode.

For repeated measurements, pass a CSV with `x,y` columns in metres and an
optional `node` column (`s1` or `s2`). Each row is a separate 15-frame map,
identity/pose/bounds gate, confirmation, bounded hold and cleanup. One JSON
array and CSV preserve every row from the command, so collect at least five
independent points per node before assessing physical accuracy:

```csv
node,x,y
s1,0.30,0.20
s1,0.45,0.30
s2,0.70,0.25
```

```bash
uv run --no-sync python scripts/aim_point.py \
  --source rtsp://127.0.0.1:8554/cam/stream --surface floor \
  --points points.csv --execute --calibration ../calibration/manual_aim_measured.yaml
```

The OFF and ON camera frames are saved as PNG with timestamps. A spot is
accepted only if the image difference localizes it on the same Z=0 table map
and the operator confirms an actual tabletop spot after relay OFF.
If there is no spot, the operator may enter a ruler-measured `X,Y` **after
relay OFF**, or leave the prompt blank to record `NOT MEASURABLE`. The error
is `100 × hypot(spot_x−target_x, spot_y−target_y)` cm, only for an observed
camera/ruler spot. No simulated coordinate is used as ground truth. Repeat at
least five independently measured points per node for physical acceptance.

## App-only flash procedure — operator action, not automated

1. Identify each board by USB serial/physical label and check its **current**
   COM port in Device Manager or `python -m serial.tools.list_ports`. Historical
   COM19/COM18 are not verified for this session. Confirm A binary goes to A
   and B binary to B; record existing accepted app binaries for rollback.
2. Verify each file against the SHA-256 above with `Get-FileHash -Algorithm
   SHA256 <path>` (Windows) or `sha256sum <path>` (Pi).
3. In an ESP-IDF v5.4.4 shell on Windows, from this worktree, flash **only the
   app partition**. Run one board at a time, substituting the verified COM:

```powershell
cd firmware/node-a-integrated
idf.py -B ../../build-output/node-a-integrated-0.1.2 -p COM_A app-flash
cd ../node-b-integrated
idf.py -B ../../build-output/node-b-integrated-0.2.5 -p COM_B app-flash
```

`app-flash` does not erase the NVS/Thread dataset partition. Do not use
`erase-flash`, full `flash`, or a generic build directory for this handover.
If rollback is needed, use the previously accepted board-specific app binary
and its recorded app offset, preserving NVS; do not substitute the other
node's binary. The ESP-IDF app-only command is documented in the
[ESP-IDF v5.4 monitor/flash guide](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-guides/tools/idf-monitor.html).

## Post-flash smoke checks — operator action

Resolve each current Thread IPv6 address. Start with `GET /info`, `/status`,
`/pantilt`, `/laser`, plus `/mq2` on A. Verify `n`, firmware version,
`laser_disabled` output descriptor, sensor fields, PWM OFF and relay OFF.
For B, verify DHT11 fields in `/status`; missing/invalid samples must stay
null rather than synthetic. Example using the current IPv6 address:

```bash
coap-client-notls -m get 'coap://[NODE_IPV6]/info' | jq .
coap-client-notls -m get 'coap://[NODE_IPV6]/status' | jq .
coap-client-notls -m get 'coap://[NODE_IPV6]/pantilt' | jq .
coap-client-notls -m get 'coap://[NODE_IPV6]/laser' | jq .
```

When the board is fixed and its travel path clear, provide heartbeats at
400–500 ms while manually testing `ARM`, `HOME`, then one axis at a time only
40 µs from HOME (e.g. Pan 1540/Tilt 1700, then Pan 1500/Tilt 1660). Inspect
`GET /pantilt` actual/target and STOP. `POST /stop` must disable PWM/relay;
allowing heartbeat to lapse must also stop within the watchdog window.
For one verified node address, an operator can run this **after** the GPIO
path is clear; the heartbeat helper contains no laser ON command:

```bash
NODE_IPV6='replace-with-current-node-ipv6'
while :; do coap-client-notls -m post "coap://[$NODE_IPV6]/hb" >/dev/null; sleep 0.4; done &
HB_PID=$!
coap-client-notls -m post -e ARM "coap://[$NODE_IPV6]/pantilt"
coap-client-notls -m post -e HOME "coap://[$NODE_IPV6]/pantilt"
coap-client-notls -m get "coap://[$NODE_IPV6]/pantilt" | jq .
coap-client-notls -m post -e '1540,1700' "coap://[$NODE_IPV6]/pantilt"
coap-client-notls -m get "coap://[$NODE_IPV6]/pantilt" | jq .
coap-client-notls -m post -e '1500,1700' "coap://[$NODE_IPV6]/pantilt"
coap-client-notls -m post -e '1500,1660' "coap://[$NODE_IPV6]/pantilt"
coap-client-notls -m get "coap://[$NODE_IPV6]/pantilt" | jq .
coap-client-notls -m post -e OFF "coap://[$NODE_IPV6]/laser"
coap-client-notls -m post "coap://[$NODE_IPV6]/stop"
kill "$HB_PID"
coap-client-notls -m get "coap://[$NODE_IPV6]/status" | jq .
```

Do **not** use the old acceptance script to sweep to servo limits. Before any
relay ON trial, check GPIO10 wiring, emitter power and polarity, beam direction,
obstructions and reflective surfaces. GPIO/LED state alone is not spot or
accuracy evidence.

## Test and station status

Pi targeted fake-node tests cover A/B identity, HOME, pulse limits, no-pump,
POST acknowledgment without actual arrival, STOP during hold, confirmation
rejection, Ctrl+C cleanup, frame mismatch, missing calibration and bounds.
Ruff passes. Full `uv run pytest -q` cannot collect in this environment:
`tests/test_decider.py` and `tests/test_jev.py` require the optional `torch`
dependency absent here. With those two modules excluded and a generated
`data/sim/commissioning.yaml` (created by `commission.py --source sim`), the
final regression run reported **422 passed, 23 skipped** in 6m47s. The first
regression run's 17 scenario failures all came from the absent generated
simulation commissioning file; its affected 28-test subset passed after
commissioning. No full-suite PASS is claimed until the optional torch tests run.

The legacy real station (`run_station.py` default `--profile station` /
`build_real`) still uses `/aim`/`/fire`, the selfcal startup sweep and pump
phases. Do not run it against these integrated nodes. Use the explicit
`vision-aim-only` profile below. Manual aim is a separate controlled trial.

## Station profile `vision-aim-only` (no pump, aim only)

Code: `pi/src/nt532/aim_only.py`, selected only by
`pi/scripts/run_station.py --profile vision-aim-only` (the sim and legacy
profiles are unchanged). It reuses the integrated adapter (`IntegratedNode`,
400 ms heartbeat thread, identity, HOME, poll, OFF/STOP shutdown), the manual
planner (`plan_target`/`load_mount`: measured mount + signed µs/degree per node,
`tag_2_table` only), `resolve_nodes` and the real `Vision` in auto mode
(Dynamic Mapping, ID 2 anchor, tabletop anchor `center`). It does not import the
legacy Orchestrator or selfcal and never reads MQ-2/DHT11.

Launch on the Pi (from `pi/`, after filling `calibration/manual_aim_measured.yaml`
and `network.nodes.s1/s2` or SRP, with `--extra yolo` installed):

```bash
uv run --no-sync python scripts/run_station.py --profile vision-aim-only \
  --source rtsp://127.0.0.1:8554/cam/stream \
  --aim-calibration ../calibration/manual_aim_measured.yaml \
  --aim-nodes s1,s2 --ok-timeout 120 --verify-light-s 3 --port 8080
```

`--source` must be a webcam index or a stream URL; `sim`, files and the
`--decider/--mqtt/--link/--detector/--camera/--look-at/--layout` options are
refused with this profile.

Startup: `START → CHECK_CAMERA` (calibration preflight, then one 15-frame
Dynamic Mapping batch; node poses and tag IDs 21/22 required) `→ CONNECT_NODES`
(`GET /info` on every selected node before any other traffic: s1=A
`a-integrated-0.1.2`, s2=B `b-integrated-0.2.5`) `→ HEARTBEAT → ARM → HOME`
(1500/1700 reached) `→ CALIBRATION → WAIT_OK` per node, one at a time: relay ON
at HOME, the operator types `OK s1` (then `OK s2`) **at the console** after
seeing that light; an OK typed before the light turned on, a bare `OK`, the other
node's OK and any OK over HTTP are ignored. Then `/laser OFF` and readback
`on=false`, then the next node. `READY` only after all selected nodes pass.
No relay command at cold boot; the first relay ON is in CALIBRATION.
`--ok-timeout` (default 120 s, max 600) expires → OFF, STOP, latched.
Missing measured calibration → state `CALIBRATION_REQUIRED` before any node
traffic (the committed template with nulls gives exactly this).

READY: each tick checks heartbeat errors and `GET /pantilt` armed/PWM per
node, reads a live frame, re-runs a 15-frame Dynamic Mapping every 20 s and
rejects a pose older than 60 s. YOLO detections are localized on Z=0 in
`tag_2_table`; a target must stay within 2 cm for 3 consecutive frames. The
nearest node whose measured plan passes (pose/visibility/area/reach/pulse
limits) receives one `POST /pantilt pan,tilt`; `GET /pantilt` is polled until
actual = target. A stationary target (≤3 cm from the last aimed/rejected
target) is not re-aimed. Each aim is logged with frame, target, node,
command degrees, pulses and `/pantilt` status to
`runs/station/aim-only-<time>.jsonl`. Out-of-reach targets are logged as
`TARGET_REJECTED` without motion.

Verification: the light is never turned on automatically after READY. Console
`VERIFY` lights the last aimed node for `--verify-light-s` (max 10 s), captures
OFF/ON frames, turns OFF and verifies the readback. The error is reported only
from a camera-localized spot on the Z=0 map; otherwise `NOT MEASURABLE`.

STOP latch: `STOP` (console) or `POST /api/stop` (HTTP; the only HTTP command)
sends `/laser OFF` then `/stop` to every verified node, reads back `/laser` and
`/status`, drops queued commands and tracked targets, and ignores camera
targets, `OK` and `VERIFY` until `RECOVER` is typed at the console. RECOVER reruns the
whole startup, including mapping, identity and WAIT_OK. The same latch is
taken on: lost Thread/heartbeat, missing node address, wrong identity, camera
read failure, invalid mapping, stale pose, servo poll timeout, OK timeout and
Ctrl+C/process shutdown. If shutdown readback fails, the log says
`OFF/STOP NOT VERIFIED; cut physical laser power`. `GET /api/health` returns
state, reason, latch, mapping age and the last aim/shutdown. Pump: the transport
wrapper allows only `info, hb, pantilt, laser (ON/OFF), stop, status`;
`/aim`, `/fire` and `/pump` raise before reaching the network.

Remaining hardware-only checks, in order: camera intrinsics for the live
1280×720 stream; a real 15-frame mapping PASS with A/B tags visible; measured
mount/pulse calibration for both nodes; `/info` identity over Thread; OK flow
with the real relay/emitter (wiring, polarity, beam path); stable YOLO
detection of the tabletop target; servo arrival times within the 8 s poll;
STOP, heartbeat lapse and Thread detach on the nodes; at least five
camera-measured VERIFY points per node.
