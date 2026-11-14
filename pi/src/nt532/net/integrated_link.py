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

Telemetry: node thật không đẩy /t /a. Nếu `status_poll_s` được đặt, một luồng nền `GET /status` từng node mỗi
chu kỳ và đổi sang đúng `Telemetry`/`Alert` của CoapLink (xem `poll_once`); mỗi lần trả lời được tính là nghe
thấy node (`on_rx`) nên phát hiện mất node hoạt động như cũ. Mặc định tắt (sa bàn ảo: node giả tự đẩy /t).

Kế thừa CoapLink để giữ server `/t /a /status` phía Pi và cách gửi/nhận aiocoap; chỉ ghi đè gửi lệnh.
"""

import asyncio
import json
import math
import threading
import time

from aiocoap import GET, POST, Message
from aiocoap.numbers.codes import BAD_REQUEST, FORBIDDEN

from .coap import CoapLink
from .integrated_node import PulseMap
from .link import LinkError
from .protocol import Aim, Alert, Fire, Info, Status, Stop, Telemetry

POLL_S = 0.05  # chu kỳ poll GET /pantilt khi chờ tới đích
PWM_WAIT_S = 1.0  # chờ PWM bật sau HOME
RAMP_US, RAMP_MS = 20, 20  # firmware: servo đi tối đa 20 µs mỗi 20 ms (CONTROL ramp)
RAMP_MARGIN_S = 0.5  # dư cho độ trễ Thread khi poll /pantilt
STEP_S = 0.1  # khoảng giữa hai đích trung gian khi giới hạn tốc độ phía Pi
HB_TIMEOUT_MS = 1500  # CONTROL_HB_TIMEOUT_MS của firmware
ERR_BY_CODE = {FORBIDDEN: "forbidden", BAD_REQUEST: "bad_request"}


def _num(v) -> float | None:
    """Số hữu hạn hoặc None (bool, null, chuỗi, NaN không phải số đo)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


class IntegratedLink(CoapLink):
    """Như CoapLink nhưng nói hợp đồng của firmware node B tích hợp. `pulse_map` giống bên node."""

    def __init__(self, nodes: dict[str, str], bind: tuple[str, int] = ("::", 5683), timeout_s: float = 2.0,
                 transports: list[str] | None = None, pulse_map: PulseMap | None = None,
                 status_poll_s: float | None = None, pulse_maps: dict[str, PulseMap] | None = None,
                 servo_speed_us_s: float | None = None) -> None:
        super().__init__(nodes, bind, timeout_s, transports)
        self.map = pulse_map or PulseMap()  # mặc định cho node không có bảng riêng
        self.maps = dict(pulse_maps or {})  # bảng riêng từng node (chiều quay, home khác nhau giữa các cụm)
        # Giới hạn tốc độ phía Pi (µs/giây), chậm hơn ramp của firmware: gửi đích theo từng bước nhỏ để servo
        # đi êm, laser không giật, dòng không vọt làm sụt nguồn node. None: gửi thẳng đích như cũ.
        self.servo_speed_us_s = servo_speed_us_s if servo_speed_us_s and servo_speed_us_s > 0 else None
        self.status_poll_s = status_poll_s  # None/0: không poll /status
        self._poll_halt = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._seq = dict.fromkeys(nodes, 0)
        self._alarm = dict.fromkeys(nodes, False)  # mq2_alarm đã thấy lần trước, để chỉ báo ở sườn lên
        self._t0: float | None = None
        self._armed = dict.fromkeys(nodes, False)  # theo GET /pantilt mới nhất
        self._pwm = dict.fromkeys(nodes, False)
        self._gen = {(n, k): 0 for n in nodes for k in ('aim', 'fire')}  # lệnh mới nhất; việc cũ tự huỷ
        self._gen_lock = threading.Lock()

    # --- telemetry bằng poll /status ---------------------------------------------------------------

    def start(self) -> "IntegratedLink":
        super().start()
        if self.status_poll_s and self.status_poll_s > 0:
            self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="status-poll")
            self._poll_thread.start()
        return self

    def close(self) -> None:
        self._poll_halt.set()
        if self._poll_thread is not None:
            self._poll_thread.join(self.timeout + 2)
        super().close()

    def _poll_loop(self) -> None:
        """Mỗi node một luồng poll riêng (khởi trong lần đầu), để node mất liên lạc (mỗi GET chờ hết timeout)
        không làm trễ telemetry của node kia."""
        threads = [threading.Thread(target=self._poll_node, args=(n,), daemon=True, name=f"status-poll-{n}")
                   for n in self.nodes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _poll_node(self, node: str) -> None:
        while not self._poll_halt.wait(self.status_poll_s):
            try:
                self.poll_once(node)
            except Exception:  # noqa: BLE001, S110 - một lần lỗi không được dừng vòng poll
                pass

    @property
    def max_aim_s(self) -> float:
        """Thời gian dài nhất một lệnh ngắm có thể cần (chạy hết dải 2000 µs theo tốc độ chậm nhất), để bên gọi
        chờ `wait` đủ lâu khi có giới hạn tốc độ phía Pi."""
        speed = RAMP_US / (RAMP_MS / 1000)
        if self.servo_speed_us_s:
            speed = min(speed, self.servo_speed_us_s)
        return 2000 / speed + RAMP_MARGIN_S

    def poll_once(self, node: str) -> None:
        """`GET /status` một node rồi phát telemetry/cảnh báo. Không tới được node: bỏ qua (không on_rx, nên
        `hb_age_ms` tăng dần và Pi coi node mất kết nối như khi mất /t). Cảm biến lỗi hoặc thiếu trường: không
        phát mẫu (không bịa số), nhưng node vẫn được tính là còn sống vì đã trả lời.

        Ánh xạ theo trường có trong JSON (không theo tên node):
        - `mq2_adc_raw` (node A) -> `Telemetry.g` là ADC THÔ 0..4095, CHƯA hiệu chuẩn ra ppm (bàn giao 13/11:
          chưa có số đo); nhiệt độ để 0.0 vì node không có.
        - `temperature_c`, `humidity_pct` (node B, DHT11) -> `Telemetry.t`, `.h`; gas 0.0 vì node không có.
        - `mq2_alarm` true -> `Alert(k="alert")` một lần ở sườn lên; false sau true -> `Alert(k="clear")`;
          null (đang khởi động) giữ nguyên trạng thái.
        """
        try:
            resp = self._call(node, GET, "status")
            if not resp.code.is_successful():
                return
            o = json.loads(resp.payload)
        except (LinkError, ValueError):
            return
        if not isinstance(o, dict):
            return
        if self._t0 is None:
            self._t0 = time.monotonic()
        up = int(time.monotonic() - self._t0)
        self._seq[node] += 1
        s = self._seq[node]
        temp, hum, gas = _num(o.get("temperature_c")), _num(o.get("humidity_pct")), None
        if o.get("mq2_sensor_ok") is True:
            gas = _num(o.get("mq2_adc_raw"))
        if o.get("sensor_ok") is not True:
            temp = hum = None
        if self.on_telemetry and (temp is not None or gas is not None):
            self.on_telemetry(Telemetry(node, s, up, temp if temp is not None else 0.0,
                                        gas if gas is not None else 0.0, hum))
        alarm = o.get("mq2_alarm")
        if isinstance(alarm, bool) and alarm != self._alarm[node]:
            self._alarm[node] = alarm
            if self.on_alert:
                self.on_alert(Alert(node, s, "alert" if alarm else "clear", temp if temp is not None else 0.0,
                                    gas if gas is not None else 0.0))

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
            m = self.pulse_map(node)
            pan_us, tilt_us = m.to_us("pan", cmd.pan), m.to_us("tilt", cmd.tilt)
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
            st = self._pantilt(node)  # sau HOME: xung hiện tại để tính quãng phải chạy
        travel_s = self._travel_s(st, pan_us, tilt_us, self.servo_speed_us_s)
        steps = self._waypoints(st, pan_us, tilt_us, self.servo_speed_us_s)
        code = self._call(node, POST, "pantilt", f"{steps[0][0]},{steps[0][1]}".encode()).code
        if not code.is_successful():
            return self._reject(node, cmd.id, ERR_BY_CODE.get(code, "node_error"))
        self.on_status(node, Status(cmd.id, "accepted", n=node))
        threading.Thread(target=self._track, args=(node, cmd, pan_us, tilt_us, gen, travel_s, steps[1:]),
                         daemon=True, name=f"track-{node}").start()

    @staticmethod
    def _travel_s(st: dict | None, pan_us: int, tilt_us: int, speed_us_s: float | None = None) -> float:
        """Thời gian servo cần để tới đích: theo ramp của firmware (RAMP_US mỗi RAMP_MS) hoặc giới hạn tốc độ phía
        Pi nếu chậm hơn, cộng dư RAMP_MARGIN_S. Không biết xung hiện tại (vừa ARM) thì coi như chạy hết dải."""
        def dist(cur, dst):
            return abs(dst - cur) if isinstance(cur, (int, float)) else 2000
        st = st or {}
        us = max(dist(st.get("pan_us"), pan_us), dist(st.get("tilt_us"), tilt_us))
        speed = RAMP_US / (RAMP_MS / 1000)
        if speed_us_s:
            speed = min(speed, speed_us_s)
        return us / speed + RAMP_MARGIN_S

    @staticmethod
    def _waypoints(st: dict | None, pan_us: int, tilt_us: int, speed_us_s: float | None) -> list[tuple[int, int]]:
        """Các đích trung gian cách nhau STEP_S theo `speed_us_s`, kết thúc ở đích; không giới hạn hoặc không biết
        xung hiện tại thì chỉ một bước (gửi thẳng đích)."""
        st = st or {}
        cp, ct = st.get("pan_us"), st.get("tilt_us")
        if not speed_us_s or not isinstance(cp, (int, float)) or not isinstance(ct, (int, float)):
            return [(pan_us, tilt_us)]
        n = max(1, math.ceil(max(abs(pan_us - cp), abs(tilt_us - ct)) / (speed_us_s * STEP_S)))
        return [(round(cp + (pan_us - cp) * k / n), round(ct + (tilt_us - ct) * k / n)) for k in range(1, n + 1)]

    def _track(self, node: str, cmd: Aim, pan_us: int, tilt_us: int, gen: int, travel_s: float = 0.0,
               steps: list | None = None) -> None:
        """Gửi các đích trung gian còn lại (nếu có giới hạn tốc độ), rồi poll GET /pantilt tới khi xung bằng đích:
        reached; quá ttl + thời gian chạy: fault timeout. Firmware chỉ cho servo chạy RAMP_US mỗi RAMP_MS, nên quãng
        dài (1000 µs mất 1 s) vượt ttl thuần."""
        end = time.monotonic() + cmd.ttl / 1000 + travel_s
        for p, t in steps or ():
            time.sleep(STEP_S)
            if not self._current(node, "aim", gen):
                return self.on_status(node, Status(cmd.id, "fault", err="superseded", n=node))
            try:
                self._call(node, POST, "pantilt", f"{p},{t}".encode())
            except LinkError:
                pass  # mất một bước trung gian không sao, bước sau hoặc đích cuối vẫn tới
        if steps:
            try:  # bảo đảm đích cuối đã gửi dù bước cuối bị mất
                self._call(node, POST, "pantilt", f"{pan_us},{tilt_us}".encode())
            except LinkError:
                pass
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

    # --- điều khiển tay (trang "Điều khiển tay" của dashboard) --------------------------------------

    def _ok(self, resp, what: str) -> None:
        if not resp.code.is_successful():
            raise LinkError(f"{what} trả {resp.code}")

    def _laser_state(self, node: str) -> bool:
        resp = self._call(node, GET, "laser")
        self._ok(resp, f"GET /laser từ {node}")
        try:
            return bool(json.loads(resp.payload).get("on"))
        except (ValueError, AttributeError) as e:
            raise LinkError(f"/laser của {node} sai dạng: {e}") from None

    def manual(self, node: str, op: str, pan_us: int | None = None, tilt_us: int | None = None,
               on: bool | None = None) -> dict:
        """Điều khiển tay một node: status|mark|arm|home|stop|laser|move. Không bao giờ ném lỗi."""
        try:
            if op in ("status", "mark"):
                pass
            elif op == "arm":
                self._ok(self._call(node, POST, "pantilt", b"ARM"), f"ARM {node}")
            elif op == "home":
                self._ok(self._call(node, POST, "pantilt", b"HOME"), f"HOME {node}")
            elif op == "stop":
                self._ok(self._call(node, POST, "laser", b"OFF"), f"laser OFF {node}")
                self._ok(self._call(node, POST, "stop"), f"POST /stop {node}")
                self._armed[node] = self._pwm[node] = False
            elif op == "laser":
                if not isinstance(on, bool):
                    return {"ok": False, "error": "laser cần on true/false"}
                if on:
                    self._ensure_ready(node, self._pantilt(node))
                resp = self._call(node, POST, "laser", b"ON" if on else b"OFF")
                if on and resp.code == FORBIDDEN:
                    raise LinkError(f"{node} từ chối bật laser (4.03): heartbeat cũ hoặc HOME chưa xong")
                self._ok(resp, f"laser {node}")
            elif op == "move":
                self._manual_move(node, pan_us, tilt_us)
            else:
                return {"ok": False, "error": f"op {op!r} không hợp lệ"}
            st = self._pantilt(node)
            return {"ok": True, "pantilt": st, "laser_on": self._laser_state(node)}
        except LinkError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 - lệnh tay không được làm sập luồng lệnh
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _manual_move(self, node: str, pan_us, tilt_us) -> None:
        for v in (pan_us, tilt_us):
            if isinstance(v, bool) or not isinstance(v, int):
                raise LinkError("pan_us và tilt_us phải là số nguyên µs")
        st = self._pantilt(node)
        for name, v in (("pan", pan_us), ("tilt", tilt_us)):
            lo, hi = st.get(f"{name}_min_us"), st.get(f"{name}_max_us")
            if lo is not None and hi is not None and not lo <= v <= hi:
                raise LinkError(f"{name}_us {v} ngoài giới hạn node [{lo}, {hi}]")
        self._ensure_ready(node, st)
        self._ok(self._call(node, POST, "pantilt", f"{pan_us},{tilt_us}".encode()), f"move {node}")

    def _ensure_ready(self, node: str, st: dict) -> None:
        """Firmware chỉ cho đặt góc hay bật relay sau ARM và HOME xong."""
        if not st.get("armed"):
            self._ok(self._call(node, POST, "pantilt", b"ARM"), f"ARM {node}")
            self._armed[node] = True
        if not st.get("pwm_active"):
            self._ok(self._call(node, POST, "pantilt", b"HOME"), f"HOME {node}")
            end = time.monotonic() + 2.0
            while time.monotonic() < end:
                if self._pantilt(node).get("pwm_active"):
                    break
                time.sleep(POLL_S)
            else:
                raise LinkError(f"PWM của {node} không bật sau HOME")

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
        pan, tilt = self.pulse_map(node).limits_deg()
        return Info(name, fw, pan, tilt, HB_TIMEOUT_MS, 0, False)

    def pulse_map(self, node: str) -> PulseMap:
        return self.maps.get(node, self.map)

