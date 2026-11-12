# Node A Thread/CoAP probe

This is an isolated ESP32-H2 diagnostic firmware for **physical node A only**. It does not reuse or overwrite `firmware/sensor-h2`, and it does not drive GPIO, ADC, LEDC, servo, relay, laser, or pump. Keep every external load unpowered throughout the first hardware test.

It exposes CoAP UDP/5683 on Thread:

| Request | Response | Meaning |
| --- | --- | --- |
| `GET /info` | `2.05` JSON | Node A, firmware version, probe mode, outputs disabled |
| `POST /hb` | `2.04` | Records the last heartbeat time; accepts an empty body |
| `GET /status` | `2.05` JSON | Thread role, heartbeat age/freshness, stop count; `stopped` always true |
| `POST /stop` | `2.04` | Increments stop count; no physical output exists to stop |

Other methods receive `4.05`. There is no `/aim`, `/fire`, sensor sampling, telemetry, SRP registration, or production actuator watchdog. This firmware is a network probe, not a drop-in replacement for the existing orchestrator.

## Build on Windows

Use ESP-IDF v5.4.4 in `D:\esp\v5.4.4\esp-idf` and the Python 3.11 venv in `D:\Espressif`. In a fresh PowerShell, activate IDF as described in `reports/nt532_rcp_flash_2026-11-11.md`. From this project directory:

```powershell
idf.py --no-ccache set-target esp32h2
idf.py --no-ccache build
```

`sdkconfig`, `build/`, and any real Thread dataset must stay out of Git. The root `.gitignore` already excludes the first two under `firmware/*/`. No dataset belongs in menuconfig, source, logs, or the build command. The `sdkconfig.defaults` here contains no network credential.

## First Thread join, after a separate flash approval

The probe does not create or start a Thread network at boot. It waits on the USB Serial/JTAG console for either `START` (use an already commissioned dataset stored on the device) or `TLV:<hex>` (set the Pi's **existing** active dataset, then start). The firmware never prints the TLV. A successful join prints `Thread role: child` or `router` and a mesh-local EID. Do not record the serial provisioning input or capture a monitor transcript while sending it.

To avoid saving the key on Windows, attach A's USB Serial/JTAG port temporarily to the Pi for provisioning. Confirm the by-id entry belongs to **A** (Espressif VID 303A/PID 1001, not the CH343 RCP), and keep the RCP connected. Run the following *on the Pi*, replacing the by-id path after inspection. The TLV is held in shell memory and sent directly to A; it is not printed or written to a file:

```sh
ls -l /dev/serial/by-id/
sudo sh -c 'tlv=$(ot-ctl dataset active -x | sed -n "1p"); [ ${#tlv} -gt 0 ] || exit 1; printf "TLV:%s\n" "$tlv" > /dev/serial/by-id/<A-USB-serial-JTAG>'
```

Only run that command after checking the serial path and the reported OTBR network/channel. Never use `dataset init new`, `factoryreset`, or `erase-flash` for this probe. On reboot, send `START` to rejoin the stored dataset; the firmware does not auto-join.

After the board reports its mesh-local EID, run on the Pi (substitute `NODE_EID` from A's console):

```sh
NODE_EID='fd..:..'
coap-client -m get "coap://[$NODE_EID]/info"
coap-client -m post -N -e '' "coap://[$NODE_EID]/hb"
coap-client -m get "coap://[$NODE_EID]/status"
coap-client -m post -e '' "coap://[$NODE_EID]/stop"
```

These are future operator tests. This task does not flash either node or touch COM5.
