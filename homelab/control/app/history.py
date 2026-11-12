"""API lịch sử: sự kiện (lọc, phân trang, CSV), telemetry (gộp ô, CSV) và các lượt xử lý."""

import asyncio
import csv
import io
import json
import time
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

LEVELS = ("info", "warn", "error")
NODES = ("s1", "s2")
T0 = Annotated[float | None, Query(alias="from", description="epoch giây")]
T1 = Annotated[float | None, Query(alias="to", description="epoch giây")]
Multi = Annotated[list[str] | None, Query()]
BOM = "﻿"  # để Excel đọc đúng chữ có dấu


def _stamp(ts: float | None) -> str:
    return "" if ts is None else datetime.fromtimestamp(ts).astimezone().isoformat(sep=" ", timespec="seconds")


def _csv(rows: list[list], header: list[str], name: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(BOM + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def make_router(require_user) -> APIRouter:
    r = APIRouter()

    def store(request: Request):
        return request.app.state.store

    def clean_node(node: str | None) -> str | None:
        return node if node in NODES else None

    # --- sự kiện ------------------------------------------------------------------------------

    def event_filters(since, node, kind, level, t0, t1, q) -> dict:
        return {"since_id": since, "node": clean_node(node), "kinds": kind or None, "levels": level or None,
                "t0": t0, "t1": t1, "q": q}

    @r.get("/api/events")
    async def events(request: Request, since: int = 0, node: str | None = None,
                     kind: Multi = None, level: Multi = None,
                     t0: T0 = None, t1: T1 = None,
                     q: str | None = None, limit: Annotated[int, Query(ge=1)] = 100, page: Annotated[int, Query(ge=1)] = 1,
                     offset: Annotated[int | None, Query(ge=0)] = None, user: str = Depends(require_user)):
        """Mới nhất trước. Tổng số dòng khớp bộ lọc nằm ở header `X-Total-Count`."""
        limit = min(limit, 1000)
        rows, total = await asyncio.to_thread(
            lambda: store(request).query_events(**event_filters(since, node, kind, level, t0, t1, q), limit=limit,
                                                offset=offset if offset is not None else (page - 1) * limit))
        return JSONResponse(rows, headers={"X-Total-Count": str(total)})

    @r.get("/api/events.csv")
    async def events_csv(request: Request, since: int = 0, node: str | None = None,
                         kind: Multi = None, level: Multi = None,
                         t0: T0 = None, t1: T1 = None,
                         q: str | None = None, user: str = Depends(require_user)):
        rows = await asyncio.to_thread(lambda: store(request).export_events(
            **event_filters(since, node, kind, level, t0, t1, q)))
        extra_keys = ("id", "seq", "time", "kind", "level", "text", "node")
        data = [[_stamp(e["time"]), e["node"] or "", e["kind"], e["level"], e["text"],
                 json.dumps({k: v for k, v in e.items() if k not in extra_keys}, ensure_ascii=False)
                 if len(e) > len(extra_keys) else ""] for e in rows]
        return _csv(data, ["Thời gian", "Node", "Loại", "Mức", "Nội dung", "Dữ liệu thêm (JSON)"], "nhat-ky.csv")

    # --- telemetry ----------------------------------------------------------------------------

    @r.get("/api/telemetry")
    async def telemetry(request: Request, node: str | None = None,
                        t0: T0 = None, t1: T1 = None,
                        points: Annotated[int, Query(ge=1)] = 300, user: str = Depends(require_user)):
        """Telemetry gộp theo ô thời gian (≤600 điểm mỗi chuỗi): min/avg/max, ngưỡng và các lượt trong khoảng."""
        t1 = time.time() if t1 is None else t1
        t0 = t1 - 3600 if t0 is None else t0
        if t0 >= t1:
            return JSONResponse({"error": "from phải nhỏ hơn to"}, 400)
        node = clean_node(node)
        s = store(request)
        out = await asyncio.to_thread(s.telemetry_buckets, t0, t1, points, node)
        out["runs"] = await asyncio.to_thread(s.runs_overlapping, t0, t1, node)
        out["limits"] = request.app.state.pi.overview(False).get("limits")
        return out

    @r.get("/api/telemetry.csv")
    async def telemetry_csv(request: Request, node: str | None = None,
                            t0: T0 = None, t1: T1 = None, user: str = Depends(require_user)):
        t1 = time.time() if t1 is None else t1
        t0 = t1 - 3600 if t0 is None else t0
        rows = await asyncio.to_thread(store(request).telemetry_rows, t0, t1, clean_node(node))
        data = [[_stamp(t), n, temp, gas, hum] for t, n, temp, gas, hum in rows]
        return _csv(data, ["Thời gian", "Node", "Nhiệt độ (°C)", "Khí gas (ppm)", "Độ ẩm (%)"], "cam-bien.csv")

    # --- lượt xử lý ---------------------------------------------------------------------------

    @r.get("/api/runs")
    async def runs(request: Request, t0: T0 = None, t1: T1 = None, outcome: str | None = None,
                   limit: Annotated[int, Query(ge=1)] = 200, user: str = Depends(require_user)):
        """`{runs: [...mới nhất trước], summary: {total, running, extinguished, ...}}`; summary không bị lọc outcome."""
        return await asyncio.to_thread(store(request).runs, t0, t1, outcome or None, limit)

    return r
