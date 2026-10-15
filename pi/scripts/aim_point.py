"""Cổng tuần 2: gõ tọa độ điểm trên bảng, Pi tính góc, node quay, bắn laser và đo vết (CSV).

    uv run --no-sync python scripts/aim_point.py --node s1 --x 0.30 --z 0.20
    uv run --no-sync python scripts/aim_point.py --node s1 --points diem.csv --repeat 3
    uv run --no-sync python scripts/aim_point.py --node s2 --grid                  # lưới 3x3 trong bảng
    uv run python scripts/aim_point.py --source sim --node s1 --x 0.30 --z 0.20    # thử trên sa bàn ảo
    uv run python scripts/aim_point.py --source sim --camera left --node s1 --surface floor --grid
    uv run --no-sync python scripts/aim_point.py --node s1 --surface floor --grid  # lưới trên nền
    uv run --no-sync python scripts/aim_point.py --node s1 --surface floor --x 0.40 --y 0.50

--points là CSV có cột x,z (m, hệ sa bàn; thêm cột node để đổi vòi từng điểm). Với `--surface floor`
(bia trên mặt bàn, Z = 0) điểm là (x, y): --y thay --z, CSV cần cột x,y, và --grid chỉ lấy các điểm nền
mà node với tới được (kiểm theo giới hạn pan/tilt 3D); kết quả có thêm cột target_y và spot_y. Mỗi lượt: tính
(pan, tilt) từ tâm quay của node (pose tag + actuator.pivot_offset), kiểm giới hạn góc (ngoài
giới hạn thì báo và bỏ qua, không gửi /aim), /aim, chờ reached, bật laser laser_ms, đo vết bằng
ảnh tắt/bật laser rồi so với điểm đích. Qua nếu lệch không quá --gate-cm (mặc định 5 cm).
Với --source sim: --camera PRESET|x,y,z, --look-at x,y,z, --layout corners|posts (mặc định corners) chọn camera và bố cục node.
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
from nt532.sim import add_sim_args

OUT_DIR = REPO_ROOT / "runs" / "measure"


def grid_points(site: dict, margin: float = 0.15, n: int = 3) -> list[tuple[float, float]]:
    """Lưới n x n điểm (x, z) trong bảng, cách mép `margin` m (đủ xa mép để vết laser không rơi ra ngoài)."""
    w, h = site["board"]["width"], site["board"]["height"]
    return [(float(x), float(z)) for z in np.linspace(margin, h - margin, n)
            for x in np.linspace(margin, w - margin, n)]


def floor_grid_points(site: dict, pivot, node: str, margin: float = 0.15, n: int = 3,
                      yaw_deg: float = 0.0) -> list[tuple[float, float]]:
    """Lưới n x n điểm (x, y) trên nền, cách mép `margin` m, chỉ giữ điểm `node` (tâm quay `pivot`) với tới."""
    w = site.get("floor", {}).get("width", site["board"]["width"])
    d = site.get("floor", {}).get("depth", site["board"]["plane_y"])
    return [(float(x), float(y)) for y in np.linspace(margin, d - margin, n)
            for x in np.linspace(margin, w - margin, n)
            if aiming.reachable_point(site, node, pivot, (x, y, 0.0), yaw_deg)]


def read_points(path: Path, default_node: str, surface: str = "wall") -> list[tuple]:
    """(node, x, z) cho bảng; (node, x, 0.0, y) cho nền (CSV cột x,y)."""
    cols = ("x", "y") if surface == "floor" else ("x", "z")
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows or not set(cols) <= set(rows[0]):
        raise ValueError(f"{path.name} cần cột {','.join(cols)} (mét)")

    def node(r):
        return (r.get("node") or default_node).strip()

    if surface == "floor":
        return [(node(r), float(r["x"]), 0.0, float(r["y"])) for r in rows]
    return [(node(r), float(r["x"]), float(r["z"])) for r in rows]


def wait_heartbeat(link, node: str, timeout: float = 5.0) -> None:
    """Chờ node trả lời /hb đầu tiên (vừa khởi động thì chưa có, _aim sẽ từ chối)."""
    end = time.monotonic() + timeout
    while link.hb_age_ms(node) > HB_MAX_MS and time.monotonic() < end:
        time.sleep(0.1)


def measure_point(st, node: str, x: float, z: float, args, y: float | None = None) -> dict:
    """Một lượt ngắm + đo. Trả các cột của dòng CSV (spot/miss để trống nếu không đo được).
    `y` có giá trị: điểm (x, y, z) trên nền (z = 0); None: điểm (x, z) trên bảng như cũ."""
    site, orch = st.site, st.orch
    row = {"node": node, "target_x": x, "target_z": z, "pan": "", "tilt": "", "spot_x": "",
           "spot_z": "", "miss_cm": "", "pass": "", "note": ""}
    if y is not None:
        row = {**row, "target_y": y, "spot_y": ""}
    y = site["board"]["plane_y"] if y is None else y
    if node not in st.vision.nodes:
        row["note"] = f"chưa thấy pose của {node} (chạy commission.py)"
        return row
    pivot = st.vision.nodes[node].to_world(aiming.pivot_offset(site))
    yaw = aiming.node_yaw(site, node, st.vision.nodes[node])
    pan, tilt = aiming.angles(pivot, (x, y, z), yaw)
    if args.water:
        tilt += aiming.water_tilt(site, node, aiming.horizontal_distance(pivot, (x, y, z)))
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
    miss_cm = float(np.linalg.norm(spot - np.array([x, y, z])) * 100)
    row.update(spot_x=round(float(spot[0]), 4), spot_z=round(float(spot[2]), 4),
               miss_cm=round(miss_cm, 2), **{"pass": miss_cm <= args.gate_cm})
    if "spot_y" in row:
        row["spot_y"] = round(float(spot[1]), 4)
    return row


def run_points(st, points, args, out: Path) -> list[dict]:
    rows, counts = [], {}
    try:
        for node, x, z, *rest in points:  # điểm trên nền có thêm phần tử thứ tư: y
            y = rest[0] if rest else None
            at = f"{x:.2f}, {z:.2f}" if y is None else f"{x:.2f}, {y:.2f} nền"
            for _ in range(args.repeat):
                counts[node] = n = counts.get(node, 0) + 1
                row = measure_point(st, node, x, z, args, y)
                rows.append(row)
                append_row(out, node, n, row)
                if row["miss_cm"] != "":
                    print(f"  {node} ({at}) pan {row['pan']} tilt {row['tilt']}: vết ({row['spot_x']}, "
                          f"{row['spot_z']}), lệch {row['miss_cm']} cm {'QUA' if row['pass'] else 'TRƯỢT'}")
                else:
                    print(f"  {node} ({at}) pan {row['pan']} tilt {row['tilt']}: {row['note']}")
    finally:
        st.link.stop()
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--node", default="s1")
    p.add_argument("--x", type=float, help="X điểm đích trên bảng (m)")
    p.add_argument("--z", type=float, help="Z điểm đích trên bảng (m)")
    p.add_argument("--y", type=float, help="Y điểm đích trên nền (m), dùng với --surface floor")
    p.add_argument("--surface", choices=["wall", "floor"], default="wall",
                   help="bia trên bảng đứng (mặc định) hay trên nền")
    p.add_argument("--points", type=Path, help="CSV cột x,z (và node) thay cho --x/--z")
    p.add_argument("--grid", action="store_true", help="lưới 3x3 trong bảng (kế hoạch: 9 vị trí)")
    p.add_argument("--repeat", type=int, default=1, help="số lượt mỗi điểm")
    p.add_argument("--water", action="store_true", help="cộng water_tilt_deg vào tilt")
    p.add_argument("--no-laser", action="store_true", help="chỉ quay node, không bắn laser")
    p.add_argument("--gate-cm", type=float, default=5.0, help="ngưỡng qua cổng")
    p.add_argument("--source", default=None, help="sim, số webcam, file video hoặc URL stream")
    p.add_argument("--link", choices=["mem", "coap"], default="mem", help="chỉ cho --source sim")
    p.add_argument("--out", type=Path, default=None, help="mặc định runs/measure/aim_<thời điểm>.csv")
    add_sim_args(p)
    args = p.parse_args()

    from nt532.sim import sim_options
    from nt532.station import build_real, build_sim

    sim = args.source == "sim"
    if not sim and (args.camera or args.look_at or args.layout):
        p.error("--camera, --look-at và --layout chỉ dùng với --source sim")
    st = build_sim(link=args.link, **sim_options(args, p)) if sim else build_real(source=args.source)
    try:
        floor = args.surface == "floor"
        if args.points:
            points = read_points(args.points, args.node, args.surface)
        elif args.grid and floor:
            if st.vision.state is None or args.node not in st.vision.nodes:
                raise SystemExit(f"chưa thấy pose của {args.node} (chạy commission.py)")
            pivot = st.vision.nodes[args.node].to_world(aiming.pivot_offset(st.site))
            points = [(args.node, x, 0.0, y) for x, y in floor_grid_points(
                st.site, pivot, args.node, yaw_deg=aiming.node_yaw(st.site, args.node, st.vision.nodes[args.node]))]
            if not points:
                raise SystemExit(f"{args.node} không với tới điểm nền nào trong lưới (giới hạn tilt?)")
        elif args.grid:
            points = [(args.node, x, z) for x, z in grid_points(st.site)]
        elif floor and args.x is not None and args.y is not None:
            points = [(args.node, args.x, 0.0, args.y)]
        elif not floor and args.x is not None and args.z is not None:
            points = [(args.node, args.x, args.z)]
        else:
            p.error("cần --x và --y (nền) hoặc --x và --z (bảng), hoặc --points, hoặc --grid")
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
