// Thẻ node: nhiệt độ, gas, độ ẩm, nhịp tim, tuổi tư thế, vật cản.
import { el, num0, num1 } from "./format.js";

const HB_FRESH_MS = 3000;
const STALE_S = 15; // mẫu cảm biến cũ hơn thế này coi là mất dữ liệu

export function createNodeCards(root, names) {
  const tpl = document.getElementById("node-tpl");
  const cards = {};
  for (const n of names) {
    const c = tpl.content.firstElementChild.cloneNode(true);
    c.querySelector(".node-name").textContent = n.toUpperCase();
    c.dataset.n = n;
    root.append(c);
    cards[n] = {
      root: c,
      pulse: c.querySelector(".pulse"),
      state: c.querySelector(".node-state"),
      vals: Object.fromEntries(["temp", "gas", "hum"].map((k) => [k, c.querySelector(`.val[data-k=${k}]`)])),
      hb: c.querySelector("[data-f=hb]"),
      pose: c.querySelector("[data-f=pose]"),
      blockers: c.querySelector("[data-f=blockers]"),
      sig: null,
    };
  }

  return function render(state, online) {
    for (const [n, c] of Object.entries(cards)) {
      const samples = state?.sensors?.[n] ?? [];
      const s = samples.at(-1);
      const nz = state?.orch?.nozzles?.[n] ?? {};
      const lim = state?.limits ?? {};
      const stale = !online || !s || s.t < -STALE_S;
      const hbLive = online && nz.hb_ms != null && nz.hb_ms < HB_FRESH_MS;
      const level = (v, limit) => (v == null || !limit ? "" : v >= limit ? "alarm" : v >= 0.8 * limit ? "warn" : "");
      const lv = { temp: level(s?.temp, lim.temp), gas: level(s?.gas, lim.gas), hum: "" };
      const alarm = !stale && (lv.temp === "alarm" || lv.gas === "alarm");

      c.pulse.classList.toggle("live", hbLive);
      c.state.textContent = stale ? "Mất dữ liệu" : alarm ? "Báo động" : "Ổn định";
      c.root.classList.toggle("stale", stale);
      c.state.className = "node-state" + (stale ? " stale" : alarm ? " alarm" : "");
      for (const k of ["temp", "gas", "hum"]) {
        const v = c.vals[k];
        const val = stale && !s ? null : s?.[k];
        v.querySelector(".num").textContent = k === "temp" ? num1(val) : num0(val);
        v.className = "val " + (stale ? "" : lv[k]);
      }
      c.hb.textContent = !online || nz.hb_ms == null ? "mất" : `${num0(nz.hb_ms)} ms`;
      c.pose.textContent = nz.pose_s == null ? "--" : `${num1(nz.pose_s)} s`;
      const bl = nz.blockers ?? [];
      const sig = bl.join("|");
      if (sig !== c.sig) {
        c.sig = sig;
        c.blockers.replaceChildren(...(bl.length ? bl.map((b) => el("li", "", String(b))) : [el("li", "none", "Không có vật cản")]));
      }
    }
  };
}
