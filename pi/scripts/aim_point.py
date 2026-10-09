"""Cổng tuần 2: gõ tọa độ điểm trên bảng, Pi tính góc, node quay, bắn laser và đo vết (CSV).

    uv run --no-sync python scripts/aim_point.py --node s1 --x 0.30 --z 0.20
    uv run --no-sync python scripts/aim_point.py --node s1 --points diem.csv --repeat 3
    uv run --no-sync python scripts/aim_point.py --node s2 --grid                  # lưới 3x3 trong bảng
    uv run python scripts/aim_point.py --source sim --node s1 --x 0.30 --z 0.20    # thử trên sa bàn ảo

--points là CSV có cột x,z (m, hệ sa bàn; thêm cột node để đổi vòi từng điểm). Mỗi lượt: tính
(pan, tilt) từ tâm quay của node (pose tag + actuator.pivot_offset), kiểm giới hạn góc (ngoài
giới hạn thì báo và bỏ qua, không gửi /aim), /aim, chờ reached, bật laser laser_ms, đo vết bằng
ảnh tắt/bật laser rồi so với điểm đích. Qua nếu lệch không quá --gate-cm (mặc định 5 cm).
--no-laser chỉ quay node, không đo. --water cộng góc bù tilt của vòi (water_tilt_table/water_tilt_deg) vào tilt như lúc phun
(vết laser sẽ nằm cao hơn đích đúng góc đó). Luôn gửi /stop khi thoát. Không chạy orchestrator.
Ghi thêm vào runs/measure/aim_<thời điểm>.csv (đổi bằng --out).
"""

import argparse
import csv
import time
from pathlib import Path

import numpy as np

from nt532.config import REPO_ROOT
from nt532.decider.rules import HB_MAX_MS
from nt532.measure import append_row
from nt532.orchestrator import aiming

OUT_DIR = REPO_ROOT / "runs" / "measure"


def grid_points(site: dict, margin: float = 0.15, n: int = 3) -> list[tuple[float, float]]:
    """Lưới n x n điểm (x, z) trong bảng, cách mép `margin` m (đủ xa mép để vết laser không rơi ra ngoài)."""
    w, h = site["board"]["width"], site["board"]["height"]
    return [(float(x), float(z)) for z in np.linspace(margin, h - margin, n)
            for x in np.linspace(margin, w - margin, n)]


def read_points(path: Path, default_node: str) -> list[tuple[str, float, float]]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows or not {"x", "z"} <= set(rows[0]):
        raise ValueError(f"{path.name} cần cột x,z (mét)")
    return [((r.get("node") or default_node).strip(), float(r["x"]), float(r["z"])) for r in rows]


def wait_heartbeat(link, node: str, timeout: float = 5.0) -> None:
    """Chờ node trả lời /hb đầu tiên (vừa khởi động thì chưa có, _aim sẽ từ chối)."""
    end = time.monotonic() + timeout
    while link.hb_age_ms(node) > HB_MAX_MS and time.monotonic() < end:
        time.sleep(0.1)


def measure_point(st, node: str, x: float, z: float, args) -> dict:
    """Một lượt ngắm + đo. Trả các cột của dòng CSV (spot/miss để trống nếu không đo được)."""
    site, orch = st.site, st.orch
    row = {"node": node, "target_x": x, "target_z": z, "pan": "", "tilt": "", "spot_x": "",
           "spot_z": "", "miss_cm": "", "pass": "", "note": ""}
    if node not in st.vision.nodes:
        row["note"] = f"chưa thấy pose của {node} (chạy commission.py)"
        return row
    pivot = st.vision.nodes[node].to_world(aiming.pivot_offset(site))
    pan, tilt = aiming.angles(pivot, (x, site["board"]["plane_y"], z))
    if args.water:
        tilt += aiming.water_tilt(site, node, aiming.horizontal_distance(pivot, (x, site["board"]["plane_y"], z)))
    row["pan"], row["tilt"] = round(pan, 2), round(tilt, 2)
    if not aiming.limits(site, node).ok(pan, tilt):
        row["note"] = "ngoài giới hạn góc, bỏ qua"
        return row
    wait_heartbeat(st.link, node)
    try:
        cmd = orch._aim(node, pan, tilt)  # kiểm giới hạn, heartbeat, gửi lại một lần như lúc chạy thật
    except Exception as e:  # noqa: BLE001 - lỗi link/abort vẫn phải ghi dòng và đi tiếp
        row["note"] = f"{type(e).__name__}: {e}"
        return row
    if args.no_laser:
        row["note"] = "đã quay, không bắn laser"
        return row
    laser_ms = orch.cfg.laser_ms
    off = st.hub.fresh()
    st.link.fire(node, cmd, "laser", laser_ms)
    time.sleep(0.15)  # chờ laser bật; fresh() chỉ trả khung chụp sau lúc gọi (đã trừ độ trễ stream)
    on = st.hub.fresh()
    spot = st.vision.find_spot(on, off)
    st.link.wait(cmd, ("done", "fault"), laser_ms / 1000 + 1)
    if spot is None:
        row["note"] = "không thấy vết laser"
        return row
    miss_cm = float(np.hypot(spot[0] - x, spot[2] - z) * 100)
    row.update(spot_x=round(float(spot[0]), 4), spot_z=round(float(spot[2]), 4),
               miss_cm=round(miss_cm, 2), **{"pass": miss_cm <= args.gate_cm})
    return row


def run_points(st, points, args, out: Path) -> list[dict]:
    rows, counts = [], {}
    try:
        for node, x, z in points:
            for _ in range(args.repeat):
                counts[node] = n = counts.get(node, 0) + 1
                row = measure_point(st, node, x, z, args)
                rows.append(row)
                append_row(out, node, n, row)
                if row["miss_cm"] != "":
                    print(f"  {node} ({x:.2f}, {z:.2f}) pan {row['pan']} tilt {row['tilt']}: vết ({row['spot_x']}, "
                          f"{row['spot_z']}), lệch {row['miss_cm']} cm {'QUA' if row['pass'] else 'TRƯỢT'}")
                else:
                    print(f"  {node} ({x:.2f}, {z:.2f}) pan {row['pan']} tilt {row['tilt']}: {row['note']}")
    finally:
        st.link.stop()
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--node", default="s1")
    p.add_argument("--x", type=float, help="X điểm đích trên bảng (m)")
    p.add_argument("--z", type=float, help="Z điểm đích trên bảng (m)")
    p.add_argument("--points", type=Path, help="CSV cột x,z (và node) thay cho --x/--z")
    p.add_argument("--grid", action="store_true", help="lưới 3x3 trong bảng (kế hoạch: 9 vị trí)")
    p.add_argument("--repeat", type=int, default=1, help="số lượt mỗi điểm")
    p.add_argument("--water", action="store_true", help="cộng water_tilt_deg vào tilt")
    p.add_argument("--no-laser", action="store_true", help="chỉ quay node, không bắn laser")
    p.add_argument("--gate-cm", type=float, default=5.0, help="ngưỡng qua cổng")
    p.add_argument("--source", default=None, help="sim, số webcam, file video hoặc URL stream")
    p.add_argument("--link", choices=["mem", "coap"], default="mem", help="chỉ cho --source sim")
    p.add_argument("--out", type=Path, default=None, help="mặc định runs/measure/aim_<thời điểm>.csv")
    args = p.parse_args()

    from nt532.station import build_real, build_sim

    sim = args.source == "sim"
    st = build_sim(link=args.link) if sim else build_real(source=args.source)
    try:
        if args.points:
            points = read_points(args.points, args.node)
        elif args.grid:
            points = [(args.node, x, z) for x, z in grid_points(st.site)]
        elif args.x is not None and args.z is not None:
            points = [(args.node, args.x, args.z)]
        else:
            p.error("cần --x và --z, hoặc --points, hoặc --grid")
        if st.vision.state is None:
            raise SystemExit("chưa có calibration/commissioning.yaml, chạy commission.py trước")
        out = args.out or OUT_DIR / f"aim_{time.strftime('%Y%m%d-%H%M%S')}.csv"
        st.start(orchestrate=False)
        rows = run_points(st, points, args, out)
    finally:
        st.stop()
    done = [r for r in rows if r["miss_cm"] != ""]
    if done:
        ok = sum(bool(r["pass"]) for r in done)
        print(f"{ok}/{len(done)} lượt trong {args.gate_cm:g} cm; "
              f"lệch trung bình {np.mean([r['miss_cm'] for r in done]):.1f} cm")
    out_s = out.relative_to(REPO_ROOT) if out.is_relative_to(REPO_ROOT) else out
    print(f"Đã ghi {out_s}")


if __name__ == "__main__":
    main()
