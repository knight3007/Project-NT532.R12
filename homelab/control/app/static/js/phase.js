// Dải pha: hàng đèn như bảng báo cháy. Chuỗi pha lấy từ orchestrator/machine.py (Phase).
import { el, mmss } from "./format.js";

const SEQ = ["IDLE", "LOCALIZE", "DECIDE", "AIM", "CORRECT", "FIRE", "VERIFY"];
const COLOR = { IDLE: "ok", ALERT: "warn", LOCALIZE: "warn", DECIDE: "water", AIM: "water", CORRECT: "water", FIRE: "ember", VERIFY: "ok" };
const FAILED = {
  ALARM: "Báo động · không phun được, chờ người",
  HUMAN: "Cần người can thiệp",
  FAULT: "Lỗi trạm",
};

export function createPhaseStrip(root) {
  const segs = SEQ.map((p) => {
    const s = el("div", "seg", p);
    s.dataset.p = p;
    root.append(s);
    return s;
  });
  const bar = el("div", "alarm-bar");
  bar.hidden = true;
  root.append(bar);
  let last = "";

  return function render(orch) {
    const phase = orch?.phase ?? "IDLE";
    const key = phase + "|" + (orch?.enabled === false);
    if (key === last) return;
    last = key;
    const failed = FAILED[phase];
    bar.hidden = !failed;
    bar.textContent = failed ? `${phase} · ${failed.split(" · ")[1] ?? failed}` : "";
    const cur = phase === "ALERT" ? "LOCALIZE" : phase;
    const idx = SEQ.indexOf(cur);
    for (const s of segs) {
      s.hidden = !!failed;
      const i = SEQ.indexOf(s.dataset.p);
      const lit = i === idx;
      const active = phase !== "IDLE";
      s.className = "seg" + (lit ? ` lit c-${COLOR[phase] ?? "ok"}` : active && i > 0 && i < idx ? " done" : "");
    }
  };
}

export function runInfo(orch, stateTime, receivedAt) {
  const run = orch?.run;
  if (!run) {
    const prev = (orch?.runs ?? []).at(-1);
    return { label: prev ? `Lượt gần nhất #${prev.id}` : "Chưa có lượt", time: "--:--" };
  }
  const label = `Lượt #${run.id} · ${run.node ?? ""}`.trim();
  if (run.outcome) { // lượt đã xong: đóng băng thời gian ở pha cuối
    const end = run.phases?.length ? run.phases.at(-1)[1] : run.started;
    return { label: `${label} · ${run.outcome}`, time: mmss(end - run.started) };
  }
  const elapsed = stateTime - run.started + (performance.now() - receivedAt) / 1000;
  return { label, time: mmss(elapsed) };
}
