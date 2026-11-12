# NT532 CP5.0.2 audit and node A Thread/CoAP probe

Date: 2026-11-11 (Asia/Saigon). Scope: read-only audit of the existing boards and an isolated host build. **No COM port was opened, no flash was written, and no Thread dataset was collected in this task.**

## Result matrix

| Check | Status | Evidence / limit |
| --- | --- | --- |
| CP5-A full-flash backup | **PASS (retrospective)** | COM19 `flash_id` says 4 MB; read log says 4,194,304 bytes at 0x0; verify log says 0x400000 bytes and `verify OK (digest matched)`; file size and SHA-256 independently checked below. |
| CP5-B full-flash backup | **PASS (retrospective)** | Same evidence on COM18, with B's distinct MAC and file. |
| Reported esptool exit code 0 | **REPORTED** | Original Markdown report records exit 0 for each operation; separate esptool logs do not include shell exit codes. No new live `verify_flash` was run. |
| Existing `sensor-h2` pinout compatibility | **FAIL (static audit)** | A's GPIO12 MQ-2 DO conflicts with configured pump output, and GPIO10 relay conflicts with SHT31 I2C SDA. B's GPIO10 relay and GPIO11 DHT11 also conflict with SHT31 I2C. |
| Node A probe host tests | **PASS** | GCC 15.2.0 compiled `test_probe_state.c` with `-Wall -Wextra -Werror`; executable exit 0. Tests cover method table, heartbeat age, stop latch, and malformed TLV rejection. |
| Node A probe ESP-IDF build | **PASS** | ESP-IDF v5.4.4, `esp32h2`, `idf.py --no-ccache build` exit 0; application binary 746,528 bytes (`0xB6420`), 29% free in 1 MiB app partition. Build ran in an isolated temporary directory. |
| Node A physical pin/electrical check | **NOT TESTED** | No independent wiring or MQ-2 voltage measurements. User-provided pinout is treated as the target map, not as measured evidence. |
| Node A flash / Thread join / CoAP runtime | **NOT TESTED** | No flash approval or load isolation confirmation for this phase; no real network packet test. |
| Node B Thread/CoAP | **NOT STARTED** | Deliberately deferred until A succeeds. |

Final node A application binary SHA-256: `68FA0529D762A112ADE651893AEA4FFEDA41A8F81BE2387A15CAE324BCE5ABA0`. Version: `a-probe-0.1.0`. The build output stayed outside the repository; rebuild from `firmware/node-a-thread-probe` before any approved flash.

## Independent backup checks

| Board | Port and chip from logs | Size | File SHA-256 (recomputed) |
| --- | --- | ---: | --- |
| A | COM19, ESP32-H2FH4, MAC `48:31:b7:ff:fe:cd:5b:ce` | 4,194,304 | `4DFAE7C5F0FDA19B6DC37E3F4A5FC3BE586B105BC682D283ADA6B1CC6A1F1E21` |
| B | COM18, ESP32-H2FH4, MAC `48:31:b7:ff:fe:cd:51:d5` | 4,194,304 | `4B181EA6A619105C223A2650260161A05B5AC36FC188877ED6F2406A2EB3F36A` |

`git check-ignore -v` points both `.bin` files at root `.gitignore:26` (`backups/`). The repo was already dirty before this probe: `.gitignore`, calibration files, `sensor-h2` tests and servo map, `build-output/`, and reports had changes or untracked content. No existing change was reset, staged, committed, or pushed. The Serial A/B `*.ino`/`*.pde` sketches are absent; full flash is a recovery image, not source or eFuse backup.

`reports/nt532_rcp_flash_2026-11-11.md` and its logs show a historical RCP flash on COM5 with three `Hash of data verified` messages. The currently reported Pi 5 OTBR `leader`, SRP running, `OpenThread-d17a` channel 13, and UART 460800 are operator information, not independently checked here. **COM5 was not touched.**

## Pin and failsafe audit

| Signal | Physical mapping supplied for A | Existing `sensor-h2` default | Decision for probe |
| --- | --- | --- | --- |
| MQ-2 AO | GPIO1 (ADC) | GPIO1 | Do not sample until voltage after the divider is measured. |
| MQ-2 DO | GPIO12 | GPIO12 = pump output | **Conflict:** never run the old default firmware on A. DO voltage and direction must be checked. |
| Relay | GPIO10, active HIGH | GPIO10 = SHT31 I2C SDA | **Conflict:** I2C transitions could energize a powered relay. Keep relay supply disconnected. |
| Servo pan / tilt | GPIO4 / GPIO5 | GPIO4 / GPIO5 | Pins align, but the old code emits PWM at startup. Probe does not enable PWM. |
| Laser / pump | External loads disconnected | GPIO14 / GPIO12 outputs | Probe has no laser or pump driver. |

B is also incompatible with `sensor-h2` defaults: its DHT11 is GPIO11, while the old code uses GPIO11 as SHT31 I2C SCL; its relay is GPIO10, while the old code uses GPIO10 as I2C SDA. B remains out of scope. The supplied servo limits for both boards are pan 500–2500 µs, tilt 600–2500 µs, and home 1500 µs. The old firmware uses a shared 500–2500 µs map and a fallback to that shared range, so it cannot be treated as calibrated for tilt.

ESP32-H2 documentation confirms GPIO1 is ADC1_CH0; GPIO4 and GPIO5 can serve digital peripheral outputs, and H2 provides LED PWM. That does **not** confirm this Super Mini board's soldered wiring or safe servo mechanics. The MQ-2 module may be supplied at 5 V. Measure **both AO after its divider and DO** at the H2-side connector with a multimeter while disconnected from H2, including warm-up and alarm states. Keep both signals within the H2's 3.3 V supply domain before connecting; attenuation is measurement scaling, not overvoltage protection. Verify common ground and a separate servo supply before any later motion test.

The existing actuator code has a 1500 ms heartbeat timeout and `/stop` off path for its configured pump/laser pins, but no explicit application task watchdog was found. With the physical pin conflicts, that logic is not sufficient for A. A production relay design needs a hardware default-off pull-down, startup-safe pin handling, a bounded activation timer, heartbeat/link loss shutdown, and a task watchdog tested under fault injection. The probe never drives an output, and its `/stop` only records a diagnostic latch; it does not claim production failsafe PASS.

## Safe next gate and commands

Before **any future A-only flash**, the operator must confirm physical A by MAC/PnP, verify every relay/laser/pump/servo load has its own supply disconnected, measure MQ-2 AO and DO, and keep the A backup available with the SHA-256 above. Confirm a rollback test plan: identify A again, hash the backup, restore the whole 4 MiB image only if needed, then verify it. Do not run this rollback command now:

```powershell
$py = 'D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe'
Get-FileHash 'D:\IOT Hien Dai\Project\Project-NT532.R12\backups\serial-2026-11-11\A_COM19_full_flash.bin' -Algorithm SHA256
& $py -m esptool --chip esp32h2 --port COM19 write_flash --flash_freq keep --flash_mode keep --flash_size keep 0x0 'D:\IOT Hien Dai\Project\Project-NT532.R12\backups\serial-2026-11-11\A_COM19_full_flash.bin'
```

The COM number can change after reconnection; use MAC/PnP to choose the current A port. Restoring 4 MiB overwrites the probe, NVS, and any subsequent flash data. The original backup report contains the complete rollback context.

On the Pi, check non-secret network state before provisioning A:

```sh
sudo ot-ctl state
sudo ot-ctl networkname
sudo ot-ctl channel
sudo ot-ctl srp server state
```

For the active dataset transfer, use the transient on-Pi procedure in `firmware/node-a-thread-probe/README.md`; it does not print or store the TLV outside the Pi and board. Do not paste the TLV/key into Git, a log, or this chat. After A joins, run the four CoAP checks in that README with all external loads still unpowered. Record actual role, EID, response codes and output; only then decide whether B or a hardware-enabled firmware is appropriate.

## Sources

- [ESP-IDF v5.4.4 ESP32-H2 standard I/O](https://docs.espressif.com/projects/esp-idf/en/v5.4.4/esp32h2/api-guides/stdio.html): USB Serial/JTAG stdin is available and nonblocking.
- [ESP-IDF v5.4 ESP32-H2 GPIO table](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/gpio.html): GPIO1 ADC1_CH0 and GPIO restrictions.
- [ESP-IDF v5.4 ESP32-H2 ADC oneshot](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/adc_oneshot.html): ADC readings and attenuation.
- [ESP32-H2 datasheet](https://documentation.espressif.com/esp32-h2_datasheet_en.html): electrical limits and LED PWM capability.
- [OpenThread operational dataset API](https://openthread.io/reference/group/api-operational-dataset): commissioned dataset and active TLV behavior.
