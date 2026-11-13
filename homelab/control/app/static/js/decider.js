// Trang Bộ quyết định: hỏi /api/decider/status mỗi 2 giây, đổi bộ quyết định, thử và tắt worker.
import { clock, el, mmss, num0, num1 } from "./format.js";

const $ = (id) => document.getElementById(id);
const POLL_MS = 2000;

const STATE_LABEL = {
  disabled: ["Chưa bật (thiếu NT532_WORKER_TOKEN)", ""],
  disconnected: ["Chưa kết nối", ""],
  connecting: ["Đang nối", "warn"],
  ready: ["Sẵn sàng", "ok"],
  busy: ["Bận", "ok"],
};

let status = null;
let fetchedAt = 0;
let formDirty = false;
let linkDown = false;

function toast(text, bad = false) {
  const t = el("div", "toast" + (bad ? " bad" : ""), text);
  $("toasts").append(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3500);
}

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  if (r.status === 401) { location.href = "/login"; throw new Error("401"); }
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok && j.ok !== false, status: r.status, j };
}

// --- vẽ ------------------------------------------------------------------------------------

// Tên bộ quyết định trên Pi -> radio tương ứng ("remote(<url>)" là hub nếu url trùng NT532_PUBLIC_DECIDE_URL).
function kindOfPi(name, hubUrl) {
  if (!name) return null;
  if (name.startsWith("remote")) return hubUrl && name.includes(hubUrl) ? "remote-hub" : "remote-url";
  return ["rules", "hybrid", "jev"].find((k) => name.startsWith(k)) ?? null;
}

function renderPi(pi, hub) {
  $("pi-dot").className = "dot " + (pi.online ? "ok" : "bad");
  $("pi-label").textContent = pi.online ? "Pi trực tuyến" : "Pi mất kết nối";
  $("cur-name").textContent = pi.decider ?? "--";
  $("cur-err").textContent = pi.decider_error ? `Lỗi: ${pi.decider_error}` : "";
  const d = pi.last_decision;
  $("cur-last").textContent = d ? `${clock(d.time)} · ${d.text}` : "Chưa có quyết định nào.";

  const hint = $("hub-hint");
  const label = (STATE_LABEL[hub.state] ?? [hub.state])[0].toLowerCase();
  hint.textContent = hub.public_url
    ? `Pi sẽ gọi ${hub.public_url}/decide · worker: ${label}`
    : "Chưa đặt NT532_PUBLIC_DECIDE_URL nên chưa chọn được.";
  hint.classList.toggle("bad", !hub.public_url);
  document.querySelector('input[value="remote-hub"]').disabled = !hub.public_url;

  if (!formDirty) {
    const k = kindOfPi(pi.decider, hub.public_url);
    const radio = k && document.querySelector(`input[name=kind][value="${k}"]`);
    if (radio && !radio.checked) radio.checked = true;
    syncFields();
  }
}

function renderWorker(hub) {
  const [label, tone] = STATE_LABEL[hub.state] ?? [hub.state, ""];
  $("w-state").textContent = label;
  $("w-dot").className = "dot " + (tone === "ok" ? "ok" : tone === "warn" ? "warn" : "");
  const w = hub.worker;
  $("w-gpu").textContent = w ? (w.gpu ?? (w.device === "cpu" ? "Không có GPU (CPU)" : w.device ?? "--")) : "--";
  $("w-run").textContent = w ? [w.decider, w.run].filter(Boolean).join(" / ") || "--" : "--";
  const up = w ? hub.now - w.connected_at + (performance.now() - fetchedAt) / 1000 : null;
  $("w-up").textContent = up == null ? "--" : mmss(up);
  $("w-req").textContent = w ? `${num0(w.done)} xong · ${num0(w.errors)} lỗi · ${num0(hub.stats.timeouts)} quá hạn` : "--";
  $("w-lat").textContent = w?.last_latency_ms != null ? `${num1(w.last_latency_ms)} ms` : "--";
  $("w-rt").textContent = hub.last_roundtrip_ms != null ? `${num1(hub.last_roundtrip_ms)} ms` : "--";
  $("w-err").textContent = w?.last_error ?? hub.last_error ?? "--";
  $("btn-off").disabled = !w;
  $("btn-test").disabled = !(w && w.ready);

  const items = hub.events.length
    ? hub.events.map((e) => {
        const li = el("li");
        li.append(el("span", "t", clock(e.time)), el("span", "lv " + (e.level === "warn" || e.level === "error" ? e.level : "")),
          el("span", "k", "hub"), el("span", "x", e.text));
        return li;
      })
    : [el("li", "empty", "Chưa có sự kiện nào.")];
  $("hub-events").replaceChildren(...items);
}

function renderBar() {
  const bar = $("offline-bar");
  bar.hidden = !linkDown;
  if (linkDown) bar.textContent = "Mất kết nối với máy chủ điều khiển, đang thử lại";
}

function render() {
  if (!status) return;
  renderPi(status.pi, status.hub);
  renderWorker(status.hub);
}

async function poll() {
  try {
    const { ok, j } = await api("/api/decider/status");
    if (ok) { status = j; fetchedAt = performance.now(); linkDown = false; render(); }
  } catch { linkDown = true; }
  renderBar();
}

// --- đổi bộ quyết định --------------------------------------------------------------------

function selectedKind() {
  return document.querySelector("input[name=kind]:checked")?.value ?? null;
}

function syncFields() {
  const k = selectedKind();
  document.querySelectorAll(".dec-fields").forEach((f) => {
    const on = f.dataset.for === k;
    f.hidden = !on;
    f.querySelectorAll("input").forEach((i) => { i.disabled = !on; });
  });
}

function buildBody() {
  const kind = selectedKind();
  if (!kind) throw new Error("Chọn một bộ quyết định trước");
  if (kind === "hybrid") {
    const tau = Number($("f-tau").value);
    if (!Number.isFinite(tau) || tau < 0 || tau > 1) throw new Error("tau phải nằm trong khoảng 0 đến 1");
    return { kind, run: $("f-run-h").value.trim() || "jev2", tau, stages: $("f-stages").value.trim() || "verify" };
  }
  if (kind === "jev") return { kind, run: $("f-run-j").value.trim() || "jev2" };
  if (kind === "remote-url") {
    const url = $("f-url").value.trim();
    if (!/^https?:\/\/[^\s/]+/.test(url)) throw new Error("URL phải có dạng http://host:cổng");
    return { kind, url };
  }
  return { kind };
}

function confirmSwitch() {
  const dlg = $("confirm-dialog");
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.returnValue = "cancel";
    dlg.showModal();
  });
}

document.querySelectorAll("input[name=kind]").forEach((r) => r.addEventListener("change", () => { formDirty = true; syncFields(); }));
document.querySelectorAll(".dec-fields input").forEach((i) => i.addEventListener("input", () => { formDirty = true; }));

$("switch-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const msg = $("apply-msg");
  msg.textContent = "";
  msg.className = "dec-msg";
  let body;
  try { body = buildBody(); } catch (e) { msg.textContent = e.message; msg.className = "dec-msg bad"; return; }
  if (status?.pi.run_active && !(await confirmSwitch())) return;
  $("apply").disabled = true;
  try {
    const { ok, j } = await api("/api/decider/switch", body);
    if (ok) { toast("Đã gửi lệnh đổi bộ quyết định"); formDirty = false; poll(); }
    else { msg.textContent = j.error ?? "Không đổi được"; msg.className = "dec-msg bad"; toast(`Không đổi được: ${j.error ?? "lỗi"}`, true); }
  } catch { /* đã chuyển về trang đăng nhập */ } finally { $("apply").disabled = false; }
});

// --- worker -------------------------------------------------------------------------------

$("btn-test").addEventListener("click", async () => {
  const out = $("test-out");
  out.className = "dec-test";
  out.textContent = "Đang thử...";
  $("btn-test").disabled = true;
  try {
    const { ok, j } = await api("/api/decider/test", {});
    if (ok) {
      const act = Object.entries(j.action ?? {}).map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join(", ") || "(không có đáp án)";
      out.textContent = `Khứ hồi ${num1(j.latency_ms)} ms` + (j.model_ms != null ? ` (mô hình ${num1(j.model_ms)} ms)` : "") + ` · ${act}`
        + (j.error ? ` · worker báo: ${j.error}` : "");
      out.classList.add(j.error ? "warn" : "ok");
    } else {
      out.textContent = `Thử thất bại: ${j.error ?? "không rõ"}`;
      out.classList.add("bad");
    }
  } catch { /* chuyển trang */ }
  poll();
});

$("btn-off").addEventListener("click", async () => {
  if (!window.confirm("Tắt worker trên Colab? Muốn bật lại phải chạy lại notebook hoặc bootstrap.sh.")) return;
  try {
    const { ok, j } = await api("/api/decider/worker/shutdown", {});
    toast(ok ? "Đã gửi lệnh tắt worker" : `Không tắt được: ${j.error ?? "lỗi"}`, !ok);
  } catch { /* chuyển trang */ }
  poll();
});

// --- hướng dẫn ----------------------------------------------------------------------------

$("ws-url").value = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/worker/ws`;
$("ws-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("ws-url").value);
    toast("Đã sao chép URL");
  } catch {
    $("ws-url").select();
    toast("Chưa sao chép tự động được, hãy chép thủ công", true);
  }
});

fetch("/api/me").then((r) => (r.ok ? r.json() : null)).then((j) => { if (j) $("user").textContent = j.user; }).catch(() => {});
syncFields();
poll();
setInterval(poll, POLL_MS);
setInterval(() => { if (status) renderWorker(status.hub); }, 1000);
