"""Đo độ trễ stream điện thoại → Pi bằng laser của một node, in giá trị để điền camera.stream.latency_s.

    uv run python scripts/stream_latency.py                              # theo camera.stream.url, node s1
    uv run python scripts/stream_latency.py --source rtsp://127.0.0.1:8554/cam --node s1
    uv run python scripts/stream_latency.py --trials 10 --pan 5 --tilt -3 --threshold 30

Mỗi lượt: bật laser, đo từ lúc gửi /fire tới khung đầu tiên trong stream có thay đổi so với ảnh nền.
Laser phải lọt vào khung hình (chiếu lên mặt bàn trong tầm nhìn camera). Luôn gửi /stop khi thoát.
"""

import argparse
import statistics
import sys
import time
from collections.abc import Iterable

import cv2
import numpy as np

from nt532.config import load_site
from nt532.vision.stream import StreamCapture

BUOC = 0.05   # làm tròn lên bội số này (s)
BIEN_AN_TOAN = 0.1  # cộng thêm vào độ trễ gợi ý (s)


def prep(frame: np.ndarray) -> np.ndarray:
    """Ảnh xám làm mờ nhẹ để nhiễu nén không vượt ngưỡng."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return cv2.GaussianBlur(gray, (5, 5), 0)


def first_change(baseline: np.ndarray, frames: Iterable[tuple[np.ndarray, float]], threshold: float) -> float | None:
    """Thời điểm (theo `t` đi kèm) của khung đầu tiên lệch ảnh nền quá ngưỡng; None nếu hết khung mà chưa thấy.

    `baseline` đã qua `prep`; `frames` là các cặp (khung, thời điểm).
    """
    base = baseline.astype(np.int16)
    for frame, t in frames:
        if int(np.abs(prep(frame).astype(np.int16) - base).max()) > threshold:
            return t
    return None


def suggest(values: list[float]) -> float:
    """Trung vị làm tròn lên 0,05 s, cộng 0,1 s dự phòng."""
    med = statistics.median(values)
    return round(-(-med // BUOC) * BUOC + BIEN_AN_TOAN, 2)


def _frames_until(cap: StreamCapture, deadline: float):
    while time.monotonic() < deadline:
        try:
            yield cap.read_stamped(timeout=max(0.1, deadline - time.monotonic()))
        except RuntimeError:
            return


def _make_link(site: dict):
    from nt532.net.coap import CoapLink

    net = site.get("network") or {}
    nodes = {n: a for n, a in (net.get("nodes") or {}).items() if a}
    if not nodes:
        raise ValueError("chưa khai địa chỉ node trong network.nodes của config/site.yaml")
    return CoapLink(nodes, (net.get("bind", "::"), int(net.get("port", 5683))),
                    transports=net.get("transports")).start()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source", default=None, help="URL stream; mặc định theo camera.stream.url trong site.yaml")
    p.add_argument("--node", default="s1")
    p.add_argument("--trials", type=int, default=7, help="số lượt đo")
    p.add_argument("--laser-ms", type=int, default=1500, help="thời gian bật laser mỗi lượt")
    p.add_argument("--pan", type=float, default=0.0)
    p.add_argument("--tilt", type=float, default=0.0)
    p.add_argument("--threshold", type=float, default=40, help="ngưỡng chênh lệch điểm ảnh (0-255) coi là có laser")
    args = p.parse_args()

    site = load_site()
    scfg = site["camera"].get("stream") or {}
    url = args.source or scfg.get("url")
    if not url:
        print("Thiếu --source và camera.stream.url trong site.yaml chưa đặt.", file=sys.stderr)
        return 2

    cap = StreamCapture(url, latency_s=0.0, reconnect_s=scfg.get("reconnect_s", 2.0),
                        timeout_s=scfg.get("timeout_s", 5.0))  # latency 0: thời điểm khung = lúc nhận
    link = None
    values: list[float] = []
    try:
        print(f"Đang mở {url} ...")
        if not cap.wait_connected(10.0):
            print(f"Không nhận được khung nào từ stream ({cap.error}).", file=sys.stderr)
            return 2
        link = _make_link(site)
        for i in range(1, args.trials + 1):
            link.stop(args.node)
            time.sleep(1.0)  # laser tắt hẳn và stream ổn định
            aim_id = link.aim(args.node, args.pan, args.tilt, 10000)
            st = link.wait(aim_id, ("reached",), timeout=5.0)
            if st is None:
                print(f"Node {args.node} không tới được góc ({args.pan}, {args.tilt}); kiểm tra node và /aim.",
                      file=sys.stderr)
                return 2
            time.sleep(0.5)
            baseline = prep(cap.read_stamped()[0])
            t_send = time.monotonic()
            link.fire(args.node, aim_id, "laser", args.laser_ms)
            t = first_change(baseline, _frames_until(cap, t_send + 5.0), args.threshold)
            if t is None:
                print(f"Lượt {i}: không thấy laser trong 5 s (laser có nằm trong khung hình? thử hạ --threshold)")
            else:
                values.append(t - t_send)
                print(f"Lượt {i}: {values[-1] * 1000:.0f} ms")
    finally:
        if link is not None:
            link.stop(args.node)
            link.close()
        cap.release()

    if len(values) < 3:
        print(f"Chỉ {len(values)}/{args.trials} lượt đo được, chưa đủ (cần ít nhất 3).", file=sys.stderr)
        return 1
    print(f"\nĐo được {len(values)}/{args.trials} lượt: trung vị {statistics.median(values):.3f} s, "
          f"lớn nhất {max(values):.3f} s")
    print(f"Gợi ý: camera.stream.latency_s: {suggest(values)}")
    print("(Giá trị đo gồm cả CoAP và thời gian laser sáng lên, vài chục ms, nên hơi lớn hơn độ trễ thật; "
          "thiên về an toàn.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
