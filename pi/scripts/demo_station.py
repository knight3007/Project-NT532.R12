"""Demo đầu cuối của trạm trên sa bàn ảo: dàn lần lượt các tình huống, để trạm tự xử lý, đo và chấm.

    uv run python scripts/demo_station.py                      # mọi cảnh, dashboard :8080, nghỉ 3 s giữa các cảnh
    uv run python scripts/demo_station.py --step               # chờ Enter trước mỗi cảnh (trình diễn)
    uv run python scripts/demo_station.py --headless --fast --scenes fire_s1,lamp,estop --report
    uv run python scripts/demo_station.py --decider hybrid --model-stages decide,verify   # cần runs/decider/jev/jev1

Cảnh: fire_s1, fire_s2, fire_large, lamp, steam_object, spike, node_offline, estop (docs/demo.md).
Mỗi cảnh in tiêu đề, hành vi mong đợi, kết quả; chấm riêng quyết định (ĐẠT/KHÔNG ĐẠT, không đổi mã thoát)
và an toàn (VI PHẠM AN TOÀN thì mã thoát 1). Ghi nhật ký vào <runs-dir>/station/<thời điểm>.jsonl, tóm tắt
vào <runs-dir>/demo/<thời điểm>/summary.json; --report dựng thêm báo cáo HTML chỉ từ lần demo này.
Ctrl+C: gửi /stop tới mọi node, vẫn ghi tóm tắt dở dang.
"""

import argparse
import json
import sys
import textwrap
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from nt532.config import REPO_ROOT
from nt532.dashboard import Dashboard
from nt532.orchestrator.decide import parse_stages
from nt532.report import build_report, collect
from nt532.sim.demo import SCENES, fast_settings, prepare, run_scene, summary_entry
from nt532.station import build_sim

WIDTH = 100


def shown(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def decision_text(ok: bool | None) -> str:
    return "không chấm" if ok is None else "ĐẠT" if ok else "KHÔNG ĐẠT"


def safety_text(ok: bool) -> str:
    return "AN TOÀN" if ok else "VI PHẠM AN TOÀN"


def say(text: str, indent: int = 6) -> None:
    print(textwrap.fill(text, WIDTH, initial_indent=" " * indent, subsequent_indent=" " * indent))


def nozzles_text(r: dict) -> str:
    return "+".join(r["sprayed"]) if r["sprayed"] else "không phun"


def out_text(r: dict) -> str:
    return "–" if r["fire_out"] is None else "có" if r["fire_out"] else "chưa"


def latency_text(r: dict) -> str:
    return "–" if r["latency_s"] is None else f"{r['latency_s']:.1f}"


def print_result(r: dict) -> None:
    v = r["verdict"]
    say(f"Kết quả: {len(r['runs'])} lượt ({', '.join(map(str, r['outcomes'])) or 'không có'}) · "
        f"vòi phun: {nozzles_text(r)}{' (cùng lúc)' if r['dual_pump'] else ''} · lửa tắt: {out_text(r)} · "
        f"trễ báo động→bơm: {latency_text(r)} s · {r['duration_s']:.0f} s")
    say(f"Quyết định: {decision_text(v['decision_ok'])}    An toàn: {safety_text(v['safety_ok'])}")
    for note in v["notes"]:
        say("- " + note, 8)


def print_table(results: list[dict]) -> None:
    head = ["Cảnh", "Kết cục", "Vòi phun", "Lửa tắt", "Trễ (s)", "Quyết định", "An toàn"]
    rows = [[r["name"], ",".join(map(str, r["outcomes"])) or "–", nozzles_text(r), out_text(r),
             latency_text(r), decision_text(r["verdict"]["decision_ok"]),
             safety_text(r["verdict"]["safety_ok"])] for r in results]
    w = [max(len(str(c)) for c in col) for col in zip(head, *rows)]
    line = "  ".join(h.ljust(n) for h, n in zip(head, w))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(str(c).ljust(n) for c, n in zip(row, w)))


def wait_model(st, timeout_s: float = 300.0) -> None:
    """Jev hoặc chế độ lai nạp mô hình ở luồng nền (vài chục giây); chờ xong để cảnh đầu không bị chậm."""
    end = time.time() + timeout_s
    shown_msg = False
    while time.time() < end:
        done = [e for e in st.events.since(0, 10_000) if e["kind"] == "config"
                and (e["text"].startswith("đã nạp mô hình") or e["text"].startswith("không nạp được mô hình"))]
        if done:
            print(f"Mô hình: {done[-1]['text']}")
            return
        if not shown_msg:
            print("Đang nạp mô hình quyết định...")
            shown_msg = True
        time.sleep(0.5)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)  # chuyển hướng ra file vẫn thấy tiến độ từng cảnh
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--scenes", default=",".join(SCENES), help="danh sách cảnh, cách nhau dấu phẩy "
                                                             f"(mặc định tất cả: {', '.join(SCENES)})")
    p.add_argument("--step", action="store_true", help="chờ Enter trước mỗi cảnh (khi trình diễn)")
    p.add_argument("--pause", type=float, default=3.0, help="giây nghỉ giữa các cảnh (bỏ qua khi --step)")
    p.add_argument("--headless", action="store_true", help="không bật dashboard")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--fast", action="store_true",
                   help="chu kỳ ngắn (khung 0.05 s, chờ VERIFY 1 s) như các test; số liệu độ trễ sẽ nhỏ hơn thật")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--runs-dir", type=Path, default=REPO_ROOT / "runs", help="thư mục ghi station/, demo/, report/")
    p.add_argument("--report", action="store_true",
                   help="sau các cảnh, dựng báo cáo HTML chỉ từ lần demo này vào <runs-dir>/report/<thời điểm>/")
    p.add_argument("--decider", choices=["rules", "jev", "hybrid", "remote"], default="rules")
    p.add_argument("--decider-url", default=None,
                   help="chỉ cho --decider remote: URL của scripts/serve_decider.py, vd http://192.168.1.50:8090")
    p.add_argument("--decider-timeout", type=float, default=3.0,
                   help="giây chờ máy chủ quyết định; quá hạn hoặc lỗi thì lượt đó dùng luật")
    p.add_argument("--jev-run", default="jev1", help="thư mục trong runs/decider/jev/")
    p.add_argument("--tau", type=float, default=0.8, help="ngưỡng tin cậy của chế độ lai")
    p.add_argument("--model-stages", default="verify",
                   help="chế độ lai: giai đoạn hỏi mô hình (verify, decide hoặc decide,verify); giai đoạn "
                        "còn lại dùng luật. Hai vòi phun cùng lúc cần mô hình trả lời ở DECIDE")
    p.add_argument("--detector", choices=["oracle", "yolo"], default="oracle",
                   help="oracle đọc thẻ thật trong scene, yolo dùng trọng số")
    args = p.parse_args()
    try:
        parse_stages(args.model_stages)
    except ValueError as e:
        p.error(str(e))
    if args.decider == "remote" and not args.decider_url:
        p.error("--decider remote cần --decider-url")
    names = [n.strip() for n in args.scenes.split(",") if n.strip()]
    if bad := [n for n in names if n not in SCENES]:
        p.error(f"không có cảnh {', '.join(bad)} (có: {', '.join(SCENES)})")
    if not names:
        p.error("--scenes rỗng")
    if args.pause < 0:
        p.error("--pause không được âm")

    runs_dir = args.runs_dir if args.runs_dir.is_absolute() else Path.cwd() / args.runs_dir
    demo_start = time.time()
    stamp = f"{datetime.now().astimezone():%Y%m%d-%H%M%S}"
    log = runs_dir / "station" / f"{stamp}.jsonl"
    out_dir = runs_dir / "demo" / stamp
    st = build_sim(args.decider, args.jev_run, args.tau, args.detector, args.seed, fps=12.0 if args.fast else 10.0,
                   log_path=log, settings=fast_settings() if args.fast else None,
                   model_stages=args.model_stages, decider_url=args.decider_url,
                   decider_timeout=args.decider_timeout)
    st.start()
    dash = None
    if not args.headless:
        dash = Dashboard(st, args.host, args.port).start()
        print(f"Dashboard: {dash.url}")
    print(f"Bộ quyết định: {st.orch.decider.name} · detector {args.detector} · seed {args.seed}"
          f"{' · chạy nhanh' if args.fast else ''}")
    print(f"Nhật ký: {shown(log)}")

    results: list[dict] = []
    interrupted = False
    try:
        if args.decider in ("jev", "hybrid"):
            wait_model(st)
        for i, name in enumerate(names, 1):
            scene = SCENES[name]
            print(f"\n[{i}/{len(names)}] {name} · {scene.title}")
            say("Mong đợi: " + scene.expect)
            if args.step:
                prepare(st)  # dọn sa bàn trước để khán giả thấy bảng sạch rồi mới bắt đầu
                input("      Nhấn Enter để bắt đầu cảnh... ")
            r = run_scene(st, scene)
            results.append(r)
            print_result(r)
            if i < len(names) and not args.step and args.pause:
                time.sleep(args.pause)
    except KeyboardInterrupt:
        interrupted = True
        print("\nĐang tắt...")
    except EOFError:  # --step mà không có đầu vào
        interrupted = True
        print("\nKhông đọc được Enter (không có đầu vào), dừng demo.")
    finally:
        st.stop()  # gửi /stop tới mọi node
        if dash:
            dash.stop()
        summary = {
            "start": demo_start, "start_text": datetime.fromtimestamp(demo_start).astimezone().isoformat(timespec="seconds"),
            "end": time.time(), "decider": st.orch.decider.name, "detector": args.detector, "seed": args.seed,
            "fast": args.fast, "settings": asdict(st.orch.cfg), "log": shown(log), "interrupted": interrupted,
            "scenes": [summary_entry(r) for r in results],
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                                              encoding="utf-8")

    if results:
        print("\n" + "=" * WIDTH)
        print_table(results)
        n_dec = sum(r["verdict"]["decision_ok"] is True for r in results)
        n_graded = sum(r["verdict"]["decision_ok"] is not None for r in results)
        n_safe = sum(r["verdict"]["safety_ok"] for r in results)
        print(f"\nQuyết định đạt {n_dec}/{n_graded} · an toàn {n_safe}/{len(results)} cảnh"
              + (" · dừng giữa chừng" if interrupted else ""))
    print(f"Tóm tắt: {shown(out_dir / 'summary.json')}")
    if args.report:
        out = runs_dir / "report" / stamp
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(build_report(collect(runs_dir, since=demo_start)), encoding="utf-8")
        print(f"Báo cáo: {out / 'index.html'}")
    return 1 if any(not r["verdict"]["safety_ok"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
