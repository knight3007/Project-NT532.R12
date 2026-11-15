// Trang Nhật ký: bộ lọc (lưu trong URL), biểu đồ cảm biến, bảng sự kiện phân trang.
import { lineChart } from "./chart.js";
import { el } from "./format.js";
import { RANGES, api, fromLocalInput, initShell, stamp, toLocalInput, toast } from "./shell.js";

const $ = (id) => document.getElementById(id);
const PAGE = 100;
const KINDS = [
  ["alert", "Cảnh báo", ["alert", "alarm", "safety"]],
  ["phase", "Pha", ["phase"]],
  ["decision", "Quyết định", ["decision"]],
  ["cmd", "Lệnh", ["cmd"]],
  ["fault", "Lỗi", ["fault"]],
  ["status", "Trạng thái", ["status"]],
  ["sim", "Sim", ["sim"]],
];
const NODES = ["s1", "s2"];
const SERIES = [["temp", "Nhiệt độ", "°C", "temp"], ["gas", "Khí gas", "ppm", "gas"], ["hum", "Độ ẩm", "%", null]];
const BASE = new Set(["id", "seq", "time", "kind", "level", "text", "node"]);

initShell();

// --- trạng thái bộ lọc, đồng bộ với URL ------------------------------------------------------

const st = { node: "", level: "", q: "", kinds: new Set(), range: "1h", from: null, to: null };
const qs = new URLSearchParams(location.search);
st.node = NODES.includes(qs.get("node")) ? qs.get("node") : "";
st.level = ["info", "warn", "error"].includes(qs.get("level")) ? qs.get("level") : "";
st.q = qs.get("q") ?? "";
qs.getAll("kind").forEach((k) => KINDS.some((x) => x[0] === k) && st.kinds.add(k));
if (qs.get("from")) {
  st.range = "custom";
  st.from = Number(qs.get("from"));
  st.to = qs.get("to") ? Number(qs.get("to")) : null;
} else if (RANGES.some((r) => r[0] === qs.get("range"))) st.range = qs.get("range");

function syncUrl() {
  const p = new URLSearchParams();
  if (st.node) p.set("node", st.node);
  if (st.level) p.set("level", st.level);
  if (st.q) p.set("q", st.q);
  st.kinds.forEach((k) => p.append("kind", k));
  if (st.range === "custom") {
    p.set("from", Math.round(st.from));
    if (st.to) p.set("to", Math.round(st.to));
  } else p.set("range", st.range);
  history.replaceState(null, "", "?" + p);
}

// khoảng thời gian thực (epoch giây); khoảng có sẵn trượt theo giờ hiện tại
function window_() {
  if (st.range === "custom") return { from: st.from, to: st.to ?? Date.now() / 1000 };
  const sec = RANGES.find((r) => r[0] === st.range)[2];
  const now = Date.now() / 1000;
  return { from: now - sec, to: now };
}

function eventParams() {
  const w = window_();
  const p = new URLSearchParams();
  if (st.node) p.set("node", st.node);
  if (st.level) p.set("level", st.level);
  if (st.q.trim()) p.set("q", st.q.trim());
  for (const k of st.kinds) KINDS.find((x) => x[0] === k)[2].forEach((v) => p.append("kind", v));
  p.set("from", w.from);
  if (st.range === "custom" && st.to) p.set("to", st.to);
  return p;
}

// --- chip và ô nhập --------------------------------------------------------------------------

const kindChips = KINDS.map(([key, label]) => {
  const b = el("button", "fchip", label);
  b.type = "button";
  b.addEventListener("click", () => {
    st.kinds.has(key) ? st.kinds.delete(key) : st.kinds.add(key);
    paintChips();
    reload();
  });
  $("f-kinds").append(b);
  return [key, b];
});
const rangeChips = [...RANGES.map((r) => [r[0], r[1]]), ["custom", "Tùy chọn"]].map(([key, label]) => {
  const b = el("button", "fchip", label);
  b.type = "button";
  b.addEventListener("click", () => {
    if (key === "custom") {
      const w = window_();
      st.range = "custom";
      st.from ??= w.from;
      st.to ??= null;
    } else st.range = key;
    paintChips();
    if (key !== "custom") reload();
  });
  $("f-range").append(b);
  return [key, b];
});

function paintChips() {
  kindChips.forEach(([k, b]) => b.setAttribute("aria-pressed", String(st.kinds.has(k))));
  rangeChips.forEach(([k, b]) => b.setAttribute("aria-pressed", String(st.range === k)));
  $("custom-range").hidden = st.range !== "custom";
  if (st.range === "custom") {
    $("f-from").value = st.from ? toLocalInput(st.from) : "";
    $("f-to").value = st.to ? toLocalInput(st.to) : "";
  }
}
$("f-node").value = st.node;
$("f-level").value = st.level;
$("f-q").value = st.q;
paintChips();

$("f-node").addEventListener("change", () => { st.node = $("f-node").value; reload(); });
$("f-level").addEventListener("change", () => { st.level = $("f-level").value; reload(); });
let qTimer;
$("f-q").addEventListener("input", () => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { st.q = $("f-q").value; reload(); }, 300);
});
$("f-apply").addEventListener("click", () => {
  const f = fromLocalInput($("f-from").value);
  const t = fromLocalInput($("f-to").value);
  if (f == null) return toast("Chọn thời điểm bắt đầu", true);
  if (t != null && t <= f) return toast("Thời điểm kết thúc phải sau thời điểm bắt đầu", true);
  st.from = f;
  st.to = t;
  reload();
});

// --- bảng sự kiện ----------------------------------------------------------------------------

let offset = 0;
let total = 0;
let seq = 0; // chống kết quả cũ ghi đè kết quả mới

function eventRow(e) {
  const li = el("li");
  const row = el("button", "hrow");
  row.type = "button";
  row.setAttribute("aria-expanded", "false");
  const lv = el("span", `lv ${e.level}`);
  lv.title = e.level;
  row.append(el("span", "t", stamp(e.time)), lv, el("span", "n", e.node ?? "·"),
    el("span", `k k-${e.kind}`, e.kind), el("span", "x", e.text));
  const extra = Object.fromEntries(Object.entries(e).filter(([k]) => !BASE.has(k)));
  const pre = el("pre", "json", Object.keys(extra).length ? JSON.stringify(extra, null, 2) : "Không có dữ liệu thêm.");
  pre.hidden = true;
  row.addEventListener("click", () => {
    pre.hidden = !pre.hidden;
    row.setAttribute("aria-expanded", String(!pre.hidden));
  });
  li.append(row, pre);
  return li;
}

async function loadEvents(append) {
  const my = ++seq;
  if (!append) offset = 0;
  const p = eventParams();
  p.set("limit", PAGE);
  p.set("offset", offset);
  try {
    const { data, total: tot } = await api("/api/events?" + p);
    if (my !== seq) return;
    total = Number.isNaN(tot) ? offset + data.length : tot;
    const rows = data.map(eventRow);
    if (!append) $("hist").replaceChildren();
    if (!rows.length && !append) $("hist").append(el("li", "empty", "Không có sự kiện nào khớp bộ lọc."));
    $("hist").append(...rows);
    offset += data.length;
    $("count").textContent = `${offset} / ${total}`;
    $("more").hidden = offset >= total;
  } catch (e) {
    if (my === seq) toast(`Không tải được nhật ký: ${e.message}`, true);
  }
}
$("more").addEventListener("click", () => loadEvents(true));

// --- biểu đồ ---------------------------------------------------------------------------------

const charts = new Map(); // khóa "node:đại lượng" -> biểu đồ
let chartSeq = 0;

function chartSkeleton(nodes, showHum) {
  const key = nodes.join(",") + showHum;
  if ($("charts").dataset.key === key) return;
  for (const slot of charts.values()) slot.chart?.destroy();
  charts.clear();
  $("charts").dataset.key = key;
  $("charts").replaceChildren(...nodes.map((n) => {
    const col = el("div", "chart-col");
    col.append(el("h3", "chart-h", n.toUpperCase()));
    for (const [k, title, unit] of SERIES) {
      if (k === "hum" && !showHum) continue;
      const box = el("div", "chart-box");
      box.append(el("span", "label", `${title} (${unit})`));
      const host = el("div", "chart-host");
      box.append(host);
      col.append(box);
      charts.set(`${n}:${k}`, { host, unit, title, chart: null });
    }
    return col;
  }));
}

async function loadCharts() {
  const my = ++chartSeq;
  const nodes = st.node ? [st.node] : NODES;
  chartSkeleton(nodes, $("c-hum").checked);
  const w = window_();
  const p = new URLSearchParams({ from: w.from, to: w.to, points: 400 });
  if (st.node) p.set("node", st.node);
  try {
    const { data } = await api("/api/telemetry?" + p);
    if (my !== chartSeq) return;
    for (const n of nodes) {
      const ns = data.nodes[n] ?? { t: [], temp: { min: [], avg: [], max: [] }, gas: { min: [], avg: [], max: [] }, hum: { min: [], avg: [], max: [] } };
      const runs = data.runs.filter((r) => r.node === n);
      for (const [k, , , limKey] of SERIES) {
        const slot = charts.get(`${n}:${k}`);
        if (!slot) continue;
        const series = { t: ns.t, ...ns[k] };
        const opts = { from: data.from, to: data.to, limit: limKey ? data.limits?.[limKey] : null, runs, unit: slot.unit, title: `${n} ${slot.title}` };
        if (slot.chart) slot.chart.update(series, opts); else slot.chart = lineChart(slot.host, series, opts);
      }
    }
  } catch (e) {
    if (my === chartSeq) toast(`Không tải được biểu đồ: ${e.message}`, true);
  }
}
$("c-hum").addEventListener("change", loadCharts);

// --- mạng Thread -----------------------------------------------------------------------------

const TSERIES = [["ping", "Ping (ms)", "ms"], ["loss", "Mất gói (%)", "%"], ["coap", "CoAP GET /status (ms)", "ms"]];
const tcharts = new Map();
let threadSeq = 0;
const threadHours = () => Math.max(0.05, (Date.now() / 1000 - window_().from) / 3600);

function threadSkeleton(nodes) {
  const key = nodes.join(",");
  if ($("thread-charts").dataset.key === key) return;
  for (const slot of tcharts.values()) slot.chart?.destroy();
  tcharts.clear();
  $("thread-charts").dataset.key = key;
  $("thread-charts").replaceChildren(...nodes.map((n) => {
    const col = el("div", "chart-col");
    col.append(el("h3", "chart-h", n.toUpperCase()));
    for (const [k, title, unit] of TSERIES) {
      const box = el("div", "chart-box");
      box.append(el("span", "label", title));
      const host = el("div", "chart-host");
      box.append(host);
      col.append(box);
      tcharts.set(`${n}:${k}`, { host, unit, title, chart: null });
    }
    return col;
  }));
}

async function loadThread() {
  const my = ++threadSeq;
  const p = new URLSearchParams({ hours: threadHours().toFixed(3), points: 300 });
  if (st.node) p.set("node", st.node);
  try {
    const { data } = await api("/api/thread/history?" + p);
    if (my !== threadSeq) return;
    const nodes = st.node ? [st.node] : [...new Set([...NODES, ...Object.keys(data.nodes)])].sort();
    threadSkeleton(nodes);
    for (const n of nodes) {
      const d = data.nodes[n] ?? { t: [], min: [], avg: [], max: [], loss: [], coap: [] };
      const sets = {
        ping: { t: d.t, min: d.min, avg: d.avg, max: d.max },
        loss: { t: d.t, min: d.loss, avg: d.loss, max: d.loss },
        coap: { t: d.t, min: d.coap, avg: d.coap, max: d.coap },
      };
      for (const [k] of TSERIES) {
        const slot = tcharts.get(`${n}:${k}`);
        const opts = { from: data.from, to: data.to, limit: null, runs: [], unit: slot.unit, title: `${n} ${slot.title}`, height: 130 };
        if (slot.chart) slot.chart.update(sets[k], opts); else slot.chart = lineChart(slot.host, sets[k], opts);
      }
    }
  } catch (e) {
    if (my === threadSeq) toast(`Không tải được số đo Thread: ${e.message}`, true);
  }
}

// --- CSV -------------------------------------------------------------------------------------

$("csv-ev").addEventListener("click", () => { location.href = "/api/events.csv?" + eventParams(); });
$("csv-thread").addEventListener("click", () => {
  const p = new URLSearchParams({ hours: threadHours().toFixed(3) });
  if (st.node) p.set("node", st.node);
  location.href = "/api/thread.csv?" + p;
});
$("csv-tel").addEventListener("click", () => {
  const w = window_();
  const p = new URLSearchParams({ from: w.from, to: w.to });
  if (st.node) p.set("node", st.node);
  location.href = "/api/telemetry.csv?" + p;
});

// --- nạp lại ---------------------------------------------------------------------------------

function reload() {
  syncUrl();
  loadEvents(false);
  loadCharts();
  loadThread();
}
reload();
// khoảng trượt thì biểu đồ tự làm mới; bảng không tự đổi để khỏi làm mất dòng đang mở
setInterval(() => { if (st.range !== "custom" && !document.hidden) { loadCharts(); loadThread(); } }, 15000);
