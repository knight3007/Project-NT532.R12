"""Số đo mạng Thread cho báo cáo: ping ICMPv6, độ trễ CoAP, vai trò (leader/router/child) và topology.

    collect({"s1": "fd59::1", "s2": "fd59::2"}) -> {"ts", "otbr", "nodes", "topology"?, "topology_error"?}

Nguồn:
  - otbr: OTBR REST (http://127.0.0.1:8081, chỉ localhost): /node và dataset đang chạy. Dataset chứa networkKey và
    pskc nên chỉ giữ channel, panId, networkName, extPanId, meshLocalPrefix; khóa không bao giờ ghi log hay gửi đi.
  - nodes: `ping -6` (mất gói, rtt min/avg/max) và CoAP GET /status (độ trễ, role), /info (fw, nhớ lại).
  - topology: JSON:API `/api/actions` của OTBR (getNetworkDiagnosticTask, đích là rloc16): routerNeighbors
    (link margin, RSSI, tỉ lệ lỗi khung) và children của từng router. Chậm (vài giây mỗi router) nên chỉ làm lại
    mỗi `topology_every` giây và chạy nền; OTBR cũ không có /api/actions thì bỏ qua, ghi lý do vào topology_error.

Chỉ dùng thư viện chuẩn và aiocoap. Các hàm phân tích thuần (parse_*) không đụng mạng để test.
"""

import asyncio
import json
import re
import time
import urllib.error
import urllib.request

OTBR_URL = "http://127.0.0.1:8081"
NODE_KEYS = ("state", "rloc16", "routerCount", "networkName", "extPanId", "baState", "routerId")
DATASET_KEYS = ("channel", "panId", "networkName", "extPanId", "meshLocalPrefix")  # KHÔNG gồm networkKey, pskc
NEIGHBOR_KEYS = ("rloc16", "extAddress", "linkMargin", "averageRssi", "lastRssi", "frameErrorRate",
                 "messageErrorRate", "linkAge")
CHILD_KEYS = ("rloc16", "extAddress", "linkMargin", "averageRssi", "lastRssi", "frameErrorRate",
              "messageErrorRate", "linkAge", "timeout", "fullThreadDevice", "rxOnWhenIdle")
DIAG_TYPES = ("extAddress", "rloc16", "routerNeighbors", "children")
COAP_TIMEOUT_S = 3.0
TOPOLOGY_EVERY_S = 60.0
ACTION_TIMEOUT_S = 8
ACTIONS_JSON = "application/vnd.api+json"

_PKT = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")
_LOSS = re.compile(r"([\d.]+)% packet loss")
_RTT = re.compile(r"= ([\d.]+)/([\d.]+)/([\d.]+)/")


# --- phân tích thuần ----------------------------------------------------------------------------


def parse_ping(out: str, count: int = 0) -> dict:
    """Kết quả `ping` Linux (iputils) -> {sent, recv, loss_pct, rtt_min, rtt_avg, rtt_max}; rtt None nếu mất hết."""
    res = {"sent": count, "recv": 0, "loss_pct": 100.0, "rtt_min": None, "rtt_avg": None, "rtt_max": None}
    if m := _PKT.search(out):
        res["sent"], res["recv"] = int(m.group(1)), int(m.group(2))
        res["loss_pct"] = round(100.0 * (res["sent"] - res["recv"]) / res["sent"], 1) if res["sent"] else 100.0
    if m := _LOSS.search(out):
        res["loss_pct"] = float(m.group(1))
    if m := _RTT.search(out):
        res["rtt_min"], res["rtt_avg"], res["rtt_max"] = (float(m.group(i)) for i in (1, 2, 3))
    return res


def parse_otbr_node(node: dict, dataset: dict | None = None) -> dict:
    """Tập con của GET /node (+ channel/panId từ dataset). Bỏ mọi khóa khác, nên không lọt bí mật."""
    out = {k: node[k] for k in NODE_KEYS if k in node}
    ld = node.get("leaderData")
    if isinstance(ld, dict):
        out["partitionId"] = ld.get("partitionId")
        out["leaderRouterId"] = ld.get("leaderRouterId")
    for k, v in (dataset or {}).items():
        if k in DATASET_KEYS and k not in out:
            out[k] = v
    return out


def _hex16(v) -> int | None:
    try:
        return int(str(v), 16)
    except ValueError:
        return None


def _pick(d: dict, keys: tuple[str, ...]) -> dict:
    return {k: d[k] for k in keys if k in d}


def parse_diagnostics(attrs: dict) -> dict:
    """attributes của một mục `networkDiagnostics` -> {rloc16, ext, routerId, neighbors[], children[]}."""
    nb = [_pick(n, NEIGHBOR_KEYS) for n in attrs.get("routerNeighbors") or [] if isinstance(n, dict)]
    ch = [_pick(c, CHILD_KEYS) for c in attrs.get("children") or [] if isinstance(c, dict)]
    return {"rloc16": attrs.get("rloc16"), "ext": attrs.get("extAddress"), "routerId": attrs.get("routerId"),
            "neighbors": nb, "children": ch}


def build_topology(diags: list[dict]) -> dict:
    """Gộp kết quả chẩn đoán của các router thành {routers: [...], links: [{a, b, margin, rssi}]} (mỗi cạnh một lần)."""
    routers = [d for d in diags if d.get("rloc16")]
    links, seen = [], set()
    for d in routers:
        for n in d["neighbors"]:
            key = tuple(sorted((str(d["rloc16"]), str(n.get("rloc16")))))
            if key in seen:
                continue
            seen.add(key)
            links.append({"a": d["rloc16"], "b": n.get("rloc16"), "margin": n.get("linkMargin"),
                          "rssi": n.get("averageRssi"), "fer": n.get("frameErrorRate")})
    return {"routers": routers, "links": links}


# --- I/O (test thay bằng giả) -------------------------------------------------------------------


async def run_ping(addr: str, count: int) -> dict:
    """`ping -6 -c N -i 0.2 -W 2 addr`; lỗi chạy lệnh -> {"error": ...}."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-6", "-c", str(count), "-i", "0.2", "-W", "2", addr,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=count * 0.2 + 8)
    except (OSError, TimeoutError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return parse_ping(out.decode(errors="replace"), count)


def _http(method: str, url: str, body: dict | None = None, accept: str = "application/json",
          timeout: float = 5.0) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": accept}
    if data is not None:
        headers["Content-Type"] = ACTIONS_JSON
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


async def http_json(method: str, url: str, body: dict | None = None, accept: str = "application/json",
                    timeout: float = 5.0) -> dict | list:
    return await asyncio.to_thread(_http, method, url, body, accept, timeout)


async def coap_get(ctx, addr: str, path: str, timeout: float = COAP_TIMEOUT_S) -> tuple[float, bytes]:
    """GET coap://[addr]/path -> (ms, payload); ném ngoại lệ khi lỗi/hết giờ."""
    from aiocoap import GET, Message

    host = f"[{addr}]" if ":" in addr else addr
    t0 = time.monotonic()
    resp = await asyncio.wait_for(ctx.request(Message(code=GET, uri=f"coap://{host}{path}")).response, timeout)
    if not resp.code.is_successful():
        raise RuntimeError(f"CoAP {resp.code}")
    return (time.monotonic() - t0) * 1000.0, resp.payload


# --- thu thập -----------------------------------------------------------------------------------


class Collector:
    def __init__(self, otbr_url: str = OTBR_URL, topology_every: float = TOPOLOGY_EVERY_S) -> None:
        self.otbr = otbr_url.rstrip("/")
        self.topology_every = topology_every
        self.fw: dict[str, str] = {}
        self.last_ok: dict[str, float] = {}
        self.topology: dict | None = None
        self.topology_error: str | None = None
        self._topo_at = 0.0
        self._topo_task: asyncio.Task | None = None

    async def _otbr(self) -> dict:
        try:
            node = await http_json("GET", self.otbr + "/node")
            try:
                ds = await http_json("GET", self.otbr + "/node/dataset/active")
            except Exception:  # noqa: BLE001 - dataset là phần thêm
                ds = {}
            # chỉ whitelist: networkKey, pskc bị bỏ ngay tại đây
            ds = {k: ds[k] for k in DATASET_KEYS if isinstance(ds, dict) and k in ds}
            return {"ok": True, **parse_otbr_node(node, ds)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def _node(self, ctx, name: str, addr: str, count: int) -> dict:
        async def coap() -> dict:
            out: dict = {"ok": False}
            try:
                ms, payload = await coap_get(ctx, addr, "/status")
                out.update(ok=True, rtt_ms=round(ms, 1))
                try:
                    role = json.loads(payload).get("role")
                    out["role"] = role if isinstance(role, str) else None
                except (ValueError, AttributeError):
                    out["role"] = None
                if name not in self.fw:
                    try:
                        _, info = await coap_get(ctx, addr, "/info")
                        fw = json.loads(info).get("fw")
                        if isinstance(fw, str):
                            self.fw[name] = fw
                    except Exception:  # noqa: BLE001, S110 - fw chỉ là phần thêm
                        pass
            except Exception as e:  # noqa: BLE001
                out["error"] = f"{type(e).__name__}: {e}"[:200]
            return out

        ping, co = await asyncio.gather(run_ping(addr, count), coap())
        if ping.get("recv") or co["ok"]:
            self.last_ok[name] = time.time()
        return {"addr": addr, "ping": ping, "coap": co, "role": co.get("role"), "fw": self.fw.get(name),
                "last_ok": self.last_ok.get(name)}

    # --- topology qua /api/actions ------------------------------------------------------------

    async def _diagnose(self, rloc: str) -> dict:
        base = self.otbr + "/api/actions"
        body = {"data": [{"type": "getNetworkDiagnosticTask", "attributes": {
            "destination": rloc, "destinationType": "rloc", "types": list(DIAG_TYPES), "timeout": ACTION_TIMEOUT_S}}]}
        made = await http_json("POST", base, body, ACTIONS_JSON)
        aid = made["data"][0]["id"]
        deadline = time.monotonic() + ACTION_TIMEOUT_S + 3
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            act = (await http_json("GET", f"{base}/{aid}", None, ACTIONS_JSON))["data"]
            status = act["attributes"].get("status")
            if status == "completed":
                rid = act["relationships"]["result"]["data"]["id"]
                res = await http_json("GET", f"{self.otbr}/api/diagnostics/{rid}", None, ACTIONS_JSON)
                return parse_diagnostics(res["data"]["attributes"])
            if status in ("failed", "stopped"):
                raise RuntimeError(f"{rloc}: {status}")
        raise TimeoutError(f"{rloc}: hết giờ chẩn đoán")

    async def _refresh_topology(self, self_rloc: str | None) -> None:
        try:
            if not self_rloc:
                raise RuntimeError("chưa biết rloc16 của border router")
            done: dict[str, dict] = {}
            todo = [self_rloc]
            while todo and len(done) < 16:
                r = todo.pop(0)
                if r in done:
                    continue
                try:
                    done[r] = await self._diagnose(r)
                except (RuntimeError, TimeoutError) as e:
                    if r == self_rloc:
                        raise
                    done[r] = {"rloc16": r, "ext": None, "routerId": None, "neighbors": [], "children": [],
                               "error": str(e)}
                    continue
                for n in done[r]["neighbors"]:
                    nr = n.get("rloc16")
                    if isinstance(nr, str) and nr not in done and nr not in todo:
                        todo.append(nr)
            self.topology = {**build_topology(list(done.values())), "ts": time.time()}
            self.topology_error = None
        except urllib.error.HTTPError as e:
            self.topology_error = f"OTBR /api/actions: HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            self.topology_error = f"{type(e).__name__}: {e}"[:200]

    async def collect_async(self, nodes: dict[str, str], ping_count: int = 5, wait_topology: bool = False) -> dict:
        from aiocoap import Context

        ctx = await Context.create_client_context()
        try:
            otbr_task = asyncio.create_task(self._otbr())
            node_tasks = {n: asyncio.create_task(self._node(ctx, n, a, ping_count)) for n, a in nodes.items() if a}
            otbr = await otbr_task
            out_nodes = {n: await t for n, t in node_tasks.items()}
        finally:
            await ctx.shutdown()
        now = time.time()
        if otbr.get("ok") and (self._topo_task is None or self._topo_task.done()) \
                and now - self._topo_at >= self.topology_every:
            self._topo_at = now
            self._topo_task = asyncio.create_task(self._refresh_topology(otbr.get("rloc16")))
        if wait_topology and self._topo_task is not None:
            await self._topo_task
        out = {"ts": now, "otbr": otbr, "nodes": out_nodes}
        if self.topology is not None:
            out["topology"] = self.topology
        if self.topology_error:
            out["topology_error"] = self.topology_error
        return out


_default = Collector()


def collect(nodes: dict[str, str], otbr_url: str = OTBR_URL, ping_count: int = 5) -> dict:
    """Một lượt đo đồng bộ (tiện cho kiểm tra tay); topology được chờ xong. Vòng lặp dài hạn dùng Collector."""
    c = _default if otbr_url.rstrip("/") == _default.otbr else Collector(otbr_url)
    return asyncio.run(c.collect_async(nodes, ping_count, wait_topology=True))
