"""Chạy trạm điều phối kèm dashboard web.

    uv run python scripts/run_station.py --source sim                     # sa bàn ảo, luật
    uv run python scripts/run_station.py --source sim --link coap         # node giả (lõi C firmware) qua UDP
    uv run python scripts/run_station.py --source sim --decider hybrid    # cần runs/decider/jev/jev1
    uv run python scripts/run_station.py --source sim --decider remote --decider-url http://laptop:8090
    uv run python scripts/run_station.py --source sim --detector yolo     # YOLO thật (models/fire-n.pt)
    uv run python scripts/run_station.py --decider rules                  # phần cứng: webcam + CoAP

Thêm --mqtt để đẩy trạm lên Home Assistant (docs/home-assistant.md).
Mở http://<địa chỉ Pi>:8080/ để xem. Ctrl+C để tắt (gửi /stop tới mọi node trước khi thoát).
Nhật ký sự kiện ghi thêm vào runs/station/<thời điểm>.jsonl.
"""

import argparse
import time
from datetime import datetime

from nt532.config import REPO_ROOT, read_site
from nt532.dashboard import Dashboard
from nt532.orchestrator.decide import parse_stages
from nt532.station import build_real, build_sim


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source", default=None, help="sim, số thứ tự webcam, file video hoặc URL stream (rtsp://...); "
                                                 "mặc định theo camera.stream.url trong site.yaml")
    p.add_argument("--decider", choices=["rules", "jev", "hybrid", "remote"], default="rules")
    p.add_argument("--decider-url", default=None,
                   help="chỉ cho --decider remote: URL của scripts/serve_decider.py, vd http://192.168.1.50:8090 "
                        "(HTTP không xác thực, chỉ dùng trong LAN phòng lab)")
    p.add_argument("--decider-timeout", type=float, default=3.0,
                   help="giây chờ máy chủ quyết định; quá hạn hoặc lỗi thì lượt đó dùng luật")
    p.add_argument("--jev-run", default="jev1", help="thư mục trong runs/decider/jev/")
    p.add_argument("--tau", type=float, default=0.8, help="ngưỡng tin cậy của chế độ lai")
    p.add_argument("--model-stages", default="verify",
                   help="chế độ lai: giai đoạn hỏi mô hình (verify, decide hoặc decide,verify); giai đoạn "
                        "còn lại dùng luật, không gọi mô hình. Mặc định verify vì DECIDE cần nhanh")
    p.add_argument("--detector", choices=["oracle", "yolo"], default="oracle",
                   help="chỉ cho sa bàn ảo: oracle đọc thẻ thật trong scene, yolo dùng trọng số")
    p.add_argument("--link", choices=["mem", "coap"], default="mem",
                   help="chỉ cho sa bàn ảo: mem là node ảo trong bộ nhớ, coap là node giả chạy lõi C của "
                        "firmware qua CoAP/UDP localhost")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--mqtt", action="store_true", help="đẩy trạm lên Home Assistant qua MQTT (mục mqtt trong site.yaml)")
    p.add_argument("--mqtt-host", default=None, help="ghi đè mqtt.host")
    p.add_argument("--mqtt-port", type=int, default=None, help="ghi đè mqtt.port")
    p.add_argument("--mqtt-user", default=None, help="ghi đè mqtt.username (mật khẩu lấy từ biến môi trường NT532_MQTT_PASSWORD)")
    p.add_argument("--mqtt-station-id", default=None, help="ghi đè mqtt.station_id")
    args = p.parse_args()
    try:
        parse_stages(args.model_stages)
    except ValueError as e:
        p.error(str(e))
    if args.decider == "remote" and not args.decider_url:
        p.error("--decider remote cần --decider-url")

    log = REPO_ROOT / "runs/station" / f"{datetime.now().astimezone():%Y%m%d-%H%M%S}.jsonl"
    if args.source == "sim":
        st = build_sim(args.decider, args.jev_run, args.tau, args.detector, args.seed, log_path=log,
                       link=args.link, model_stages=args.model_stages,
                       decider_url=args.decider_url, decider_timeout=args.decider_timeout)
    else:
        st = build_real(args.decider, args.jev_run, args.tau, args.source, log_path=log,
                        model_stages=args.model_stages, decider_url=args.decider_url,
                        decider_timeout=args.decider_timeout)
    bridge = None
    if args.mqtt:
        try:
            import paho.mqtt  # noqa: F401
        except ImportError:
            p.error("--mqtt cần gói paho-mqtt: uv sync --extra mqtt")
    st.start()
    if args.mqtt:
        from nt532.integrations.mqtt import MqttBridge

        cfg = dict(read_site().get("mqtt") or {})
        for key, val in (("host", args.mqtt_host), ("port", args.mqtt_port), ("username", args.mqtt_user),
                         ("station_id", args.mqtt_station_id)):
            if val is not None:
                cfg[key] = val
        bridge = MqttBridge(st, cfg).start()
        print(f"MQTT: {cfg.get('host', 'localhost')}:{cfg.get('port', 1883)}")
    dash = Dashboard(st, args.host, args.port).start()
    print(f"Dashboard: {dash.url}  (nhật ký: {log.relative_to(REPO_ROOT)})")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Đang tắt...")
    finally:
        if bridge:
            bridge.stop()
        st.stop()
        dash.stop()


if __name__ == "__main__":
    main()
