// Panel "Mạng Thread" trên Tổng quan: thẻ OTBR, bảng node (vai trò, ping, mất gói, CoAP, fw) và danh sách topology.
import { el, num0, num1 } from "./format.js";

const STALE_S = 40; // thread_monitor đẩy mỗi ~10 s; quá ngưỡng này coi là cũ
const ROLE = { leader: "Leader", router: "Router", child: "Child", detached: "Mất liên kết", disabled: "Tắt" };
const fmtAge = (s) => (s == null ? "--" : s < 90 ? `${Math.round(s)} s trước` : s < 5400 ? `${Math.round(s / 60)} phút trước` : `${(s / 3600).toFixed(1)} giờ trước`);
const hex = (v) => (v == null ? "--" : String(v));

function kvCell(label, value) {
  const d = el("div", "th-kv");
  d.append(el("span", "label", label), el("span", "mono", value == null || value === "" ? "--" : String(value)));
  return d;
}

export function createThreadPanel(root) {
  const q = (s) => root.querySelector(s);
  return function render(th) {
    root.hidden = !th;
    if (!th) return;
    const s = th.latest ?? {};
    const stale = th.age > STALE_S;
    q("[data-f=age]").textContent = stale ? `số liệu cũ ${fmtAge(th.age)}` : `cập nhật ${fmtAge(th.age)}`;
    q("[data-f=age]").classList.toggle("bad", stale);

    // OTBR
    const o = s.otbr ?? {};
    const cards = q("[data-f=otbr]");
    if (o.ok === false) {
      cards.replaceChildren(el("span", "th-err", `OTBR không trả lời: ${o.error ?? "lỗi không rõ"}`));
    } else {
      cards.replaceChildren(
        kvCell("Trạng thái", ROLE[o.state] ?? o.state), kvCell("Partition", o.partitionId),
        kvCell("Số router", o.routerCount), kvCell("RLOC16", o.rloc16),
        kvCell("Kênh", o.channel), kvCell("PAN ID", o.panId != null ? `0x${Number(o.panId).toString(16)}` : null),
        kvCell("Mạng", o.networkName), kvCell("Ext PAN", o.extPanId),
      );
    }

    // bảng node
    const rows = Object.entries(s.nodes ?? {}).map(([name, n]) => {
      const p = n.ping ?? {}, c = n.coap ?? {};
      const reach = (p.recv ?? 0) > 0 || c.ok;
      const tr = el("tr", reach ? "" : "down");
      const loss = p.loss_pct;
      const age = n.last_ok ? Math.max(0, Date.now() / 1000 - n.last_ok) : null;
      for (const [text, cls] of [
        [name.toUpperCase(), "nm"], [n.role ? (ROLE[n.role] ?? n.role) : "--"], [num1(p.rtt_avg), "mono"],
        [num1(p.rtt_min), "mono"], [num1(p.rtt_max), "mono"],
        [loss == null ? "--" : num0(loss), loss >= 20 ? "mono bad" : "mono"], [c.ok ? num1(c.rtt_ms) : "--", "mono"],
        [reach ? "trực tuyến" : `mất · thấy lần cuối ${fmtAge(age)}`], [n.fw ?? "--", "mono"],
      ]) tr.append(el("td", cls, text));
      tr.title = [p.error, c.error].filter(Boolean).join(" · ");
      return tr;
    });
    q("[data-f=nodes]").replaceChildren(...rows);
    q("[data-f=none]").hidden = rows.length > 0;

    // topology
    const t = s.topology;
    const box = q("[data-f=topo]");
    if (!t) {
      box.replaceChildren(el("p", "th-note", s.topology_error ? `Chưa có topology: ${s.topology_error}` : "Chưa có topology (đang lấy từ OTBR, mất khoảng một phút)."));
      return;
    }
    const list = el("ul", "th-topo");
    for (const r of t.routers ?? []) {
      const li = el("li");
      li.append(el("span", "nm", `Router ${hex(r.rloc16)}`), el("span", "dim mono", r.ext ?? ""));
      const sub = el("ul", "th-sub");
      for (const n of r.neighbors ?? []) {
        sub.append(el("li", "mono", `↔ ${hex(n.rloc16)} · margin ${num0(n.linkMargin)} dB · RSSI ${num0(n.averageRssi)} dBm · lỗi khung ${n.frameErrorRate == null ? "--" : `${num1(n.frameErrorRate * 100)}%`}`));
      }
      for (const c of r.children ?? []) {
        sub.append(el("li", "mono", `└ child ${hex(c.rloc16)} · margin ${num0(c.linkMargin)} dB · RSSI ${num0(c.averageRssi)} dBm`));
      }
      if (r.error) sub.append(el("li", "mono bad", r.error));
      if (!sub.children.length) sub.append(el("li", "dim", "Không có neighbor hay child"));
      li.append(sub);
      list.append(li);
    }
    const age = t.ts ? Math.max(0, Date.now() / 1000 - t.ts) : null;
    box.replaceChildren(el("p", "th-note", `${(t.routers ?? []).length} router, ${(t.links ?? []).length} liên kết · lấy ${fmtAge(age)}`), list);
  };
}
