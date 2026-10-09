"""Chạy bộ quyết định (Jev) ở máy khác, vd laptop có GPU, để Pi gọi qua HTTP.

    uv run python scripts/serve_decider.py --decider jev --jev-run jev1 --device cuda --host 0.0.0.0 --port 8090
    uv run python scripts/serve_decider.py --decider hybrid --model-stages verify --host 192.168.1.50
    # trên Pi:
    uv run python scripts/run_station.py --decider remote --decider-url http://192.168.1.50:8090

Chỉ dùng thư viện chuẩn cho phần HTTP (nt532.orchestrator.remote). POST /decide nhận
{"obs", "questions"} và trả Decision dạng JSON; GET /health để kiểm tra. Một thể hiện mô hình, các
yêu cầu xếp hàng sau một khóa; mỗi yêu cầu in độ trễ ra stderr.

AN TOÀN: không có xác thực và không mã hóa. Chỉ bind vào địa chỉ trong LAN tin cậy của phòng lab
(--host bắt buộc phải nêu rõ; 0.0.0.0 là mọi giao diện), không mở cổng này ra Internet. Máy chủ
không thể phá chặn an toàn: các chặn cứng vẫn nằm ở orchestrator trên Pi, và Pi dùng luật nếu
máy chủ chậm hoặc lỗi.
"""

import argparse

from nt532.orchestrator.decide import JevDecider, make_decider, parse_stages
from nt532.orchestrator.remote import make_server


def warm_up(decider) -> None:
    """Nạp mô hình ngay lúc khởi động (mất vài chục giây) thay vì ở yêu cầu đầu tiên."""
    jev = getattr(decider, "model_decider", decider)
    if isinstance(jev, JevDecider):
        jev.model.predict_logits([{"state": "stage: decide", "questions": {
            "real_fire": {"type": "boolean", "candidates": []}}}])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--decider", choices=["rules", "jev", "hybrid"], default="jev")
    p.add_argument("--jev-run", default="jev1", help="thư mục trong runs/decider/jev/")
    p.add_argument("--device", default="cpu", help="cpu hoặc cuda")
    p.add_argument("--tau", type=float, default=0.8, help="ngưỡng tin cậy của chế độ lai")
    p.add_argument("--model-stages", default="verify", help="chế độ lai: verify, decide hoặc decide,verify")
    p.add_argument("--host", required=True, help="địa chỉ bind, vd 192.168.1.50 hoặc 0.0.0.0 (mọi giao diện)")
    p.add_argument("--port", type=int, default=8090)
    args = p.parse_args()
    try:
        parse_stages(args.model_stages)
    except ValueError as e:
        p.error(str(e))

    decider = make_decider(args.decider, args.jev_run, args.tau, args.device, args.model_stages)
    if args.decider != "rules":
        print(f"Đang nạp mô hình {args.jev_run} trên {args.device}...", flush=True)
        warm_up(decider)
    server = make_server(decider, args.host, args.port)
    print(f"Bộ quyết định {decider.name} tại http://{args.host}:{args.port}  "
          "(không xác thực, chỉ dùng trong LAN phòng lab)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Đang tắt...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
