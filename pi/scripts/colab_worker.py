"""Worker chạy trên Google Colab: chạy bộ quyết định (Jev, GPU) và phục vụ quyết định cho hub homelab.

    uv run python scripts/colab_worker.py --hub-ws wss://control.example/api/worker/ws \
        --token "$NT532_WORKER_TOKEN" --decider jev --jev-run jev2 --device cuda

Colab không nhận kết nối vào, nên worker tự MỞ WebSocket RA hub (có tự nối lại, lùi 1 -> 30 s).
Giao thức xem homelab/colab/protocol.md. Cùng JSON với POST /decide của serve_decider.py:
hub gửi {"type":"decide","id","obs","questions","deadline_ms"}, worker trả {"type":"result",...}.
Token chỉ gửi trong header Authorization và không bao giờ được ghi log.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from datetime import UTC, datetime

log = logging.getLogger("colab_worker")

MAX_BACKOFF_S = 30.0
MAX_MSG = 4_000_000  # byte


class Worker:
    """Giữ kết nối tới hub, nhận yêu cầu decide, chạy `decide_fn(obs, questions) -> dict` (JSON của
    Decision) từng cái một dưới khóa trong thread riêng để socket luôn phản hồi ping."""

    def __init__(self, hub_ws: str, decide_fn, *, token: str | None = None, worker_id: str | None = None,
                 info: dict | None = None, ready: bool = True, backoff_min_s: float = 1.0,
                 backoff_max_s: float = MAX_BACKOFF_S) -> None:
        self.hub_ws = hub_ws
        self.decide_fn = decide_fn
        self.token = token
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
        self.info = info or {}
        self.ready = ready
        self.backoff_min_s = backoff_min_s
        self.backoff_max_s = backoff_max_s
        self.done = 0
        self.busy = False
        self.connects = 0
        self._lock = threading.Lock()  # mô hình không reentrant
        self._stop = asyncio.Event()
        self.exit_code = 0

    # --- tiện ích --------------------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()

    def hello(self) -> dict:
        return {"type": "hello", "worker_id": self.worker_id, "decider": self.info.get("decider"),
                "run": self.info.get("run"), "device": self.info.get("device", "cpu"),
                "gpu": self.info.get("gpu"), "started": self.info.get("started") or _now()}

    def status(self) -> dict:
        return {"type": "status", "ready": self.ready, "busy": self.busy, "done": self.done}

    def _run_decide(self, msg: dict, t_recv: float) -> dict:
        """Chạy trong thread. Trả message result/error (luôn là dict, không ném)."""
        rid = msg.get("id")
        try:
            obs, questions = msg["obs"], msg["questions"]
            if not isinstance(obs, dict) or not isinstance(questions, dict):
                raise TypeError("obs và questions phải là object")
        except (KeyError, TypeError) as e:
            return {"type": "error", "id": rid, "error": f"yêu cầu sai dạng: {e}"}
        deadline = msg.get("deadline_ms")
        with self._lock:
            waited_ms = (time.monotonic() - t_recv) * 1000
            if isinstance(deadline, (int, float)) and not isinstance(deadline, bool) and waited_ms > deadline:
                log.warning("decide %s quá hạn (chờ %.0f ms > %s ms)", rid, waited_ms, deadline)
                return {"type": "error", "id": rid, "error": "deadline"}
            self.busy = True
            t0 = time.perf_counter()
            try:
                decision = self.decide_fn(obs, questions)
            except Exception as e:  # noqa: BLE001
                log.error("decide %s lỗi: %s: %s", rid, type(e).__name__, e)
                return {"type": "error", "id": rid, "error": f"{type(e).__name__}: {e}"}
            finally:
                self.busy = False
            ms = (time.perf_counter() - t0) * 1000
            self.done += 1
        log.info("decide %s %s -> %.0f ms", rid, obs.get("stage", "?"), ms)
        return {"type": "result", "id": rid, "decision": decision, "latency_ms": round(ms, 1)}

    # --- vòng lặp kết nối -------------------------------------------------------------------
    async def run(self) -> int:
        import websockets

        backoff = self.backoff_min_s
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.hub_ws, additional_headers=headers,
                                              max_size=MAX_MSG, ping_interval=None) as ws:
                    self.connects += 1
                    backoff = self.backoff_min_s
                    log.info("đã nối hub %s (worker %s)", _redact(self.hub_ws), self.worker_id)
                    await self._session(ws)
                    log.info("mất kết nối hub")
            except (TimeoutError, OSError, websockets.WebSocketException) as e:
                log.warning("không nối được hub: %s: %s (thử lại sau %.0f s)",
                            type(e).__name__, _redact(str(e)), backoff)
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._stop.wait(), backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, self.backoff_max_s)
        return self.exit_code

    async def _session(self, ws) -> None:
        await ws.send(json.dumps(self.hello()))
        if self.ready:
            await ws.send(json.dumps(self.status()))
        tasks: set[asyncio.Task] = set()
        stop_task = asyncio.ensure_future(self._stop.wait())
        try:
            while not self._stop.is_set():
                recv = asyncio.ensure_future(ws.recv())
                await asyncio.wait({recv, stop_task}, return_when=asyncio.FIRST_COMPLETED)
                if not recv.done():
                    recv.cancel()
                    break
                try:
                    raw = recv.result()
                except Exception:  # noqa: BLE001 - đóng kết nối
                    break
                await self._handle(ws, raw, tasks)
        finally:
            stop_task.cancel()
            for t in tasks:
                t.cancel()

    async def _handle(self, ws, raw, tasks: set) -> None:
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                raise TypeError("không phải object")
        except (ValueError, TypeError) as e:
            log.warning("bỏ qua frame không phải JSON: %s", e)
            return
        kind = msg.get("type")
        if kind == "ping":
            await ws.send(json.dumps({"type": "pong", "busy": self.busy, "done": self.done}))
        elif kind == "decide":
            t_recv = time.monotonic()
            t = asyncio.ensure_future(self._decide_task(ws, msg, t_recv))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
        elif kind == "shutdown":
            log.info("hub yêu cầu tắt")
            self.exit_code = 0
            self._stop.set()
        else:
            log.warning("bỏ qua message type=%r", kind)

    async def _decide_task(self, ws, msg: dict, t_recv: float) -> None:
        reply = await asyncio.to_thread(self._run_decide, msg, t_recv)
        try:
            await ws.send(json.dumps(reply, default=str))
        except Exception as e:  # noqa: BLE001 - kết nối vừa đứt, hub sẽ tự rơi về luật
            log.warning("không gửi được trả lời %s: %s", msg.get("id"), type(e).__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _redact(text: str) -> str:
    """Bỏ giá trị token khỏi URL/thông báo lỗi trước khi ghi log."""
    import re
    return re.sub(r"(token=)[^&\s'\"]+", r"\1***", text)


# --- dựng bộ quyết định thật ---------------------------------------------------------------------

def warm_up(decider) -> None:
    """Nạp mô hình ngay lúc khởi động thay vì ở yêu cầu đầu tiên (như serve_decider.warm_up)."""
    from nt532.orchestrator.decide import JevDecider
    jev = getattr(decider, "model_decider", decider)
    if isinstance(jev, JevDecider):
        jev.model.predict_logits([{"state": "stage: decide", "questions": {
            "real_fire": {"type": "boolean", "candidates": []}}}])


def make_decide_fn(decider):
    """Bọc decider thành hàm obs, questions -> JSON y hệt POST /decide của serve_decider."""
    def decide(obs: dict, questions: dict) -> dict:
        dec = decider.decide(obs, questions)
        reply = dec.to_json()
        err = getattr(decider, "last_error", None)
        if err:
            reply["error"] = err
        return reply
    return decide


def gpu_name() -> str | None:
    try:
        import torch
        return torch.cuda.get_device_name() if torch.cuda.is_available() else None
    except Exception:  # noqa: BLE001
        return None


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--hub-ws", default=env("NT532_HUB_WS"), help="wss://.../api/worker/ws (env NT532_HUB_WS)")
    p.add_argument("--token", default=env("NT532_WORKER_TOKEN"), help="env NT532_WORKER_TOKEN")
    p.add_argument("--decider", choices=["rules", "jev", "hybrid"], default="jev")
    p.add_argument("--jev-run", default=env("NT532_JEV_RUN", "jev2"), help="thư mục trong runs/decider/jev/")
    p.add_argument("--tau", type=float, default=0.8, help="ngưỡng tin cậy của chế độ lai")
    p.add_argument("--model-stages", default="verify", help="chế độ lai: verify, decide hoặc decide,verify")
    p.add_argument("--device", default="cuda" if gpu_name() else "cpu", help="cpu hoặc cuda")
    p.add_argument("--worker-id", default=env("NT532_WORKER_ID"))
    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if not args.hub_ws:
        p.error("thiếu --hub-ws (hoặc env NT532_HUB_WS)")
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    from nt532.orchestrator.decide import make_decider, parse_stages
    try:
        parse_stages(args.model_stages)
    except ValueError as e:
        p.error(str(e))

    decider = make_decider(args.decider, args.jev_run, args.tau, args.device, args.model_stages)
    if args.decider != "rules":
        log.info("đang nạp mô hình %s trên %s...", args.jev_run, args.device)
        warm_up(decider)
    worker = Worker(args.hub_ws, make_decide_fn(decider), token=args.token, worker_id=args.worker_id,
                    info={"decider": decider.name, "run": args.jev_run, "device": args.device,
                          "gpu": gpu_name(), "started": _now()})

    async def runner() -> int:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, worker.stop)
            except (NotImplementedError, RuntimeError):  # Windows
                pass
        return await worker.run()

    try:
        return asyncio.run(runner())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
