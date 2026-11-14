// Trang Tổng quan: SSE từ /api/stream, lệnh qua /api/cmd, hình qua /camera/stream.mjpg.
import { el } from "./js/format.js";
import { createLog } from "./js/log.js";
import { createNodeCards } from "./js/nodes.js";
import { createPhaseStrip, runInfo } from "./js/phase.js";
import { createThreadPanel } from "./js/thread.js";

const $ = (id) => document.getElementById(id);
const NODES = ["s1", "s2"];

const renderPhase = createPhaseStrip($("phase"));
const renderNodes = createNodeCards($("nodes"), NODES);
const log = createLog($("log"), $("filters"));
const renderThread = createThreadPanel($("thread-panel"));

let ov = null; // overview mới nhất
let receivedAt = performance.now();
let linkDown = false; // SSE tới máy chủ điều khiển đang đứt

// --- vẽ ------------------------------------------------------------------------------------

function render() {
  const state = ov?.state;
  const online = !!ov?.pi_online;
  renderPhase(state?.orch);
  renderNodes(state, online);
  renderMeta();
  renderTop(online);
  renderChips(state);
  renderDecider(state);
  renderCmds(state);
  ensureCamera(online || !!ov?.camera);
  renderCalib(ov?.calib);
  renderThread(ov?.thread);
}

const CALIB_STATE = { idle: "Nghỉ", capturing: "Đang chụp", computing: "Đang tính", done: "Xong", error: "Lỗi" };
function renderCalib(c) {
  const st = c?.status;
  $("calib-panel").hidden = !(ov?.camera || st);
  if (!c) return;
  $("calib-state").textContent = st ? (CALIB_STATE[st.state] ?? st.state) : "Chưa có báo cáo từ Pi";
  const d = [];
  if (st) {
    if (st.saved != null) d.push(`đã lưu ${st.saved}/${st.target ?? c.target}`);
    if (st.corners != null && st.state === "capturing") d.push(`thấy ${st.corners} góc`);
    if (c.status_age != null && c.status_age > 10) d.push(`(báo cáo cũ ${Math.round(c.status_age)} s)`);
  }
  $("calib-detail").textContent = d.join(" · ");
  const res = $("calib-result");
  res.className = "calib-result" + (st?.state === "error" ? " bad" : "");
  if (st?.state === "done" && st.rms != null) res.textContent = `RMS ${Number(st.rms).toFixed(3)} px · ${st.file ?? ""}`;
  else if (st?.state === "error") res.textContent = st.error ?? "Lỗi không rõ";
  else res.textContent = "";
}

async function sendCalib(action) {
  try {
    const r = await fetch("/api/calib", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
    if (r.status === 401) { location.href = "/login"; return; }
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok !== false) toast(`Đã gửi: hiệu chuẩn ${action}`);
    else toast(`Hiệu chuẩn lỗi: ${j.error ?? r.status}`, true);
  } catch {
    toast("Chưa gửi được lệnh hiệu chuẩn", true);
  }
}
document.querySelectorAll("[data-calib]").forEach((b) => b.addEventListener("click", () => sendCalib(b.dataset.calib)));

function renderTop(online) {
  $("pi-dot").className = "dot " + (online ? "ok" : "bad");
  $("pi-label").textContent = online ? "Pi trực tuyến" : "Pi mất kết nối";
}

// chạy mỗi giây: thời gian lượt và thanh mất kết nối
function renderMeta() {
  const info = runInfo(ov?.state?.orch, ov?.state?.time ?? 0, receivedAt);
  $("run-label").textContent = info.label;
  $("run-time").textContent = info.time;
  const bar = $("offline-bar");
  let msg = "";
  if (linkDown) msg = "Mất kết nối với máy chủ điều khiển, đang thử lại";
  else if (ov && !ov.pi_online) {
    msg = ov.offline_s == null ? "Chưa kết nối được với Pi"
      : `Mất kết nối với Pi từ ${Math.floor(ov.offline_s + (performance.now() - receivedAt) / 1000)} giây`;
  }
  bar.hidden = !msg;
  if (msg && bar.textContent !== msg) bar.textContent = msg;
}

let chipSig = "";
function renderChips(state) {
  const items = [];
  if (state?.sim) items.push(["chip sim", "Sa bàn ảo"]);
  if (state?.detector) items.push(["chip", `detector: ${state.detector}`]);
  if (state?.camera_error) items.push(["chip bad", `camera: ${state.camera_error}`]);
  const sig = JSON.stringify(items);
  if (sig === chipSig) return;
  chipSig = sig;
  $("chips").replaceChildren(...items.map(([c, t]) => el("span", c, t)));
}

function renderDecider(state) {
  const o = state?.orch;
  $("dec-name").textContent = o?.decider ?? "--";
  $("dec-err").textContent = o?.decider_error ? `Lỗi: ${o.decider_error}` : "";
  const last = log.events.find((e) => e.kind === "decision");
  $("dec-last").textContent = last ? `${new Date(last.time * 1000).toLocaleTimeString("vi-VN", { hour12: false })} · ${last.text}` : "Chưa có quyết định nào.";
  const sel = $("dec-kind");
  if (o?.decider && document.activeElement !== sel) {
    const kind = ["rules", "hybrid", "jev", "remote"].find((k) => o.decider.startsWith(k));
    if (kind && sel.dataset.synced !== o.decider) { sel.value = kind; sel.dataset.synced = o.decider; syncTau(); }
  }
}

function syncTau() {
  $("dec-tau").hidden = !["hybrid", "jev"].includes($("dec-kind").value);
  const remote = $("dec-kind").value === "remote";
  $("dec-url").hidden = !remote;
  $("dec-timeout").hidden = !remote;
}

function renderCmds(state) {
  const on = state?.orch?.enabled !== false;
  const sw = $("auto-switch");
  sw.setAttribute("aria-checked", String(on));
  $("auto-label").textContent = on ? "Tự động: đang bật" : "Tự động: đang tắt";
  $("sim-box").hidden = !state?.sim;
}

// --- camera --------------------------------------------------------------------------------

let camOn = false;
let camRetry = 0;
function ensureCamera(online) {
  const img = $("cam");
  if (online && !camOn && !camRetry) {
    camOn = true;
    img.src = `/camera/stream.mjpg?t=${Date.now()}`;
  } else if (!online && camOn) {
    camOn = false;
    img.removeAttribute("src");
    showCam(false, "Đang chờ kết nối với Pi hoặc hình từ camera");
  }
}
function showCam(ok, hint) {
  $("cam").hidden = !ok;
  $("cam-none").hidden = ok;
  if (hint) $("cam-hint").textContent = hint;
}
$("cam").addEventListener("load", () => showCam(true));
$("cam").addEventListener("error", () => {
  if (!camOn) return;
  camOn = false;
  $("cam").removeAttribute("src");
  showCam(false, "Camera chưa có khung hình, đang thử lại");
  camRetry = setTimeout(() => { camRetry = 0; ensureCamera(!!ov?.pi_online || !!ov?.camera); }, 5000);
});

// --- lệnh ----------------------------------------------------------------------------------

function toast(text, bad = false) {
  const t = el("div", "toast" + (bad ? " bad" : ""), text);
  $("toasts").append(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3500);
}

async function send(body, label) {
  try {
    const r = await fetch("/api/cmd", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401) { location.href = "/login"; return; }
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok !== false) toast(`Đã gửi: ${label}`);
    else toast(`Lệnh "${label}" lỗi: ${j.error ?? r.status}`, true);
  } catch {
    toast(`Lệnh "${label}" chưa gửi được`, true);
  }
}

document.querySelectorAll("[data-stop]").forEach((b) => b.addEventListener("click", () => $("stop-dialog").showModal()));
$("stop-dialog").addEventListener("close", (e) => {
  if (e.target.returnValue === "ok") send({ cmd: "stop" }, "Dừng khẩn cấp");
  e.target.returnValue = "";
});
$("auto-switch").addEventListener("click", () => {
  const next = $("auto-switch").getAttribute("aria-checked") !== "true";
  send({ cmd: "enable", on: next }, next ? "Bật tự động" : "Tắt tự động");
});
$("recommission").addEventListener("click", () => send({ cmd: "recommission" }, "Commissioning lại"));
$("check-camera").addEventListener("click", () => send({ cmd: "check_camera" }, "Kiểm tra camera"));
$("dec-kind").addEventListener("change", syncTau);
$("dec-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const kind = $("dec-kind").value;
  const body = { cmd: "decider", kind };
  if (["hybrid", "jev"].includes(kind)) body.tau = Number($("dec-tau").value);
  if (kind === "remote") {
    const url = $("dec-url").value.trim();
    if (url) body.url = url; // trống: Pi dùng URL đang cấu hình
    const t = Number($("dec-timeout").value);
    if (url && t) body.timeout = t;
  }
  send(body, `Đổi bộ quyết định: ${kind}`);
});
$("selfcal").addEventListener("click", () => send({ cmd: "calibrate", node: "all" }, "Tự hiệu chuẩn vòi"));
$("cam-jitter").addEventListener("click", () => send({ cmd: "camera_jitter" }, "Xê dịch camera"));
document.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () => {
  placeKind = b.dataset.kind;
  document.querySelectorAll("[data-kind]").forEach((x) => x.classList.toggle("on", x === b));
}));
// bấm lên hình khi sa bàn ảo: gửi pixel (theo ảnh gốc), Pi đổi ra tọa độ bảng hoặc nền
let placeKind = "fire";
$("cam").addEventListener("click", (e) => {
  if (!ov?.state?.sim) return;
  const img = e.target, r = img.getBoundingClientRect();
  const nw = img.naturalWidth, nh = img.naturalHeight;
  if (!nw || !nh) return;
  const s = Math.min(r.width / nw, r.height / nh), ox = (r.width - nw * s) / 2, oy = (r.height - nh * s) / 2;
  const u = (e.clientX - r.left - ox) / s, v = (e.clientY - r.top - oy) / s;
  if (u < 0 || v < 0 || u > nw || v > nh) return;
  send({ cmd: placeKind, u: Math.round(u), v: Math.round(v) }, `Đặt ${placeKind} lên hình`);
});
document.querySelectorAll("[data-sim]").forEach((b) => b.addEventListener("click", () => send(JSON.parse(b.dataset.sim), b.textContent)));

// --- dữ liệu -------------------------------------------------------------------------------

function apply(msg) {
  ov = msg.overview;
  receivedAt = performance.now();
  if (msg.init) {
    ov.last_events && log.add(ov.last_events);
    loadHistory();
  }
  if (msg.events?.length) log.add(msg.events);
  render();
}

async function loadHistory() {
  try {
    const r = await fetch("/api/events?limit=200");
    if (r.ok) { log.add(await r.json()); render(); }
  } catch { /* SSE sẽ tiếp tục đẩy sự kiện mới */ }
}

async function checkAuth() {
  try {
    const r = await fetch("/api/me");
    if (r.status === 401) location.href = "/login";
  } catch { /* máy chủ điều khiển đang tắt */ }
}

let es = null;
function connect() {
  es?.close();
  es = new EventSource("/api/stream");
  es.addEventListener("overview", (ev) => {
    linkDown = false;
    // giữ trạng thái cũ nếu tin nhắn lỗi định dạng
    try { apply(JSON.parse(ev.data)); } catch (err) { console.error(err); }
  });
  es.onopen = () => { linkDown = false; };
  es.onerror = () => {
    linkDown = true;
    renderMeta();
    if (es.readyState === EventSource.CLOSED) { // không tự nối lại (vd 401): kiểm tra phiên rồi nối tay
      checkAuth();
      setTimeout(connect, 3000);
    }
  };
}

fetch("/api/me").then((r) => (r.ok ? r.json() : null)).then((j) => { if (j) $("user").textContent = j.user; }).catch(() => {});
log.render();
render();
connect();
setInterval(renderMeta, 1000);
