"""Dashboard web cho trạm: chỉ dùng thư viện chuẩn (chạy được trên Pi không cần cài thêm).

    GET  /                 trang dashboard (static/index.html)
    GET  /api/state        trạng thái orchestrator, cảm biến, sức khỏe vòi, ground truth (sim)
    GET  /api/events?since=N
    GET  /stream.mjpg      video MJPEG có vẽ bảng bia, bia, điểm ngắm và vết laser của từng vòi
    GET  /snapshot.jpg     một khung
    POST /api/cmd          {"cmd": ...}: stop, enable, recommission, decider, và lệnh sa bàn ảo

Không có xác thực: chỉ mở trong mạng LAN của sa bàn.
"""

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

from ..orchestrator.fusion import alarm_limits
from ..vision.localize import project

STATIC = files("nt532.dashboard") / "static"
CYAN, YELLOW, RED, GREEN, WHITE = (255, 200, 0), (0, 220, 255), (40, 40, 255), (80, 220, 80), (240, 240, 240)


def _default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _clean(o):
    """inf/nan không hợp lệ trong JSON: đổi thành None."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


class Dashboard:
    def __init__(self, station, host: str = "0.0.0.0", port: int = 8080, stream_fps: float = 8.0):
        self.st = station
        self.stream_fps = stream_fps
        self.httpd = ThreadingHTTPServer((host, port), self._handler())
        self.httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{'localhost' if host in ('0.0.0.0', '::') else host}:{port}/"

    def start(self) -> "Dashboard":
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="dashboard")
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()

    # --- dữ liệu ----------------------------------------------------------------------------

    def state(self) -> dict:
        st = self.st
        now = st.sensors.clock()
        sensors = {}
        for n in st.link.nodes:
            sensors[n] = [{"t": round(s.t - now, 1), "temp": s.temp, "gas": s.gas, "hum": s.hum}
                          for s in st.sensors.samples(n, 150)]
        out = {
            "time": time.time(), "orch": st.orch.snapshot(), "sensors": sensors,
            "limits": alarm_limits(st.site), "sim": st.world is not None,
            "detector": st.detector_name, "camera_error": st.hub.error,
            "board": {"width": st.site["board"]["width"], "height": st.site["board"]["height"]},
        }
        if st.world is not None:
            out["truth"] = st.world.truth()
        return _clean(out)

    def frame(self) -> np.ndarray | None:
        _, frame = self.st.hub.latest()
        if frame is None:
            return None
        return self.annotate(frame.copy())

    def annotate(self, img: np.ndarray) -> np.ndarray:
        vision = self.st.vision
        if vision.state is None:
            cv2.putText(img, "chua commissioning", (12, 30), 0, 0.8, RED, 2)
            return img
        cam, y = vision.camera, self.st.site["board"]["plane_y"]

        def px(x, z):
            u, v = project((x, y, z), cam)
            return int(u), int(v)

        cv2.polylines(img, [vision.board_polygon().astype(np.int32)], True, CYAN, 1)
        ov = self.st.orch.overlay
        for t in ov.get("tracks", []):
            c = px(t["x"], t["z"])
            confs = [v for v in t["conf"] if v is not None]
            cv2.circle(img, c, 18, YELLOW, 2)
            cv2.putText(img, f"{max(confs, default=0):.2f}", (c[0] + 20, c[1] - 8), 0, 0.5, YELLOW, 1)
        if "target" in ov:
            c = px(ov["target"]["x"], ov["target"]["z"])
            cv2.drawMarker(img, c, RED, cv2.MARKER_CROSS, 34, 2)
            cv2.putText(img, ov["target"]["id"], (c[0] + 14, c[1] + 22), 0, 0.6, RED, 2)
        # mỗi vòi một điểm ngắm (chữ thập nghiêng trắng) và một vết laser (vòng xanh), ghi tên vòi
        # bên cạnh để lượt hai vòi vẫn phân biệt được
        for n, a in ov.get("aims", {}).items():
            c = px(a["x"], a["z"])
            cv2.drawMarker(img, c, WHITE, cv2.MARKER_TILTED_CROSS, 16, 1)
            cv2.putText(img, n, (c[0] + 10, c[1] - 8), 0, 0.4, WHITE, 1)
        for n, sp in ov.get("spots", {}).items():
            c = px(sp["x"], sp["z"])
            cv2.circle(img, c, 7, GREEN, 2)
            cv2.putText(img, n, (c[0] + 10, c[1] + 16), 0, 0.4, GREEN, 1)
        for name, pose in vision.nodes.items():
            u, v = project(pose.position, cam)
            cv2.putText(img, name, (int(u) - 12, int(v) + 34), 0, 0.7, WHITE, 2)
        return img

    def command(self, body: dict) -> dict:
        st, cmd = self.st, body.get("cmd")
        orch, world = st.orch, st.world
        if cmd == "stop":
            orch.emergency_stop("dừng từ dashboard")
        elif cmd == "enable":
            orch.enabled = bool(body.get("on", True))
            st.events.emit("config", "orchestrator " + ("bật" if orch.enabled else "tắt"))
        elif cmd == "recommission":
            orch.recommission()
        elif cmd == "decider":
            st.set_decider(body.get("kind", "rules"), body.get("run", "jev1"), float(body.get("tau", 0.8)),
                           body.get("stages", "verify"))
        elif world is None:
            return {"ok": False, "error": f"lệnh {cmd!r} chỉ có trên sa bàn ảo"}
        else:
            return self._sim(body)
        return {"ok": True}

    def _sim(self, body: dict) -> dict:
        w, cmd = self.st.world, body["cmd"]
        x, z = body.get("x"), body.get("z")
        if "u" in body and "v" in body:  # bấm lên video: đổi pixel sang tọa độ bảng
            p = self.st.vision.localize((float(body["u"]), float(body["v"])))
            if p is None:
                return {"ok": False, "error": "điểm bấm không nằm trên bảng bia"}
            x, z = float(p[0]), float(p[2])
        board = self.st.site["board"]
        if x is not None:
            x = min(max(x, 0.05), board["width"] - 0.05)
        if z is not None:
            z = min(max(z, 0.05), board["height"] - 0.05)
        from ..sim.world import FIRE_SIZES  # chỉ lệnh sa bàn ảo mới cần tới sim

        make = {"lamp": (w.add_lamp, "đèn nóng"), "object": (w.add_object, "vật màu cam")}
        if cmd in ("fire", "fire_large"):  # `fire` nhận thêm "size": small hoặc large
            size = "large" if cmd == "fire_large" else body.get("size", "small")
            if size not in FIRE_SIZES:
                return {"ok": False, "error": f"cỡ đám cháy phải là {' hoặc '.join(FIRE_SIZES)}, "
                                              f"không phải {size!r}"}
            s = w.ignite(x, z, size=size)
            text = "đám cháy lớn" if size == "large" else "đám cháy"
        elif cmd in make:
            s, text = make[cmd][0](x, z), make[cmd][1]
        elif cmd == "steam":
            s, text = w.add_steam(x), "hơi nước"
        elif cmd == "spike":
            s, text = w.add_spike(body.get("node")), "xung nhiễu cảm biến"
        elif cmd == "clear":
            w.clear()
            self.st.events.emit("sim", "xóa mọi nguồn trên sa bàn ảo")
            return {"ok": True}
        elif cmd == "online":
            w.set_online(body["node"], bool(body.get("on", True)))
            self.st.events.emit("sim", f"node {body['node']} " + ("có" if body.get("on", True) else "mất")
                                + " liên lạc", level="warn")
            return {"ok": True}
        elif cmd == "move_node":
            with w.lock:
                w.scene.move_node(body["node"], delta=(float(body.get("dx", 0.03)), 0, 0))
            self.st.events.emit("sim", f"dời node {body['node']}", level="warn")
            return {"ok": True}
        else:
            return {"ok": False, "error": f"không biết lệnh {cmd!r}"}
        where = f" tại x {s.x:.2f} z {s.z:.2f}" if s.kind not in ("spike", "steam") else ""
        self.st.events.emit("sim", f"thêm {text}{where}", source=s.id)
        return {"ok": True, "source": s.to_json()}

    # --- HTTP -------------------------------------------------------------------------------

    def _handler(self):
        dash = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # im lặng: dashboard gọi API mỗi nửa giây
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200) -> None:
                body = json.dumps(_clean(obj), ensure_ascii=False, default=_default).encode()
                self._send(code, body, "application/json; charset=utf-8")

            def do_GET(self):
                url = urlparse(self.path)
                if url.path in ("/", "/index.html"):
                    self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/api/state":
                    self._json(dash.state())
                elif url.path == "/api/events":
                    since = int(parse_qs(url.query).get("since", ["0"])[0])
                    self._json(dash.st.events.since(since))
                elif url.path == "/snapshot.jpg":
                    img = dash.frame()
                    if img is None:
                        self._send(503, b"no frame", "text/plain")
                    else:
                        self._send(200, cv2.imencode(".jpg", img)[1].tobytes(), "image/jpeg")
                elif url.path == "/stream.mjpg":
                    self._stream()
                elif url.path == "/favicon.ico":
                    self._send(204, b"", "image/x-icon")
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                if urlparse(self.path).path != "/api/cmd":
                    self._send(404, b"not found", "text/plain")
                    return
                n = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                    self._json(dash.command(body))
                except Exception as e:  # noqa: BLE001 - trả lỗi cho trình duyệt thay vì đóng kết nối
                    self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                last = -1
                try:
                    while True:
                        seq, _ = dash.st.hub.latest()
                        if seq != last:
                            last = seq
                            img = dash.frame()
                            if img is not None:
                                jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])[1]
                                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                                 + f"Content-Length: {len(jpg)}\r\n\r\n".encode()
                                                 + jpg.tobytes() + b"\r\n")
                        time.sleep(1 / dash.stream_fps)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        return Handler
