# Node B integrated Thread/CoAP firmware

Version `b-integrated-0.2.4`, target ESP32-H2, ESP-IDF v5.4.4. Based on 0.2.2, which the user flashed and verified for Pan/Tilt, relay LED, HOME and STOP. This version restores the independently calibrated Pan and Tilt pulse ranges. Thread Auto-Start, DHT11, relay control, and the 0.2.2 STOP/watchdog behavior remain.

0.2.4: fixes a heartbeat race that could cause a false STOP (clock now sampled inside the lock; a heartbeat newer than `now_ms` counts as fresh) and limits the servo ramp to one 20 µs step per 20 ms in real time.

## Hardware contract

| Node B signal | GPIO / supply | Firmware behavior |
| --- | --- | --- |
| DHT11 DATA | GPIO11, 4.7 kΩ pull-up to 3V3; VCC 3V3, shared GND | Existing driver; reads at least 2 seconds apart |
| Pan servo signal | GPIO4 | LOW at boot; 50 Hz PWM only after heartbeat, `ARM`, `HOME` |
| Tilt servo signal | GPIO5 | Same as Pan |
| Both servo power | Separate regulated 5 V, 2–3 A recommended for the assembly, shared GND with H2 | Never powered from H2 GPIO or 3V3 |
| Active-HIGH relay input | GPIO10 | LOW by default; `POST /laser` ON drives HIGH only after attached + fresh heartbeat + ARM + HOME |
| Laser simulation | LED through GPIO10 relay contacts | LED only; no physical laser emitter or separate laser GPIO |
| Pump | Separate 12 V supply in older design | Not controlled by this firmware; keep supply disconnected |

Older documents propose GPIO14 for a laser MOSFET and GPIO10/11 for SHT31. They are proposals, not evidence of the wired Node B. GPIO4 was used for laser in a previous test and is now reserved for Pan servo. **Disconnect any old laser wiring from GPIO4 before powering servos.** GPIO10 drives the relay module input, never the relay coil directly. Verify that its input reliably accepts 3.3 V HIGH and add an external pull-down to keep the relay OFF during reset, before firmware owns the pin. The repository describes the two nodes as H2 Super Mini, so DevKitM-1 connector locations must not be assumed.

Pan accepts **500–2500 µs** and Tilt accepts **600–2500 µs**. HOME is 1500/1500 µs. These are calibrated pulse envelopes, not a guarantee that every position is mechanically clear in the assembled mount. Each 20 ms control step changes at most 20 µs per axis. The first HOME pulse can still move a servo abruptly from an unknown physical position; mechanically align and check clearance before enabling the separate 5 V servo supply.

## CoAP contract

| Method/path | Request | Response |
| --- | --- | --- |
| `GET /info` | none | Node B and version, integrated mode |
| `GET /status` | none | Thread role, heartbeat, stop count, DHT11 fields, servo state, commanded `relay_on`; `laser_on:false` means no real laser |
| `POST /hb` | empty | `2.04 Changed`; refreshes 1500 ms watchdog |
| `POST /stop` | empty | `2.04 Changed`; disarms servo, stops PWM and drives relay LOW |
| `GET /pantilt` | none | JSON `armed`, `pwm_active`, current and target µs, `pan_min_us:500`, `pan_max_us:2500`, `tilt_min_us:600`, `tilt_max_us:2500` |
| `POST /pantilt` | ASCII `ARM` | `2.04` only while attached and heartbeat fresh; does not start PWM |
| `POST /pantilt` | ASCII `HOME` | `2.04` after ARM; starts 1500/1500 µs PWM or ramps back to HOME |
| `POST /pantilt` | ASCII `pan_us,tilt_us`, e.g. `1600,1450` | `2.04` only after HOME; Pan must be an integer 500–2500, Tilt an integer 600–2500; task ramps asynchronously |
| `GET /laser` | none | `available:true`, `mode:"relay_led_simulation"`, `on` is commanded GPIO10 relay state |
| `POST /laser` | ASCII `ON` | `2.04` only when attached, heartbeat fresh, ARM and HOME completed; drives GPIO10 HIGH |
| `POST /laser` | ASCII `OFF` | `2.04` and GPIO10 LOW even if heartbeat stale or disarmed, when request arrives |

Malformed/out-of-range Pan/Tilt payloads return `4.00 Bad Request`; not attached, stale heartbeat, missing ARM or missing HOME return `4.03 Forbidden`. A payload over 16 bytes returns `4.13 Request Entity Too Large`. `/laser` rejects malformed payloads with `4.00`, disallowed `ON` with `4.03`, and payloads over 16 bytes with `4.13`. STOP, 1500 ms heartbeat expiry and Thread detach clear ARM/PWM and drive relay LOW; resuming requires a new heartbeat, ARM and HOME. A reboot never restores authorization or relay ON. The servo task owns LEDC and advances at 20 ms intervals; GPIO10 is synchronized with authorization under the control lock, including while servo PWM is inactive. `/status.relay_on` and `/laser.on` are commanded relay LED state, not verified LED light or laser emission. `/status.laser_on` remains `false` for compatibility and means no physical laser is controlled. `/status` keeps DHT11 `sensor_ok`, `temperature_c`, `humidity_pct`, `sample_age_ms`, `last_attempt_age_ms`, `sensor_error`; absent/bad sensor data remains `null` as in the base firmware.

The existing USB Serial/JTAG provisioning commands `TLV:<dataset-hex>` and `START` remain. With the active dataset already in B's NVS, Thread starts on cold boot without Serial. No dataset or Network Key is stored in this source or build log.

## Build and host tests

Use `D:\esp\v5.4.4\esp-idf`, `IDF_TOOLS_PATH=D:\Espressif`, and `D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe` in a process-local ESP-IDF PowerShell environment. From this project directory:

```powershell
idf.py --no-ccache -B ../../build-output/node-b-integrated-0.2.4 build
```

Host tests cover probe, DHT11, Thread Auto-Start, control state, independent pulse boundaries, STOP GPIO handoff and relay GPIO calls. The accepted 0.2.2 binary remains at `build-output/node-b-integrated-0.2.2/nt532_node_b_integrated.bin`. The v0.2.2 investigation and test results are in `reports/nt532_node_b_stop_relay_2026-11-12.md`; the v0.2.3 range report is `reports/nt532_node_b_full_servo_range_2026-11-13.md`.

## Hardware acceptance after separate flash approval

1. Identify Node B by PnP/MAC and verify the current COM port. Keep physical laser and pump disconnected. Confirm GPIO4/5 are servo signals only. Verify the relay input has an external pull-down, its module accepts 3.3 V HIGH and its contacts control only the simulation LED.
2. Flash **app only** from this directory after separate approval: `idf.py --no-ccache -B ../../build-output/node-b-integrated-0.2.4 -p COM_PORT app-flash`. Do not run project-wide `flash` or erase NVS. On cold boot, check Thread Auto-Start, DHT11, GPIO10 LOW, `/laser` simulation mode and idle `/pantilt`.
3. With the servo 5 V supply disconnected, record GPIO4/5 using a logic analyzer or oscilloscope at a time scale that resolves short pulses. Send heartbeat, ARM, HOME, a small target, then STOP. Verify the last valid PWM pulse is followed by continuously LOW output with no HIGH spike, pull-up interval or floating level. Repeat for heartbeat timeout and Thread detach. Repeat with the 5 V servo supply only after the signal trace is acceptable; confirm Pan does not move on STOP. If movement remains with a verified LOW signal, inspect servo power, common GND and signal integrity before changing control logic.
4. With only the relay simulation LED connected, verify `ON` is rejected before heartbeat/ARM/HOME and accepted afterward. Check GPIO10 HIGH, relay and LED ON; `OFF`, STOP, heartbeat timeout and Thread detach must bring GPIO10 LOW, relay and LED OFF. The relay-only watchdog case (PWM state forced inactive while relay is ON) is a host fault-injection test because the normal CoAP contract never permits that state. Reboot and confirm relay OFF before sending commands. Keep a physical laser emitter disconnected.
5. Move one axis at a time from HOME in small increments while keeping the other at 1500 µs. Check clearance and servo load after each step in both directions; STOP immediately if the mount binds. Do not sweep to 500/2500 µs on the first run. The detailed acceptance sequence is in the v0.2.3 report.

Example Pi commands after obtaining B's current EID (keep heartbeat running every 400–500 ms while authorized):

```sh
NODE_B_EID='fd..:..'
coap-client -m post -e '' "coap://[$NODE_B_EID]/hb"
coap-client -m post -e ARM "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e HOME "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e '1550,1500' "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e ON "coap://[$NODE_B_EID]/laser"
coap-client -m get "coap://[$NODE_B_EID]/laser"
coap-client -m post -e OFF "coap://[$NODE_B_EID]/laser"
coap-client -m post -e '' "coap://[$NODE_B_EID]/stop"
```

For watchdog validation, stop the periodic `/hb` sender and wait more than 1500 ms before checking `/status`, `/pantilt`, `/laser` and GPIO10. A subsequent `ON` requires fresh heartbeat, ARM and HOME.

## Rollback, instructions only

The accepted 0.2.2 app binary is `build-output/node-b-integrated-0.2.2/nt532_node_b_integrated.bin` (SHA-256 `63A8B8AAAC029EC7A9E3A0FCBD27711D0516E31440340F939A642227B64CC13B`). After verifying Node B's port, write only that app image at offset `0x10000` using `python -m esptool --chip esp32h2 -p COM_PORT write_flash --flash_mode dio --flash_freq 48m --flash_size 4MB 0x10000 ../../build-output/node-b-integrated-0.2.2/nt532_node_b_integrated.bin` from this project directory. This leaves NVS intact. No rollback is performed here.
