"""Chạy trạm điều phối kèm dashboard web.

    uv run python scripts/run_station.py --source sim                     # sa bàn ảo, luật
    uv run python scripts/run_station.py --source sim --link coap         # node giả (lõi C firmware) qua UDP
    uv run python scripts/run_station.py --source sim --decider hybrid    # cần runs/decider/jev/jev1
    uv run python scripts/run_station.py --source sim --detector yolo     # YOLO thật (models/fire-n.pt)
    uv run python scripts/run_station.py --decider rules                  # phần cứng: webcam + CoAP

Mở http://<địa chỉ Pi>:8080/ để xem. Ctrl+C để tắt (gửi /stop tới mọi node trước khi thoát).
Nhật ký sự kiện ghi thêm vào runs/station/<thời điểm>.jsonl.
"""

import argparse
import time
from datetime import datetime

from nt532.config import REPO_ROOT
from nt532.dashboard import Dashboard
from nt532.station import build_real, build_sim


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source", default=None, help="sim, số thứ tự webcam, file video hoặc URL stream (rtsp://...); "
                                                 "mặc định theo camera.stream.url trong site.yaml")
    p.add_argument("--decider", choices=["rules", "jev", "hybrid"], default="rules")
    p.add_argument("--jev-run", default="jev1", help="thư mục trong runs/decider/jev/")
    p.add_argument("--tau", type=float, default=0.8, help="ngưỡng tin cậy của chế độ lai")
    p.add_argument("--detector", choices=["oracle", "yolo"], default="oracle",
                   help="chỉ cho sa bàn ảo: oracle đọc thẻ thật trong scene, yolo dùng trọng số")
    p.add_argument("--link", choices=["mem", "coap"], default="mem",
                   help="chỉ cho sa bàn ảo: mem là node ảo trong bộ nhớ, coap là node giả chạy lõi C của "
                        "firmware qua CoAP/UDP localhost")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    log = REPO_ROOT / "runs/station" / f"{datetime.now().astimezone():%Y%m%d-%H%M%S}.jsonl"
    if args.source == "sim":
        st = build_sim(args.decider, args.jev_run, args.tau, args.detector, args.seed, log_path=log,
                       link=args.link)
    else:
        st = build_real(args.decider, args.jev_run, args.tau, args.source, log_path=log)
    st.start()
    dash = Dashboard(st, args.host, args.port).start()
    print(f"Dashboard: {dash.url}  (nhật ký: {log.relative_to(REPO_ROOT)})")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Đang tắt...")
    finally:
        st.stop()
        dash.stop()


if __name__ == "__main__":
    main()
