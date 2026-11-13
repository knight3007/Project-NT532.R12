# Node A integrated firmware

Version `a-integrated-0.1.0` for ESP32-H2 with ESP-IDF v5.4.4. This is a separate application. It reuses Node A's stored-dataset Thread Auto-Start implementation and Node B 0.2.3's Pan/Tilt, relay, STOP, watchdog, and CoAP control logic. The sensor is MQ-2 on ADC1 channel 0 instead of DHT11.

## Connections and limits

| Signal | Proposed GPIO | Firmware behavior |
|---|---:|---|
| MQ-2 AO through a suitable divider | 1 | ADC1_CH0, 12-bit, 12 dB attenuation; 8 samples averaged every 1 s |
| MQ-2 DO through a suitable level shifter | 12 | Disabled until wiring and polarity are measured |
| Pan servo signal | 4 | 50 Hz, 500–2500 µs, HOME 1500 µs |
| Tilt servo signal | 5 | 50 Hz, 600–2500 µs, HOME 1500 µs |
| Active-HIGH relay input | 10 | LOW at boot and on STOP, heartbeat timeout, or Thread detach; LED simulation only |

Use a separate 5 V servo supply with common ground. Power the MQ-2 heater from its specified supply, never from a GPIO. **Measure AO and DO voltages after any divider or level shifter before connecting them to the ESP32-H2.** The chip-level GPIO1 ADC capability is confirmed by ESP-IDF; the Super Mini board pin labeling and actual harness still require inspection. GPIO12 was also allocated to a pump in an older plan, so DO stays disabled. Servo travel proven on the B assembly must be checked separately on A.

All MQ-2 settings are in [mq2_config.h](main/mq2_config.h). The default 300 s warm-up is a status timer only. `alarm` is `null` because no measured threshold exists. Calibration yields `adc_mv` at the GPIO1 pin, after any external divider, or `null` if eFuse calibration is unavailable. Neither field is gas concentration or ppm. MQ-2 never switches the relay.

## CoAP

| Request | Result |
|---|---|
| `GET /info` | Node A identity, firmware, mode, sensor |
| `GET /status` | Thread, heartbeat, STOP, servo, relay, MQ-2 summary |
| `GET /mq2` | ADC raw/mV, optional DO, warm-up, freshness, unknown alarm |
| `GET /pantilt` | Position, target, independent pulse limits |
| `POST /pantilt` with `ARM`, `HOME`, or `pan,tilt` | Authorization, HOME, or ramped target |
| `GET /laser`; `POST /laser` with `ON` or `OFF` | Relay LED state/control; no laser emitter |
| `POST /hb`; `POST /stop` | 1500 ms watchdog refresh; emergency stop |

Keep heartbeat at 400–500 ms while outputs are authorized. Boot and Thread detach leave outputs OFF. ARM needs attached Thread and fresh heartbeat; HOME must finish before a target or relay ON. Targets outside the stated pulse ranges are rejected. OFF and STOP remain available when the heartbeat is stale. The servo advances by at most 20 µs per 20 ms tick. STOP does not move to HOME and returns the PWM pins LOW.

## Build and checks

From the repository root, run `powershell -ExecutionPolicy Bypass -File firmware/node-a-integrated/test/run_host_tests.ps1`. After exporting ESP-IDF v5.4.4, run `idf.py -B ../../build-output/node-a-integrated-0.1.0 build` from this directory. The CoAP script in [test/node_a_coap_acceptance.sh](test/node_a_coap_acceptance.sh) is for the Raspberry Pi after physical checks; it was created but not run here. It requires an explicit `--allow-outputs` and either `--allow-extremes` or `--watchdog-only`. It always attempts OFF and STOP on exit.

Use app-only flashing at offset `0x10000` after measuring hardware; the partition-table binary matches the accepted Node A Auto-Start partition table. Do not erase NVS, or flash the generated bootloader or partition table during this update. The firmware does not contain a hardcoded Thread dataset or Network Key and does not form a new Thread network if NVS lacks an active dataset. `START` and `TLV:` provisioning remain available on USB serial when deliberately used.
