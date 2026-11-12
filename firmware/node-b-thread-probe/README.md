# Node B Thread/CoAP probe

This ESP32-H2 firmware is for **physical node B only**. It reuses the tested node A Thread and CoAP probe logic, but identifies itself as `B` with firmware version `b-probe-0.1.0`. It initializes no GPIO, ADC, LEDC, DHT11, servo, relay, laser, or pump driver. Keep all external loads separately unpowered. The future B pin map is DHT11 GPIO11, pan GPIO4, tilt GPIO5, active-HIGH relay GPIO10, and servo HOME 1500/1500 µs; none of these functions is implemented here.

The probe exposes CoAP UDP/5683 on Thread:

| Request | Response | Meaning |
| --- | --- | --- |
| `GET /info` | `2.05` JSON | `n: B`, firmware version, probe mode, outputs disabled |
| `GET /status` | `2.05` JSON | `n: B`, Thread role, heartbeat age/freshness, stop count; `stopped` true |
| `POST /hb` | `2.04` | Records a heartbeat; empty body is allowed |
| `POST /stop` | `2.04` | Increments stop count; no physical output is present |

Other methods return `4.05`. `/stop` is only a diagnostic state change, not an actuator safety mechanism. This probe does not implement sensor sampling, `/aim`, `/fire`, telemetry, or an actuator watchdog.

## Build and unit test on Windows

Activate ESP-IDF v5.4.4 from `D:\esp\v5.4.4\esp-idf` using the Python 3.11 venv under `D:\Espressif`, as described in `reports/nt532_rcp_flash_2026-11-11.md`. From this directory:

```powershell
idf.py --no-ccache set-target esp32h2
idf.py --no-ccache build

# Host unit test: put MSYS2 UCRT64 first only in this PowerShell process.
$oldPath = $env:PATH
$env:PATH = 'C:\msys64\ucrt64\bin;C:\Windows\System32;C:\Windows'
gcc -std=c11 -Wall -Wextra -Werror -Imain main/probe_state.c test/test_probe_state.c -o build/test_probe_state.exe
& build/test_probe_state.exe
$env:PATH = $oldPath
```

The root `.gitignore` excludes this project's `build/`, `sdkconfig`, and `dependencies.lock`. Do not copy node A's `sdkconfig`, `build/`, NVS, flash image, or Thread dataset into this project. This firmware uses B's own hardware identity and the `nvs` partition on B. The source and `sdkconfig.defaults` contain no real network credential.

## Flash gate — not part of CP6 preparation

**Do not run these steps until the operator separately approves flashing B.** Confirm the current Windows COM port by PnP and by B's MAC `48:31:b7:ff:fe:cd:51:d5`; COM18 was its port at backup time and may change. Verify `backups/serial-2026-11-11/B_COM18_full_flash.bin` remains available with SHA-256 `4B181EA6A619105C223A2650260161A05B5AC36FC188877ED6F2406A2EB3F36A`. Keep B's loads unpowered. Never select A's COM19 or the RCP COM5.

After approval, from this directory in an ESP-IDF PowerShell session, substitute the newly confirmed B port:

```powershell
idf.py --no-ccache -p COM18 flash
```

Do not use `erase-flash`. The flash command writes the B probe image; it is not a test command and was not executed during preparation.

## Provision on the existing Thread network

The probe does not create a network or auto-start Thread. On B's USB Serial/JTAG console it accepts `TLV:<active-dataset-hex>` or `START`, terminated by CR, LF, or CRLF. The input is not logged. A `TLV:` command sets the **existing Pi OTBR active dataset** on B and starts Thread. `START` only starts a dataset already commissioned on B; use it after a successful provisioning, not as proof that B contains the correct network. The dataset stays in B's own OpenThread NVS storage; no A flash/NVS data is copied. The 520-byte input buffer accommodates the maximum 254-byte OpenThread TLV (4 prefix + 508 hex + terminator + NUL).

For first provisioning after the approved flash, temporarily attach **B's** USB Serial/JTAG to the Pi while keeping the CH343 RCP connected. Identify B's by-id device (Espressif VID 303A/PID 1001) before writing. Check non-secret OTBR state, then send the current active dataset directly from the Pi shell to B without displaying or saving it:

```sh
sudo ot-ctl state
sudo ot-ctl networkname
sudo ot-ctl channel
ls -l /dev/serial/by-id/
sudo sh -c 'tlv=$(ot-ctl dataset active -x | sed -n "1p"); [ ${#tlv} -gt 0 ] || exit 1; printf "TLV:%s\n" "$tlv" > /dev/serial/by-id/<B-USB-serial-JTAG>'
```

Replace the by-id placeholder only after identifying B. Do not paste the dataset into the command line, chat, source, report, or logs. Do not capture a serial monitor transcript while sending it. A successful join reports a Thread role and a mesh-local EID, but never the key. On a later reboot, send `START` to join B's stored dataset. Do not run `dataset init new`, `factoryreset`, or any flash erase command.

Once B has joined, run these checks from the Pi using **B's** EID:

```sh
NODE_B_EID='fd..:..'
coap-client -m get "coap://[$NODE_B_EID]/info"
coap-client -m post -N -e '' "coap://[$NODE_B_EID]/hb"
coap-client -m get "coap://[$NODE_B_EID]/status"
coap-client -m post -e '' "coap://[$NODE_B_EID]/stop"
```

Confirm `/info` reports `n=B`, `fw=b-probe-0.1.0`; `/status` reports `n=B`, the actual Thread role, and `stopped=true`. `/hb` and `/stop` return `2.04`. Distinguish B from A by their physical MACs and mesh-local EIDs. These runtime tests have **not** been performed in the preparation phase.

## Rollback plan — instructions only

If a later approved flash must be reversed, identify the physical B board again by MAC/PnP, keep loads unpowered, verify the backup hash, and obtain separate approval before restoring its **own** full 4 MB image. Restoring overwrites the probe, NVS, and any data written after backup. Use the current B COM port in place of COM18 if it changed:

```powershell
$py = 'D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe'
Get-FileHash 'D:\IOT Hien Dai\Project\Project-NT532.R12\backups\serial-2026-11-11\B_COM18_full_flash.bin' -Algorithm SHA256
& $py -m esptool --chip esp32h2 --port COM18 write_flash --flash_freq keep --flash_mode keep --flash_size keep 0x0 'D:\IOT Hien Dai\Project\Project-NT532.R12\backups\serial-2026-11-11\B_COM18_full_flash.bin'
```

The rollback command is documented for future use; it is not run during CP6. A full flash image does not include eFuses or source sketches.
