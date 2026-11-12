// Biểu đồ đường SVG thuần: dải min–max, đường trung bình, ngưỡng, vùng lượt xử lý, chú giải khi rê chuột.
const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}, cls) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (cls) e.setAttribute("class", cls);
  return e;
};
const pad = (n) => String(n).padStart(2, "0");
const nf = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 });

function niceTicks(lo, hi, n = 4) {
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

function timeLabel(ts, span) {
  const d = new Date(ts * 1000);
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (span <= 3600 * 2) return `${hm}:${pad(d.getSeconds())}`;
  if (span <= 86400 * 1.5) return hm;
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${hm}`;
}

/**
 * Vẽ một biểu đồ vào `host` và tự vẽ lại khi đổi độ rộng.
 * series: { t[], min[], avg[], max[] }; opts: { from, to, limit, runs[], unit, title, height }
 * Trả về { update(series, opts), destroy() }.
 */
export function lineChart(host, series, opts) {
  let cur = { series, opts };
  const tip = document.createElement("div");
  tip.className = "ch-tip";
  tip.hidden = true;
  host.classList.add("chart");
  const ro = new ResizeObserver(() => draw());
  ro.observe(host);

  function draw() {
    const { series: s, opts: o } = cur;
    const W = Math.max(host.clientWidth, 200);
    const H = o.height ?? 150;
    const m = { l: 44, r: 10, t: 8, b: 22 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
      "aria-label": `${o.title ?? "Biểu đồ"}${o.unit ? ` (${o.unit})` : ""}` }, "ch-svg");
    host.replaceChildren(svg, tip);

    const pts = s.t.map((t, i) => ({ t, lo: s.min[i], av: s.avg[i], hi: s.max[i] })).filter((p) => p.av != null);
    const span = o.to - o.from;
    const x = (t) => m.l + ((t - o.from) / span) * iw;

    let lo = Infinity, hi = -Infinity;
    for (const p of pts) { lo = Math.min(lo, p.lo ?? p.av); hi = Math.max(hi, p.hi ?? p.av); }
    if (o.limit != null) { hi = Math.max(hi, o.limit * 1.05); lo = Math.min(lo, o.limit); }
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
    const room = (hi - lo) * 0.08;
    lo -= room; hi += room;
    if (lo < 0 && pts.every((p) => (p.lo ?? p.av) >= 0)) lo = 0;
    const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * ih;

    // vùng các lượt xử lý
    for (const r of o.runs ?? []) {
      const a = Math.max(x(r.start), m.l), b = Math.min(x(r.end), m.l + iw);
      if (b < m.l || a > m.l + iw) continue;
      const rect = svgEl("rect", { x: a, y: m.t, width: Math.max(b - a, 2), height: ih }, "ch-run");
      const t = svgEl("title");
      t.textContent = `Lượt ${r.id}${r.outcome ? ` · ${r.outcome}` : " · đang chạy"}`;
      rect.append(t);
      svg.append(rect);
    }
    // lưới và trục
    for (const v of niceTicks(lo, hi)) {
      svg.append(svgEl("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }, "ch-grid"));
      const t = svgEl("text", { x: m.l - 6, y: y(v) + 3.5, "text-anchor": "end" }, "ch-axis");
      t.textContent = nf.format(v);
      svg.append(t);
    }
    const nt = Math.max(2, Math.min(6, Math.floor(iw / 90)));
    for (let i = 0; i <= nt; i++) {
      const ts = o.from + (span * i) / nt;
      const t = svgEl("text", { x: x(ts), y: H - 5, "text-anchor": i === 0 ? "start" : i === nt ? "end" : "middle" }, "ch-axis");
      t.textContent = timeLabel(ts, span);
      svg.append(t);
    }
    // ngưỡng
    if (o.limit != null) {
      svg.append(svgEl("line", { x1: m.l, x2: m.l + iw, y1: y(o.limit), y2: y(o.limit) }, "ch-limit"));
      const t = svgEl("text", { x: m.l + iw - 2, y: y(o.limit) - 4, "text-anchor": "end" }, "ch-limit-label");
      t.textContent = `ngưỡng ${nf.format(o.limit)}`;
      svg.append(t);
    }
    // dải min–max và đường trung bình, ngắt ở chỗ mất dữ liệu
    const gap = Math.max((span / Math.max(s.t.length, 1)) * 4, 10);
    const segs = [];
    for (const p of pts) {
      const last = segs.at(-1);
      if (last && p.t - last.at(-1).t <= gap) last.push(p); else segs.push([p]);
    }
    for (const seg of segs) {
      if (seg.length > 1) {
        const up = seg.map((p) => `${x(p.t).toFixed(1)},${y(p.hi ?? p.av).toFixed(1)}`);
        const dn = seg.map((p) => `${x(p.t).toFixed(1)},${y(p.lo ?? p.av).toFixed(1)}`).reverse();
        svg.append(svgEl("polygon", { points: [...up, ...dn].join(" ") }, "ch-band"));
        svg.append(svgEl("polyline", { points: seg.map((p) => `${x(p.t).toFixed(1)},${y(p.av).toFixed(1)}`).join(" ") }, "ch-line"));
      } else {
        svg.append(svgEl("circle", { cx: x(seg[0].t), cy: y(seg[0].av), r: 2 }, "ch-dot"));
      }
    }
    if (!pts.length) {
      const t = svgEl("text", { x: m.l + iw / 2, y: m.t + ih / 2, "text-anchor": "middle" }, "ch-empty");
      t.textContent = "Chưa có dữ liệu trong khoảng này";
      svg.append(t);
    }
    // chú giải khi rê chuột hoặc chạm
    const cross = svgEl("line", { y1: m.t, y2: m.t + ih }, "ch-cross");
    cross.style.display = "none";
    svg.append(cross);
    const overlay = svgEl("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
    svg.append(overlay);
    const show = (ev) => {
      if (!pts.length) return;
      const bx = svg.getBoundingClientRect();
      const px = ((ev.clientX - bx.left) / bx.width) * W;
      const t = o.from + ((px - m.l) / iw) * span;
      let best = pts[0];
      for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
      const cx = x(best.t);
      cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.style.display = "";
      const d = new Date(best.t * 1000);
      tip.replaceChildren();
      const when = document.createElement("div");
      when.textContent = `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
      const val = document.createElement("div");
      val.className = "mono";
      val.textContent = `${nf.format(best.av)}${o.unit ? " " + o.unit : ""}`;
      const rng = document.createElement("div");
      rng.className = "mono dim";
      rng.textContent = `min ${nf.format(best.lo ?? best.av)} · max ${nf.format(best.hi ?? best.av)}`;
      tip.append(when, val, rng);
      tip.hidden = false;
      const tw = tip.offsetWidth;
      tip.style.left = `${Math.min(Math.max(cx * (bx.width / W) - tw / 2, 0), bx.width - tw)}px`;
      tip.style.top = "0px";
    };
    const hide = () => { cross.style.display = "none"; tip.hidden = true; };
    overlay.addEventListener("pointermove", show);
    overlay.addEventListener("pointerdown", show);
    overlay.addEventListener("pointerleave", hide);
  }

  draw();
  return {
    update(s, o) { cur = { series: s, opts: o }; draw(); },
    destroy() { ro.disconnect(); host.replaceChildren(); },
  };
}
