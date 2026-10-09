"""Node chấp hành + cảm biến giả chạy lõi C thật của firmware, nói CoAP với Pi.

    uv run python scripts/fake_node.py --node s1 --port 5683 --pi coap://[fd00::1]:5683
    uv run python scripts/fake_node.py --node s1 --bind 127.0.0.1 --port 5701 --pi coap://127.0.0.1:5683 --temp 60

Nhận /aim /fire /stop /hb /alarm đúng hợp đồng, gửi /status (CON), /a (CON), /t (NON). Dùng để thử Pi khi
chưa có H2 và làm bản tham chiếu cho firmware. Cần gcc lần đầu (biên dịch firmware/sensor-h2/test/libcore.so).
Phím: gõ `t <độ C>` hoặc `g <gas>` rồi Enter để đổi số đọc, `off`/`on` để cắt/nối mạng, Ctrl+C để thoát.
"""

import argparse
import sys
import threading
import time

from nt532.net.fakenode import ConstSensors, FakeNode


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--node", default="s1", help="tên node (tối đa 7 ký tự), khớp site.yaml")
    p.add_argument("--bind", default="::", help="địa chỉ lắng nghe")
    p.add_argument("--port", type=int, default=5683)
    p.add_argument("--pi", default=None, help="URI gốc của Pi, ví dụ coap://[fd00::1]:5683 (bỏ trống: không gửi lên)")
    p.add_argument("--peer", action="append", default=[], help="URI gốc node khác nhận /alarm (lặp lại được)")
    p.add_argument("--telemetry-s", type=float, default=1.0, help="chu kỳ /t, giây")
    p.add_argument("--temp", type=float, default=27.0, help="nhiệt độ ban đầu, °C")
    p.add_argument("--gas", type=float, default=180.0, help="gas ban đầu")
    p.add_argument("--warmup-s", type=float, default=0.0, help="bỏ qua gas trong chừng này giây đầu")
    p.add_argument("--transport", action="append", default=None,
                   help="transport aiocoap (ví dụ simplesocketserver khi máy không có IPv6)")
    args = p.parse_args()

    src = ConstSensors(args.temp, args.gas)
    node = FakeNode(args.node, (args.bind, args.port), args.pi, sensors=src, telemetry_s=args.telemetry_s,
                    warmup_s=args.warmup_s, transports=args.transport, alarm_peers=args.peer,
                    on_event=lambda kind, text: print(f"{time.strftime('%H:%M:%S')} {kind:7s} {text}", flush=True))
    node.hw.set_dev = _echo(node.hw.set_dev)
    node.start()
    print(f"Node giả {args.node} nghe {node.url}, gửi lên {args.pi or '(không)'}", flush=True)
    threading.Thread(target=_keys, args=(node, src), daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Đang tắt...")
    finally:
        node.stop()


def _echo(fn):
    def wrapped(dev, on):
        print(f"{time.strftime('%H:%M:%S')} hw      {dev} {'BẬT' if on else 'tắt'}", flush=True)
        fn(dev, on)

    return wrapped


def _keys(node: FakeNode, src: ConstSensors) -> None:
    for line in sys.stdin:
        w = line.split()
        try:
            if w[0] == "t":
                src.temp = float(w[1])
            elif w[0] == "g":
                src.gas = float(w[1])
            elif w[0] in ("on", "off"):
                node.online = w[0] == "on"
        except (IndexError, ValueError):
            print("lệnh: t <°C> | g <gas> | on | off", flush=True)


if __name__ == "__main__":
    main()
