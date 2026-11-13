"""Adapter phía Pi: dịch hợp đồng cũ của orchestrator (`/aim /fire /stop /hb`, JSON, nt532.net.protocol)
sang hợp đồng của firmware node B tích hợp (`/pantilt /laser /stop /hb /info /status`, payload text).

Orchestrator vẫn nói `aim(node, pan_deg, tilt_deg, ttl)`, `fire(node, id, dev, ms)`; adapter đổi:

- aim: độ -> µs bằng `PulseMap` (TẠM THỜI, xem integrated_node.py), rồi ARM (nếu chưa armed), HOME (nếu chưa
  có PWM, đợi `GET /pantilt` báo pwm_active), rồi "pan_us,tilt_us". Firmware không báo "đã tới": adapter
  poll `GET /pantilt` mỗi 50 ms tới khi xung hiện tại bằng đích, rồi báo `reached`.
- fire laser: `POST /laser ON`, sau `ms` thì `OFF`, báo `done`. Relay của firmware chỉ mô phỏng laser.
- fire pump: bị từ chối ngay (`rejected`, err `no_pump`), không gửi gì: firmware của Hậu không có bơm.
- stop: `POST /stop`. heartbeat: `POST /hb` như CoapLink.
- info: `GET /info`, giới hạn góc suy ra từ PulseMap.

Kế thừa CoapLink để giữ server `/t /a /status` phía Pi và cách gửi/nhận aiocoap; chỉ ghi đè gửi lệnh.
"""

import asyncio
import json
import threading
import time

from aiocoap import GET, POST, Message
from aiocoap.numbers.codes import BAD_REQUEST, FORBIDDEN

from .coap import CoapLink
from .integrated_node import PulseMap
from .link import LinkError
from .protocol import Aim, Fire, Info, Status, Stop

POLL_S = 0.05  # chu kỳ poll GET /pantilt khi chờ tới đích
PWM_WAIT_S = 1.0  # chờ PWM bật sau HOME
HB_TIMEOUT_MS = 1500  # CONTROL_HB_TIMEOUT_MS của firmware
ERR_BY_CODE = {FORBIDDEN: "forbidden", BAD_REQUEST: "bad_request"}


class IntegratedLink(CoapLink):
    """Như CoapLink nhưng nói hợp đồng của firmware node B tích hợp. `pulse_map` giống bên node."""

    def __init__(self, nodes: dict[str, str], bind: tuple[str, int] = ("::", 5683), timeout_s: float = 2.0,
                 transports: list[str] | None = None, pulse_map: PulseMap | None = None) -> None:
        super().__init__(nodes, bind, timeout_s, transports)
        self.map = pulse_map or PulseMap()
        self._armed = dict.fromkeys(nodes, False)  # theo GET /pantilt mới nhất
        self._pwm = dict.fromkeys(nodes, False)
        self._gen = {(n, k): 0 for n in nodes for k in ('aim', 'fire')}  # lệnh mới nhất; việc cũ tự huỷ
        self._gen_lock = threading.Lock()

    # --- gửi ------------------------------------------------------------------------------------

    def _send(self, node: str, path: str, msg) -> None:
        if path == "hb":
            return super()._send(node, path, msg)  # POST /hb NON, có trả lời thì on_rx
        if path == "aim":
            return self._aim(node, msg)
        if path == "fire":
            return self._fire(node, msg)
        if path == "stop":
            return self._stop(node, msg)
        raise LinkError(f"adapter không biết đường /{path}")

    def _call(self, node: str, method: int, path: str, payload: bytes = b"") -> Message:
        """Một request CON tới node, chờ trả lời; lỗi mạng thành LinkError."""
        req = Message(code=method, payload=payload, uri=f"coap://{self._host(node)}/{path}")
        fut = asyncio.run_coroutine_threadsafe(self._request(req, self.timeout), self._loop)
        try:
            resp = fut.result(self.timeout + 1)
        except Exception as e:  # noqa: BLE001 - mọi lỗi mạng đều thành LinkError cho orchestrator
            raise LinkError(f"{'GET' if method == GET else 'POST'} /{path} tới {node} lỗi: "
                            f"{type(e).__name__}: {e}") from None
        self.on_rx(node)
        return resp

    def _pantilt(self, node: str) -> dict:
        """`GET /pantilt`, cập nhật cờ armed/pwm đã nhớ."""
        resp = self._call(node, GET, "pantilt")
        if not resp.code.is_successful():
            raise LinkError(f"GET /pantilt từ {node} trả {resp.code}")
        try:
            st = json.loads(resp.payload)
        except ValueError as e:
            raise LinkError(f"/pantilt của {node} sai dạng: {e}") from None
        self._armed[node], self._pwm[node] = bool(st.get("armed")), bool(st.get("pwm_active"))
        return st

    def _new_gen(self, node: str, kind: str) -> int:
        with self._gen_lock:
            self._gen[node, kind] += 1
            return self._gen[node, kind]

    def _current(self, node: str, kind: str, gen: int) -> bool:
        with self._gen_lock:
            return self._gen[node, kind] == gen

    def _reject(self, node: str, cmd_id: int, err: str) -> None:
        self.on_status(node, Status(cmd_id, "rejected", err=err, n=node))

    # --- aim ------------------------------------------------------------------------------------

    def _aim(self, node: str, cmd: Aim) -> None:
        try:
            pan_us, tilt_us = self.map.to_us("pan", cmd.pan), self.map.to_us("tilt", cmd.tilt)
        except ValueError:
            return self._reject(node, cmd.id, "out_of_range")
        gen = self._new_gen(node, "aim")
        st = self._pantilt(node)  # trạng thái hiện tại: tránh ARM thừa và tin nhầm cờ cũ
        if not st.get("armed"):
            code = self._call(node, POST, "pantilt", b"ARM").code
            if not code.is_successful():
                return self._reject(node, cmd.id, ERR_BY_CODE.get(code, "node_error"))
            self._armed[node] = True
        if not self._pwm[node]:
            code = self._call(node, POST, "pantilt", b"HOME").code
            if not code.is_successful():
                return self._reject(node, cmd.id, ERR_BY_CODE.get(code, "node_error"))
            end = time.monotonic() + PWM_WAIT_S
            while not self._pwm[node] and time.monotonic() < end:
                time.sleep(POLL_S)
                self._pantilt(node)
            if not self._pwm[node]:
                return self.on_status(node, Status(cmd.id, "fault", err="pwm", n=node))
        code = self._call(node, POST, "pantilt", f"{pan_us},{tilt_us}".encode()).code
        if not code.is_successful():
            return self._reject(node, cmd.id, ERR_BY_CODE.get(code, "node_error"))
        self.on_status(node, Status(cmd.id, "accepted", n=node))
        threading.Thread(target=self._track, args=(node, cmd, pan_us, tilt_us, gen), daemon=True,
                         name=f"track-{node}").start()

    def _track(self, node: str, cmd: Aim, pan_us: int, tilt_us: int, gen: int) -> None:
        """Poll GET /pantilt tới khi xung bằng đích: reached; quá ttl: fault timeout."""
        end = time.monotonic() + cmd.ttl / 1000
        while True:
            if not self._current(node, "aim", gen):
                return self.on_status(node, Status(cmd.id, "fault", err="superseded", n=node))
            try:
                st = self._pantilt(node)
            except LinkError:
                st = None  # mất gói tạm thời: thử lại tới hết ttl
            if st is not None:
                if not st.get("armed") or not st.get("pwm_active"):
                    return self.on_status(node, Status(cmd.id, "fault", err="stopped", n=node))
                if st.get("pan_us") == pan_us and st.get("tilt_us") == tilt_us:
                    return self.on_status(node, Status(cmd.id, "reached", round(cmd.pan, 2),
                                                       round(cmd.tilt, 2), n=node))
            if time.monotonic() >= end:
                return self.on_status(node, Status(cmd.id, "fault", err="timeout", n=node))
            time.sleep(POLL_S)

    # --- fire -----------------------------------------------------------------------------------

    def _fire(self, node: str, cmd: Fire) -> None:
        if cmd.dev != "laser":  # firmware không có bơm, relay chỉ mô phỏng laser
            return self._reject(node, cmd.id, "no_pump")
        gen = self._new_gen(node, "fire")
        code = self._call(node, POST, "laser", b"ON").code
        if not code.is_successful():
            return self._reject(node, cmd.id, ERR_BY_CODE.get(code, "node_error"))
        self.on_status(node, Status(cmd.id, "accepted", n=node))
        threading.Thread(target=self._laser_off, args=(node, cmd, gen), daemon=True,
                         name=f"laser-{node}").start()

    def _laser_off(self, node: str, cmd: Fire, gen: int) -> None:
        end = time.monotonic() + cmd.ms / 1000
        while time.monotonic() < end and self._current(node, "fire", gen):
            time.sleep(0.01)
        stopped = not self._current(node, "fire", gen)
        for _ in range(3):  # OFF luôn được node nhận; thử lại nếu mất gói
            try:
                self._call(node, POST, "laser", b"OFF")
                break
            except LinkError:
                continue
        self.on_status(node, Status(cmd.id, "fault" if stopped else "done", err="stopped" if stopped else None,
                                    n=node))

    # --- stop -----------------------------------------------------------------------------------

    def _stop(self, node: str, cmd: Stop) -> None:
        for kind in ("aim", "fire"):  # huỷ việc theo dõi và hẹn tắt laser
            self._new_gen(node, kind)
        self._armed[node] = self._pwm[node] = False
        resp = self._call(node, POST, "stop")
        if not resp.code.is_successful():
            raise LinkError(f"POST /stop tới {node} trả {resp.code}")

    # --- info -----------------------------------------------------------------------------------

    def _get_info(self, node: str, timeout: float) -> Info:
        req = Message(code=GET, uri=f"coap://{self._host(node)}/info")
        fut = asyncio.run_coroutine_threadsafe(self._request(req, timeout), self._loop)
        try:
            resp = fut.result(timeout + 1)
        except Exception as e:  # noqa: BLE001
            raise LinkError(f"GET /info từ {node} lỗi: {type(e).__name__}: {e}") from None
        if not resp.code.is_successful():
            raise LinkError(f"GET /info từ {node} trả {resp.code}")
        try:
            o = json.loads(resp.payload)
            name, fw = str(o["n"]), str(o["fw"])
        except (ValueError, KeyError, TypeError) as e:
            raise LinkError(f"/info của {node} sai dạng: {e}") from None
        self.on_rx(node)
        pan, tilt = self.map.limits_deg()
        return Info(name, fw, pan, tilt, HB_TIMEOUT_MS, 0, False)

