"""State machine của orchestrator (kế hoạch mục 4), nối thị giác, cảm biến, bộ quyết định và vòi.

    IDLE -> ALERT -> LOCALIZE -> DECIDE -> AIM -> CORRECT -> FIRE -> VERIFY -> (xong | lặp | HUMAN)
                                       \\-> ALARM (chỉ báo động) hoặc về IDLE (bỏ qua)
    lỗi ở bất kỳ bước nào -> FAULT: gửi /stop, ghi nguyên nhân

Phun hai vòi (đáp án `nozzle` = BOTH): AIM và CORRECT lần lượt từng vòi (mỗi lúc một laser, vì
`find_spot` so khung tắt/bật), FIRE bật hai bơm cùng lúc, VERIFY coi như một lần phun. Vòi nào hỏng
giữa chừng (mất liên lạc, không trả lời lệnh ngắm) thì bị bỏ và lượt chạy tiếp bằng vòi còn lại.

Mô hình (hoặc luật) chỉ quyết định: có cháy thật không, làm gì, bia nào, vòi nào, sau khi phun
thì làm gì. Các chặn an toàn cứng luôn là luật ở đây: chưa commissioning hoặc pose hết hạn, mất
heartbeat, mục tiêu ngoài bảng, góc ngoài giới hạn, quá số lần thử, nút dừng khẩn cấp.
"""

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from ..decider.rules import HB_MAX_MS, POSE_MAX_S
from ..decider.scenario import MAX_ATTEMPTS
from ..decider.state_text import ACTIONS, BOTH, NOZZLES, VERIFY, nozzles_of
from ..net.link import LinkError, NodeLink
from ..net.protocol import Alert
from ..vision import Vision
from . import aiming
from .events import EventLog
from .frames import FrameHub
from .fusion import (
    CAP_AGE,
    SensorHistory,
    aggregate,
    alarm_limits,
    candidate_id,
    decide_obs,
    questions_for,
    verify_obs,
)


class Phase(str, Enum):
    IDLE = "IDLE"
    ALERT = "ALERT"
    LOCALIZE = "LOCALIZE"
    DECIDE = "DECIDE"
    AIM = "AIM"
    CORRECT = "CORRECT"
    FIRE = "FIRE"
    VERIFY = "VERIFY"
    ALARM = "ALARM"  # cháy thật nhưng không phun được: chỉ báo động, chờ người
    HUMAN = "HUMAN"  # đã thử mà không xong: gọi người
    FAULT = "FAULT"


class Abort(Exception):
    """Dừng khẩn cấp hoặc lỗi không thể tiếp tục; thông điệp là lý do."""


@dataclass
class Settings:
    frames: int = 4  # số khung gộp cho một lần quyết định
    frame_gap_s: float = 0.25
    laser_ms: int = 700  # thời gian bật laser mỗi lần CORRECT
    status_timeout_s: float = 3.0
    verify_wait_s: float = 3.0  # chờ sau khi phun để cảm biến và camera kịp thay đổi
    health_period_s: float = 10.0  # kiểm tra camera và pose node lúc IDLE
    alert_max_age_s: float = 30.0  # cảnh báo xếp hàng lâu hơn chừng này thì bỏ
    realert_s: float = 20.0  # cảm biến vẫn vượt ngưỡng chừng này sau lượt trước thì tự xử lý lại
    realert_max: int = 2  # quá số lần tự xử lý lại liên tiếp thì thôi, gọi người


@dataclass
class Run:
    """Một lượt xử lý một cảnh báo, để hiển thị và ghi log."""

    id: int
    node: str
    started: float = field(default_factory=time.time)
    phases: list = field(default_factory=list)  # [(phase, time, ghi chú)]
    decisions: list = field(default_factory=list)  # Decision.to_json() của decide và từng verify
    outcome: str | None = None  # extinguished, ignored, alarm_only, human, fault, stopped
    target: dict | None = None
    nozzle: str | None = None
    attempts: int = 0
    shots: list = field(default_factory=list)  # [{round, miss_cm, nozzle}] mỗi vòng CORRECT

    def to_json(self) -> dict:
        return {"id": self.id, "node": self.node, "started": self.started,
                "phases": self.phases, "decisions": self.decisions, "outcome": self.outcome,
                "target": self.target, "nozzle": self.nozzle, "attempts": self.attempts,
                "shots": self.shots}


class Orchestrator:
    def __init__(self, site: dict, vision: Vision, frames: FrameHub, link: NodeLink, decider,
                 sensors: SensorHistory, events: EventLog, settings: Settings | None = None,
                 clock=time.monotonic) -> None:
        self.site, self.vision, self.hub, self.link = site, vision, frames, link
        self.decider, self.sensors, self.events = decider, sensors, events
        self.cfg = settings or Settings()
        self.clock = clock
        self.tcfg = site["targeting"]
        self.acfg = site["actuator"]
        self.phase = Phase.IDLE
        self.run: Run | None = None
        self.runs: deque = deque(maxlen=20)
        # cho dashboard: bia, bia chọn, vòi, `active` (các vòi đang được ngắm/phun), `aims` và `spots`
        # ({vòi: {"x", "z"}}: điểm ngắm đã bù và vết laser đo được của từng vòi)
        self.overlay: dict = {}
        self.pose_time = {n: clock() for n in vision.nodes}
        self.last_health: dict | None = None
        self.enabled = True  # False: nhận cảnh báo nhưng không xử lý (bảo trì)
        self._queue: queue.Queue = queue.Queue()
        self._abort = threading.Event()
        self._lock = threading.Lock()
        self._run_ids = 0
        self._last_end: dict[str, float] = {}  # node -> lúc lượt gần nhất của node đó kết thúc
        self._realerts: dict[str, int] = {}  # node -> số lần tự xử lý lại liên tiếp
        self._thread: threading.Thread | None = None
        self._halt = threading.Event()

    # --- vào/ra -----------------------------------------------------------------------------

    def on_alert(self, alert: Alert) -> None:
        """Gọi từ server CoAP (`/a`) hoặc từ sim, đã chống lặp."""
        self.link.on_rx(alert.n)
        if alert.k != "alert":
            self.events.emit("alert", f"{alert.n} hết báo động", node=alert.n)
            return
        self.events.emit("alert", f"cảnh báo từ {alert.n} (nhiệt {alert.t:g}, khí {alert.g:g})",
                         level="warn", node=alert.n)
        self._queue.put((self.clock(), alert))

    def emergency_stop(self, reason: str = "nút dừng khẩn cấp") -> None:
        self._abort.set()
        self.link.stop()
        self.events.emit("fault", f"DỪNG: {reason}", level="error")

    def recommission(self) -> None:
        """Commissioning lại từ ảnh hiện tại (sau khi dời node hoặc camera)."""
        state = self.vision.commission(self.hub.frames(self.site["vision"]["commission_frames"]))
        now = self.clock()
        self.pose_time = {n: now for n in state.nodes}
        self.events.emit("vision", f"commissioning lại: chiếu lại {state.reprojection_px:.2f} px, "
                                   f"node {sorted(state.nodes)}, thiếu {state.missing}")

    def start(self) -> "Orchestrator":
        self._thread = threading.Thread(target=self._loop, daemon=True, name="orchestrator")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._halt.set()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "phase": self.phase.value, "enabled": self.enabled,
                "run": self.run.to_json() if self.run else None,
                "runs": [r.to_json() for r in list(self.runs)[-8:]],
                "queue": self._queue.qsize(), "overlay": dict(self.overlay),
                "decider": getattr(self.decider, "name", type(self.decider).__name__),
                "decider_error": getattr(self.decider, "last_error", None),
                "nozzles": {n: {"hb_ms": _num(self.link.hb_age_ms(n)), "pose_s": _num(self.pose_age(n)),
                                "blockers": self.vision.blockers(n)} for n in NOZZLES},
                "health": self.last_health,
            }

    # --- vòng chính -------------------------------------------------------------------------

    def _loop(self) -> None:
        next_health = self.clock()
        while not self._halt.is_set():
            try:
                t_rx, alert = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self.clock() >= next_health:
                    self._health_check()
                    next_health = self.clock() + self.cfg.health_period_s
                self._realert()
                continue
            if not self.enabled:
                self.events.emit("alert", f"bỏ qua cảnh báo {alert.n}: orchestrator đang tắt")
                continue
            if self.clock() - t_rx > self.cfg.alert_max_age_s:
                self.events.emit("alert", f"bỏ cảnh báo cũ từ {alert.n}", level="warn")
                continue
            self._abort.clear()
            self._handle(alert)

    def _realert(self) -> None:
        """Node chỉ gửi `/a` lúc chuyển sang báo động. Nếu lượt trước kết luận xong mà cảm biến vẫn
        vượt ngưỡng (lửa chưa tắt, hoặc bộ quyết định sai) thì tự tạo lại cảnh báo."""
        if not self.enabled:
            return
        lim = alarm_limits(self.site)
        for node, t_end in list(self._last_end.items()):
            if self.clock() - t_end < self.cfg.realert_s:
                continue
            last = self.sensors.samples(node, 3)
            if len(last) == 3 and all(s.t > t_end and (s.temp > lim["temp"] or s.gas > lim["gas"])
                                      for s in last):
                n = self._realerts.get(node, 0) + 1
                if n > self.cfg.realert_max:
                    del self._last_end[node]
                    self.events.emit("fault", f"{node} vẫn vượt ngưỡng sau {n - 1} lần xử lý lại: "
                                              "cần người kiểm tra", level="error", node=node)
                    continue
                self._realerts[node] = n
                self._last_end[node] = self.clock()
                self.events.emit("alert", f"{node} vẫn vượt ngưỡng sau lượt trước: xử lý lại "
                                          f"({n}/{self.cfg.realert_max})", level="warn", node=node)
                s = last[-1]
                self._queue.put((self.clock(), Alert(node, -1, "alert", s.temp, s.gas)))
            elif last and last[-1].temp <= lim["temp"] and last[-1].gas <= lim["gas"]:
                del self._last_end[node]
                self._realerts.pop(node, None)

    def _health_check(self) -> None:
        if self.vision.state is None:
            return
        try:
            h = self.vision.check(self.hub.frames(5))
        except Exception as e:  # noqa: BLE001
            self.events.emit("vision", f"kiểm tra camera lỗi: {e}", level="warn")
            return
        now = self.clock()
        for n in h.node_moved_m:
            if n not in h.stale:
                self.pose_time[n] = now
        self.last_health = {"camera_shift_px": h.camera_shift_px, "stale": h.stale,
                            "node_moved_cm": {n: round(m * 100, 2) for n, m in h.node_moved_m.items()}}
        if h.stale:
            self.events.emit("vision", f"hết hạn: {', '.join(h.stale)}", level="warn")

    def pose_age(self, node: str) -> float:
        if self.vision.blockers(node) or node not in self.pose_time:
            return float("inf")
        return self.clock() - self.pose_time[node]

    def _set(self, phase: Phase, note: str = "", active: list[str] | None = None) -> None:
        """Đổi pha. `active`: các vòi đang được làm việc trong pha này (mặc định không vòi nào), đổi
        cùng lúc với pha để snapshot không thấy pha mới với danh sách vòi của pha cũ."""
        with self._lock:
            self.phase = phase
            self.overlay["active"] = list(active or [])
            if self.run:
                self.run.phases.append((phase.value, time.time(), note))
        self.events.emit("phase", f"{phase.value}" + (f": {note}" if note else ""), phase=phase.value)

    def _check(self) -> None:
        if self._abort.is_set():
            raise Abort("dừng khẩn cấp")

    def _sleep(self, s: float) -> None:
        if self._abort.wait(s):
            raise Abort("dừng khẩn cấp")

    # --- một lượt ---------------------------------------------------------------------------

    def _handle(self, alert: Alert) -> None:
        self._run_ids += 1
        with self._lock:
            self.run = Run(self._run_ids, alert.n)
            self.overlay = {"active": [], "aims": {}, "spots": {}}
        run = self.run
        try:
            self._set(Phase.ALERT, f"node {alert.n}")
            run.outcome = self._process(run, alert.n)
        except Abort as e:
            run.outcome = "stopped"
            self._set(Phase.FAULT, str(e))
        except (LinkError, TimeoutError) as e:
            run.outcome = "fault"
            self._set(Phase.FAULT, str(e))
            self.events.emit("fault", str(e), level="error")
        except Exception as e:  # noqa: BLE001 - mọi lỗi lạ đều phải tắt bơm và về trạng thái an toàn
            run.outcome = "fault"
            self._set(Phase.FAULT, f"{type(e).__name__}: {e}")
            self.events.emit("fault", f"lỗi không lường trước: {type(e).__name__}: {e}", level="error")
        finally:
            self.link.stop()
            self.events.emit("run", f"lượt {run.id} kết thúc: {run.outcome}", outcome=run.outcome,
                             run=run.id, node=run.node, target=run.target, nozzle=run.nozzle,
                             attempts=run.attempts)
            with self._lock:
                self.runs.append(run)
                self.phase = Phase.IDLE
                self.overlay["active"] = []
            if run.outcome in ("extinguished", "ignored", "alarm_only"):  # đã gọi người thì thôi
                self._last_end[run.node] = self.clock()
            self.events.emit("phase", "IDLE", phase="IDLE")

    def _process(self, run: Run, alarm: str) -> str:
        # LOCALIZE: gộp vài khung, không lọc theo sensor (bộ quyết định thấy mọi bia)
        self._set(Phase.LOCALIZE)
        frames, size = [], None
        deadline = self.clock() + self.tcfg["localize_timeout_s"]
        while len(frames) < self.cfg.frames or (not any(frames) and self.clock() < deadline):
            self._check()
            frame = self.hub.fresh()
            size = frame.shape[1], frame.shape[0]
            frames.append(self.vision.targets(frame) if self.vision.state else [])
            if len(frames) > self.cfg.frames:
                frames.pop(0)
            self._sleep(self.cfg.frame_gap_s)
        tracks = aggregate(frames, size)
        self.overlay["tracks"] = [_track_json(t) for t in tracks]

        # DECIDE
        self._set(Phase.DECIDE)
        pivots = {n: self.vision.nodes[n].to_world(aiming.pivot_offset(self.site))
                  for n in NOZZLES if n in self.vision.nodes}
        nozzles = {n: {"hb_ms": self.link.hb_age_ms(n), "pose_s": self.pose_age(n),
                       "reach": self._reach_fn(n, pivots.get(n)), "pivot": pivots.get(n)} for n in NOZZLES}
        node_x = {n: float(p[0]) if (p := self._node_pos(n)) is not None else -1.0 for n in NOZZLES}
        obs, by_id = decide_obs(alarm, self.sensors, tracks, nozzles, node_x, len(frames),
                                alarm_limits(self.site))
        questions = questions_for(obs)
        dec = self.decider.decide(obs, questions)
        run.decisions.append({"stage": "decide", **dec.to_json()})
        action = dec.get("action")
        tid = candidate_id(dec.get("target") or "")
        nozzle = dec.get("nozzle") or alarm
        self.events.emit("decision", f"{dec.decider}: cháy thật {'có' if dec.get('real_fire') else 'không'}, {action}, "
                                     f"bia {tid}, vòi {nozzle} ({dec.latency_ms:.0f} ms)",
                         decision=dec.to_json(), run=run.id, obs=obs, questions=questions)
        if action == ACTIONS[2]:
            return "ignored"
        if action != ACTIONS[0] or tid is None or tid not in by_id:
            self._set(Phase.ALARM, "cháy thật nhưng không phun được" if action == ACTIONS[1]
                      else "không chọn được bia")
            return "alarm_only"
        target = by_id[tid].position
        run.target = {"id": tid, "x": round(float(target[0]), 3), "z": round(float(target[2]), 3)}
        self.overlay["target"] = run.target

        # chặn an toàn cứng; vòi được chọn bị chặn thì thử vòi còn lại
        if nozzle == BOTH:  # hai vòi: vòi nào bị chặn thì bỏ, còn một vòi thì phun như lượt một vòi
            blocked = {n: self._blocked(n, target, pivots) for n in nozzles_of(BOTH)}
            usable = [n for n, why in blocked.items() if not why]
            if not usable:
                self._set(Phase.ALARM, "bị chặn: " + "; ".join(
                    f"{n}: {'; '.join(why)}" for n, why in blocked.items()))
                return "alarm_only"
            if len(usable) == 1:
                bad = next(n for n in blocked if n not in usable)
                self.events.emit("safety", f"vòi {bad} bị chặn ({'; '.join(blocked[bad])}), "
                                           f"chỉ phun bằng {usable[0]}", level="warn")
                nozzle = usable[0]
        else:
            reasons = self._blocked(nozzle, target, pivots)
            if reasons:
                other = NOZZLES[1 - NOZZLES.index(nozzle)]
                if not self._blocked(other, target, pivots):
                    self.events.emit("safety", f"vòi {nozzle} bị chặn ({'; '.join(reasons)}), đổi sang {other}",
                                     level="warn")
                    nozzle = other
                else:
                    self._set(Phase.ALARM, "bị chặn: " + "; ".join(reasons))
                    return "alarm_only"
        run.nozzle = nozzle
        self.overlay["nozzle"] = nozzle
        return self._engage(run, obs, tid, target, nozzles_of(nozzle), pivots)

    def _use(self, run: Run, nozzles: list[str]) -> None:
        """Ghi vòi đang dùng (BOTH nếu còn hai vòi) vào lượt và overlay."""
        run.nozzle = BOTH if len(nozzles) > 1 else nozzles[0]
        self.overlay["nozzle"] = run.nozzle

    def _mark(self, key: str, node: str, point) -> None:
        """Ghi điểm (x, z) của `node` vào overlay[key] ({vòi: {"x", "z"}}). Thay cả dict con thay vì sửa
        tại chỗ, để dashboard đang dựng JSON từ snapshot không gặp dict đổi kích thước."""
        p = {"x": round(float(point[0]), 3), "z": round(float(point[2]), 3)}
        with self._lock:
            self.overlay[key] = {**self.overlay.get(key, {}), node: p}

    def _engage(self, run, obs, tid, target, nozzles: list[str], pivots: dict) -> str:
        """AIM/CORRECT/FIRE/VERIFY với một hoặc hai vòi. Mỗi vòi giữ điểm ngắm và độ lệch CORRECT
        cuối cùng của riêng nó; vòi bị bỏ (lúc AIM/CORRECT hoặc ngay trước khi phun) thì cả lượt chạy
        tiếp bằng vòi còn lại."""
        nozzles = list(nozzles)
        aim_points = {n: np.asarray(target, float).copy() for n in nozzles}
        misses: dict[str, float | None] = dict.fromkeys(nozzles)
        need_aim = True
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            attempt += 1
            run.attempts = attempt
            if need_aim:
                for n in list(nozzles):  # lần lượt: mỗi lúc chỉ một laser
                    self._check()
                    try:
                        aim_points[n], misses[n] = self._aim_and_correct(
                            run, n, pivots[n], target, aim_points[n], dual=len(nozzles) > 1)
                    except LinkError as e:
                        if len(nozzles) < 2:  # một vòi: lỗi ném thẳng (FAULT) như trước
                            raise
                        self.events.emit("safety", f"vòi {n} hỏng lúc ngắm ({e}), bỏ vòi này",
                                         level="warn")
                        nozzles.remove(n)
                        aim_points.pop(n)
                        misses.pop(n)
                        self._use(run, nozzles)

            # FIRE: ngắm lần cuối cho mọi vòi rồi mới bật bơm, để hai bơm chạy cùng lúc
            self._set(Phase.FIRE, f"lần {attempt}")
            cmds = self._final_aim(nozzles, target, pivots, aim_points)
            if len(cmds) < len(nozzles):
                nozzles = list(cmds)
                self._use(run, nozzles)
            dual = len(nozzles) > 1
            self._check()
            with self._lock:
                self.overlay["active"] = list(nozzles)  # từ đây các bơm chạy
            t_spray = self.clock()
            for n in nozzles:
                self.link.fire(n, cmds[n], "pump", self.acfg["fire_ms"])
            deadline = t_spray + self.acfg["fire_ms"] / 1000 + self.cfg.status_timeout_s
            status = "ok"
            for n in nozzles:
                st = self.link.wait(cmds[n], ("done", "fault", "rejected"),
                                    max(0.0, deadline - self.clock()))
                if st is None or st.st != "done":
                    status = "fault"
                if st is None:
                    self.events.emit("fault", f"{n} không báo xong phun", level="warn")
                elif st.st != "done" and dual:
                    self.events.emit("fault", f"{n} {st.st} khi phun ({st.err or 'không rõ'})", level="warn")

            # VERIFY: hai vòi thì coi như một lần phun (nhiệt trung bình, độ lệch lớn nhất)
            self._set(Phase.VERIFY)
            self._sleep(self.cfg.verify_wait_s)
            conf = []
            for _ in range(3):
                found = self.vision.targets(self.hub.fresh())
                near = [t.detection.conf for t in found
                        if np.hypot(*(t.position - target)[[0, 2]]) <= 0.05]
                conf.append(round(max(near), 2) if near else None)
            posts = []
            for n in nozzles:
                post = [s.temp for s in self.sensors.since(n, t_spray)][-3:]
                post = post or [s.temp for s in self.sensors.samples(n, 1)] or [0.0]
                posts.append([round(post[0], 1)] * (3 - len(post)) + [round(v, 1) for v in post])
            post = posts[0] if len(posts) == 1 else [round(float(np.mean(c)), 1) for c in zip(*posts)]
            marks = [misses[n] for n in nozzles if misses[n] is not None]
            mark_cm = max(marks) if marks else None
            vobs = verify_obs(obs, tid, run.nozzle, attempt, status, post, conf, mark_cm, self.sensors)
            vq = questions_for(vobs)
            dec = self.decider.decide(vobs, vq)
            run.decisions.append({"stage": "verify", "attempt": attempt, **dec.to_json()})
            nxt = dec.get("after_verify")
            self.events.emit("decision", f"{dec.decider} sau lần phun {attempt}: {nxt}",
                             decision=dec.to_json(), run=run.id, obs=vobs, questions=vq)
            if nxt == VERIFY[0]:
                return "extinguished"
            if nxt == VERIFY[3]:
                break
            need_aim = nxt == VERIFY[1]
        self._set(Phase.HUMAN, f"sau {attempt} lần phun")
        return "human"

    def _final_aim(self, nozzles: list[str], target, pivots: dict, aim_points: dict) -> dict[str, int]:
        """AIM cuối (đã bù góc tia nước) cho từng vòi, trả {vòi: id lệnh} để /fire. Hai vòi thì kiểm lại
        ngay trước khi phun: vòi nào mất heartbeat, bị chặn hoặc không trả lời lệnh ngắm bị bỏ và
        phun bằng vòi còn lại; một vòi thì lỗi ném thẳng (FAULT) như trước."""
        dual = len(nozzles) > 1
        cmds: dict[str, int] = {}
        for n in nozzles:
            try:
                if dual and (why := self._blocked(n, target, pivots)):
                    raise LinkError("; ".join(why))
                pan, tilt = aiming.angles(pivots[n], aim_points[n])
                tilt += aiming.water_tilt(self.site, n, aiming.horizontal_distance(pivots[n], aim_points[n]))
                cmds[n] = self._aim(n, pan, tilt)
            except LinkError as e:
                if not dual:
                    raise
                self.events.emit("safety", f"vòi {n} hỏng ngay trước khi phun ({e}), bỏ vòi này",
                                 level="warn")
        if not cmds:
            raise LinkError("không vòi nào ngắm được trước khi phun")
        return cmds

    def _aim(self, node: str, pan: float, tilt: float) -> int:
        self._check()
        if not aiming.limits(self.site, node).ok(pan, tilt):
            raise Abort(f"góc ({pan:.1f}, {tilt:.1f}) ngoài giới hạn của {node}")
        if self.link.hb_age_ms(node) > HB_MAX_MS:
            raise LinkError(f"mất heartbeat của {node}")
        cmd = self.link.aim(node, pan, tilt, self.acfg["aim_ttl_ms"])
        st = self.link.wait(cmd, timeout=self.cfg.status_timeout_s)
        if st is None:
            # gửi lại tối đa một lần (kế hoạch mục 4) với id mới
            cmd = self.link.aim(node, pan, tilt, self.acfg["aim_ttl_ms"])
            st = self.link.wait(cmd, timeout=self.cfg.status_timeout_s)
        if st is None:
            raise LinkError(f"{node} không trả lời lệnh ngắm")
        if st.st != "reached":
            raise LinkError(f"{node} {st.st} lệnh ngắm ({st.err or 'không rõ'})")
        self._check()
        return cmd

    def _aim_and_correct(self, run, node, pivot, target, aim_point, dual=False):
        """AIM rồi CORRECT bằng vết laser; trả (điểm ngắm đã bù, độ lệch đo được cuối cùng cm).
        `dual`: lượt hai vòi, ghi tên vòi vào pha và dòng sự kiện."""
        self._set(Phase.AIM, node if dual else "", active=[node])
        cmd = self._aim(node, *aiming.angles(pivot, aim_point))
        self._set(Phase.CORRECT, node if dual else "", active=[node])
        miss_cm = None
        for i in range(self.tcfg["correct_max_iters"]):
            off = self.hub.fresh()
            self.link.fire(node, cmd, "laser", self.cfg.laser_ms)
            self._sleep(0.15)  # chờ laser bật và khung mới
            on = self.hub.fresh()
            spot = self.vision.find_spot(on, off)
            self.link.wait(cmd, ("done", "fault"), self.cfg.laser_ms / 1000 + 1)
            if spot is None:
                self.events.emit("vision", "không thấy vết laser", level="warn")
                run.shots.append({"round": i + 1, "miss_cm": None, "nozzle": node})
                break
            miss = np.asarray(target, float) - spot
            miss_cm = float(np.hypot(miss[0], miss[2]) * 100)
            run.shots.append({"round": i + 1, "miss_cm": round(miss_cm, 2), "nozzle": node})
            self._mark("spots", node, spot)
            self.events.emit("correct", f"vòng {i + 1}: lệch {miss_cm:.1f} cm" + (f" ({node})" if dual else ""))
            if miss_cm < self.tcfg["correct_done_m"] * 100 or i == self.tcfg["correct_max_iters"] - 1:
                break
            aim_point = aim_point + miss * np.array([1.0, 0.0, 1.0])
            cmd = self._aim(node, *aiming.angles(pivot, aim_point))
        self._mark("aims", node, aim_point)
        return aim_point, miss_cm

    # --- tiện ích ---------------------------------------------------------------------------

    def _node_pos(self, n: str):
        p = self.vision.nodes.get(n)
        return None if p is None else p.position

    def _reach_fn(self, node, pivot):
        if pivot is None:
            return lambda x, z: False
        return lambda x, z: aiming.reachable(self.site, node, pivot, x, z)

    def _blocked(self, node: str, target, pivots: dict) -> list[str]:
        reasons = list(self.vision.blockers(node))
        if self.pose_age(node) > POSE_MAX_S and not reasons:
            reasons.append(f"pose của {node} quá cũ")
        if self.link.hb_age_ms(node) > HB_MAX_MS:
            reasons.append(f"mất heartbeat {node}")
        if not self.vision.on_board(np.asarray(target)):
            reasons.append("mục tiêu ngoài bảng")
        if node in pivots and not aiming.reachable(self.site, node, pivots[node], target[0], target[2]):
            reasons.append(f"{node} không với tới bia")
        return reasons


def _num(v: float):
    return None if v == float("inf") else round(min(v, CAP_AGE), 1)


def _track_json(t) -> dict:
    return {"x": round(float(t.position[0]), 3), "z": round(float(t.position[2]), 3), "conf": t.conf}
