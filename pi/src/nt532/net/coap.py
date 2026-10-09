"""CoAP thật bằng aiocoap: server cho node (`/t`, `/a`, `/status`) và NodeLink gửi lệnh tới node.

Chạy vòng asyncio ở một luồng riêng; phía orchestrator gọi các hàm đồng bộ như với `SimLink`.
Địa chỉ node lấy từ `network.nodes` trong site.yaml (trước khi có SRP/DNS-SD ở tuần 4).

CHƯA THỬ VỚI PHẦN CỨNG: phần này viết theo hợp đồng ở kế hoạch mục 4 để Hậu nối vào firmware;
các test chỉ chạy qua localhost.
"""

import asyncio
import ipaddress
import threading
from collections.abc import Callable

from aiocoap import CHANGED, GET, POST, Context, Message, Unreliable, resource
from aiocoap.numbers.codes import BAD_REQUEST

from .link import LinkError, NodeLink
from .protocol import Alert, Deduper, Info, PayloadError, Status, Telemetry, dumps

HB_REPLY_S = 1.0  # chờ trả lời /hb tối đa; trễ hơn thì coi như mất nhịp đó


class _Post(resource.Resource):
    def __init__(self, handle: Callable[[bytes, str], None]) -> None:
        super().__init__()
        self.handle = handle

    async def render_post(self, request):
        try:
            self.handle(request.payload, request.remote.hostinfo)
        except PayloadError as e:
            return Message(code=BAD_REQUEST, payload=str(e).encode())
        return Message(code=CHANGED)


class CoapLink(NodeLink):
    """`nodes`: {tên node: địa chỉ host[:port]}. Callback `on_telemetry`, `on_alert` gán sau khi tạo."""

    def __init__(self, nodes: dict[str, str], bind: tuple[str, int] = ("::", 5683),
                 timeout_s: float = 2.0, transports: list[str] | None = None) -> None:
        super().__init__(list(nodes))
        self.transports = transports  # None: mặc định của aiocoap (udp6, cần IPv6 như trên Thread)
        self.addr = dict(nodes)
        self.bind, self.timeout = bind, timeout_s
        self.on_telemetry: Callable[[Telemetry], None] | None = None
        self.on_alert: Callable[[Alert], None] | None = None
        self.dedup = Deduper()
        self.errors: list[str] = []
        self._loop = asyncio.new_event_loop()
        self._ctx: Context | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="coap")

    # --- vòng asyncio -----------------------------------------------------------------------

    def start(self) -> "CoapLink":
        self._thread.start()
        if not self._ready.wait(10):
            raise LinkError("không khởi động được CoAP server")
        if self.errors:
            raise LinkError(self.errors[-1])
        return self

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.run_forever()

    async def _serve(self) -> None:
        root = resource.Site()
        root.add_resource(["t"], _Post(self._rx_telemetry))
        root.add_resource(["a"], _Post(self._rx_alert))
        root.add_resource(["status"], _Post(self._rx_status))
        try:
            self._ctx = await Context.create_server_context(root, bind=self.bind,
                                                            transports=self.transports)
        except OSError as e:
            self.errors.append(f"không mở được cổng CoAP {self.bind}: {e}")
        self._ready.set()

    def close(self) -> None:
        if self._ctx is not None:
            asyncio.run_coroutine_threadsafe(self._ctx.shutdown(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)

    # --- nhận -------------------------------------------------------------------------------

    def _node_of(self, host: str, fallback: str | None) -> str | None:
        host = host.strip("[]").rsplit("]:", 1)[0]
        for name, addr in self.addr.items():
            if addr.split("%")[0].strip("[]") in host:
                return name
        return fallback

    def _rx_telemetry(self, raw: bytes, host: str) -> None:
        tel = Telemetry.parse(raw)
        self.on_rx(tel.n)
        if self.on_telemetry:
            self.on_telemetry(tel)

    def _rx_alert(self, raw: bytes, host: str) -> None:
        alert = Alert.parse(raw)
        self.on_rx(alert.n)
        if not self.dedup.seen((alert.n, alert.s)) and self.on_alert:
            self.on_alert(alert)

    def _rx_status(self, raw: bytes, host: str) -> None:
        st = Status.parse(raw)
        node = self._node_of(host, st.n)
        if node is None:
            raise PayloadError(f"/status từ địa chỉ lạ {host}")
        self.on_status(node, st)

    # --- gửi --------------------------------------------------------------------------------

    def _send(self, node: str, path: str, msg) -> None:
        confirmable = path != "hb"
        payload = b"" if msg is None else dumps(msg)
        req = Message(code=POST, payload=payload, uri=f"coap://{self._host(node)}/{path}",
                      **({} if confirmable else {"transport_tuning": Unreliable}))
        fut = asyncio.run_coroutine_threadsafe(self._request(req, self.timeout if confirmable else HB_REPLY_S),
                                               self._loop)
        if not confirmable:
            # /hb là NON (không gửi lại), nhưng node vẫn trả 2.04 NON: có trả lời là node còn sống.
            # Không có bước này thì trên mạng thật hb_age_ms chỉ được làm mới bởi /t (5–10 s) và vòi bị chặn.
            fut.add_done_callback(lambda f: self._hb_reply(node, f))
            return
        try:
            fut.result(self.timeout + 1)
        except Exception as e:  # noqa: BLE001 - mọi lỗi mạng đều thành LinkError cho orchestrator
            raise LinkError(f"gửi /{path} tới {node} lỗi: {type(e).__name__}: {e}") from None
        self.on_rx(node)

    def _get_info(self, node: str, timeout: float) -> Info:
        req = Message(code=GET, uri=f"coap://{self._host(node)}/info")
        fut = asyncio.run_coroutine_threadsafe(self._request(req, timeout), self._loop)
        try:
            resp = fut.result(timeout + 1)
        except Exception as e:  # noqa: BLE001 - mọi lỗi mạng đều thành LinkError
            raise LinkError(f"GET /info từ {node} lỗi: {type(e).__name__}: {e}") from None
        if not resp.code.is_successful():
            raise LinkError(f"GET /info từ {node} trả {resp.code}")
        try:
            info = Info.parse(resp.payload)
        except PayloadError as e:
            raise LinkError(f"/info của {node} sai dạng: {e}") from None
        self.on_rx(node)
        return info

    def _hb_reply(self, node: str, fut) -> None:
        if not fut.cancelled() and fut.exception() is None:
            self.on_rx(node)

    async def _request(self, req: Message, timeout: float):
        if self._ctx is None:
            raise LinkError("CoAP chưa khởi động")
        # không dùng cơ chế gửi lại mặc định (có thể kéo hàng chục giây): chờ có hạn
        return await asyncio.wait_for(self._ctx.request(req).response, timeout)

    def _host(self, node: str) -> str:
        """'fd00::1' -> '[fd00::1]'; '[fd00::1]:5683', '10.0.0.5:5683', 'node.local' giữ nguyên."""
        a = self.addr[node]
        try:
            if ipaddress.ip_address(a.split("%")[0]).version == 6:
                return f"[{a}]"
        except ValueError:
            pass
        return a
