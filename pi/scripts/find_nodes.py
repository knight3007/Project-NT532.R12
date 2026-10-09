"""Tìm node trên mạng Thread qua SRP của border router (cần ot-ctl trên máy này).

    uv run python scripts/find_nodes.py              # in địa chỉ s1, s2 tìm được
    uv run python scripts/find_nodes.py s1 --timeout 10

Thoát 0 nếu thấy đủ node, 1 nếu thiếu. Lệnh in sẵn dòng dán vào config/site.yaml khi cần nhập tay.
"""

import argparse
import sys

from nt532.net.discover import discover_nodes


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("names", nargs="*", default=["s1", "s2"], help="tên node cần tìm")
    p.add_argument("--timeout", type=float, default=5.0, help="thời gian chờ ot-ctl, giây")
    a = p.parse_args()
    found, why = discover_nodes(a.names, a.timeout)
    for n in a.names:
        print(f"{n}: {found.get(n, 'không thấy')}")
    if why:
        print(why, file=sys.stderr)
    if found:
        print("\nnetwork.nodes (nhập tay nếu cần):")
        for n, addr in found.items():
            print(f'    {n}: "{addr}"')
    return 0 if len(found) == len(a.names) else 1


if __name__ == "__main__":
    sys.exit(main())
