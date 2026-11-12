// Định dạng số và thời gian theo kiểu Việt Nam.
const nf1 = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 });

export const num1 = (v) => (v == null || Number.isNaN(v) ? "--" : nf1.format(v));
export const num0 = (v) => (v == null || Number.isNaN(v) ? "--" : nf0.format(v));

export function clock(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("vi-VN", { hour12: false });
}

export function mmss(sec) {
  sec = Math.max(0, Math.floor(sec));
  const m = String(Math.floor(sec / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
