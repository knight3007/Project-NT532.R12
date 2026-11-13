// Nhật ký trực tiếp: tối đa 100 dòng, mới nhất ở trên, lọc bằng chip.
import { clock, el } from "./format.js";

const FILTERS = [
  ["all", "Tất cả", () => true],
  ["s1", "s1", (e) => e.node === "s1"],
  ["s2", "s2", (e) => e.node === "s2"],
  ["alert", "Cảnh báo", (e) => e.kind === "alert"],
  ["decision", "Quyết định", (e) => e.kind === "decision"],
  ["cmd", "Lệnh", (e) => e.kind === "cmd"],
  ["error", "Lỗi", (e) => e.level === "error" || e.kind === "fault"],
];
const SHOW = 100;
const KEEP = 300;

export function createLog(listEl, filtersEl) {
  let events = []; // mới nhất trước
  let active = "all";
  const ids = new Set();

  const chips = FILTERS.map(([key, label]) => {
    const b = el("button", "fchip", label);
    b.type = "button";
    b.setAttribute("aria-pressed", String(key === active));
    b.addEventListener("click", () => {
      active = key;
      chips.forEach((c, i) => c.setAttribute("aria-pressed", String(FILTERS[i][0] === key)));
      render();
    });
    filtersEl.append(b);
    return b;
  });

  function render() {
    const pred = FILTERS.find((f) => f[0] === active)[2];
    const rows = events.filter(pred).slice(0, SHOW);
    if (!rows.length) {
      const li = el("li", "empty", "Chưa có sự kiện. Trạm đang chờ cảnh báo.");
      listEl.replaceChildren(li);
      return;
    }
    listEl.replaceChildren(...rows.map((e) => {
      const li = el("li");
      li.append(el("span", "t", clock(e.time)), el("span", `lv ${e.level}`), el("span", `k k-${e.kind}`, e.kind), el("span", "x", e.text));
      li.querySelector(".lv").title = e.level;
      return li;
    }));
  }

  return {
    // `batch` mới nhất trước hoặc cũ nhất trước đều được: sắp lại theo id
    add(batch) {
      const fresh = batch.filter((e) => e.id != null && !ids.has(e.id));
      if (!fresh.length) return false;
      fresh.forEach((e) => ids.add(e.id));
      events = [...fresh, ...events].sort((a, b) => b.id - a.id).slice(0, KEEP);
      render();
      return true;
    },
    get events() { return events; },
    get maxId() { return events.length ? events[0].id : 0; },
    render,
  };
}
