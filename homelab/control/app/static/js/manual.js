// Trang Điều khiển tay: pan-tilt và laser từng node qua /api/cmd {cmd:"manual"}; hình qua /camera/stream.mjpg.
import { el } from "./format.js";
import { api, initShell, stamp, toast } from "./shell.js";

const $ = (id) => document.getElementById(id);
const NODES = ["s1", "s2"];
const PAN = [500, 2500];
const TILT = [600, 2500];
const clamp = (v, [lo, hi]) => Math.min(hi, Math.max(lo, Math.round(v)));

initShell();

async function postCmd(body) {
  try {
    const r = await fetch("/api/cmd", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401) { location.href = "/login"; return { ok: false, error: "Cần đăng nhập" }; }
    const j = await r.json().catch(() => ({}));
    return r.ok ? j : { ok: false, error: j.error ?? `HTTP ${r.status}` };
  } catch {
    return { ok: false, error: "Không gửi được lệnh tới máy chủ" };
  }
}
const manual = (body) => postCmd({ cmd: "manual", ...body });

// --- thẻ node ---------------------------------------------------------------------------------

function makeCard(node) {
  const s = { pan: 1500, tilt: 1500, inited: false, laser: false, busy: false, pending: false };
  const root = el("section", "panel mcard");
  root.tabIndex = 0;
  root.setAttribute("aria-label", `Điều khiển tay ${node}`);
  const btn = (label, cls = "") => {
    const b = el("button", `btn ${cls}`.trim(), label);
    b.type = "button";
    return b;
  };

  const arm = btn("ARM"), home = btn("HOME"), stop = btn("STOP", "estop"), laser = btn("Laser: --");
  const readout = el("div", "readout", "Chưa có số đo");
  const err = el("div", "err", "");

  function axis(name, range) {
    const slider = Object.assign(document.createElement("input"), { type: "range", min: range[0], max: range[1], step: 1, value: 1500 });
    const num = Object.assign(document.createElement("input"), { type: "number", min: range[0], max: range[1], step: 1, value: 1500 });
    slider.setAttribute("aria-label", `${name} µs`);
    num.setAttribute("aria-label", `${name} µs, nhập số`);
    const row = el("div", "row");
    const steps = [-50, -10, 10, 50].map((d) => {
      const b = btn(d > 0 ? `+${d}` : `−${-d}`, "sm");
      b.addEventListener("click", () => nudge(name, d));
      return b;
    });
    row.append(num, ...steps);
    const wrap = el("div", "axis");
    wrap.append(el("span", "label", `${name === "pan" ? "Pan" : "Tilt"} (µs)`), slider, row);
    const set = (v) => { s[name] = clamp(v, range); slider.value = num.value = s[name]; };
    slider.addEventListener("input", () => set(slider.value));
    slider.addEventListener("change", () => move());
    num.addEventListener("change", () => { set(num.value); move(); });
    return { wrap, set };
  }
  const ax = { pan: axis("pan", PAN), tilt: axis("tilt", TILT) };
  const send = btn("Gửi", "water");

  const noteIn = Object.assign(document.createElement("input"), { type: "text", maxLength: 120, placeholder: "Ghi chú, ví dụ: bị chân trụ chắn" });
  noteIn.setAttribute("aria-label", "Ghi chú điểm");
  const mark = btn("Ghi điểm");
  const marks = el("ul", "marks");
  const keys = el("p", "keys", "Chọn thẻ rồi: ←/→ pan, ↑/↓ tilt (10 µs, Shift ×5), Space bật/tắt laser, Esc STOP.");

  // --- cần điều khiển ảo: điều khiển theo tốc độ, 150 ms một nhịp --------------------------------
  const TICK_MS = 150, DEAD = 0.1, PAD = 160, KNOB = 56;
  const joy = { x: 0, y: 0, active: false, id: null, timer: 0, last: 0, fp: 0, ft: 0, sent: null };
  const pad = el("div", "joy-pad");
  pad.setAttribute("role", "application");
  pad.setAttribute("aria-label", `Cần điều khiển ${node}`);
  const knob = el("div", "joy-knob");
  pad.append(knob);
  const speed = Object.assign(document.createElement("input"), { type: "range", min: 50, max: 1000, step: 10, value: 400 });
  speed.setAttribute("aria-label", "Tốc độ tối đa µs/s");
  const speedVal = el("span", "mono", "400 µs/s");
  speed.addEventListener("input", () => { speedVal.textContent = `${speed.value} µs/s`; });
  const flip = {};
  const flipRow = el("div", "row");
  for (const [name, label] of [["pan", "Đảo trục pan"], ["tilt", "Đảo trục tilt"]]) {
    const cb = Object.assign(document.createElement("input"), { type: "checkbox" });
    const lab = el("label", "chk");
    lab.append(cb, ` ${label}`);
    flip[name] = cb;
    flipRow.append(lab);
  }
  const joyInfo = el("div", "readout", "x 0.00 · y 0.00 · đích --");
  const joyBox = el("div", "joy");
  const joyCtl = el("div", "joy-ctl");
  const sRow = el("div", "row");
  sRow.append(el("span", "label", "Tốc độ"), speedVal);
  joyCtl.append(sRow, speed, flipRow, joyInfo);
  joyBox.append(pad, joyCtl);

  function joyShow() {
    joyInfo.textContent = `x ${joy.x.toFixed(2)} · y ${joy.y.toFixed(2)} · đích pan ${s.pan}, tilt ${s.tilt} µs`;
  }
  function joyVec(e) {
    const r = pad.getBoundingClientRect();
    const rad = r.width / 2, lim = rad - KNOB / 2;
    let dx = e.clientX - (r.left + rad), dy = e.clientY - (r.top + rad);
    const d = Math.hypot(dx, dy);
    if (d > lim) { dx *= lim / d; dy *= lim / d; }
    knob.style.transform = `translate(${dx}px, ${dy}px)`;
    let x = dx / lim, y = dy / lim;
    if (Math.hypot(x, y) < DEAD) { x = 0; y = 0; }
    joy.x = x; joy.y = y;
    joyShow();
  }
  function joyTick() {
    const now = performance.now();
    const dt = Math.min((now - joy.last) / 1000, 0.5);
    joy.last = now;
    if (!joy.x && !joy.y) return;
    const rate = Number(speed.value);
    const sx = flip.pan.checked ? -1 : 1, sy = flip.tilt.checked ? -1 : 1;
    joy.fp = Math.min(PAN[1], Math.max(PAN[0], joy.fp + sx * joy.x * rate * dt));
    joy.ft = Math.min(TILT[1], Math.max(TILT[0], joy.ft - sy * joy.y * rate * dt));
    ax.pan.set(joy.fp);
    ax.tilt.set(joy.ft);
    joyShow();
    const key = `${s.pan},${s.tilt}`;
    if (key !== joy.sent) { joy.sent = key; move(); }
  }
  function joyEnd() {
    if (!joy.active) return;
    joy.active = false;
    clearInterval(joy.timer);
    joy.x = joy.y = 0;
    knob.style.transform = "";
    pad.classList.remove("on");
    joyShow();
  }
  pad.addEventListener("pointerdown", (e) => {
    if (joy.active) return;
    e.preventDefault();
    joy.active = true; joy.id = e.pointerId;
    pad.setPointerCapture(e.pointerId);
    pad.classList.add("on");
    joy.fp = s.pan; joy.ft = s.tilt; joy.sent = `${s.pan},${s.tilt}`;
    joy.last = performance.now();
    joyVec(e);
    joy.timer = setInterval(joyTick, TICK_MS);
  });
  pad.addEventListener("pointermove", (e) => { if (joy.active && e.pointerId === joy.id) joyVec(e); });
  for (const t of ["pointerup", "pointercancel", "lostpointercapture"]) {
    pad.addEventListener(t, (e) => { if (e.pointerId === joy.id) joyEnd(); });
  }
  window.addEventListener("blur", joyEnd);
  document.addEventListener("visibilitychange", () => { if (document.hidden) joyEnd(); });

  const nrow = el("div", "row");
  nrow.append(arm, home, stop, laser);
  const mrow = el("div", "form-row");
  mrow.append(noteIn, mark);
  root.append(el("h2", "", node), nrow, joyBox, ax.pan.wrap, ax.tilt.wrap, send, readout, err,
    el("span", "label", "Điểm đã ghi"), mrow, marks, keys);

  const setErr = (r) => { err.textContent = r.ok ? "" : `Lỗi: ${r.error}`; };
  function show(r) {
    setErr(r);
    if (!r.ok || !r.pantilt) return;
    const p = r.pantilt;
    s.laser = !!r.laser_on;
    laser.textContent = `Laser: ${s.laser ? "BẬT" : "tắt"}`;
    laser.classList.toggle("laser-on", s.laser);
    readout.textContent = `pan ${p.pan_us} · tilt ${p.tilt_us} µs (đích ${p.target_pan_us}, ${p.target_tilt_us}) · ` +
      `armed ${p.armed ? "có" : "không"} · pwm ${p.pwm_active ? "bật" : "tắt"}`;
    if (!s.inited && p.pan_us != null) { // lần đầu: lấy vị trí thật làm điểm xuất phát
      s.inited = true;
      ax.pan.set(p.pan_us);
      ax.tilt.set(p.tilt_us);
    } else if (!s.inited && p.target_pan_us != null) { // chưa ARM: xuất phát từ HOME, khỏi giật
      ax.pan.set(p.target_pan_us);
      ax.tilt.set(p.target_tilt_us);
    }
  }
  async function run(body) {
    const r = await manual({ node, ...body });
    show(r);
    return r;
  }
  async function move() { // gộp lệnh dồn lại: chỉ gửi vị trí mới nhất khi lệnh trước xong
    if (s.busy) { s.pending = true; return; }
    s.busy = true;
    try {
      do {
        s.pending = false;
        await run({ op: "move", pan_us: s.pan, tilt_us: s.tilt });
      } while (s.pending);
    } finally { s.busy = false; }
  }
  function nudge(name, d) {
    ax[name].set(s[name] + d);
    move();
  }
  const toggleLaser = () => run({ op: "laser", on: !s.laser });

  arm.addEventListener("click", () => run({ op: "arm" }));
  home.addEventListener("click", async () => { s.inited = false; await run({ op: "home" }); });
  stop.addEventListener("click", () => run({ op: "stop" }));
  laser.addEventListener("click", toggleLaser);
  send.addEventListener("click", move);
  mark.addEventListener("click", async () => {
    const note = noteIn.value.trim();
    if (!note) { setErr({ ok: false, error: "nhập ghi chú trước khi ghi điểm" }); return; }
    const r = await run({ op: "mark", note });
    if (r.ok) { noteIn.value = ""; toast(`Đã ghi điểm ${node}: ${note}`); loadMarks(); }
  });

  root.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t instanceof HTMLButtonElement || t instanceof HTMLSelectElement) return;
    if (t instanceof HTMLInputElement && t.type !== "range") return;
    const k = e.shiftKey ? 50 : 10;
    const act = {
      ArrowLeft: () => nudge("pan", -k), ArrowRight: () => nudge("pan", k),
      ArrowUp: () => nudge("tilt", k), ArrowDown: () => nudge("tilt", -k),
      " ": toggleLaser, Escape: () => run({ op: "stop" }),
    }[e.key];
    if (act) { e.preventDefault(); act(); }
  });

  return {
    root, show,
    setMarks(list) {
      marks.replaceChildren(...(list.length ? list.map((m) =>
        el("li", "", `${stamp(m.time)} · ${m.reported_pan_us ?? "?"},${m.reported_tilt_us ?? "?"} µs · ${m.note ?? ""}`))
        : [el("li", "", "Chưa có điểm nào.")]));
    },
  };
}

const cards = Object.fromEntries(NODES.map((n) => [n, makeCard(n)]));
$("cards").append(...NODES.map((n) => cards[n].root));

// --- trạng thái định kỳ -----------------------------------------------------------------------

let ov = null;
async function pollOverview() {
  try {
    ov = (await api("/api/overview")).data;
  } catch { ov = null; }
  const on = ov?.state?.orch?.enabled;
  $("auto-state").textContent = on == null ? "Tự động: không rõ" : `Tự động: ${on ? "ĐANG BẬT, hãy tắt" : "đã tắt"}`;
  $("auto-toggle").textContent = on ? "Tắt Tự động" : "Bật lại Tự động";
  $("auto-toggle").dataset.next = on ? "off" : "on";
  $("banner").classList.toggle("ok", on === false);
  ensureCamera(!!ov?.pi_online || !!ov?.camera);
}
$("auto-toggle").addEventListener("click", async () => {
  const next = $("auto-toggle").dataset.next === "on";
  const r = await postCmd({ cmd: "enable", on: next });
  toast(r.ok === false ? `Lỗi: ${r.error}` : next ? "Đã bật Tự động" : "Đã tắt Tự động", r.ok === false);
  pollOverview();
});

async function pollStatus() {
  if (document.hidden || !ov?.pi_online) return;
  await Promise.all(NODES.map(async (n) => {
    const r = await manual({ node: n, op: "status" });
    cards[n].show(r);
  }));
}

async function loadMarks() {
  try {
    const { data } = await api("/api/events?kind=manual&limit=200");
    for (const n of NODES) cards[n].setMarks(data.filter((e) => e.node === n && e.op === "mark").slice(0, 10));
  } catch { /* giữ danh sách cũ */ }
}

// --- camera -----------------------------------------------------------------------------------

let camOn = false;
let camRetry = 0;
function ensureCamera(available) {
  const img = $("cam");
  if (available && !camOn && !camRetry) {
    camOn = true;
    img.src = `/camera/stream.mjpg?t=${Date.now()}`;
  } else if (!available && camOn) {
    camOn = false;
    img.removeAttribute("src");
    img.hidden = true;
    $("cam-none").hidden = false;
  }
}
$("cam").addEventListener("load", () => { $("cam").hidden = false; $("cam-none").hidden = true; });
$("cam").addEventListener("error", () => {
  if (!camOn) return;
  camOn = false;
  $("cam").removeAttribute("src");
  $("cam").hidden = true;
  $("cam-none").hidden = false;
  $("cam-hint").textContent = "Camera chưa có khung hình, đang thử lại";
  camRetry = setTimeout(() => { camRetry = 0; ensureCamera(!!ov?.pi_online || !!ov?.camera); }, 5000);
});

pollOverview().then(pollStatus);
loadMarks();
setInterval(pollOverview, 2000);
setInterval(pollStatus, 1000);
setInterval(loadMarks, 4000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { pollOverview().then(pollStatus); loadMarks(); }
});
