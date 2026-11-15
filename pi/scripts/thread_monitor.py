"""Đo mạng Thread (ping, CoAP, vai trò, topology) và đẩy lên dashboard control, cho báo cáo.

    NT532_PI_TOKEN=... uv run --no-sync python scripts/thread_monitor.py --control-url https://control.example.com
    uv run --no-sync python scripts/thread_monitor.py --control-url ... --period 10 --node s1=fd59::1 --node s2=fd59::2

Node lấy từ config/site.yaml (network.nodes, bỏ node chưa có địa chỉ); --node ghi đè. Pi chỉ là HTTP client:
POST <control-url>/api/pi/thread với Bearer token. Token không bao giờ ghi ra log. Ctrl+C để thoát.
"""

import argparse
import asyncio
import os
import time

from nt532.config import load_site
from nt532.dashboard.push import MAX_BACKOFF, describe_error, post_json
from nt532.net.thread_stats import OTBR_URL, Collector


def parse_nodes(site_nodes: dict | None, overrides: list[str]) -> dict[str, str]:
    nodes = {k: v for k, v in (site_nodes or {}).items() if isinstance(v, str) and v}
    for item in overrides:
        name, sep, addr = item.partition("=")
        if not sep or not name or not addr:
            raise SystemExit(f"--node phải có dạng tên=địa_chỉ, nhận được {item!r}")
        nodes[name] = addr
    return nodes


async def run(args, nodes: dict[str, str]) -> None:
    endpoint = args.control_url.rstrip("/") + "/api/pi/thread"
    collector = Collector(args.otbr_url, args.topology_every)
    backoff, down = 0.0, False
    print(f"đo {', '.join(nodes)} mỗi {args.period:g} s, đẩy lên {endpoint}, Ctrl+C để thoát")
    while True:
        t0 = time.monotonic()
        wait = args.period
        try:
            sample = await collector.collect_async(nodes, args.ping_count)
            await asyncio.to_thread(post_json, endpoint, args.control_token, sample, 10.0)
            if down:
                print("đã nối lại control")
                down = False
            backoff = 0.0
        except Exception as e:  # noqa: BLE001 - mạng/HTTP: thử lại với backoff
            why, _ = describe_error(e)
            if not down:
                print(f"không đẩy được lên control: {why}")
                down = True
            backoff = min(MAX_BACKOFF, max(1.0, backoff * 2))
            wait = max(wait, backoff)
        await asyncio.sleep(max(0.0, wait - (time.monotonic() - t0)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control-url", required=True)
    ap.add_argument("--control-token", default=os.environ.get("NT532_PI_TOKEN", ""))
    ap.add_argument("--period", type=float, default=10.0, help="giây giữa hai lượt đo")
    ap.add_argument("--ping-count", type=int, default=5)
    ap.add_argument("--otbr-url", default=OTBR_URL)
    ap.add_argument("--topology-every", type=float, default=60.0, help="giây giữa hai lần lấy topology")
    ap.add_argument("--node", action="append", default=[], metavar="TÊN=ĐỊA_CHỈ")
    args = ap.parse_args()
    if not args.control_token:
        raise SystemExit("thiếu token: đặt NT532_PI_TOKEN hoặc --control-token")
    if args.period <= 0 or args.ping_count < 1:
        raise SystemExit("--period phải > 0 và --ping-count >= 1")
    nodes = parse_nodes((load_site().get("network") or {}).get("nodes"), args.node)
    if not nodes:
        raise SystemExit("không có node nào: điền network.nodes trong config/site.yaml hoặc dùng --node")
    try:
        asyncio.run(run(args, nodes))
    except KeyboardInterrupt:
        print("thoát")


if __name__ == "__main__":
    main()
