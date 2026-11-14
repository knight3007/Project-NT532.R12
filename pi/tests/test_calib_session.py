"""Hiệu chuẩn camera từ xa: quyết định lưu khung, đọc lệnh từ response, phiên chụp/tính/xóa."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import pytest

from nt532.dashboard.push import post_jpeg, post_json
from nt532.vision import calib_session
from nt532.vision.calib_session import CalibSession, board_pose, parse_order, should_save

BOARD = {"dictionary": "4X4_50", "squares_x": 6, "squares_y": 9, "square": 0.030, "marker": 0.022}
POSE = ((100.0, 100.0), 0.7)


def test_parse_order():
    assert parse_order({"ok": True, "calib": {"req": 3, "action": "capture", "target": 30}}) == {
        "req": 3, "action": "capture", "target": 30}
    assert parse_order({"calib": {"req": 1, "action": "idle"}})["target"] == 25  # thiếu target -> mặc định
    for bad in ({}, {"calib": None}, {"calib": {"req": "1", "action": "idle"}},
                {"calib": {"req": True, "action": "idle"}}, {"calib": {"req": 1, "action": "nổ"}}):
        assert parse_order(bad) is None
    assert parse_order(None) is None  # type: ignore[arg-type]


def test_should_save_rules():
    assert not should_save(11, 10.0, None, POSE, None)  # thiếu góc
    assert not should_save(30, 10.0, None, None, None)
    assert should_save(30, 10.0, None, POSE, None)  # ảnh đầu tiên
    assert not should_save(30, 11.0, 10.0, ((300.0, 100.0), 0.7), POSE)  # quá sát lần lưu trước (< 1,5 s)
    assert not should_save(30, 12.0, 10.0, ((120.0, 110.0), 0.72), POSE)  # gần như không dời
    assert should_save(30, 12.0, 10.0, ((160.0, 100.0), 0.7), POSE)  # dời > 40 px
    assert should_save(30, 12.0, 10.0, ((100.0, 100.0), 0.4), POSE)  # nghiêng khác hẳn


def test_board_pose():
    centre, flat = board_pose(np.array([[0, 0], [100, 0], [100, 50], [0, 50]], np.float32))
    assert centre == (50.0, 25.0) and flat == pytest.approx(0.5)


def board_frame(dx=0, dy=0):
    board = calib_session.charuco_board(BOARD)
    img = board.generateImage((400, 600), marginSize=20)
    canvas = np.full((720, 1280, 3), 255, np.uint8)
    canvas[60 + dy:60 + dy + 600, 100 + dx:100 + dx + 400] = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return canvas


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(calib_session, "REPO_ROOT", tmp_path)
    return CalibSession({"calib_board": BOARD, "intrinsics": "calibration/camera.yaml"}, tmp_path / "calib")


def order(req, action, target=25):
    return {"req": req, "action": action, "target": target}


def test_capture_saves_raw_until_target(session, tmp_path):
    session.apply_order(order(0, "idle"))  # lệnh có sẵn lúc khởi động bị bỏ qua
    session.apply_order(order(1, "capture", target=2))
    assert session.capturing and session.status()["state"] == "capturing"
    raw = board_frame()
    view = session.process(raw, now=100.0)
    assert not np.array_equal(view, raw)  # có vẽ góc và nhãn
    assert session.saved == 1 and session.corners >= 12
    saved = cv2.imread(str(tmp_path / "calib" / "calib_000.jpg"))
    assert saved.shape == raw.shape and np.abs(saved.astype(int) - raw.astype(int)).max() < 30  # ảnh gốc, chỉ khác do JPEG
    session.process(board_frame(), now=100.5)  # cùng chỗ, quá sớm
    assert session.saved == 1
    session.process(board_frame(dx=300), now=102.0)
    assert session.saved == 2 and not session.capturing
    st = session.status()
    assert st["state"] == "idle" and st["saved"] == 2 and st["req"] == 1


def test_same_req_ignored_and_idle_stops(session):
    session.apply_order(order(0, "idle"))
    session.apply_order(order(1, "capture"))
    session.apply_order(order(1, "idle"))  # cùng req: không đổi
    assert session.capturing
    session.apply_order(order(2, "idle"))
    assert not session.capturing


def test_reset_deletes_images(session, tmp_path):
    session.apply_order(order(0, "idle"))
    session.apply_order(order(1, "capture"))
    session.process(board_frame(), now=1.0)
    assert list((tmp_path / "calib").glob("*.jpg"))
    session.apply_order(order(2, "reset"))
    assert not list((tmp_path / "calib").glob("*.jpg")) and session.status()["saved"] == 0


def test_compute_too_few_images_reports_error(session):
    session.apply_order(order(0, "idle"))
    session.apply_order(order(1, "capture"))
    session.process(board_frame(), now=1.0)
    session.apply_order(order(2, "compute"))
    session._thread.join(10)
    st = session.status()
    assert st["state"] == "error" and "cần ít nhất 5" in st["error"]


def test_compute_success_writes_intrinsics(session, tmp_path, monkeypatch):
    fake = (tmp_path / "calibration" / "camera.yaml", 0.4, 9, 10)
    monkeypatch.setattr(calib_session, "compute_from_dir", lambda cfg, d: (fake[0], *fake[1:]))
    session.apply_order(order(0, "idle"))
    session.apply_order(order(1, "compute"))
    session._thread.join(10)
    st = session.status()
    assert st["state"] == "done" and st["rms"] == 0.4 and st["file"] == "calibration/camera.yaml"


class Server:
    def __init__(self):
        self.reqs = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                outer.reqs.append((self.path, self.headers.get("Authorization"), raw))
                data = json.dumps({"ok": True, "calib": {"req": 4, "action": "capture", "target": 20}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_response_json_parsed_and_status_posted():
    srv = Server()
    try:
        reply = post_jpeg(srv.url + "/api/pi/frame", "tok", b"\xff\xd8x")
        assert parse_order(reply) == {"req": 4, "action": "capture", "target": 20}
        post_json(srv.url + "/api/pi/calib", "tok", {"state": "idle", "saved": 0})
    finally:
        srv.close()
    assert srv.reqs[0][:2] == ("/api/pi/frame", "Bearer tok")
    path, auth, raw = srv.reqs[1]
    assert path == "/api/pi/calib" and auth == "Bearer tok" and json.loads(raw) == {"state": "idle", "saved": 0}
    time.sleep(0)
