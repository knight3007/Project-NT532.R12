"""Tìm địa chỉ node theo tên qua SRP server của border router (DNS-SD của Thread).

Node đăng ký `<id>._nt532._udp` với SRP server trong otbr-agent; Pi đọc bảng đó bằng
`ot-ctl srp server service`. Định dạng (openthread src/cli/README_SRP_SERVER.md), mỗi dịch vụ một khối,
dòng đầu không thụt, các dòng sau thụt 4 khoảng trắng, kết thúc bằng `Done`:

    s1._nt532._udp.default.service.arpa.
        deleted: false
        subtypes: (null)
        port: 5683
        ...
        TXT: [6e=7331]
        host: s1.default.service.arpa.
        addresses: [fd00:db8:a0:0:1:2:3:4, fd12:3456:789a:1:1:2:3:4]

Không tìm được (thiếu ot-ctl, không đủ quyền, quá hạn, node chưa đăng ký) thì người gọi dùng địa chỉ tĩnh
trong site.yaml; hàm này không bao giờ ném lỗi, chỉ trả kèm lý do.
"""

import ipaddress
import re
import subprocess

SERVICE = "_nt532._udp"


def _rank(addr: str, mesh_local_prefix: str | None = None) -> int | None:
    """Thứ tự ưu tiên (nhỏ hơn là tốt hơn), None nếu bỏ.

    Ưu tiên địa chỉ OMR/global hơn mesh-local: prefix OMR do border router quảng bá và không đổi khi
    xóa dữ liệu Thread, còn mesh-local EID đổi theo dataset. Node dùng auto host address nên chỉ đăng ký
    OMR khi có, còn không thì mesh-local EID. Địa chỉ RLOC (đuôi 0:ff:fe00:xxxx) đổi theo cây định tuyến
    nên xếp cuối; link-local không route được từ Pi nên bỏ."""
    try:
        ip = ipaddress.IPv6Address(addr.strip())
    except ValueError:
        return None
    if ip.is_link_local or ip.is_loopback or ip.is_unspecified or ip.is_multicast:
        return None
    if ip.packed[8:14] == bytes.fromhex("000000fffe00"):
        return 3  # RLOC
    if (int(ip) >> 125) == 0b001:
        return 0  # global unicast
    if mesh_local_prefix and ip in ipaddress.IPv6Network(mesh_local_prefix, strict=False):
        return 2
    return 1  # ULA khác (thường là OMR)


def parse_srp_services(text: str, mesh_local_prefix: str | None = None) -> dict[str, str]:
    """Từ đầu ra `srp server service` trả {tên instance: địa chỉ tốt nhất}, chỉ `_nt532._udp`, bỏ mục đã xóa."""
    found: dict[str, str] = {}
    name: str | None = None
    deleted = False
    addrs: list[str] = []

    def flush():
        if name and not deleted and addrs:
            ranked = [(r, i, a) for i, a in enumerate(addrs) if (r := _rank(a, mesh_local_prefix)) is not None]
            if ranked:
                found[name] = min(ranked)[2]

    for line in text.splitlines():
        if not line.strip() or line.strip() in ("Done",) or line.startswith("Error"):
            continue
        if not line[0].isspace():  # dòng đầu của khối: tên đầy đủ của instance
            flush()
            deleted, addrs, name = False, [], None
            label, _, rest = line.strip().partition(".")
            if rest.startswith(SERVICE + "."):
                name = label
            continue
        key, _, val = line.strip().partition(":")
        val = val.strip()
        if key == "deleted":
            deleted = val.lower() == "true"
        elif key == "addresses":
            addrs = [a.strip() for a in val.strip("[]").split(",") if a.strip()]
    flush()
    return found


def discover_nodes(names, timeout_s: float = 5.0, cmd: tuple[str, ...] = ("ot-ctl",),
                   mesh_local_prefix: str | None = None) -> tuple[dict[str, str], str | None]:
    """Hỏi SRP server, trả ({tên: địa chỉ} cho các tên tìm thấy, lý do lỗi hoặc None).
    Không dùng sudo; nếu ot-ctl cần quyền, lý do ghi rõ để chạy lại dưới nhóm/ người dùng phù hợp."""
    try:
        p = subprocess.run([*cmd, "srp", "server", "service"], capture_output=True, text=True, timeout=timeout_s,
                           check=False)
    except FileNotFoundError:
        return {}, f"không có lệnh {cmd[0]} trên máy này"
    except subprocess.TimeoutExpired:
        return {}, f"{cmd[0]} không trả lời trong {timeout_s:g} s"
    except OSError as e:
        return {}, f"không chạy được {cmd[0]}: {e}"
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0 or re.search(r"permission denied|Error \d+:", out, re.IGNORECASE):
        why = out.strip().splitlines()[-1] if out.strip() else f"mã thoát {p.returncode}"
        hint = " (có thể cần quyền: thêm người dùng vào nhóm của otbr-agent hoặc chạy ot-ctl bằng sudo)" \
            if re.search(r"permission|denied|socket|connect", out, re.IGNORECASE) else ""
        return {}, f"ot-ctl báo lỗi: {why}{hint}"
    every = parse_srp_services(p.stdout, mesh_local_prefix)
    got = {n: every[n] for n in names if n in every}
    missing = [n for n in names if n not in every]
    return got, (f"SRP chưa có node {', '.join(missing)}" if missing else None)
