# Fire Incident Detection and Localization Using Thread and Edge AI

NT532 uses a Raspberry Pi 5 for vision processing and two ESP32-H2 nodes connected through OTBR, Thread, and CoAP. The demonstration uses a fire target displayed on a laptop to evaluate detection, localization, and aiming; it does not suppress a real fire. The current integrated Node A/B firmware controls pan–tilt servos and a relay/LED demonstration output, not a pump.

## Project Overview

An Android camera streams video through MediaMTX to the Pi. The Pi localizes the target, calculates pan–tilt angles, and sends commands over CoAP/Thread. The presentation covers Node A as the primary node and Node B as the backup. A dashboard supports the local/LAN demonstration.

## System Architecture

![NT532 architecture: Android camera, Raspberry Pi 5, Thread, and two ESP32-H2 nodes](docs/picture%20and%20video/nt532_system_architecture.png)


## Hardware Design & GPIO Mapping

The tables below show **signal mappings in the integrated firmware source** for ESP32-H2 Super Mini boards. They do not establish that every physical wire and voltage level has been verified.

### Node A — Primary Node

| Component | Signal | GPIO | Firmware status |
| --- | --- | --- | --- |
| Pan servo | PWM | GPIO4 | Pan control |
| Tilt servo | PWM | GPIO5 | Tilt control |
| Relay | IN | GPIO10 | Active-HIGH; drives the demonstration LED output |
| MQ-2 | AOUT / ADC | GPIO1 | ADC1_CH0 through a suitable voltage divider |
| MQ-2 | DOUT | GPIO12 | Declared but **disabled** (`MQ2_DO_ENABLED = 0`) |

![Node A component and signal wiring diagram](docs/picture%20and%20video/nodeA.png)


### Node B — Backup Node

| Component | Signal | GPIO | Firmware status |
| --- | --- | --- | --- |
| Pan servo | PWM | GPIO4 | Pan control |
| Tilt servo | PWM | GPIO5 | Tilt control |
| Relay | IN | GPIO10 | Active-HIGH; drives the demonstration LED output |
| DHT11 | DATA | GPIO11 | Sensor data |

![Node B component and signal wiring diagram](docs/picture%20and%20video/nodeB.png)




## Core Technologies & Features

- **Edge AI and localization:** YOLO26n detects the target; ChArUco calibrates the camera; AprilTag and Dynamic Mapping support localization.
- **Control:** The Pi calculates pan–tilt angles and communicates with both nodes through OTBR/RCP, Thread, and CoAP.
- **Demonstration:** Node A/B drive servos and a relay/LED demonstration output while the fire target appears on a laptop screen.

## Performance & Experimental Results

The two charts below provide the experimental results: **11 runs were recorded, and 9 with complete timestamps were used for the averages**. Two runs used an older log format with missing timestamps and were excluded. The mean duration of the 9 valid runs was **74.15 seconds**.

| Phase | Mean duration | Share |
| --- | ---: | ---: |
| LOCALIZE | 7.83 s | 10.6% |
| AIM | 26.17 s | 35.3% |
| FIRE | 39.77 s | 53.6% |
| VERIFY | 0.37 s | 0.5% |
| **Total** | **74.15 s** | **100%** |

The intervals run from alarm to DECIDE; DECIDE to target hit, including scanning and adjustment; target hit to the log event `đã dập`; and that event to run completion. FIRE, `đã dập`, and VERIFY are log labels for a laptop-displayed fire target, not evidence of real fire suppression.

![Mean phase durations across 9 runs with complete timestamps](docs/picture%20and%20video/nt532-average-phase-durations.png)

![Timelines for 6 representative runs](docs/picture%20and%20video/nt532-representative-trial-timelines.png)

[Detailed results and media](docs/README.md#experimental-results--media)

## Technology Stack & Repository Structure

| Component | Role | Location |
| --- | --- | --- |
| Raspberry Pi 5: Python, YOLO, MediaMTX, OTBR/CoAP | Vision, localization, orchestration, and dashboard | `pi/src/nt532/` |
| ESP32-H2: ESP-IDF, Thread | Integrated Node A/B firmware and RCP | `firmware/node-a-integrated/`, `firmware/node-b-integrated/`, `firmware/rcp/` |
| Configuration and calibration | Table geometry, limits, and calibration results | `config/site.yaml`, `calibration/` |
| Documentation | Protocols, results, and procedures | `docs/`, `firmware/README.md` |

## Demo

[Browse the published images](docs/picture%20and%20video/). The demonstration uses physical hardware assemblies and a fire target displayed on a laptop. Videos remain local and are excluded from GitHub.

## Current Status & Limitations

The [E2E runbook](docs/nt532-e2e-runbook.md) records a passing software E2E test with simulated nodes. Checks of individual hardware assemblies do not establish full physical E2E verification. The GPIO tables reflect current source code; wiring and voltage levels still require measurement before operation.

## Documentation & Future Work

The [documentation index](docs/README.md) links to firmware, localization, experiments, and the runbook. Jev2 is future work and was not part of the workflow that produced the results above.
