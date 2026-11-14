"""Poller: hỏi Pi mỗi chu kỳ, giữ trạng thái mới nhất trong bộ nhớ, ghi sự kiện và telemetry vào SQLite."""

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque

import httpx

from .config import Settings
from .store import Store

log = logging.getLogger("nt532.pi")

RECENT = 50  # số sự kiện giữ sẵn trong bộ nhớ cho /api/overview
PROBE_EVERY = 10  # cứ N lần hỏi lại một lần từ since=0 để phát hiện Pi khởi động lại (seq về nhỏ)


class PiClient:
    def __init__(self, cfg: Settings, store: Store, client: httpx.AsyncClient | None = None,
                 probe_every: int = PROBE_EVERY) -> None:
        self.cfg, self.store = cfg, store
        headers = {}
        if cfg.pi_access_id and cfg.pi_access_secret:  # Pi sau Cloudflare Access: mọi request kèm service token
            headers = {"CF-Access-Client-Id": cfg.pi_access_id, "CF-Access-Client-Secret": cfg.pi_access_secret}
        self.http = client or httpx.AsyncClient(base_url=cfg.pi_url, timeout=httpx.Timeout(4.0, connect=3.0),
                                                headers=headers)
        self._owns_http = client is None
        self.state: dict | None = None
        self.last_ok: float | None = None  # time.monotonic() của lần hỏi thành công gần nhất
        self.error: str | None = None
        self.last_seq = store.last_pi_seq()
        self.probe_every = max(1, probe_every)
        self.recent: deque[dict] = deque(reversed(store.events(limit=RECENT)), maxlen=RECENT)  # cũ -> mới
        self._n = 0
        self._boot: str | None = None  # chuỗi khởi động của Pi (chế độ push)
        self.ack_seq = 0  # seq lớn nhất đã nhận trong boot hiện tại, trả cho Pi
        self._queue: dict[str, tuple[float, dict, asyncio.Future]] = {}  # lệnh chờ Pi lấy: id -> (hạn, lệnh, future)
        self._waiting: dict[str, asyncio.Future] = {}  # lệnh đã trao cho Pi, chờ kết quả
        self._fails = 0
        self._was_online: bool | None = None
        self._subs: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    # --- trạng thái -------------------------------------------------------------------------

    @property
    def offline_s(self) -> float | None:
        """Số giây từ lần hỏi thành công gần nhất; None nếu chưa lần nào."""
        return None if self.last_ok is None else time.monotonic() - self.last_ok

    @property
    def pi_online(self) -> bool:
        age = self.offline_s
        return age is not None and age < max(5.0, 3 * self.cfg.poll_s)

    def overview(self, with_events: bool = True) -> dict:
        st = self.state or {}
        out = {"pi_online": self.pi_online,
               "offline_s": None if self.offline_s is None else round(self.offline_s, 1),
               "error": self.error, "state": self.state, "limits": st.get("limits"),
               "sim": bool(st.get("sim"))}
        if with_events:
            out["last_events"] = list(reversed(self.recent))  # mới nhất trước
        return out

    # --- SSE --------------------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=20)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _publish(self, events: list[dict]) -> None:
        msg = {"overview": self.overview(with_events=False), "events": events}
        for q in list(self._subs):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(msg)

    # --- ghi sự kiện ------------------------------------------------------------------------

    async def record_events(self, events: list[dict], publish: bool = True) -> list[dict]:
        saved = await asyncio.to_thread(self.store.add_events, events)
        self.recent.extend(saved)
        if publish:
            self._publish(saved)
        return saved

    # --- một chu kỳ -------------------------------------------------------------------------

    async def poll_once(self) -> None:
        self._n += 1
        probe = self._n % self.probe_every == 1 or self.probe_every == 1
        try:
            r = await self.http.get("/api/state")
            r.raise_for_status()
            state = r.json()
            since = 0 if probe else self.last_seq
            r = await self.http.get("/api/events", params={"since": since})
            r.raise_for_status()
            evs = r.json()
        except (httpx.HTTPError, ValueError) as e:
            await self._failed(e)
            return
        await self.ingest(state, evs, probe=probe)

    async def ingest(self, state: dict, evs: list[dict], boot: str | None = None, probe: bool = False) -> None:
        """Xử lý chung cho kéo và đẩy: cập nhật trạng thái, lọc sự kiện theo seq, ghi SQLite, phát SSE.

        boot: chuỗi khởi động của Pi (chế độ push); đổi so với lần trước = Pi khởi động lại.
        probe: kiểm tra seq về nhỏ (chế độ kéo; chế độ push thì lần đầu thấy một boot sau khi VM khởi động lại).
        """
        self.state, self.error = state, None
        self.last_ok = time.monotonic()
        self._fails = 0
        out: list[dict] = []
        if self._was_online is False:
            out.append({"kind": "status", "level": "info", "text": "Đã kết nối lại với Pi"})
        self._was_online = True

        evs = [e for e in evs if isinstance(e, dict) and isinstance(e.get("seq"), int)]
        if boot is not None:
            first = self._boot is None
            if boot != self._boot:
                if not first:
                    out.append({"kind": "status", "level": "warn", "text": "Pi khởi động lại, đồng bộ lại"})
                    self.last_seq = 0
                self.ack_seq = 0
            self._boot = boot
            probe = first
        if probe and evs and max(e["seq"] for e in evs) < self.last_seq:
            out.append({"kind": "status", "level": "warn",
                        "text": f"Pi khởi động lại (seq {self.last_seq} về {max(e['seq'] for e in evs)}), đồng bộ lại"})
            self.last_seq = 0
        if boot is not None and evs:
            self.ack_seq = max(self.ack_seq, max(e["seq"] for e in evs))
        new = sorted((e for e in evs if e["seq"] > self.last_seq), key=lambda e: e["seq"])
        if new:
            self.last_seq = new[-1]["seq"]
        out.extend(new)

        try:
            await self._store_telemetry(state)
            if out:
                await self.record_events(out, publish=False)
        except Exception:  # noqa: BLE001 - lỗi ghi đĩa không được làm sập poller
            log.exception("không ghi được vào SQLite")
        self._publish(self.recent_tail(len(out)))

    # --- lệnh xuống Pi ----------------------------------------------------------------------

    @property
    def push_mode(self) -> bool:
        return bool(self.cfg.pi_token)

    async def send_cmd(self, body: dict) -> tuple[int, dict]:
        """Gửi lệnh tới Pi, trả (http status, reply). Status 200 nghĩa là đã nhận được reply từ Pi."""
        if not self.push_mode:
            try:
                r = await self.http.post("/api/cmd", json=body)
                return (200 if r.status_code == 200 else 502), r.json()
            except (httpx.HTTPError, ValueError) as e:
                return 502, {"ok": False, "error": f"Pi không phản hồi ({type(e).__name__})"}
        if not self.pi_online:
            return 502, {"ok": False, "error": "Pi đang mất kết nối"}
        cid = uuid.uuid4().hex
        t = self.cfg.cmd_timeout_s
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._queue[cid] = (time.monotonic() + t, {"id": cid, **body}, fut)
        try:
            return 200, await asyncio.wait_for(fut, t)
        except TimeoutError:
            # còn trong hàng đợi = Pi chưa nhận: xóa để lệnh cũ (vd phun) không chạy muộn
            not_taken = self._queue.pop(cid, None) is not None
            self._waiting.pop(cid, None)
            what = "không nhận lệnh" if not_taken else "không trả kết quả"
            return 504, {"ok": False, "error": f"Pi {what} trong {t:g} s"}

    def take_cmds(self) -> list[dict]:
        """Lấy các lệnh còn hạn ra khỏi hàng đợi để trao cho Pi (mỗi lệnh đúng một lần)."""
        now, out = time.monotonic(), []
        for cid, (deadline, cmd, fut) in list(self._queue.items()):
            del self._queue[cid]
            if deadline > now and not fut.done():
                self._waiting[cid] = fut
                out.append(cmd)
        return out

    def deliver_results(self, results: list[dict]) -> None:
        for r in results:
            fut = self._waiting.pop(r.get("id"), None)
            if fut is not None and not fut.done():
                fut.set_result(r["reply"] if isinstance(r.get("reply"), dict) else {"ok": False})

    def recent_tail(self, n: int) -> list[dict]:
        return list(self.recent)[-n:] if n else []

    async def _failed(self, e: Exception) -> None:
        self._fails += 1
        self.error = f"{type(e).__name__}: {e}"
        if self._fails == 1:
            log.warning("không hỏi được Pi: %s", self.error)
        if self._fails >= 3 and self._was_online is not False:
            self._was_online = False
            await self.record_events([{"kind": "status", "level": "error", "text": "Mất kết nối với Pi"}])
            return
        self._publish([])

    async def _store_telemetry(self, state: dict) -> None:
        now = float(state.get("time") or time.time())
        rows = []
        for node, samples in (state.get("sensors") or {}).items():
            if samples:
                s = samples[-1]
                rows.append((now + float(s.get("t") or 0), node, s.get("temp"), s.get("gas"), s.get("hum")))
        if rows:
            await asyncio.to_thread(self.store.add_telemetry, rows)
        orch = state.get("orch") or {}
        runs = [r for r in (orch.get("runs") or []) if isinstance(r, dict) and "id" in r and "started" in r]
        if isinstance(orch.get("run"), dict):
            runs.append(orch["run"])
        if runs:
            await asyncio.to_thread(self.store.upsert_runs, runs)

    # --- vòng đời ---------------------------------------------------------------------------

    async def check_push_timeout(self) -> None:
        """Chế độ push: lâu không có push thì ghi "Mất kết nối với Pi" một lần."""
        if self.last_ok is None or self.pi_online or self._was_online is False:
            return
        self._was_online = False
        self.error = "Không nhận được dữ liệu Pi đẩy lên"
        log.warning(self.error)
        await self.record_events([{"kind": "status", "level": "error", "text": "Mất kết nối với Pi"}])

    async def _loop(self) -> None:
        next_cleanup = 0.0
        while True:
            t0 = time.monotonic()
            try:
                if self.push_mode:
                    await self.check_push_timeout()
                else:
                    await self.poll_once()
                if t0 >= next_cleanup:
                    n = await asyncio.to_thread(self.store.cleanup, self.cfg.retention_days)
                    next_cleanup = t0 + 86400
                    if n:
                        log.info("dọn %d dòng cũ hơn %d ngày", n, self.cfg.retention_days)
            except Exception:  # noqa: BLE001
                log.exception("lỗi poller")
            await asyncio.sleep(max(0.05, self.cfg.poll_s - (time.monotonic() - t0)))

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="pi-poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._owns_http:
            await self.http.aclose()
