"""Hiệu chuẩn webcam bằng bảng ChArUco (docs/print/charuco-a4.pdf, dán lên bìa phẳng).

    uv run python scripts/calibrate_camera.py --capture   # SPACE lưu ảnh, Q thoát
    uv run python scripts/calibrate_camera.py             # tính từ ảnh trong data/calib/

Chụp 20–30 ảnh: bảng ở nhiều vị trí trong khung, nghiêng nhiều hướng, có ảnh sát mép khung.
Khóa lấy nét và độ phân giải trước khi chụp; đổi một trong hai thì phải hiệu chuẩn lại.
"""

import argparse

import cv2

from nt532.config import REPO_ROOT, load_site
from nt532.vision.calibration import calibrate, charuco_board, save_intrinsics

CALIB_DIR = REPO_ROOT / "data" / "calib"


def capture(cam_cfg: dict) -> None:
    CALIB_DIR.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(cam_cfg["index"])
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam_cfg["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam_cfg["height"])
    detector = cv2.aruco.CharucoDetector(charuco_board(cam_cfg["calib_board"]))
    n = len(list(CALIB_DIR.glob("*.jpg")))
    while True:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("không đọc được webcam")
        corners, ids, _, _ = detector.detectBoard(frame)
        view = frame.copy()
        if ids is not None:
            cv2.aruco.drawDetectedCornersCharuco(view, corners, ids)
        found = 0 if ids is None else len(ids)
        cv2.putText(
            view,
            f"saved {n}  corners {found}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
        )
        cv2.imshow("calibrate", view)
        key = cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            cv2.imwrite(str(CALIB_DIR / f"{n:03d}.jpg"), frame)
            n += 1
        elif key == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--capture", action="store_true")
    args = p.parse_args()
    cam_cfg = load_site()["camera"]
    if args.capture:
        capture(cam_cfg)
        return
    paths = sorted(CALIB_DIR.glob("*.jpg"))
    intrinsics, rms, used = calibrate(
        [cv2.imread(str(f)) for f in paths], charuco_board(cam_cfg["calib_board"])
    )
    out = REPO_ROOT / cam_cfg["intrinsics"]
    save_intrinsics(out, intrinsics, rms, used)
    print(f"dùng {used}/{len(paths)} ảnh, RMS {rms:.3f} px, lưu {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
