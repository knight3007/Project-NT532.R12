// Phần chung của các trang phụ (Nhật ký, Lượt): gọi API, tên người dùng, trạng thái Pi.
import { el } from "./format.js";

export async function api(url) {
  const r = await fetch(url, { credentials: "same-origin" });
  if (r.status === 401) {
    location.href = "/login";
    throw new Error("Cần đăng nhập");
  }
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 120)}`);
  return { data: await r.json(), total: Number(r.headers.get("X-Total-Count") ?? "NaN") };
}

export function toast(msg, bad = false) {
  const box = document.getElementById("toasts");
  if (!box) return;
  const t = el("div", "toast" + (bad ? " bad" : ""), msg);
  box.append(t);
  setTimeout(() => t.remove(), 4000);
}

export function initShell() {
  const user = document.getElementById("user");
  const dot = document.getElementById("pi-dot");
  const label = document.getElementById("pi-label");
  api("/api/me").then(({ data }) => { user.textContent = data.user; }).catch(() => {});
  async function ping() {
    try {
      const { data } = await api("/api/overview");
      dot.className = "dot " + (data.pi_online ? "ok" : "bad");
      label.textContent = data.pi_online ? "Pi trực tuyến" : "Pi mất kết nối";
    } catch {
      dot.className = "dot bad";
      label.textContent = "Mất kết nối";
    }
  }
  ping();
  setInterval(ping, 5000);
}

// --- thời gian ------------------------------------------------------------------------------

const pad = (n) => String(n).padStart(2, "0");

export function stamp(ts, seconds = true) {
  const d = new Date(ts * 1000);
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}` +
    (seconds ? `:${pad(d.getSeconds())}` : "");
}

// epoch giây <-> giá trị của <input type="datetime-local"> (giờ địa phương)
export function toLocalInput(ts) {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export const fromLocalInput = (v) => (v ? new Date(v).getTime() / 1000 : null);

export const RANGES = [["15m", "15 phút", 900], ["1h", "1 giờ", 3600], ["24h", "24 giờ", 86400], ["7d", "7 ngày", 604800]];
