"""Lưu sự kiện, telemetry và các lượt xử lý vào SQLite (thư viện chuẩn, đồng bộ; gọi qua `to_thread`)."""

import json
import sqlite3
import threading
import time
from pathlib import Path

# Lược đồ gốc của M1. Mọi thay đổi sau đó đi qua `Store._migrate` (idempotent, đánh dấu bằng PRAGMA user_version).
SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seq_pi INTEGER, time REAL NOT NULL, kind TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'info',
    text TEXT NOT NULL DEFAULT '', node TEXT, data_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS ix_events_time ON events(time);
CREATE TABLE IF NOT EXISTS telemetry(
    time REAL NOT NULL, node TEXT NOT NULL, temp REAL, gas REAL, hum REAL);
CREATE INDEX IF NOT EXISTS ix_tel_node_time ON telemetry(node, time);
CREATE TABLE IF NOT EXISTS runs(
    id_pi INTEGER NOT NULL, started REAL NOT NULL, node TEXT, outcome TEXT, data_json TEXT NOT NULL,
    PRIMARY KEY(id_pi, started));
"""
_BASE = ("seq", "time", "kind", "level", "text", "node")

# 0/1 = lược đồ M1; 2 = M2 (chỉ mục cho trang Nhật ký, cột phẳng của `runs`); 3 = bảng `thread` (số đo mạng Thread).
SCHEMA_VERSION = 3
THREAD_DDL = """
CREATE TABLE IF NOT EXISTS thread(
    time REAL NOT NULL, node TEXT NOT NULL, role TEXT, ping_avg REAL, ping_min REAL, ping_max REAL,
    loss_pct REAL, coap_rtt_ms REAL, ok INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_thread_node_time ON thread(node, time);
CREATE INDEX IF NOT EXISTS ix_thread_time ON thread(time);"""
THREAD_COLS = ("time", "node", "role", "ping_avg", "ping_min", "ping_max", "loss_pct", "coap_rtt_ms", "ok")
_RUN_COLS = {"ended": "REAL", "target_id": "TEXT", "x": "REAL", "y": "REAL", "z": "REAL", "surface": "TEXT",
             "nozzle": "TEXT", "decider": "TEXT", "attempts": "INTEGER", "shots": "INTEGER", "miss_cm": "REAL"}
OUTCOMES = ("extinguished", "ignored", "alarm_only", "human", "fault", "stopped")
MAX_TEL_POINTS = 600
EXPORT_CAP = 200_000


def run_fields(r: dict) -> dict:
    """Các trường phẳng suy ra từ JSON của một lượt (`Run.to_json()` bên Pi)."""
    tgt = r.get("target") if isinstance(r.get("target"), dict) else {}
    decisions = r.get("decisions") if isinstance(r.get("decisions"), list) else []
    first = decisions[0] if decisions and isinstance(decisions[0], dict) else {}
    shots = r.get("shots") if isinstance(r.get("shots"), list) else []
    misses = [s.get("miss_cm") for s in shots if isinstance(s, dict) and s.get("miss_cm") is not None]
    phases = r.get("phases") if isinstance(r.get("phases"), list) else []
    ended = None
    if r.get("outcome"):
        ts = [p[1] for p in phases if isinstance(p, list | tuple) and len(p) > 1 and isinstance(p[1], int | float)]
        ended = max(ts) if ts else None
    return {"ended": ended, "target_id": tgt.get("id"), "x": tgt.get("x"), "y": tgt.get("y"), "z": tgt.get("z"),
            "surface": tgt.get("surface"), "nozzle": r.get("nozzle"), "decider": first.get("decider"),
            "attempts": r.get("attempts"), "shots": len(shots), "miss_cm": misses[-1] if misses else None}


class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            # tìm chuỗi con không phân biệt hoa thường, kể cả chữ có dấu (LIKE của SQLite chỉ lo ASCII)
            self._db.create_function("contains_ci", 2, lambda needle, hay: int(needle in (hay or "").casefold()),
                                     deterministic=True)
            self._db.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Nâng lược đồ M1 lên bản hiện tại; chạy lại bao nhiêu lần cũng an toàn."""
        db = self._db
        have = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
        for name, typ in _RUN_COLS.items():
            if name not in have:
                db.execute(f"ALTER TABLE runs ADD COLUMN {name} {typ}")
        db.executescript("""
            CREATE INDEX IF NOT EXISTS ix_events_node_time ON events(node, time);
            CREATE INDEX IF NOT EXISTS ix_events_kind_time ON events(kind, time);
            CREATE INDEX IF NOT EXISTS ix_runs_started ON runs(started);
            CREATE INDEX IF NOT EXISTS ix_tel_time ON telemetry(time);""")
        db.executescript(THREAD_DDL)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            # điền cột phẳng cho các lượt M1 đã lưu (chỉ có data_json)
            for row in db.execute("SELECT id_pi, started, data_json FROM runs").fetchall():
                try:
                    f = run_fields(json.loads(row["data_json"]))
                except (ValueError, TypeError):
                    continue
                db.execute(f"UPDATE runs SET {','.join(f'{k}=?' for k in f)} WHERE id_pi=? AND started=?",
                           (*f.values(), row["id_pi"], row["started"]))
        if version < SCHEMA_VERSION:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.commit()

    def schema_version(self) -> int:
        with self._lock:
            return self._db.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- sự kiện ----------------------------------------------------------------------------

    def add_events(self, events: list[dict]) -> list[dict]:
        """Ghi các sự kiện (dạng của Pi hoặc của ta), trả về chúng kèm `id` cục bộ."""
        out = []
        with self._lock:
            for ev in events:
                extra = {k: v for k, v in ev.items() if k not in _BASE and k != "id"}
                cur = self._db.execute(
                    "INSERT INTO events(seq_pi,time,kind,level,text,node,data_json) VALUES(?,?,?,?,?,?,?)",
                    (ev.get("seq"), ev.get("time") or time.time(), ev.get("kind", "status"),
                     ev.get("level", "info"), ev.get("text", ""), ev.get("node"),
                     json.dumps(extra, ensure_ascii=False, default=str)))
                out.append(self._row_to_event(self._db.execute(
                    "SELECT * FROM events WHERE id=?", (cur.lastrowid,)).fetchone()))
            self._db.commit()
        return out

    @staticmethod
    def _row_to_event(r: sqlite3.Row) -> dict:
        ev = {"id": r["id"], "seq": r["seq_pi"], "time": r["time"], "kind": r["kind"],
              "level": r["level"], "text": r["text"], "node": r["node"]}
        ev.update(json.loads(r["data_json"]))
        return ev

    def events(self, since_id: int = 0, kind: str | None = None, node: str | None = None,
               limit: int = 100) -> list[dict]:
        """Mới nhất trước. `since_id` là id cục bộ (không đổi khi Pi khởi động lại)."""
        q, args = "SELECT * FROM events WHERE id>?", [since_id]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if node:
            q += " AND node=?"
            args.append(node)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))
        with self._lock:
            return [self._row_to_event(r) for r in self._db.execute(q, args)]

    def query_events(self, *, since_id: int = 0, node: str | None = None, kinds: list[str] | None = None,
                     levels: list[str] | None = None, t0: float | None = None, t1: float | None = None,
                     q: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        """Lọc sự kiện cho trang Nhật ký. Trả (các dòng mới nhất trước, tổng số dòng khớp bộ lọc)."""
        where, args = ["id>?"], [since_id]
        if node:
            where.append("node=?")
            args.append(node)
        for col, vals in (("kind", kinds), ("level", levels)):
            if vals:
                where.append(f"{col} IN ({','.join('?' * len(vals))})")
                args.extend(vals)
        if t0 is not None:
            where.append("time>=?")
            args.append(t0)
        if t1 is not None:
            where.append("time<=?")
            args.append(t1)
        if q and q.strip():
            where.append("contains_ci(?, text)")
            args.append(q.strip().casefold())
        cond = " WHERE " + " AND ".join(where)
        limit = max(1, min(limit, 1000))
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM events{cond}", args).fetchone()[0]
            rows = self._db.execute(f"SELECT * FROM events{cond} ORDER BY time DESC, id DESC LIMIT ? OFFSET ?",
                                    [*args, limit, max(0, offset)]).fetchall()
        return [self._row_to_event(r) for r in rows], total

    def export_events(self, **filters) -> list[dict]:
        """Toàn bộ sự kiện khớp bộ lọc (cho CSV), tối đa EXPORT_CAP dòng."""
        out: list[dict] = []
        while len(out) < EXPORT_CAP:
            rows, total = self.query_events(**filters, limit=1000, offset=len(out))
            out.extend(rows)
            if not rows or len(out) >= total:
                break
        return out

    def last_pi_seq(self) -> int:
        """seq lớn nhất của Pi trong sự kiện gần nhất có seq (0 nếu chưa có)."""
        with self._lock:
            r = self._db.execute("SELECT seq_pi FROM events WHERE seq_pi IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return r[0] if r else 0

    # --- telemetry --------------------------------------------------------------------------

    def add_telemetry(self, rows: list[tuple]) -> None:
        with self._lock:
            self._db.executemany("INSERT INTO telemetry(time,node,temp,gas,hum) VALUES(?,?,?,?,?)", rows)
            self._db.commit()

    def telemetry(self, node: str, since: float = 0, limit: int = 1000) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT time,temp,gas,hum FROM telemetry WHERE node=? AND time>? ORDER BY time DESC LIMIT ?",
                (node, since, limit)).fetchall()
        return [dict(r) for r in rows][::-1]

    def telemetry_buckets(self, t0: float, t1: float, points: int = 300, node: str | None = None) -> dict:
        """Gộp telemetry theo ô thời gian bằng nhau (tối đa `points` ô): min/avg/max của mỗi đại lượng.

        Ô rỗng bị bỏ, nên khoảng mất dữ liệu hiện ra thành chỗ đứt trên biểu đồ."""
        points = max(1, min(points, MAX_TEL_POINTS))
        width = max(t1 - t0, 1e-6) / points
        cols = ("temp", "gas", "hum")
        sql = ("SELECT node, MIN(CAST((time-?)/? AS INTEGER), ?) AS b, AVG(time) AS t, COUNT(*) AS n, "
               + ", ".join(f"MIN({c}) AS {c}_min, AVG({c}) AS {c}_avg, MAX({c}) AS {c}_max" for c in cols)
               + " FROM telemetry WHERE time>=? AND time<=?")
        args: list = [t0, width, points - 1, t0, t1]
        if node:
            sql += " AND node=?"
            args.append(node)
        with self._lock:
            rows = self._db.execute(sql + " GROUP BY node, b ORDER BY node, b", args).fetchall()

        def rnd(v):
            return None if v is None else round(v, 2)

        nodes: dict[str, dict] = {}
        for r in rows:
            d = nodes.setdefault(r["node"], {"t": [], "n": [], **{c: {"min": [], "avg": [], "max": []} for c in cols}})
            d["t"].append(round(r["t"], 2))
            d["n"].append(r["n"])
            for c in cols:
                for agg in ("min", "avg", "max"):
                    d[c][agg].append(rnd(r[f"{c}_{agg}"]))
        return {"from": t0, "to": t1, "bucket_s": round(width, 3), "points": points, "nodes": nodes}

    def telemetry_rows(self, t0: float, t1: float, node: str | None = None) -> list[tuple]:
        """Mẫu thô (time, node, temp, gas, hum) cho CSV."""
        sql, args = "SELECT time, node, temp, gas, hum FROM telemetry WHERE time>=? AND time<=?", [t0, t1]
        if node:
            sql += " AND node=?"
            args.append(node)
        with self._lock:
            return [tuple(r) for r in self._db.execute(sql + " ORDER BY time, node LIMIT ?", [*args, EXPORT_CAP])]

    # --- mạng Thread ------------------------------------------------------------------------

    def add_thread(self, rows: list[tuple]) -> None:
        """Mỗi hàng: (time, node, role, ping_avg, ping_min, ping_max, loss_pct, coap_rtt_ms, ok)."""
        with self._lock:
            self._db.executemany(f"INSERT INTO thread({','.join(THREAD_COLS)}) VALUES({','.join('?' * 9)})", rows)
            self._db.commit()

    def thread_rows(self, t0: float, t1: float, node: str | None = None) -> list[tuple]:
        """Mẫu thô theo thời gian (cho CSV), cột như THREAD_COLS."""
        sql, args = f"SELECT {','.join(THREAD_COLS)} FROM thread WHERE time>=? AND time<=?", [t0, t1]
        if node:
            sql += " AND node=?"
            args.append(node)
        with self._lock:
            return [tuple(r) for r in self._db.execute(sql + " ORDER BY time, node LIMIT ?", [*args, EXPORT_CAP])]

    def thread_buckets(self, t0: float, t1: float, points: int = 300, node: str | None = None) -> dict:
        """Gộp theo ô thời gian bằng nhau: ping min/avg/max, loss, CoAP, số mẫu ok/hỏng; ô rỗng bị bỏ."""
        points = max(1, min(points, MAX_TEL_POINTS))
        width = max(t1 - t0, 1e-6) / points
        sql = ("SELECT node, MIN(CAST((time-?)/? AS INTEGER), ?) AS b, AVG(time) AS t, COUNT(*) AS n, "
               "MIN(ping_min) AS pmin, AVG(ping_avg) AS pavg, MAX(ping_max) AS pmax, AVG(loss_pct) AS loss, "
               "AVG(coap_rtt_ms) AS coap, SUM(ok) AS n_ok FROM thread WHERE time>=? AND time<=?")
        args: list = [t0, width, points - 1, t0, t1]
        if node:
            sql += " AND node=?"
            args.append(node)
        with self._lock:
            rows = self._db.execute(sql + " GROUP BY node, b ORDER BY node, b", args).fetchall()

        def rnd(v):
            return None if v is None else round(v, 2)

        nodes: dict[str, dict] = {}
        for r in rows:
            d = nodes.setdefault(r["node"], {"t": [], "n": [], "n_ok": [], "min": [], "avg": [], "max": [],
                                             "loss": [], "coap": []})
            d["t"].append(round(r["t"], 2))
            d["n"].append(r["n"])
            d["n_ok"].append(r["n_ok"])
            d["min"].append(rnd(r["pmin"]))
            d["avg"].append(rnd(r["pavg"]))
            d["max"].append(rnd(r["pmax"]))
            d["loss"].append(rnd(r["loss"]))
            d["coap"].append(rnd(r["coap"]))
        return {"from": t0, "to": t1, "bucket_s": round(width, 3), "points": points, "nodes": nodes}

    # --- lượt xử lý -------------------------------------------------------------------------

    def upsert_runs(self, runs: list[dict]) -> None:
        """Mỗi lượt một dòng, khóa (id Pi, lúc bắt đầu). Ghi lại thì cập nhật kết quả, không thêm dòng."""
        cols = ("id_pi", "started", "node", "outcome", "data_json", *_RUN_COLS)
        sets = ",".join(f"{c}=excluded.{c}" for c in cols[2:] if c not in ("outcome", "ended"))
        sql = (f"INSERT INTO runs({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
               f"ON CONFLICT(id_pi,started) DO UPDATE SET {sets}, "
               "outcome=COALESCE(excluded.outcome, runs.outcome), ended=COALESCE(excluded.ended, runs.ended)")
        with self._lock:
            for r in runs:
                f = run_fields(r)
                if r.get("outcome") and f["ended"] is None:
                    f["ended"] = time.time()
                self._db.execute(sql, (r["id"], r["started"], r.get("node"), r.get("outcome"),
                                       json.dumps(r, ensure_ascii=False, default=str), *f.values()))
            self._db.commit()

    @staticmethod
    def _run_row(r: sqlite3.Row) -> dict:
        return {"id": r["id_pi"], "started": r["started"], "ended": r["ended"], "node": r["node"],
                "outcome": r["outcome"], "target_id": r["target_id"], "x": r["x"], "y": r["y"], "z": r["z"],
                "surface": r["surface"], "nozzle": r["nozzle"], "decider": r["decider"],
                "attempts": r["attempts"], "shots": r["shots"], "miss_cm": r["miss_cm"],
                "raw": json.loads(r["data_json"])}

    def runs(self, t0: float | None = None, t1: float | None = None, outcome: str | None = None,
             limit: int = 200) -> dict:
        """Các lượt bắt đầu trong [t0, t1], mới nhất trước, kèm số đếm theo kết quả (không bị lọc `outcome`)."""
        where, args = [], []
        if t0 is not None:
            where.append("started>=?")
            args.append(t0)
        if t1 is not None:
            where.append("started<=?")
            args.append(t1)
        base = " WHERE " + " AND ".join(where) if where else ""
        sel, sargs = base, list(args)
        if outcome:
            sel += (" AND" if where else " WHERE") + (" outcome IS NULL" if outcome == "running" else " outcome=?")
            if outcome != "running":
                sargs.append(outcome)
        with self._lock:
            counts = {r[0]: r[1] for r in self._db.execute(
                f"SELECT COALESCE(outcome,'running'), COUNT(*) FROM runs{base} GROUP BY 1", args)}
            rows = [self._run_row(r) for r in self._db.execute(
                f"SELECT * FROM runs{sel} ORDER BY started DESC, id_pi DESC LIMIT ?",
                [*sargs, max(1, min(limit, 1000))])]
        summary = {"total": sum(counts.values()), "running": counts.get("running", 0),
                   **{o: counts.get(o, 0) for o in OUTCOMES}}
        return {"runs": rows, "summary": summary}

    def runs_overlapping(self, t0: float, t1: float, node: str | None = None) -> list[dict]:
        """Khoảng thời gian các lượt giao với [t0, t1], để tô trên biểu đồ; lượt chưa xong tính đến t1."""
        sql = "SELECT id_pi, started, ended, node, outcome FROM runs WHERE started<=? AND COALESCE(ended, ?)>=?"
        args: list = [t1, t1, t0]
        if node:
            sql += " AND node=?"
            args.append(node)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY started", args).fetchall()
        return [{"id": r["id_pi"], "node": r["node"], "start": r["started"],
                 "end": r["ended"] if r["ended"] is not None else t1, "outcome": r["outcome"]} for r in rows]

    # --- chung ------------------------------------------------------------------------------

    def count(self, table: str) -> int:
        assert table in ("events", "telemetry", "runs", "thread")
        with self._lock:
            return self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def cleanup(self, retention_days: int) -> int:
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            n = self._db.execute("DELETE FROM events WHERE time<?", (cutoff,)).rowcount
            n += self._db.execute("DELETE FROM telemetry WHERE time<?", (cutoff,)).rowcount
            n += self._db.execute("DELETE FROM runs WHERE started<?", (cutoff,)).rowcount
            n += self._db.execute("DELETE FROM thread WHERE time<?", (cutoff,)).rowcount
            self._db.commit()
        return n
