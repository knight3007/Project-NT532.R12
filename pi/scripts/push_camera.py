"""Đẩy hình camera lên dashboard control mà không cần chạy trạm (ngắm/chỉnh camera, hiệu chuẩn nội tại).

    uv run --no-sync python scripts/push_camera.py --control-url https://control.example.com
    NT532_PI_TOKEN=... uv run --no-sync python scripts/push_camera.py --control-url ... --fps 10 --source rtsp://...

Bình thường chỉ có nhãn nhỏ "push_camera" ở góc. Khi bấm "Bắt đầu chụp" trong panel "Hiệu chuẩn camera"
trên dashboard, script tự chụp ảnh bảng ChArUco vào data/calib/, tính hiệu chuẩn và báo kết quả về.
Ctrl+C để thoát.
"""

import argparse
import os
import time

import cv2

from nt532.config import load_site
from nt532.dashboard.push import (
    JPEG_QUALITY,
    MAX_BACKOFF,
    alive_path,
    describe_error,
    post_jpeg,
    post_json,
)
from nt532.vision.calib_session import CalibSession, parse_order
from nt532.vision.camera import open_camera

REPORT_EVERY_S = 2.0
YIELD_WITHIN_S = 3.0  # trạm vừa đẩy hình trong khoảng này thì nhường, tránh hai luồng xen nhau


def station_is_pushing(alive_mtime: float | None, now: float, within: float = YIELD_WITHIN_S) -> bool:
    """True nếu file nhịp tim của trạm mới hơn `within` giây (None: không có file)."""
    return alive_mtime is not None and 0 <= now - alive_mtime < within


YIELD_POLL_S = 0.5  # lúc nhường chỉ ngủ rồi xem lại nhịp tim, không giữ camera
REOPEN_RETRY_S = 3.0  # mở lại camera lỗi thì thử lại sau chừng này


def camera_action(pushing: bool, cap_open: bool) -> str:
    """Quyết định mỗi vòng: "release" (vừa phải nhường, nhả camera), "wait" (đang nhường),
    "reopen" (trạm ngừng đẩy, mở lại camera) hay "run" (đọc và đẩy bình thường)."""
    if pushing:
        return "release" if cap_open else "wait"
    return "run" if cap_open else "reopen"


def _alive_mtime() -> float | None:
    try:
        return alive_path().stat().st_mtime
    except OSError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control-url", required=True)
    ap.add_argument("--control-token", default=os.environ.get("NT532_PI_TOKEN", ""))
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--source", default=None, help="chỉ số webcam, đường dẫn video hoặc URL; mặc định theo site.yaml")
    args = ap.parse_args()
    if not args.control_token:
        raise SystemExit("thiếu token: đặt NT532_PI_TOKEN hoặc --control-token")
    if args.fps <= 0:
        raise SystemExit("--fps phải > 0")
    source: int | str | None = args.source
    if isinstance(source, str) and source.isdigit():
        source = int(source)
    site = load_site()
    try:
        cap = open_camera(site["camera"], source)
    except RuntimeError as e:
        raise SystemExit(str(e)) from e
    session = CalibSession(site["camera"])
    base = args.control_url.rstrip("/")
    endpoint, calib_endpoint = base + "/api/pi/frame", base + "/api/pi/calib"
    period, backoff, down, last_report = 1.0 / args.fps, 0.0, False, 0.0
    yielding = False
    print(f"đẩy hình lên {endpoint} ({args.fps:g} fps), Ctrl+C để thoát", flush=True)
    try:
        while True:
            t0 = time.monotonic()
            pushing = station_is_pushing(_alive_mtime(), time.time())
            if pushing != yielding:
                yielding = pushing
                print("trạm đang đẩy hình: tạm nhường" if yielding else "trạm ngừng đẩy hình: đẩy lại", flush=True)
            action = camera_action(pushing, cap is not None)
            if action in ("release", "wait"):
                if cap is not None:
                    cap.release()  # nhường hẳn camera/RTSP cho trạm, khỏi giải mã 1080p vô ích
                    cap = None
                time.sleep(YIELD_POLL_S)
                continue
            if action == "reopen":
                try:
                    cap = open_camera(site["camera"], source)
                except RuntimeError as e:
                    print(f"không mở lại được camera: {e}; thử lại sau {REOPEN_RETRY_S:g}s", flush=True)
                    time.sleep(REOPEN_RETRY_S)
                    continue
                t0 = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                raise SystemExit("không đọc được camera")
            view = session.process(frame)
            if not session.capturing:
                cv2.putText(view, "push_camera", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
            ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            wait = period
            try:
                if ok:
                    reply = post_jpeg(endpoint, args.control_token, buf.tobytes())
                    session.apply_order(parse_order(reply))
                if session.dirty or (session.soft and t0 - last_report >= REPORT_EVERY_S):
                    status = session.status()  # xóa cờ soft
                    session.dirty = False
                    last_report = t0
                    try:
                        post_json(calib_endpoint, args.control_token, status)
                    except Exception:
                        session.dirty = True
                        raise
                if down:
                    print("đã nối lại control", flush=True)
                    down = False
                backoff = 0.0
            except Exception as e:  # noqa: BLE001 - mạng/HTTP: thử lại với backoff
                why, _ = describe_error(e)
                if not down:
                    print(f"mất liên lạc với control: {why}", flush=True)
                    down = True
                backoff = min(MAX_BACKOFF, max(1.0, backoff * 2))
                wait = backoff
            time.sleep(max(0.0, wait - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        print("thoát", flush=True)
    finally:
        if cap is not None:
            cap.release()


if __name__ == "__main__":
    main()
