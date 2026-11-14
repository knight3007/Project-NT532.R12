"""Phiên hiệu chuẩn nội tại camera điều khiển từ xa (dashboard control -> scripts/push_camera.py).

Phần quyết định (có lưu khung này không, đọc lệnh từ response) là hàm thuần để test; phần còn lại
giữ ảnh trong data/calib/ và chạy phép tính ở luồng nền để khung hình vẫn đi lên dashboard.
"""

import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import REPO_ROOT
from .calibration import calibrate, charuco_board, save_intrinsics

CALIB_DIR = REPO_ROOT / "data" / "calib"
MIN_CORNERS = 12
MIN_GAP_S = 1.5
MIN_MOVE_PX = 40.0
MIN_TILT = 0.12  # chênh lệch độ dẹt của hộp bao góc coi là "nghiêng khác hẳn"
DEFAULT_TARGET = 25

Pose = tuple[tuple[float, float], float]


def parse_order(reply: dict) -> dict | None:
    """Lấy {req, action, target} từ response của /api/pi/frame; None nếu không có hoặc hỏng."""
    c = reply.get("calib") if isinstance(reply, dict) else None
    if not isinstance(c, dict) or isinstance(c.get("req"), bool) or not isinstance(c.get("req"), int):
        return None
    action = c.get("action")
    if action not in ("idle", "capture", "compute", "reset"):
        return None
    t = c.get("target")
    target = t if isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= 200 else DEFAULT_TARGET
    return {"req": c["req"], "action": action, "target": target}


def board_pose(corners: np.ndarray) -> Pose:
    """(tâm bảng theo pixel, độ dẹt = cạnh ngắn/cạnh dài của hộp bao các góc)."""
    pts = np.asarray(corners, dtype=float).reshape(-1, 2)
    centre = (float(pts[:, 0].mean()), float(pts[:, 1].mean()))
    span = pts.max(axis=0) - pts.min(axis=0)
    lo, hi = float(span.min()), float(span.max())
    return centre, (lo / hi if hi > 0 else 1.0)


def should_save(n_corners: int, now: float, last_t: float | None, pose: Pose | None, last_pose: Pose | None) -> bool:
    """Lưu khung này không: đủ góc, cách lần lưu trước >= MIN_GAP_S, và bảng đã dời/nghiêng khác rõ."""
    if n_corners < MIN_CORNERS or pose is None:
        return False
    if last_t is not None and now - last_t < MIN_GAP_S:
        return False
    if last_pose is None:
        return True
    (cx, cy), tilt = pose
    (lx, ly), ltilt = last_pose
    return float(np.hypot(cx - lx, cy - ly)) > MIN_MOVE_PX or abs(tilt - ltilt) > MIN_TILT


def compute_from_dir(cam_cfg: dict[str, Any], calib_dir: Path = CALIB_DIR) -> tuple[Path, float, int, int]:
    """Hiệu chuẩn từ mọi *.jpg trong calib_dir, ghi vào cam_cfg['intrinsics'].

    Trả (đường dẫn file, rms px, số ảnh dùng được, số ảnh đọc được); ValueError nếu quá ít ảnh.
    """
    paths = sorted(calib_dir.glob("*.jpg"))
    images = [im for im in (cv2.imread(str(f)) for f in paths) if im is not None]
    intrinsics, rms, used = calibrate(images, charuco_board(cam_cfg["calib_board"]))
    out = REPO_ROOT / cam_cfg["intrinsics"]
    out.parent.mkdir(parents=True, exist_ok=True)
    save_intrinsics(out, intrinsics, rms, used)
    return out, rms, used, len(images)


def _rel(p: Path) -> str:
    try:
        return p.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(p)


class CalibSession:
    """Trạng thái hiệu chuẩn trên Pi. Gọi apply_order() với mỗi response, process() với mỗi khung."""

    def __init__(self, cam_cfg: dict[str, Any], calib_dir: Path = CALIB_DIR) -> None:
        self.cam_cfg, self.dir = cam_cfg, calib_dir
        self.detector = cv2.aruco.CharucoDetector(charuco_board(cam_cfg["calib_board"]))
        self.req = 0
        self.seen_req: int | None = None  # None = chưa nhận response nào; lệnh còn tồn từ trước khi khởi động bị bỏ qua
        self.capturing = False
        self.target = DEFAULT_TARGET
        self.saved = len(list(calib_dir.glob("*.jpg"))) if calib_dir.exists() else 0
        self.corners = 0
        self.state = "idle"
        self.rms: float | None = None
        self.file: str | None = None
        self.error: str | None = None
        self._last_t: float | None = None
        self._last_pose: Pose | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.dirty = True  # trạng thái/số ảnh đổi: báo lên control ngay
        self.soft = False  # chỉ số góc thấy được đổi: báo thưa (<= 1 lần / 2 s)

    # --- lệnh -----------------------------------------------------------------------------

    def apply_order(self, order: dict | None) -> None:
        if order is None:
            return
        if self.seen_req is None:
            self.seen_req = self.req = order["req"]
            return
        if order["req"] == self.seen_req:
            return
        self.seen_req = self.req = order["req"]
        self.target = order["target"]
        act = order["action"]
        with self._lock:
            if act == "capture":
                self.capturing = True
                self._last_t = self._last_pose = None
                self.state, self.error = "capturing", None
            elif act == "idle":
                self.capturing = False
                self.state = "idle"
            elif act == "reset":
                self.capturing = False
                for f in self.dir.glob("*.jpg"):
                    f.unlink(missing_ok=True)
                self.saved, self.state, self.error = 0, "idle", None
                self.rms = self.file = None
            elif act == "compute":
                self.capturing = False
                if self._thread is None or not self._thread.is_alive():
                    self.state, self.error = "computing", None
                    self._thread = threading.Thread(target=self._compute, daemon=True, name="calib-compute")
                    self._thread.start()
            self.dirty = True

    def _compute(self) -> None:
        try:
            out, rms, _, _ = compute_from_dir(self.cam_cfg, self.dir)
            with self._lock:
                self.rms, self.file, self.state, self.error = rms, _rel(out), "done", None
        except Exception as e:  # noqa: BLE001 - mọi lỗi (thiếu ảnh, cv2) đều báo lên dashboard
            with self._lock:
                self.state, self.error = "error", str(e) or type(e).__name__
        self.dirty = True

    # --- khung hình -----------------------------------------------------------------------

    def process(self, frame: np.ndarray, now: float | None = None) -> np.ndarray:
        """Trả khung để đẩy lên dashboard (vẽ góc + nhãn khi đang chụp); khung gốc được lưu nếu đạt."""
        if not self.capturing:
            return frame
        now = time.monotonic() if now is None else now
        corners, ids, _, _ = self.detector.detectBoard(frame)
        n = 0 if ids is None else len(ids)
        if n != self.corners:
            self.corners = n
            self.soft = True
        pose = board_pose(corners) if n else None
        if should_save(n, now, self._last_t, pose, self._last_pose):
            self.dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(self.dir / f"calib_{self._next_index():03d}.jpg"), frame)
            self.saved += 1
            self._last_t, self._last_pose = now, pose
            self.dirty = True
            if self.saved >= self.target:
                self.capturing, self.state = False, "idle"
        view = frame.copy()
        if n:  # tự vẽ: drawDetectedCornersCharuco đòi hình dạng mảng khác nhau giữa các bản OpenCV
            for x, y in np.asarray(corners, dtype=float).reshape(-1, 2):
                cv2.circle(view, (round(x), round(y)), 4, (0, 255, 0), -1)
        cv2.putText(view, f"calib {self.saved}/{self.target}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        return view

    def _next_index(self) -> int:
        nums = [int(f.stem[6:]) for f in self.dir.glob("calib_*.jpg") if f.stem[6:].isdigit()]
        return max(nums, default=-1) + 1

    # --- báo cáo --------------------------------------------------------------------------

    def status(self) -> dict:
        self.soft = False
        with self._lock:
            out: dict[str, Any] = {"req": self.req, "state": self.state, "saved": self.saved,
                                   "target": self.target, "corners": self.corners}
            if self.rms is not None:
                out["rms"] = round(self.rms, 4)
            if self.file:
                out["file"] = self.file
            if self.error:
                out["error"] = self.error
        return out
