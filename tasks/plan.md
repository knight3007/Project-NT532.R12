# NT532 node A Thread/CoAP probe — implementation plan

## Scope

Build an isolated ESP32-H2 firmware probe for physical node A. It exposes CoAP `/info`, `/hb`, `/status`, and `/stop` on Thread. It never configures or drives sensor, relay, laser, pump, or servo pins. The existing `firmware/sensor-h2` and Serial flash backups remain untouched.

## Decisions

- Use the installed ESP-IDF v5.4.4 OpenThread stack and its built-in CoAP API. No additional component is needed.
- Provision the Pi's existing active dataset TLV at runtime over USB serial; do not place it in source, `sdkconfig`, build logs, or command arguments.
- Require an explicit `START` or `TLV:` serial command before enabling Thread. Never create a new dataset.
- Keep `/stop` a diagnostic latch. This probe has no actuator code, so it cannot prove the production actuator watchdog.

## Tasks

1. Audit CP5.0.2 logs/hashes, Git state, pin maps, and installed toolchain. Acceptance: independent hashes match, logs show full 4 MiB reads and digest matches, hardware conflicts documented.
2. Define and host-test probe endpoint state transitions. Acceptance: GET/POST method table, heartbeat age, and stop latch have passing focused tests.
3. Implement isolated firmware with serial dataset provisioning and OpenThread CoAP resources. Acceptance: no GPIO/LEDC/ADC driver or Thread secret in source; ESP-IDF build passes for esp32h2.
4. Write a handoff report and safe commands. Acceptance: separate PASS/FAIL/NOT TESTED for backup, build, join, endpoints, electrical checks, and rollback; no flash performed.

## Checkpoints

- After 1: confirm source mismatches and safe build design.
- After 3: inspect binary size, build result, source diff, and secret scan.
- Before any physical test: operator confirms A's MAC/PnP, all loads unpowered, measured MQ-2 voltages, and rollback procedure. Flash remains outside this task.

## Risks

- Actual A wiring is user-reported and cannot be verified from source alone.
- A full-flash backup allows recovery of flash contents, not source sketch or eFuse state.
- Runtime Thread join and CoAP delivery cannot be marked PASS without flashing and testing A.
