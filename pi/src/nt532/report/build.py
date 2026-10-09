"""Gom số đo trong runs/ thành một trang HTML tự đủ (CSS và biểu đồ SVG nhúng sẵn).

Mỗi mục chỉ xuất hiện khi có dữ liệu; mục nào cũng ghi nguồn file và số mẫu N.
"""

import csv
import json
import math
import re
import statistics
import time
from datetime import datetime
from html import escape
from pathlib import Path

from . import svg

GATE_CM = 5.0
CORRECT_RE = re.compile(r"vòng (\d+): lệch ([\d.]+) cm")
QUESTIONS = ("real_fire", "action", "target", "nozzle", "after_verify")
SYS_LABELS = {  # khóa -> (mô tả, khóa mẫu số)
    "false_spray": ("phun khi không có lửa", "no_fire"),
    "missed_spray": ("lửa thật cần phun mà không phun", "should_spray"),
    "wrong_target": ("phun nhầm bia", "should_spray"),
    "wrong_nozzle": ("phun đúng bia, nhầm vòi", "should_spray"),
    "sprayed_correctly": ("phun đúng bia đúng vòi", "should_spray"),
}


# --- đọc dữ liệu ----------------------------------------------------------------------------


def parse_since(text: str | None) -> float:
    """"2026-10-09", "20261009", "2026-10-09 08:00" hoặc "20261009-080000" -> epoch; rỗng -> 0."""
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d-%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).astimezone().timestamp()  # giờ máy
        except ValueError:
            pass
    raise ValueError(f"--since không hiểu: {text!r} (dạng 2026-10-09 hoặc 20261009-080000)")


def num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _row_time(row: dict) -> float | None:
    try:
        return datetime.strptime(row.get("time", ""), "%Y-%m-%d %H:%M:%S").astimezone().timestamp()
    except ValueError:
        return None


def read_csv(path: Path, since: float) -> list[dict]:
    try:
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError, csv.Error):
        return []
    if since:
        rows = [r for r in rows if (t := _row_time(r)) is None or t >= since]
    return rows


def read_jsonl(path: Path, since: float) -> list[dict]:
    out = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("time", since) >= since:
                    out.append(ev)
    except (OSError, UnicodeDecodeError):
        pass
    return out


def read_json(path: Path, since: float) -> dict | None:
    try:
        if path.stat().st_mtime < since:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def collect(runs_dir: Path, since: float = 0.0) -> dict:
    """Đọc mọi nguồn có trong runs_dir; nguồn thiếu thì để rỗng. Phân loại CSV theo tên cột."""
    runs_dir = Path(runs_dir)
    d = {"root": runs_dir, "target": [], "tag": [], "aim": [], "stream": [], "load": [],
         "station": [], "rules_eval": [], "jev_eval": []}
    csvs = sorted([*runs_dir.glob("measure/*.csv"), *runs_dir.glob("pi_load*.csv")])
    for p in csvs:
        rows = read_csv(p, since)
        if not rows:
            continue
        cols = set(rows[0])
        if "miss_cm" in cols:
            kind = "aim"
        elif "cpu_pct" in cols:
            kind = "load"
        elif cols & {"latency_ms", "latency_s"}:
            kind = "stream"
        elif "truth_y" in cols:
            kind = "tag"
        elif "truth_z" in cols:
            kind = "target"
        else:
            continue
        d[kind].append((p, rows))
    for p in sorted(runs_dir.glob("station/*.jsonl")):
        evs = read_jsonl(p, since)
        if evs:
            d["station"].append((p, evs))
    for name in ("rules_eval.json", "rules_eval_data.json"):
        if (data := read_json(runs_dir / "decider" / name, since)) is not None:
            d["rules_eval"].append((runs_dir / "decider" / name, data))
    for p in sorted(runs_dir.glob("decider/jev/*/eval.json")):
        if (data := read_json(p, since)) is not None:
            d["jev_eval"].append((p, data))
    return d


# --- thống kê -------------------------------------------------------------------------------


def pctl(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def stats(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "p95": pctl(values, 0.95), "max": max(values), "min": min(values)}


def f(v, nd=2) -> str:
    return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{nd}f}"


def pct(a: int, b: int) -> str:
    return "–" if not b else f"{a / b:.0%}"


def table(head: list[str], rows: list[list], cls: str = "") -> str:
    th = "".join(f"<th>{escape(str(h))}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def stat_row(name: str, s: dict | None, nd: int = 2) -> list:
    if s is None:
        return [name, 0, "–", "–", "–", "–"]
    return [name, s["n"], f(s["mean"], nd), f(s["median"], nd), f(s["p95"], nd), f(s["max"], nd)]


STAT_HEAD = ["", "N", "trung bình", "trung vị", "p95", "lớn nhất"]


def names(items) -> str:
    return ", ".join(f"<code>{escape(p.name)}</code>" for p, *_ in items)


class Section:
    def __init__(self, sid: str, title: str, sources: str, n: str, body: str, note: str = "") -> None:
        self.sid, self.title, self.sources, self.n, self.body, self.note = sid, title, sources, n, body, note

    def html(self) -> str:
        note = f'<p class="note">{self.note}</p>' if self.note else ""
        return (f'<section id="{self.sid}"><h2>{escape(self.title)}</h2>'
                f'<p class="src">Nguồn: {self.sources} &middot; N = {escape(self.n)}</p>{self.body}{note}</section>')


# --- các mục --------------------------------------------------------------------------------


def sec_localization(d: dict) -> Section | None:
    items = d["target"]
    rows = [r for _, rs in items for r in rs]
    if not rows:
        return None
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("label", "?"), []).append(r)
    errs = lambda rs: [e * 100 for r in rs if (e := num(r.get("err"))) is not None]
    trs = [[lab, len(rs), *stat_row("", stats(errs(rs)))[1:]] for lab, rs in sorted(by.items())]
    allerr = errs(rows)
    trs.append(["Tổng", len(rows), *stat_row("", stats(allerr))[1:]])
    ax = []
    for key, name in (("err_x", "trục X"), ("err_z", "trục Z")):
        v = [abs(e) * 100 for r in rows if (e := num(r.get(key))) is not None]
        ax.append(stat_row(name + " (|sai số|)", stats(v)))
    pts = {lab: [(num(r["err_x"]) * 100, num(r["err_z"]) * 100) for r in rs
                 if num(r.get("err_x")) is not None and num(r.get("err_z")) is not None]
           for lab, rs in sorted(by.items())}
    pts = {k: v for k, v in pts.items() if v}
    body = (f"<p>Sai số vị trí bia trên bảng (cm); {len(allerr)}/{len(rows)} lượt đo được.</p>"
            + table(["nhãn", "lượt", "N đo được", *STAT_HEAD[2:]], trs)
            + table(STAT_HEAD, ax))
    if pts:
        body += "<h3>Vector sai số (cm)</h3>" + svg.scatter(pts, "sai số X (cm)", "sai số Z (cm)", gate=GATE_CM)
    return Section("localization", "Sai số định vị bia", names(items), f"{len(rows)} lượt", body,
                   "Đường đứt là cổng 5 cm; điểm trong vòng là qua cổng.")


def sec_tag(d: dict) -> Section | None:
    rows = [r for _, rs in d["tag"] for r in rs]
    if not rows:
        return None
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("node") or r.get("label", "?"), []).append(r)
    trs = []
    for node, rs in sorted(by.items()):
        e = [x * 100 for r in rs if (x := num(r.get("err"))) is not None]
        rate = [x for r in rs if (x := num(r.get("tag_rate"))) is not None]
        s = stats(e)
        trs.append([node, len(rs), len(e), f(s and s["mean"]), f(s and s["median"]), f(s and s["p95"]),
                    f(s and s["max"]), f(statistics.fmean(rate) * 100 if rate else None, 0) + "%"])
    body = table(["node", "lượt", "đo được", "tb (cm)", "trung vị", "p95", "max", "tỷ lệ khung thấy tag"], trs)
    return Section("tag", "Pose tag của node", names(d["tag"]), f"{len(rows)} lượt", body)


def _passrate(rows: list[dict]) -> tuple[int, int]:
    ok = [r for r in rows if r.get("pass") in ("True", "False", "true", "false")]
    return sum(r["pass"].lower() == "true" for r in ok), len(ok)


def sec_aim(d: dict) -> Section | None:
    items = d["aim"]
    rows = [r for _, rs in items for r in rs]
    if not rows:
        return None
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("node") or r.get("label", "?"), []).append(r)
    miss = lambda rs: [m for r in rs if (m := num(r.get("miss_cm"))) is not None]
    trs = []
    for node, rs in sorted(by.items()):
        ok, n = _passrate(rs)
        trs.append([*stat_row(node, stats(miss(rs)))[:2], *stat_row("", stats(miss(rs)))[2:], f"{ok}/{n} ({pct(ok, n)})"])
    ok, n = _passrate(rows)
    trs.append([*stat_row("Tổng", stats(miss(rows)))[:2], *stat_row("", stats(miss(rows)))[2:], f"{ok}/{n} ({pct(ok, n)})"])
    body = (f"<p>Độ lệch giữa vết laser đo được và điểm đích (cm); qua cổng khi lệch từ {GATE_CM:g} cm trở xuống. "
            f"{len(miss(rows))}/{len(rows)} lượt thấy vết.</p>"
            + table(["vòi", "N thấy vết", "tb (cm)", "trung vị", "p95", "max", f"qua cổng {GATE_CM:g} cm"], trs))
    # lưới điểm: tỷ lệ qua cổng theo (x, z), mọi vòi gộp
    cells: dict[tuple[float, float], list[dict]] = {}
    for r in rows:
        x, z = num(r.get("target_x")), num(r.get("target_z"))
        if x is not None and z is not None:
            cells.setdefault((round(x, 3), round(z, 3)), []).append(r)
    if len(cells) >= 2:
        grid = {}
        for k, rs in cells.items():
            ok, n = _passrate(rs)
            med = stats(miss(rs))
            label = f"{ok}/{n}" + (f" · {med['median']:.1f} cm" if med else "")
            grid[k] = (ok / n if n else 0.0, label)
        body += ("<h3>Lưới điểm đích: qua cổng / số lượt, độ lệch trung vị</h3>" + svg.heat_grid(grid)
                 + '<p class="note">Ô xanh: qua cổng hết; ô đỏ: trượt hết; ô không có chữ số lượt hợp lệ coi là 0.</p>')
    notes: dict[str, int] = {}
    for r in rows:
        if r.get("note"):
            notes[r["note"]] = notes.get(r["note"], 0) + 1
    if notes:
        body += "<h3>Lượt không đo được</h3>" + table(["ghi chú", "số lượt"], sorted(notes.items(), key=lambda kv: -kv[1]))
    return Section("aim", "Sai số ngắm theo vòi và lưới điểm", names(items), f"{len(rows)} lượt", body)


def build_runs(station: list) -> list[dict]:
    """Dựng lại từng lượt xử lý từ log sự kiện của trạm."""
    runs: list[dict] = []
    for path, evs in station:
        cur = None

        def close(cur=None):
            if cur is not None:
                runs.append(cur)

        for ev in evs:
            kind, t = ev.get("kind"), ev.get("time", 0.0)
            if kind == "phase" and ev.get("phase") == "ALERT":
                close(cur)
                cur = {"file": path.name, "t0": t, "phases": {"ALERT": t}, "correct": [], "decisions": [],
                       "outcome": None}
            elif cur is None:
                continue
            elif kind == "phase" and ev.get("phase") not in (None, "IDLE"):
                cur["phases"].setdefault(ev["phase"], t)
            elif kind == "correct" and (m := CORRECT_RE.search(ev.get("text", ""))):
                cur["correct"].append((int(m[1]), float(m[2])))
            elif kind == "decision" and isinstance(ev.get("decision"), dict):
                cur["decisions"].append(ev["decision"])
            elif kind == "run":
                cur.update(outcome=ev.get("outcome"), id=ev.get("run"), node=ev.get("node"),
                           nozzle=ev.get("nozzle"), target=ev.get("target"), attempts=ev.get("attempts"))
                close(cur)
                cur = None
        close(cur)
    return runs


def sec_convergence(d: dict, runs: list[dict]) -> Section | None:
    rs = [r for r in runs if r["correct"]]
    if not rs:
        return None
    series = {}
    for i, r in enumerate(rs[:8]):
        series[f"lượt {r.get('id', i + 1)} ({r['file'][:13]})"] = ([float(k) for k, _ in r["correct"]],
                                                                    [m for _, m in r["correct"]])
    first = [r["correct"][0][1] for r in rs]
    last = [r["correct"][-1][1] for r in rs]
    rounds = [len(r["correct"]) for r in rs]
    okn = sum(m <= GATE_CM for m in last)
    body = (table(STAT_HEAD, [stat_row("lệch vòng 1 (cm)", stats(first)), stat_row("lệch vòng cuối (cm)", stats(last)),
                              stat_row("số vòng CORRECT", stats([float(x) for x in rounds]), 1)])
            + f"<p>Vòng cuối qua cổng {GATE_CM:g} cm: <b>{okn}/{len(rs)}</b> ({pct(okn, len(rs))}).</p>"
            + svg.lines(series, "vòng CORRECT", "lệch vết laser (cm)", gate=GATE_CM, gate_label="cổng"))
    if len(rs) > 8:
        body += f'<p class="note">Biểu đồ chỉ vẽ 8 lượt đầu; bảng tính trên cả {len(rs)} lượt.</p>'
    return Section("convergence", "Hội tụ của bước CORRECT (laser)", names(d["station"]), f"{len(rs)} lượt", body)


def sec_latency(d: dict, runs: list[dict]) -> Section | None:
    cols = (("DECIDE", "tới DECIDE"), ("CORRECT", "tới CORRECT (kim đã reached)"), ("FIRE", "tới FIRE (phun)"))
    vals = {k: [r["phases"][k] - r["t0"] for r in runs if k in r["phases"]] for k, _ in cols}
    if not vals["FIRE"] and not vals["CORRECT"]:
        return None
    trs = [stat_row(label, stats(vals[k])) for k, label in cols]
    fires = [(r, r["phases"]["FIRE"] - r["t0"]) for r in runs if "FIRE" in r["phases"]]
    body = ("<p>Thời gian từ lúc Pi nhận cảnh báo (vào ALERT) tới từng mốc, giây, theo đồng hồ của Pi.</p>"
            + table(STAT_HEAD, trs))
    if fires:
        body += svg.bars([f"{r.get('id', '?')}" for r, _ in fires[-24:]], [round(v, 2) for _, v in fires[-24:]],
                         "alert → FIRE (s)", unit=" s")
        body += '<p class="note">Cột: từng lượt (số lượt trong file), tối đa 24 lượt gần nhất.</p>'
    dec = [x["latency_ms"] for r in runs for x in r["decisions"] if num(x.get("latency_ms")) is not None]
    if dec:
        body += "<h3>Thời gian bộ quyết định</h3>" + table(STAT_HEAD, [stat_row("decide() (ms)", stats(dec), 1)])
    return Section("latency", "Độ trễ đầu cuối (cảnh báo tới phun)", names(d["station"]),
                   f"{len(fires)} lượt có FIRE / {len(runs)} lượt", body,
                   "Log từ sa bàn ảo hay node giả cũng được tính; phân biệt bằng tên file log.")


def sec_outcomes(d: dict, runs: list[dict]) -> Section | None:
    if not runs:
        return None
    n = len(runs)
    oc: dict[str, int] = {}
    for r in runs:
        oc[r["outcome"] or "(không kết thúc)"] = oc.get(r["outcome"] or "(không kết thúc)", 0) + 1
    real = sum(bool(r["decisions"] and r["decisions"][0].get("real_fire")) for r in runs)
    sprayed = sum("FIRE" in r["phases"] for r in runs)
    ign = oc.get("ignored", 0)
    kpis = [["Lượt có cảnh báo", n, ""],
            ["Bộ quyết định kết luận cháy thật", real, pct(real, n)],
            ["Bỏ qua, coi là báo nhầm (ignored)", ign, pct(ign, n)],
            ["Có phun (vào FIRE)", sprayed, pct(sprayed, n)],
            ["Dập tắt (extinguished)", oc.get("extinguished", 0), pct(oc.get("extinguished", 0), n)],
            ["Dừng hoặc lỗi an toàn (stopped, fault)", oc.get("stopped", 0) + oc.get("fault", 0),
             pct(oc.get("stopped", 0) + oc.get("fault", 0), n)]]
    body = ("<h3>Tỷ lệ phát hiện và báo nhầm</h3>" + table(["", "số lượt", "tỷ lệ"], kpis)
            + '<p class="note">Log trạm không có đáp án thật: &quot;báo nhầm&quot; ở đây là lượt bộ quyết định bỏ qua. '
              "Đối chiếu với ghi chú từng lượt có bia và không bia (kế hoạch mục 7).</p>"
            + "<h3>Kết cục các lượt</h3>"
            + table(["kết cục", "số lượt", "tỷ lệ"], [[k, v, pct(v, n)] for k, v in sorted(oc.items(), key=lambda kv: -kv[1])]))
    rows = []
    for r in runs[-60:]:
        fire = r["phases"].get("FIRE")
        tgt = r.get("target") or {}
        rows.append([r["file"].removesuffix(".jsonl"), r.get("id", "–"), r.get("node") or "–", r["outcome"] or "–",
                     r.get("nozzle") or "–", tgt.get("id", "–"), r.get("attempts") if r.get("attempts") is not None else "–",
                     f(fire - r["t0"] if fire else None, 1)])
    body += ("<h3>Từng lượt</h3>"
             + table(["log", "lượt", "node", "kết cục", "vòi", "bia", "lần phun", "alert→FIRE (s)"], rows))
    if n > 60:
        body += f'<p class="note">Chỉ liệt kê 60 lượt gần nhất trên tổng {n}.</p>'
    return Section("outcomes", "Kết cục các lượt chạy", names(d["station"]), f"{n} lượt", body)


def _acc_table(cols: list[tuple[str, dict]]) -> str:
    head = [""] + [c for c, _ in cols]
    rows = [["số bản ghi"] + [r.get("n_records", "–") for _, r in cols]]
    for q in QUESTIONS:
        rows.append([f"độ chính xác {q}"] + [f"{r['accuracy'][q]:.1%}" if q in r.get("accuracy", {}) else "–" for _, r in cols])
    for key, (text, base) in SYS_LABELS.items():
        row = [text]
        for _, r in cols:
            s = r.get("system") or {}
            row.append(f"{s[key]}/{s[base]}" if key in s and base in s else "–")
        rows.append(row)
    return table(head, rows)


def sec_decision(d: dict) -> Section | None:
    cols: list[tuple[str, dict]] = []
    hybrid: list[list] = []
    srcs = []
    for p, data in d["rules_eval"]:
        srcs.append((p,))
        real = p.name == "rules_eval_data.json"
        for split, r in data.items():
            if isinstance(r, dict) and "accuracy" in r:
                cols.append((f"luật · {split}" + (" (lượt thật)" if real else ""), r))
    for p, data in d["jev_eval"]:
        srcs.append((p,))
        name = p.parent.name
        for split, r in data.items():
            if not isinstance(r, dict) or "model" not in r:
                continue
            if not any(c.startswith(f"luật · {split}") for c, _ in cols):
                cols.append((f"luật · {split}", r["rules"]))
            cols.append((f"{name} · {split}", r["model"]))
            for tau, h in sorted((r.get("hybrid") or {}).items()):
                s = h.get("system", {})
                hybrid.append([name, split, tau, pct(round(h.get("coverage", 0) * 1000), 1000),
                               f"{s.get('false_spray', '–')}/{s.get('no_fire', '–')}",
                               f"{s.get('missed_spray', '–')}/{s.get('should_spray', '–')}",
                               f"{s.get('sprayed_correctly', '–')}/{s.get('should_spray', '–')}"])
            lat = r.get("latency")
            if lat:
                hybrid_lat = f"{name} · {split}: decide() trung vị {lat.get('median_ms', 0):.0f} ms, p95 {lat.get('p95_ms', 0):.0f} ms"
                hybrid.append(["(độ trễ)", split, hybrid_lat, "", "", "", ""])
    if not cols:
        return None
    body = _acc_table(cols)
    hy = [h for h in hybrid if h[0] != "(độ trễ)"]
    if hy:
        body += "<h3>Chế độ lai (mô hình khi đủ tin cậy tau, ngược lại luật)</h3>" + table(
            ["mô hình", "tập", "tau", "phủ mô hình", "phun nhầm", "bỏ sót", "đúng bia+vòi"], hy)
    lat = [h[2] for h in hybrid if h[0] == "(độ trễ)"]
    if lat:
        body += "<ul>" + "".join(f"<li>{escape(x)}</li>" for x in lat) + "</ul>"
    nrec = sum(r.get("n_records", 0) for _, r in cols)
    return Section("decision", "Chất lượng bộ quyết định", names(srcs), f"{len(cols)} cột, {nrec} bản ghi cộng dồn", body,
                   "Cột (lượt thật) chấm trên tập xuất từ lượt chạy thật nếu có rules_eval_data.json.")


def sec_load(d: dict) -> Section | None:
    items = d["load"]
    rows = [r for _, rs in items for r in rs]
    if not rows:
        return None
    col = lambda k: [x for r in rows if (x := num(r.get(k))) is not None]
    cpu, ram, temp = col("cpu_pct"), col("ram_used_mb"), col("temp_c")
    thr = [r for r in rows if r.get("throttled") not in ("", "0x0", None)]
    trs = [stat_row("CPU (%)", stats(cpu), 1), stat_row("RAM dùng (MB)", stats(ram), 0), stat_row("Nhiệt độ (°C)", stats(temp), 1)]
    body = table(STAT_HEAD, trs) + f"<p>Mẫu có cờ throttled khác 0: <b>{len(thr)}</b>/{len(rows)}.</p>"

    def down(xs, ys, cap=300):
        k = max(1, len(xs) // cap)
        return xs[::k], ys[::k]

    for key, label in (("cpu_pct", "CPU (%)"), ("temp_c", "nhiệt độ (°C)"), ("ram_used_mb", "RAM (MB)")):
        pts = [(x, y) for r in rows if (x := num(r.get("elapsed_s"))) is not None and (y := num(r.get(key))) is not None]
        if len(pts) >= 2:
            xs, ys = down([p[0] for p in pts], [p[1] for p in pts])
            body += svg.lines({label: (xs, ys)}, "thời gian (s)", label)
    return Section("load", "Tải của Pi", names(items), f"{len(rows)} mẫu", body)


def sec_stream(d: dict) -> Section | None:
    rows = [r for _, rs in d["stream"] for r in rs]
    ms = [x for r in rows if (x := num(r.get("latency_ms"))) is not None]
    if not ms:
        ms = [x * 1000 for r in rows if (x := num(r.get("latency_s"))) is not None]
    if not ms:
        return None
    body = table(STAT_HEAD, [stat_row("độ trễ stream (ms)", stats(ms), 0)])
    body += svg.bars([str(i + 1) for i in range(len(ms[-24:]))], [round(v) for v in ms[-24:]], "ms", unit=" ms")
    return Section("stream", "Độ trễ stream điện thoại tới Pi", names(d["stream"]), f"{len(ms)} lượt", body,
                   "Đo bằng laser của node (stream_latency.py); giá trị dùng làm camera.stream.latency_s.")


def sec_telemetry(d: dict) -> Section | None:
    per: dict[str, list[dict]] = {}
    for _, evs in d["station"]:
        for ev in evs:
            if ev.get("kind") == "tel" and ev.get("n"):
                per.setdefault(ev["n"], []).append(ev)
    if not per:
        return None
    trs = []
    for node, evs in sorted(per.items()):
        t = [x for e in evs if (x := num(e.get("t"))) is not None]
        g = [x for e in evs if (x := num(e.get("g"))) is not None]
        span = evs[-1]["time"] - evs[0]["time"]
        trs.append([node, len(evs), f"{span:.0f} s", f"{f(min(t), 1)} – {f(max(t), 1)}" if t else "–",
                    f"{f(min(g), 0)} – {f(max(g), 0)}" if g else "–"])
    body = table(["node", "mẫu", "khoảng ghi", "nhiệt (°C)", "khí"], trs)
    return Section("telemetry", "Telemetry ghi được", names(d["station"]), f"{sum(len(v) for v in per.values())} mẫu", body,
                   "Trạm ghi tối đa một mẫu mỗi giây mỗi node nên không suy ra PDR từ số thứ tự gói ở đây.")


# --- trang ----------------------------------------------------------------------------------

CSS = """
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6a6a64;--card:#fff;--line:#dcdcd4;--c1:#2a6fdb;--c2:#d9731a;--c3:#2a9d6f;
--c4:#a23fb5;--c5:#c8a400;--c6:#d6336c;--good:#2a9d6f;--bad:#d64545}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#16171a;--fg:#e8e8e3;--mut:#9a9a92;--card:#1f2024;
--line:#35363c;--c1:#6ea1ff;--c2:#f0974a;--c3:#4fc79a;--c4:#c985da;--c5:#e5c43a;--c6:#f06a9a;--good:#3aa97b;--bad:#e5645f}}
:root[data-theme=dark]{--bg:#16171a;--fg:#e8e8e3;--mut:#9a9a92;--card:#1f2024;--line:#35363c;--c1:#6ea1ff;--c2:#f0974a;
--c3:#4fc79a;--c4:#c985da;--c5:#e5c43a;--c6:#f06a9a;--good:#3aa97b;--bad:#e5645f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:16px}h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.2rem;margin:0 0 .2em}
h3{font-size:1rem;margin:1.2em 0 .3em}section{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin:14px 0}.src,.note,.meta{color:var(--mut);font-size:.85rem;margin:.2em 0 .8em}
nav{display:flex;flex-wrap:wrap;gap:6px 14px;margin:10px 0}nav a{color:var(--c1);text-decoration:none;font-size:.9rem}
.tw{overflow-x:auto}table{border-collapse:collapse;margin:.4em 0 1em;font-size:.88rem;min-width:50%}
th,td{border-bottom:1px solid var(--line);padding:4px 10px;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--mut);font-weight:600}
figure{margin:.6em 0;max-width:640px}svg{width:100%;height:auto;display:block}
.grid{stroke:var(--line);stroke-width:1}.axisline{stroke:var(--mut);stroke-width:1}
.tick{fill:var(--mut);font-size:11px}.axis{fill:var(--mut);font-size:12px}.cell{fill:#fff;font-size:12px;font-weight:600}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:.8rem;color:var(--mut)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
code{font-size:.85em}.empty{color:var(--mut)}
"""


def build_report(data: dict, gate_cm: float = GATE_CM) -> str:
    """HTML của báo cáo từ kết quả `collect`."""
    global GATE_CM
    GATE_CM = gate_cm
    runs = build_runs(data["station"])
    makers = [lambda: sec_localization(data), lambda: sec_tag(data), lambda: sec_aim(data),
              lambda: sec_convergence(data, runs), lambda: sec_latency(data, runs),
              lambda: sec_outcomes(data, runs), lambda: sec_decision(data), lambda: sec_load(data),
              lambda: sec_stream(data), lambda: sec_telemetry(data)]
    secs = [s for m in makers if (s := m()) is not None]
    nav = "".join(f'<a href="#{s.sid}">{escape(s.title)}</a>' for s in secs)
    body = "".join(s.html() for s in secs) or '<p class="empty">Chưa có số đo nào trong thư mục runs.</p>'
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    return (f'<!doctype html><html lang="vi"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>Báo cáo thử nghiệm NT532</title>'
            f"<style>{CSS}</style></head><body><main><h1>Báo cáo thử nghiệm NT532</h1>"
            f'<p class="meta">Tạo lúc {stamp} từ <code>{escape(str(data["root"]))}</code> &middot; {len(secs)} mục có dữ liệu</p>'
            f"<nav>{nav}</nav>{body}</main></body></html>")
