"""Fail-closed CoAP client for one real integrated A/B node.

This client never addresses the legacy /aim or /fire endpoints. GPIO10 remains
the firmware's relay/LED simulation; a physical laser spot needs separate proof.
"""

import asyncio
import ipaddress
import json
import math
import threading
import time
from dataclasses import dataclass

IDENTITY = {"s1": ("A", "a-integrated-0.1.2"), "s2": ("B", "b-integrated-0.2.5")}
LIMITS = (500, 2500, 600, 2500)
HOME = (1500, 1700)


@dataclass(frozen=True)
class PulseCalibration:
    """Measured signed pulse gain for physical command degrees relative to HOME."""

    pan_home_us: int
    tilt_home_us: int
    pan_us_per_deg: float
    tilt_us_per_deg: float

    def __post_init__(self):
        if ((self.pan_home_us, self.tilt_home_us) != HOME
                or not all(math.isfinite(v) and v != 0 for v in
                           (self.pan_us_per_deg, self.tilt_us_per_deg))):
            raise ValueError("CALIBRATION_REQUIRED: measured signed pulse gains and HOME required")

    def pulses(self, pan_deg: float, tilt_deg: float) -> tuple[int, int]:
        if not all(math.isfinite(v) for v in (pan_deg, tilt_deg)):
            raise ValueError("non-finite angle")
        pulses = (round(self.pan_home_us + pan_deg * self.pan_us_per_deg),
                  round(self.tilt_home_us + tilt_deg * self.tilt_us_per_deg))
        if not (LIMITS[0] <= pulses[0] <= LIMITS[1]
                and LIMITS[2] <= pulses[1] <= LIMITS[3]):
            raise ValueError("pulse outside firmware limits")
        return pulses


class CoapTransport:
    """Persistent aiocoap context; requests have a bounded response wait."""

    def __init__(self, address: str, timeout_s: float = 1.0):
        from aiocoap import Context

        self._context_type = Context
        try:
            if ipaddress.ip_address(address.split("%", 1)[0]).version == 6:
                address = f"[{address}]"
        except ValueError:
            pass
        self.base = f"coap://{address}"
        self.timeout = timeout_s
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="integrated-coap")
        self.thread.start()
        self.ctx = asyncio.run_coroutine_threadsafe(
            self._context_type.create_client_context(), self.loop).result(5)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _request(self, method: str, path: str, payload: str | None):
        from aiocoap import GET, POST, Message

        req = Message(code=GET if method == "GET" else POST,
                      uri=f"{self.base}/{path}", payload=(payload or "").encode())
        response = await asyncio.wait_for(self.ctx.request(req).response, self.timeout)
        if not response.code.is_successful():
            raise RuntimeError(f"{method} /{path}: {response.code}")
        if method == "GET":
            return json.loads(response.payload)
        return {}

    def __call__(self, method: str, path: str, payload: str | None = None) -> dict:
        future = asyncio.run_coroutine_threadsafe(self._request(method, path, payload), self.loop)
        try:
            return future.result(self.timeout + 0.5)
        except Exception:
            future.cancel()
            raise

    def close(self):
        try:
            asyncio.run_coroutine_threadsafe(self.ctx.shutdown(), self.loop).result(5)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2)


class IntegratedNode:
    """One-node transaction: identity, heartbeat, HOME, target, relay, OFF/STOP."""

    def __init__(self, node: str, request, timeout_s: float = 5.0):
        if node not in IDENTITY:
            raise ValueError(f"unknown node {node}")
        self.node, self.request, self.timeout_s = node, request, timeout_s
        self.info = None
        self._ready = False
        self._halt = threading.Event()
        self._hb_error = None
        self._hb_thread = None
        self.last_shutdown = None
        self._target_pulses = None

    def verify_identity(self) -> dict:
        info = self.request("GET", "info")
        expected_name, expected_fw = IDENTITY[self.node]
        if (info.get("n"), info.get("fw"), info.get("mode")) != (
                expected_name, expected_fw, "integrated"):
            raise ValueError(f"identity mismatch for {self.node}: expected {expected_name}/{expected_fw}")
        if "relay_led_simulation" not in info.get("outputs", ""):
            raise ValueError("unexpected relay output contract")
        self.info = info
        return info

    def _heartbeat_loop(self):
        while not self._halt.wait(0.4):
            try:
                self.request("POST", "hb")
            except Exception as exc:  # noqa: BLE001 - any transport error must abort relay hold
                self._hb_error = exc
                self._halt.set()

    def _check_hb(self):
        if self._hb_error is not None:
            raise RuntimeError(f"heartbeat lost: {self._hb_error}")

    def arm_home(self) -> dict:
        if self.info is None:
            self.verify_identity()
        self.request("POST", "hb")
        self._halt.clear()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True,
                                           name=f"hb-{self.node}")
        self._hb_thread.start()
        self.request("POST", "pantilt", "ARM")
        self.request("POST", "pantilt", "HOME")
        actual = self._poll(*HOME)
        self._ready = True
        return actual

    def _poll(self, pan_us: int, tilt_us: int) -> dict:
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            self._check_hb()
            status = self.request("GET", "pantilt")
            if (status.get("armed") is True and status.get("pwm_active") is True
                    and (status.get("pan_us"), status.get("tilt_us")) == (pan_us, tilt_us)
                    and (status.get("target_pan_us"), status.get("target_tilt_us")) ==
                    (pan_us, tilt_us)
                    and tuple(status.get(k) for k in ("pan_min_us", "pan_max_us",
                                                      "tilt_min_us", "tilt_max_us")) == LIMITS):
                return status
            time.sleep(0.05)
        raise TimeoutError(f"GET /pantilt did not reach {pan_us},{tilt_us}")

    def move(self, pan_us: int, tilt_us: int) -> dict:
        if not all(isinstance(v, int) for v in (pan_us, tilt_us)) or not (
                LIMITS[0] <= pan_us <= LIMITS[1] and LIMITS[2] <= tilt_us <= LIMITS[3]):
            raise ValueError("pulse outside firmware limits")
        if not self._ready:
            raise RuntimeError("ARM/HOME required before target")
        self._check_hb()
        self.request("POST", "pantilt", f"{pan_us},{tilt_us}")
        actual = self._poll(pan_us, tilt_us)
        self._target_pulses = (pan_us, tilt_us)
        return actual

    def aim(self, pan_us: int, tilt_us: int) -> dict:
        return self.move(pan_us, tilt_us)

    def fire(self, dev: str):
        raise ValueError(f"{dev} rejected: pump/fire is disabled in manual mode")

    def laser_on(self):
        if not self._ready or self._target_pulses is None:
            raise RuntimeError("reached target required before relay ON")
        self._check_hb()
        pulse_status = self.request("GET", "pantilt")
        if (pulse_status.get("armed") is not True or pulse_status.get("pwm_active") is not True
                or (pulse_status.get("pan_us"), pulse_status.get("tilt_us")) != self._target_pulses
                or (pulse_status.get("target_pan_us"), pulse_status.get("target_tilt_us")) !=
                self._target_pulses):
            raise RuntimeError("target moved or STOP before relay ON")
        laser = self.request("GET", "laser")
        if (laser.get("available") is not True or laser.get("mode") != "relay_led_simulation"
                or laser.get("on") is not False):
            raise RuntimeError("unexpected /laser state")
        self.request("POST", "laser", "ON")
        if self.request("GET", "laser").get("on") is not True:
            raise RuntimeError("relay did not report ON")

    def hold(self, seconds: float, tick=None):
        if not 0 < seconds <= 120:
            raise ValueError("hold-seconds must be in (0,120]")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check_hb()
            status = self.request("GET", "pantilt")
            laser = self.request("GET", "laser")
            if (status.get("armed") is not True or status.get("pwm_active") is not True
                    or laser.get("on") is not True):
                raise RuntimeError("STOP or relay OFF during hold")
            if tick:
                tick(max(0.0, deadline - time.monotonic()))
            self._halt.wait(min(0.25, max(0.0, deadline - time.monotonic())))
        self._check_hb()

    def shutdown(self) -> dict:
        errors = []
        for method, path, payload in (("POST", "laser", "OFF"), ("POST", "stop", None)):
            try:
                self.request(method, path, payload)
            except Exception as exc:  # noqa: BLE001 - continue best-effort OFF then STOP
                errors.append(f"{path}: {exc}")
        self._halt.set()
        if self._hb_thread:
            self._hb_thread.join(timeout=2)
        self._ready = False
        self._target_pulses = None
        final = {}
        for path in ("laser", "status"):
            try:
                final[path] = self.request("GET", path)
            except Exception as exc:  # noqa: BLE001 - report unverified final state
                errors.append(f"GET {path}: {exc}")
        if (final.get("laser", {}).get("on") is not False
                or final.get("status", {}).get("relay_on") is not False
                or final.get("status", {}).get("servo_pwm") is not False
                or final.get("status", {}).get("stopped") is not True):
            errors.append("OFF/STOP state not verified")
        self.last_shutdown = {"final": final, "errors": errors}
        return self.last_shutdown
