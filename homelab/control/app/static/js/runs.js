// Trang Lượt: ô tổng hợp theo kết quả và bảng các lượt xử lý, mở ra xem JSON gốc.
import { el, mmss, num1 } from "./format.js";
import { api, fromLocalInput, initShell, stamp, toLocalInput, toast } from "./shell.js";

const $ = (id) => document.getElementById(id);

// khóa -> [nhãn, lớp màu]
const OUTCOMES = {
  extinguished: ["Dập được", "ok"],
  ignored: ["Bỏ qua", "muted"],
  alarm_only: ["Chỉ báo động", "ember"],
  human: ["Gọi người", "warn"],
  fault: ["Lỗi", "ember"],
  stopped: ["Dừng", "muted"],
  running: ["Đang chạy", "water"],
};
const RANGES = [["24h", "24 giờ", 86400], ["7d", "7 ngày", 604800], ["30d", "30 ngày", 2592000], ["all", "Tất cả", null]];
const TILES = ["total", "extinguished", "ignored", "alarm_only", "human", "fault"];

initShell();

const st = { range: "24h", from: null, to: null, outcome: "" };
const qs = new URLSearchParams(location.search);
if (RANGES.some((r) => r[0] === qs.get("range"))) st.range = qs.get("range");
if (qs.get("from")) { st.range = "custom"; st.from = Number(qs.get("from")); st.to = qs.get("to") ? Number(qs.get("to")) : null; }
if (OUTCOMES[qs.get("outcome")]) st.outcome = qs.get("outcome");

function syncUrl() {
  const p = new URLSearchParams();
  if (st.outcome) p.set("outcome", st.outcome);
  if (st.range === "custom") { p.set("from", Math.round(st.from)); if (st.to) p.set("to", Math.round(st.to)); } else p.set("range", st.range);
  history.replaceState(null, "", "?" + p);
}

function params() {
  const p = new URLSearchParams({ limit: 500 });
  if (st.range === "custom") {
    p.set("from", st.from);
    if (st.to) p.set("to", st.to);
  } else {
    const sec = RANGES.find((r) => r[0] === st.range)[2];
    if (sec) p.set("from", Date.now() / 1000 - sec);
  }
  if (st.outcome) p.set("outcome", st.outcome);
  return p;
}

const chips = [...RANGES.map((r) => [r[0], r[1]]), ["custom", "Tùy chọn"]].map(([key, label]) => {
  const b = el("button", "fchip", label);
  b.type = "button";
  b.addEventListener("click", () => {
    if (key === "custom") { st.from ??= Date.now() / 1000 - 86400; st.range = "custom"; } else st.range = key;
    paint();
    if (key !== "custom") load();
  });
  $("f-range").append(b);
  return [key, b];
});
function paint() {
  chips.forEach(([k, b]) => b.setAttribute("aria-pressed", String(st.range === k)));
  $("custom-range").hidden = st.range !== "custom";
  if (st.range === "custom") {
    $("f-from").value = st.from ? toLocalInput(st.from) : "";
    $("f-to").value = st.to ? toLocalInput(st.to) : "";
  }
}
$("f-outcome").value = st.outcome;
$("f-outcome").addEventListener("change", () => { st.outcome = $("f-outcome").value; load(); });
$("f-apply").addEventListener("click", () => {
  const f = fromLocalInput($("f-from").value);
  const t = fromLocalInput($("f-to").value);
  if (f == null) return toast("Chọn thời điểm bắt đầu", true);
  if (t != null && t <= f) return toast("Thời điểm kết thúc phải sau thời điểm bắt đầu", true);
  st.from = f; st.to = t;
  load();
});
paint();

// --- vẽ --------------------------------------------------------------------------------------

function renderTiles(sum) {
  const tiles = TILES.map((k) => [k, k === "total" ? "Số lượt" : OUTCOMES[k][0], k === "total" ? "" : OUTCOMES[k][1]]);
  if (sum.stopped) tiles.push(["stopped", OUTCOMES.stopped[0], "muted"]);
  if (sum.running) tiles.push(["running", OUTCOMES.running[0], "water"]);
  $("tiles").replaceChildren(...tiles.map(([k, label, cls]) => {
    const t = el("div", `tile ${cls}`);
    t.append(el("span", "label", label), el("span", "tile-n", String(sum[k] ?? 0)));
    return t;
  }));
}

const m2 = (v) => (v == null ? "--" : v.toLocaleString("vi-VN", { maximumFractionDigits: 2 }));

function targetText(r) {
  if (r.x == null) return r.target_id != null ? `#${r.target_id}` : "--";
  const surf = r.surface === "floor" ? "sàn" : r.surface === "wall" ? "tường" : r.surface ?? "";
  return `#${r.target_id ?? "?"} (${m2(r.x)}; ${m2(r.y)}; ${m2(r.z)}) m, ${surf}`;
}

function runItem(r) {
  const [label, cls] = OUTCOMES[r.outcome ?? "running"] ?? [r.outcome, "muted"];
  const li = el("li");
  const row = el("button", "rrow");
  row.type = "button";
  row.setAttribute("aria-expanded", "false");
  const dur = r.ended != null ? mmss(r.ended - r.started) : "--";
  const cell = (cls_, text, lab) => {
    const c = el("span", cls_);
    if (lab) c.append(el("span", "cl", lab));
    c.append(document.createTextNode(text));
    return c;
  };
  const start = el("span", "c-start");
  start.append(el("span", "mono", stamp(r.started)), el("span", "dim mono", ` · ${dur}`));
  row.append(
    start,
    cell("c-node mono", r.node ?? "--", "Node"),
    cell("c-target mono", targetText(r), "Mục tiêu"),
    cell("c-noz mono", r.nozzle ?? "--", "Vòi"),
    cell("c-dec", r.decider ?? "--", "Quyết định"),
    el("span", `c-out out-${cls}`, label),
    cell("c-att mono", r.attempts != null ? `${r.attempts} / ${r.shots ?? 0}` : "--", "Lần/phun"),
    cell("c-miss mono", r.miss_cm != null ? `${num1(r.miss_cm)} cm` : "--", "Sai số"),
  );
  const detail = el("div", "detail");
  detail.hidden = true;
  const w0 = r.started - 5;
  const w1 = (r.ended ?? Date.now() / 1000) + 5;
  const a = el("a", "btn linkbtn2", "Xem nhật ký của lượt");
  a.href = `/history?from=${Math.floor(w0)}&to=${Math.ceil(w1)}`;
  detail.append(a, el("pre", "json", JSON.stringify(r.raw, null, 2)));
  row.addEventListener("click", () => {
    detail.hidden = !detail.hidden;
    row.setAttribute("aria-expanded", String(!detail.hidden));
  });
  li.append(row, detail);
  return li;
}

let seq = 0;
async function load() {
  syncUrl();
  const my = ++seq;
  try {
    const { data } = await api("/api/runs?" + params());
    if (my !== seq) return;
    renderTiles(data.summary);
    $("count").textContent = `${data.runs.length} / ${data.summary.total}`;
    $("runs").replaceChildren(...(data.runs.length ? data.runs.map(runItem) : [el("li", "empty", "Chưa có lượt nào trong khoảng này.")]));
  } catch (e) {
    if (my === seq) toast(`Không tải được các lượt: ${e.message}`, true);
  }
}
load();
setInterval(() => { if (!document.hidden && !document.querySelector(".rrow[aria-expanded=true]")) load(); }, 10000);
