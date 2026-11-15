"""State machine của orchestrator (kế hoạch mục 4), nối thị giác, cảm biến, bộ quyết định và vòi.

    IDLE -> ALERT -> LOCALIZE -> DECIDE -> ENGAGE -> (xong | HUMAN)
    (ENGAGE gộp AIM, CORRECT, FIRE, VERIFY: phun, bám tia, bám lửa, xác nhận dập cùng lúc; pha cũ còn trong enum)
                                       \\-> ALARM (chỉ báo động) hoặc về IDLE (bỏ qua)
    lỗi ở bất kỳ bước nào -> FAULT: gửi /stop, ghi nguyên nhân

Phun hai vòi (đáp án `nozzle` = BOTH): AIM và CORRECT lần lượt từng vòi (mỗi lúc một laser, để hai chấm
không lẫn nhau), FIRE bật hai bơm (hoặc hai laser, với link không bơm) cùng lúc, VERIFY coi như một lần phun. Vòi nào hỏng
giữa chừng (mất liên lạc, không trả lời lệnh ngắm) thì bị bỏ và lượt chạy tiếp bằng vòi còn lại.

Mô hình (hoặc luật) chỉ quyết định: có cháy thật không, làm gì, bia nào, vòi nào, sau khi phun
thì làm gì. Các chặn an toàn cứng luôn là luật ở đây: chưa commissioning hoặc pose hết hạn, mất
heartbeat, mục tiêu ngoài vùng bia, góc ngoài giới hạn, quá số lần thử, nút dừng khẩn cấp.
"""

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import ClassVar

import numpy as np

from ..decider.rules import HB_MAX_MS, POSE_MAX_S
from ..decider.state_text import ACTIONS, BOTH, NOZZLES, VERIFY, nozzles_of
from ..net.link import LinkError, NodeLink
from ..net.protocol import Alert
from ..vision import Vision
from . import aiming, selfcal
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
from .selfcal import NodeCal


class Phase(str, Enum):
    IDLE = "IDLE"
    ALERT = "ALERT"
    LOCALIZE = "LOCALIZE"
    DECIDE = "DECIDE"
    AIM = "AIM"
    CORRECT = "CORRECT"
    FIRE = "FIRE"
    VERIFY = "VERIFY"
    ENGAGE = "ENGAGE"  # phun, bám tia, bám lửa và xác nhận dập cùng lúc (thay AIM/CORRECT/FIRE/VERIFY)
    ALARM = "ALARM"  # cháy thật nhưng không phun được: chỉ báo động, chờ người
    HUMAN = "HUMAN"  # đã thử mà không xong: gọi người
    FAULT = "FAULT"


class Abort(Exception):
    """Dừng khẩn cấp hoặc lỗi không thể tiếp tục; thông điệp là lý do."""


@dataclass
class Settings:
    frames: int = 4  # số khung gộp cho một lần quyết định
    frame_gap_s: float = 0.25
    laser_ms: int = 700  # thời gian bật laser của một lần đo đơn lẻ (quét hiệu chuẩn, scripts/aim_point.py)
    # CORRECT giữ laser sáng liên tục (không chớp): mỗi vòng gia hạn hẹn giờ tắt thêm chừng này, nên nếu Pi chết
    # giữa chừng laser vẫn tự tắt sau ngần ấy (cộng với heartbeat của node)
    laser_hold_ms: int = 4000
    spot_pair_gap_s: float = 0.12  # hai khung bật cách nhau chừng này khi đo vết (lọc theo độ ổn định)
    laser_settle_s: float = 0.3  # sau khi servo báo reached: chờ khung camera mới có vết ổn định
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
    # mặc định cấp lớp: tự hiệu chuẩn tắt và chưa có cal (đối tượng dựng tối giản không qua __init__ trong test)
    selfcal_on = False
    scfg: ClassVar[dict] = {}
    cals: ClassVar[dict] = {}
    _lit: ClassVar[frozenset] = frozenset()  # vòi mà orchestrator đang giữ laser sáng (thay cả tập, không sửa tại chỗ)

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
        # ({vòi: {"x", "y", "z", "surface"}}: điểm ngắm đã bù và vết laser đo được của từng vòi)
        self.overlay: dict = {}
        self.pose_time = {n: clock() for n in vision.nodes}
        self.last_health: dict | None = None
        self.enabled = True  # False: nhận cảnh báo nhưng không xử lý (bảo trì)
        self._queue: queue.Queue = queue.Queue()
        self._abort = threading.Event()
        self._lock = threading.Lock()
        self._health_lock = threading.Lock()  # vòng chính và nút "Kiểm tra camera" không chạy chồng nhau
        self._run_ids = 0
        self._last_end: dict[str, float] = {}  # node -> lúc lượt gần nhất của node đó kết thúc
        self._realerts: dict[str, int] = {}  # node -> số lần tự xử lý lại liên tiếp
        # Cảnh báo và dấu "lượt trước đã xong" có từ lúc này trở về trước thì bỏ (dừng khẩn cấp, forget).
        # Chỉ ghi một số thực từ luồng khác; hai dict trên chỉ luồng orchestrator đọc và sửa.
        self._cutoff = float("-inf")
        self._thread: threading.Thread | None = None
        self._halt = threading.Event()
        # tự hiệu chuẩn vòi (selfcal.py): cal hiện dùng, mẫu (pose tag, góc lệnh, vết laser) gần đây của từng vòi
        self.scfg = site.get("selfcal", {}) or {}
        self.selfcal_on = bool(self.scfg.get("enabled", True))
        dropped: list[str] = []
        saved = selfcal.load_cals(site, dropped) if self.selfcal_on else {}
        for why in dropped:  # cal đã lưu vô lý (lần khớp xấu cũ): dùng số mồi của site thay vì tin nó
            events.emit("calib", f"bỏ cal đã lưu, dùng số mồi: {why}", level="warn")
        self.cals: dict[str, NodeCal] = {n: saved.get(n) or NodeCal.from_site(site, n) for n in NOZZLES}
        self._cal_state = {n: "ok" if self.cals[n].fitted else "idle" for n in NOZZLES}
        self._cal_samples = {n: deque(maxlen=60) for n in NOZZLES}
        self._cal_new = dict.fromkeys(NOZZLES, 0)  # số mẫu CORRECT mới từ lần khớp lại gần nhất
        self._cal_busy: set[str] = set()  # vòi đang khớp lại ở luồng nền
        self._cal_pending: dict[str, NodeCal] = {}  # kết quả khớp lại chờ hết lượt để áp dụng
        self._cal_requests: queue.Queue = queue.Queue()  # yêu cầu quét từ dashboard, chạy ở vòng chính
        self._startup_sweep_done = False

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
        """Dừng lượt đang chạy và quên mọi việc còn treo từ trước lúc dừng: cảnh báo trong hàng đợi và
        lượt "xử lý lại" (nếu không, cảm biến còn nóng làm `_realert` tự mở lượt mới vài giây sau).
        Cảnh báo mới từ node sau lúc này vẫn được xử lý như thường."""
        self._cutoff = self.clock()
        self._abort.set()
        self.link.stop()
        self.events.emit("fault", f"DỪNG: {reason}", level="error")

    def forget_followups(self) -> None:
        """Quên cảnh báo đang chờ và các lượt "xử lý lại" còn treo, không dừng gì (giữa các cảnh demo)."""
        self._cutoff = self.clock()

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
                "calib": {n: {**self.cals[n].to_json(), "state": self._cal_state[n]} for n in NOZZLES},
            }

    # --- vòng chính -------------------------------------------------------------------------

    def _loop(self) -> None:
        next_health = self.clock()
        while not self._halt.is_set():
            try:
                t_rx, alert = self._queue.get(timeout=0.5)
            except queue.Empty:
                self._auto_calibrate()
                if self._run_calibrations():
                    continue
                if self.clock() >= next_health:
                    self._health_check()
                    next_health = self.clock() + self.cfg.health_period_s
                self._realert()
                continue
            if not self.enabled:
                self.events.emit("alert", f"bỏ qua cảnh báo {alert.n}: orchestrator đang tắt")
                continue
            if t_rx <= self._cutoff:
                self.events.emit("alert", f"bỏ cảnh báo {alert.n} có trước lệnh dừng", level="warn")
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
            if t_end <= self._cutoff:  # lượt xong trước lệnh dừng: không tự làm lại
                del self._last_end[node]
                self._realerts.pop(node, None)
                continue
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

    def check_camera(self) -> bool:
        """Kiểm tra camera và pose node ngay (nút dashboard), thay vì đợi `health_period_s`. False nếu
        trạm đang xử lý một lượt (không đụng vào vision giữa lượt)."""
        if self.phase != Phase.IDLE:
            return False
        self._health_check()
        return True

    def _health_check(self) -> None:
        if self.vision.state is None and self.vision.reference_mode != "auto":
            return
        with self._health_lock:
            self._health_check_locked()

    def _health_check_locked(self) -> None:
        if self.vision.state is None and self.vision.reference_mode == "auto":
            try:
                state = self.vision.commission(self.hub.frames(5))
            except Exception as e:  # noqa: BLE001
                self.events.emit("vision", f"auto reference map chưa sẵn sàng: {e}", level="warn")
                return
            self.pose_time = {n: self.clock() for n in state.nodes}
            self.events.emit("vision", f"auto reference map: {sorted(state.nodes)}")
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
        if h.repose:
            r = h.repose
            try:
                path = self.vision.save()  # ghi đè file commissioning đã nạp để lần khởi động sau dùng pose mới
                saved = f", đã lưu {path.name}"
            except Exception as e:  # noqa: BLE001
                saved = f", lưu pose lỗi: {e}"
            self.events.emit(
                "vision", f"camera xê dịch {r['shift_px']} px, đã tính lại pose: dời "
                          f"{r['moved_m'] * 100:.1f} cm, xoay {r['rotated_deg']} độ, đo lại node "
                          f"{', '.join(r['nodes']) or 'không có'}{saved}",
                level="warn", repose=r)
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
            self._apply_pending()
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
        pivots = {n: self.cal(n).pivot_world(self.vision.nodes[n]) for n in NOZZLES if n in self.vision.nodes}
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
        run.target = {"id": tid, **_point_json(target, by_id[tid].surface)}
        self.overlay["target"] = run.target
        # hộp pixel của lửa trên nền: che khi tìm vết laser (laser chạm dưới chân lửa). Bia trên bảng thì laser rơi
        # vào giữa hộp thẻ nên không che.
        self._fire_box = by_id[tid].box if by_id[tid].surface == "floor" else None

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
        """Ghi điểm của `node` vào overlay[key] ({vòi: {"x", "y", "z", "surface"}}). Thay cả dict con
        thay vì sửa tại chỗ, để dashboard đang dựng JSON từ snapshot không gặp dict đổi kích thước."""
        p = _point_json(point, self._surface_of(point))
        with self._lock:
            self.overlay[key] = {**self.overlay.get(key, {}), node: p}

    def _engage(self, run, obs, tid, target, nozzles: list[str], pivots: dict) -> str:
        """ENGAGE: một pha liên tục thay AIM/CORRECT/FIRE/VERIFY. Phun, bám tia (visual servoing như
        `_aim_and_correct`), bám lửa và xác nhận lửa tắt chạy cùng lúc, `engage_hz` lần mỗi giây.

        Bật laser (và bơm, nếu node có) một lần rồi giữ tới hết lượt bằng cách gia hạn hẹn giờ. Mỗi chu kỳ: tìm lại
        lửa gần đích cũ (trong `engage_track_m`, làm mượt EMA), đo vết laser (lọc ổn định, che hộp lửa và chân
        trụ), cập nhật Broyden và quay một bước `engage_gain` x J^-1 e. Kết thúc:
        - đã dập: không thấy lửa gần đích liên tục `engage_clear_s` sau khi trúng tổng cộng >= `engage_min_hit_s`;
        - gọi người: trúng quá `engage_max_hit_s` mà lửa không yếu đi, cả pha quá `engage_timeout_s`, hoặc mất
          vết hay mất lửa liên tục quá `engage_lost_s`, hoặc bộ quyết định (hỏi mỗi `engage_verify_s`) bảo bỏ.
        Bộ quyết định chạy ở luồng nền để không làm chậm việc gia hạn laser. "done" chỉ được nhận khi camera
        cũng không thấy lửa trong vài khung cuối. Hai vòi: chỉ vòi đầu bám tia (hai chấm không phân biệt được),
        vòi sau ngắm theo mô hình rồi bật cùng (hạn chế). Bơm và laser dùng chung một góc: chưa bù tia nước."""
        from concurrent.futures import ThreadPoolExecutor

        nozzles = list(nozzles)
        tc = self.tcfg
        period = 1.0 / max(0.5, float(tc.get("engage_hz", 4)))
        track_m = float(tc.get("engage_track_m", 0.15))
        gain = float(tc.get("engage_gain", 0.7))
        clear_s = float(tc.get("engage_clear_s", 3.0))
        min_hit_s = float(tc.get("engage_min_hit_s", 1.0))
        max_hit_s = float(tc.get("engage_max_hit_s", 15.0))
        timeout_s = float(tc.get("engage_timeout_s", 30.0))
        lost_s = float(tc.get("engage_lost_s", 5.0))
        verify_s = float(tc.get("engage_verify_s", 3.0))
        tol_cm = float(tc.get("tolerance_m", 0.03)) * 100
        done_cm = float(tc["correct_done_m"]) * 100
        step_max = float(tc.get("correct_step_max_deg", 20.0))
        radius_m = float(tc.get("correct_search_m", 0.15))
        probe = float(tc.get("correct_probe_deg", 3.0))
        sweep_on = bool(tc.get("engage_sweep", True))
        sweep_step = float(tc.get("engage_sweep_step_m", 0.04))
        dwell_s = float(tc.get("engage_dwell_s", 0.4))
        serpentine = bool(tc.get("engage_serpentine", False))
        enter_m = float(tc.get("engage_sweep_enter_m", 0.06))
        hide_in_sweep = bool(tc.get("engage_sweep_hide_box", False))
        enter_meas, lost_pts_max = 4, 3
        escalate_s = float(tc.get("engage_escalate_s", 30.0))
        escalated = len(nozzles) > 1 or escalate_s <= 0  # chỉ gọi thêm vòi một lần, và chỉ khi lượt đang dùng một vòi
        pivots = dict(pivots)
        sec_pts: dict[str, np.ndarray] = {}  # điểm vòi phụ đang ngắm (dự đoán chấm của nó) để loại khỏi phép đo

        target = np.asarray(target, float)
        tgt = target.copy()  # đích đang bám (lửa có thể xê dịch)
        surface = self._surface_of(target)
        idx = np.flatnonzero(self._plane_mask(target))
        run.attempts = 1
        self._use(run, nozzles)
        self._set(Phase.ENGAGE, "ngắm, bật laser" + (" và bơm" if self._has_pump() else ""), active=nozzles)
        with self._lock:
            self.overlay["engage"] = {}  # bỏ lưới quét của lượt trước
        self._engage_state("aiming", None, 0.0, 0.0)

        cmds: dict[str, int] = {}
        for n in list(nozzles):  # ngắm theo mô hình; vòi nào hỏng thì bỏ (còn một vòi mà hỏng thì lỗi thẳng)
            try:
                cmds[n] = self._aim(n, *self._cmd(n, pivots[n], target))
            except LinkError as e:
                if len(nozzles) < 2:
                    raise
                self.events.emit("safety", f"vòi {n} hỏng lúc ngắm ({e}), bỏ vòi này", level="warn")
                self._drop_laser(n)
                nozzles.remove(n)
        self._use(run, nozzles)
        node = nozzles[0]  # vòi bám tia; vòi còn lại (nếu có) chỉ ngắm theo mô hình
        for n in nozzles[1:]:
            self._mark("aims", n, target)
        tag = self._tag(node)
        lim = aiming.limits(self.site, node)
        c = np.array(self._cmd(node, pivots[node], target), float)
        with self._lock:
            self.overlay["active"] = list(nozzles)
        self._sleep(self.cfg.laser_settle_s)
        off = self.hub.fresh()  # nền: laser tắt
        pump = self._has_pump()
        hold_s = self.acfg["fire_ms"] / 1000
        t0 = last_fire = last_pump = last_seen = last_t = last_log = self.clock()
        for n in nozzles:
            self._laser_on(n, cmds[n])
            if pump:
                self.link.fire(n, cmds[n], "pump", self.acfg["fire_ms"])
        J = self._model_jacobian(node, pivots[node], surface, idx, c, target)
        if J is None:
            J = np.zeros((2, 2))
        probes = [np.array([probe, 0.0]), np.array([0.0, probe])]
        center, prev = tgt.copy(), None
        hit_s, miss_cm, state, box = 0.0, None, "aiming", None
        conf_hist: list = []
        extra: dict = {}
        mode, n_meas, bias, sw, passes, lost_pts = "approach", 0, np.zeros(2), None, 0, 0
        pool, pending, attempt, next_verify = ThreadPoolExecutor(1), None, 1, t0 + verify_s
        outcome = None
        try:
            while outcome is None:
                self._check()
                now = self.clock()
                if now - t0 > timeout_s:
                    self.events.emit("engage", f"quá {timeout_s:g} s: gọi người", level="warn")
                    outcome = "human"
                    break
                if not escalated and now - t0 >= escalate_s:
                    escalated = True
                    self._escalate(run, nozzles, node, tgt if mode != "sweep" else sw["pts"][(sw["i"] + 4) % len(sw["pts"])][2],
                                   pivots, cmds, pump, hold_s, sec_pts)
                for n in nozzles:  # gia hạn: laser mỗi chu kỳ, bơm khi hẹn giờ còn dưới 30%
                    self._laser_on(n, cmds[n])
                if pump and now - last_pump > 0.7 * hold_s:
                    for n in nozzles:
                        self.link.fire(n, cmds[n], "pump", self.acfg["fire_ms"])
                    last_pump = now
                # bám lửa
                frame = self.hub.fresh()
                try:
                    found = self.vision.targets(frame) if self.vision.state else []
                except Exception as e:  # noqa: BLE001 - mất một khung không được làm hỏng pha phun
                    self.events.emit("vision", f"bám lửa lỗi: {e}", level="warn")
                    found = []
                near = [t for t in found if np.linalg.norm(t.position - tgt) <= track_m]
                if near:
                    t = min(near, key=lambda t: np.linalg.norm(t.position - tgt))
                    tgt = 0.5 * tgt + 0.5 * np.asarray(t.position, float)
                    last_fire = now
                    box = (t.detection.u, t.detection.v, t.detection.w, t.detection.h)
                    conf_hist.append(round(float(t.detection.conf), 2))
                else:
                    conf_hist.append(None)
                self._fire_box = box if surface == "floor" and (mode == "approach" or hide_in_sweep) else None
                dt, last_t = now - last_t, now
                if mode == "sweep":
                    # quét vùng cháy: mỗi điểm chuyển, chờ ổn định, đo một lần, rồi dừng `dwell_s`
                    if sw["phase"] == "dwell" and self.clock() - sw["t_arrive"] >= dwell_s:
                        sw["i"] += 1
                        sw["phase"] = "move"
                        if sw["i"] >= len(sw["pts"]):  # hết lưới: dựng lại vùng theo hộp lửa mới nhất
                            passes += 1
                            nsw = self._new_pass(node, pivots[node], box, passes, sweep_step, serpentine)
                            if nsw is None:
                                mode, n_meas, prev, center = "approach", 0, None, tgt.copy()
                                self.events.emit("engage", "không dựng lại được vùng cháy: về dắt vào điểm neo")
                            else:
                                sw = nsw
                    if mode == "sweep" and sw["phase"] == "move":
                        P = sw["pts"][sw["i"]][2]
                        c = np.array([min(max(v, lo), hi) for v, (lo, hi) in
                                      zip(np.array(self._cmd(node, pivots[node], P), float) + bias,
                                          (lim.pan, lim.tilt))])
                        cmds[node] = self._aim(node, *c)  # chờ reached rồi mới đo: không đo lúc đang chuyển
                        self._laser_on(node, cmds[node])
                        self._sleep(self.cfg.laser_settle_s)
                        spot = self._observe(node, P, radius_m * (2 if sw["first"] else 1), off)
                        sw["first"] = False
                        sec_then = list(sec_pts.values())  # chấm vòi phụ lúc đo (trước khi nó chuyển sang điểm mới)
                        if len(nozzles) > 1:  # vòi phụ đi trước vòi chính 4 điểm của lưới (gần nửa hàng, chấm cách >= 12 cm; không đo vết của nó)
                            nxt = sw["pts"][(sw["i"] + 4) % len(sw["pts"])][2]
                            for n2 in nozzles[1:]:
                                self._aim_extra(n2, pivots[n2], nxt, cmds, nozzles, sec_pts)
                        sw["t_arrive"], sw["phase"], sw["hit"] = self.clock(), "dwell", False
                        # hai chấm laser: vết sát chấm vòi phụ (gần nó hơn gần điểm quét, trong 8 cm) có thể là chấm
                        # của vòi phụ, không phân biệt được thì bỏ lần đo, giữ bù đã học, không tính là mất vết
                        ambiguous = False
                        if spot is not None and sec_then:
                            d_sec = min(float(np.linalg.norm(spot[:2] - q[:2])) for q in sec_then)
                            ambiguous = d_sec < 0.08 and d_sec < float(np.linalg.norm(spot[:2] - P[:2]))
                        if ambiguous:
                            self.events.emit("vision", f"vết sát chấm vòi phụ ({d_sec * 100:.0f} cm), bỏ lần đo này, cách điểm quét {float(np.linalg.norm(spot[:2] - P[:2])) * 100:.0f} cm", level="warn")
                        elif spot is None:
                            lost_pts += 1
                            sw["state"], miss_cm = "lost", None
                            run.shots.append({"round": len(run.shots) + 1, "miss_cm": None, "nozzle": node,
                                              "t": round(now - t0, 2)})
                            if lost_pts > lost_pts_max:
                                mode, n_meas, prev, center, lost_pts = "approach", 0, None, tgt.copy(), 0
                                self.events.emit("engage", f"mất vết quá {lost_pts_max} điểm: về dắt vào điểm neo",
                                                 level="warn")
                        else:
                            lost_pts, last_seen = 0, now
                            self._learn(node, tag, (float(c[0]), float(c[1])), spot)
                            e = P - spot
                            miss_cm = float(np.linalg.norm(e) * 100)  # lệch so với điểm quét, không phải điểm neo
                            if prev is not None:  # Broyden: học J từ các bước nhảy giữa điểm quét
                                dc, ds = c - prev[0], spot[idx] - prev[1]
                                if float(dc @ dc) > 0.02**2:
                                    J = J + np.outer(ds - J @ dc, dc) / float(dc @ dc)
                            prev = (c.copy(), spot[idx].copy())
                            if not self._ill(J):  # phần bù đã học: điểm quét sau dùng lệnh mô hình + bù
                                db = gain * np.linalg.solve(J, e[idx])
                                n_db = float(np.linalg.norm(db))
                                bias = bias + (db * (step_max / n_db) if n_db > step_max else db)
                            sw["hit"] = zone_contains(sw["zone"], spot, tol_cm / 100)
                            sw["state"] = "on_target" if sw["hit"] else "aiming"
                            run.shots.append({"round": len(run.shots) + 1, "miss_cm": round(miss_cm, 2),
                                              "nozzle": node, "t": round(now - t0, 2)})
                            self._mark("spots", node, spot)
                            if sw["hit"]:
                                hit_s += dt
                    elif mode == "sweep" and sw.get("hit"):
                        hit_s += dt  # đứng yên ở điểm đã đo trúng: tiếp tục tính trúng
                    if mode == "sweep":
                        state = sw["state"]
                        extra = {"row": sw["pts"][sw["i"]][0], "col": sw["pts"][sw["i"]][1]}
                        spot = False  # đã xử lý xong, bỏ qua nhánh bám tia điểm neo
                # bám tia (dắt vào điểm neo; cũng là chế độ cũ khi không quét)
                if mode == "approach":
                    spot = self._observe(node, center, radius_m * (2 if prev is None else 1), off, on=frame)
                if spot is False:  # nhánh quét đã xử lý xong khung này
                    pass
                elif spot is None:
                    state = "lost"
                    miss_cm = None
                    run.shots.append({"round": len(run.shots) + 1, "miss_cm": None, "nozzle": node,
                                      "t": round(now - t0, 2)})
                else:
                    last_seen = now
                    self._learn(node, tag, (float(c[0]), float(c[1])), spot)
                    e = tgt - spot
                    miss_cm = float(np.linalg.norm(e) * 100)
                    state = "on_target" if miss_cm < tol_cm else "aiming"
                    if state == "on_target":
                        hit_s += dt
                    run.shots.append({"round": len(run.shots) + 1, "miss_cm": round(miss_cm, 2),
                                      "nozzle": node, "t": round(now - t0, 2)})
                    self._mark("spots", node, spot)
                    if prev is not None:  # Broyden: J khớp cặp (dc, ds) vừa đo
                        dc, ds = c - prev[0], spot[idx] - prev[1]
                        if float(dc @ dc) > 0.02**2:
                            J = J + np.outer(ds - J @ dc, dc) / float(dc @ dc)
                    prev = (c.copy(), spot[idx].copy())
                    center = tgt.copy()
                    center[idx] = spot[idx]
                    if miss_cm >= done_cm:
                        ill = self._ill(J)
                        if ill and probes:
                            dc = probes.pop(0)
                        elif ill:  # không có J: theo mô hình (dời điểm ngắm đúng bằng độ lệch)
                            dc = np.array(self._cmd(node, pivots[node], tgt + (tgt - spot) * self._plane_mask(tgt)),
                                          float) - c
                        else:
                            dc = gain * np.linalg.solve(J, e[idx])
                        n_dc = float(np.linalg.norm(dc))
                        if n_dc > step_max:
                            dc = dc * (step_max / n_dc)
                        c_new = np.array([min(max(c[0] + dc[0], lim.pan[0]), lim.pan[1]),
                                          min(max(c[1] + dc[1], lim.tilt[0]), lim.tilt[1])])
                        if float(np.linalg.norm(c_new - c)) >= 0.02:
                            if not ill:
                                center[idx] = spot[idx] + J @ (c_new - c)
                            c = c_new
                            cmds[node] = self._aim(node, *c)
                            self._laser_on(node, cmds[node])
                    n_meas += 1
                    # đủ gần điểm neo (hoặc đã đo đủ lần): chuyển sang quét vùng cháy (chỉ bia trên sàn)
                    if sweep_on and surface == "floor" and box is not None \
                            and (miss_cm < enter_m * 100 or n_meas >= enter_meas):
                        nsw = self._new_pass(node, pivots[node], box, passes, sweep_step, serpentine)
                        if nsw is not None:
                            mode, lost_pts = "sweep", 0
                            bias = c - np.array(self._cmd(node, pivots[node], tgt), float)  # bù đã học khi dắt vào
                            sw = nsw
                            self.events.emit("engage", f"vết lệch {miss_cm:.1f} cm: bắt đầu quét vùng cháy")
                fwd = self._forward(node, pivots[node], surface, c)
                self._mark("aims", node, tgt if fwd is None else fwd)
                self._mark_target(tgt)
                clear = now - last_fire
                self._engage_state(state, miss_cm, hit_s, clear, extra if mode == "sweep" else None)
                if now - last_log >= 1.0:
                    last_log = now
                    self.events.emit("engage", f"{state}: lệch " + ("?" if miss_cm is None else f"{miss_cm:.1f}")
                                     + f" cm, trúng {hit_s:.1f} s, không thấy lửa {clear:.1f} s")
                # kết thúc
                if clear >= clear_s and hit_s >= min_hit_s:
                    self.events.emit("engage", "không còn thấy lửa gần đích: đã dập")
                    outcome = "extinguished"
                elif now - last_seen > lost_s or (clear > lost_s and hit_s < min_hit_s):
                    why = "mất vết laser" if now - last_seen > lost_s else "mất lửa mà chưa trúng"
                    self.events.emit("engage", f"{why} quá {lost_s:g} s: gọi người", level="warn")
                    outcome = "human"
                elif hit_s > max_hit_s and not self._weakening(conf_hist, period):
                    self.events.emit("engage", f"trúng {hit_s:.0f} s mà lửa không yếu đi: gọi người",
                                     level="warn")
                    outcome = "human"
                if outcome:
                    break
                # bộ quyết định (luồng nền)
                if pending is not None and pending[0].done():
                    fut, vobs, vq = pending
                    pending = None
                    try:
                        dec = fut.result()
                    except Exception as e:  # noqa: BLE001 - bộ quyết định lỗi thì bỏ qua, camera vẫn xét
                        self.events.emit("decision", f"bộ quyết định lỗi: {e}", level="warn")
                        dec = None
                    if dec is not None:
                        run.decisions.append({"stage": "verify", "attempt": attempt, **dec.to_json()})
                        nxt = dec.get("after_verify")
                        self.events.emit("decision", f"{dec.decider} lúc phun (lần {attempt}): {nxt}",
                                         decision=dec.to_json(), run=run.id, obs=vobs, questions=vq)
                        seen = [x for x in conf_hist[-3:] if x is not None]
                        if nxt == VERIFY[0] and not seen and hit_s > 0:
                            outcome = "extinguished"
                        elif nxt == VERIFY[3]:
                            outcome = "human"
                        attempt += 1
                        run.attempts = attempt
                elif pending is None and now >= next_verify:
                    next_verify = now + verify_s
                    posts = []
                    for n in nozzles:
                        post = [x.temp for x in self.sensors.since(n, t0)][-3:]
                        post = post or [x.temp for x in self.sensors.samples(n, 1)] or [0.0]
                        posts.append([round(post[0], 1)] * (3 - len(post)) + [round(v, 1) for v in post])
                    post = posts[0] if len(posts) == 1 else [round(float(np.mean(v)), 1) for v in zip(*posts)]
                    tail = ([None] * 3 + conf_hist)[-3:]
                    vobs = verify_obs(obs, tid, run.nozzle, attempt, "ok", post, tail, miss_cm, self.sensors)
                    vq = questions_for(vobs)
                    pending = (pool.submit(self.decider.decide, vobs, vq), vobs, vq)
                if outcome:
                    break
                self._sleep(max(0.0, period - (self.clock() - now)))
        finally:
            pool.shutdown(wait=False)
            self._fire_box = None
        if outcome == "human":
            self._set(Phase.HUMAN, f"sau {self.clock() - t0:.0f} s phun")
        return outcome

    def _aim_extra(self, n: str, pivot, point, cmds: dict, nozzles: list, sec_pts: dict) -> bool:
        """Vòi phụ ngắm `point` theo mô hình (không đo vết), laser sáng liên tục. Lỗi link thì bỏ vòi phụ."""
        lim = aiming.limits(self.site, n)
        c = [min(max(v, lo), hi) for v, (lo, hi) in zip(self._cmd(n, pivot, point), (lim.pan, lim.tilt))]
        try:
            cmds[n] = self._aim(n, *c)
            self._laser_on(n, cmds[n])
            sec_pts[n] = np.asarray(point, float).copy()
            return True
        except LinkError as e:
            self.events.emit("safety", f"vòi phụ {n} hỏng ({e}), bỏ vòi này", level="warn")
            self._drop_laser(n)
            sec_pts.pop(n, None)
            if n in nozzles:
                nozzles.remove(n)
            return False

    def _escalate(self, run, nozzles: list, node: str, tgt, pivots: dict, cmds: dict, pump: bool, hold_s: float,
                  sec_pts: dict):
        """Lửa chưa tắt sau `engage_escalate_s`: gọi thêm vòi còn lại nếu khỏe (heartbeat, pose) và không bị chặn.
        Vòi phụ ngắm theo mô hình vào điểm neo, laser (và bơm) sáng liên tục; chỉ vòi chính đo vết và bám tia."""
        escalate_s = float(self.tcfg.get("engage_escalate_s", 30.0))
        others = [n for n in NOZZLES if n not in nozzles]
        if not others:
            return
        other = others[0]
        tag = self._tag(other)
        if other not in pivots and tag is not None:
            pivots[other] = self.cal(other).pivot_world(tag)
        reasons = self._not_ready(other)
        reasons += [r for r in self._blocked(other, tgt, pivots) if r not in reasons]
        if reasons:
            self.events.emit("engage", f"lửa chưa tắt sau {escalate_s:g} s nhưng không gọi thêm vòi {other}: "
                                       + "; ".join(reasons), level="warn")
            return
        nozzles.append(other)
        if not self._aim_extra(other, pivots[other], tgt, cmds, nozzles, sec_pts):
            return
        if pump:
            self.link.fire(other, cmds[other], "pump", self.acfg["fire_ms"])
        self._use(run, nozzles)
        with self._lock:
            self.overlay["active"] = list(nozzles)
        self.events.emit("engage", f"lửa chưa tắt sau {escalate_s:g} s, gọi thêm vòi {other}")

    def _new_pass(self, node: str, pivot, box, passno: int, step: float, serpentine: bool) -> dict | None:
        """Dựng một lượt quét từ hộp lửa mới nhất `box` (u, v, w, h): chiếu hai góc ĐÁY hộp xuống sàn bằng
        `vision.localize` được bề ngang và mép gần (không chiếu góc trên: ngọn lửa cao, chiếu xuống sẽ ra quá xa).
        Mép xa = mép gần + `engage_depth_ratio` x bề ngang, theo hướng từ vòi tới lửa; cả hai kích thước kẹp
        [4, 40] cm. Ghi overlay `engage.pattern` và sự kiện `engage`. None nếu không chiếu được (vision giả, góc
        rơi ngoài sàn): khi đó giữ chế độ bám một điểm."""
        loc = getattr(self.vision, "localize", None)
        if loc is None or box is None:
            return None
        u, v, w, h = box
        try:
            left, right = loc((u - w / 2, v + h / 2)), loc((u + w / 2, v + h / 2))
        except Exception as e:  # noqa: BLE001 - chiếu lỗi thì quay về một điểm neo
            self.events.emit("vision", f"không chiếu được vùng cháy: {e}", level="warn")
            return None
        if left is None or right is None:
            return None
        left, right = np.asarray(left, float), np.asarray(right, float)
        m = (left + right) / 2
        fwd = m[:2] - np.asarray(pivot, float)[:2]
        if float(np.linalg.norm(fwd)) < 1e-6:
            return None
        fwd = fwd / np.linalg.norm(fwd)
        right_v = np.array([fwd[1], -fwd[0]])  # bên phải khi nhìn từ vòi về lửa (z hướng lên)

        def clamp(x):
            return min(max(float(x), ZONE_MIN_M), ZONE_MAX_M)

        width = clamp(abs(float((right - left)[:2] @ right_v)))
        depth = clamp(float(self.tcfg.get("engage_depth_ratio", 0.6)) * width)
        zone = {"m": np.array([m[0], m[1], 0.0]), "fwd": fwd, "right": right_v, "width": width, "depth": depth}
        pts = sweep_pattern(zone, step, serpentine)
        rows, cols = pts[-1][0] + 1, max(c for _, c, _ in pts) + 1
        with self._lock:
            self.overlay["engage"] = {**self.overlay.get("engage", {}), "rows": rows, "cols": cols,
                                      "pattern": [[round(float(p[0]), 3), round(float(p[1]), 3)] for _, _, p in pts],
                                      "zone_cm": [round(width * 100, 1), round(depth * 100, 1)]}
        self.events.emit("engage", f"lượt quét {passno + 1}: {rows} hàng x {cols} cột, vùng "
                                   f"{width * 100:.0f} x {depth * 100:.0f} cm")
        return {"zone": zone, "pts": pts, "i": 0, "phase": "move", "first": True, "hit": False,
                "state": "aiming", "t_arrive": 0.0}

    def _engage_state(self, state: str, miss_cm, hit_s: float, clear_s: float, extra: dict | None = None) -> None:
        """Trạng thái con của ENGAGE cho dashboard; ghi sự kiện `engage` khi đổi trạng thái. `extra`: hàng/cột
        đang quét; lưới (`pattern`, `rows`, `cols`) giữ lại từ lần dựng lượt quét."""
        old = self.overlay.get("engage", {})
        prev = old.get("state")
        info = {"state": state, "miss_cm": None if miss_cm is None else round(miss_cm, 1),
                "hit_s": round(hit_s, 1), "clear_s": round(clear_s, 1)}
        info.update({k: old[k] for k in ("pattern", "rows", "cols", "zone_cm") if k in old})
        info.update(extra or {})
        with self._lock:
            self.overlay["engage"] = info
        if prev is not None and prev != state:
            self.events.emit("engage", f"{prev} -> {state}")

    def _mark_target(self, tgt) -> None:
        """Đích đang bám (đã làm mượt) lên overlay."""
        with self._lock:
            self.overlay["target"] = {**self.overlay.get("target", {}), **_point_json(tgt, self._surface_of(tgt))}

    @staticmethod
    def _weakening(conf_hist: list, period: float) -> bool:
        """Lửa yếu đi: conf trung bình (khung thấy lửa) ~3 s cuối thấp hơn 70% của ~3 s đầu."""
        n = max(3, int(3 / max(period, 1e-3)))
        first = [c for c in conf_hist[:n] if c is not None]
        last = [c for c in conf_hist[-n:] if c is not None]
        return bool(first) and (not last or float(np.mean(last)) < 0.7 * float(np.mean(first)))

    def _final_aim(self, nozzles: list[str], target, pivots: dict, aim_points: dict,
                   held: dict | None = None) -> dict[str, int]:
        """AIM cuối cho từng vòi, trả {vòi: id lệnh} để /fire. Lệnh là lệnh CORRECT đã hội tụ của vòi đó (`held`:
        cái đang giữ laser, nên node không phải quay thêm, laser không giật) cộng phần bù tia nước nếu vòi có
        bơm. Vòi chưa có lệnh CORRECT (CORRECT không thấy vết) thì tính từ `aim_points`. Hai vòi thì kiểm lại
        ngay trước khi phun: vòi nào mất heartbeat, bị chặn hoặc không trả lời lệnh ngắm bị bỏ và
        phun bằng vòi còn lại; một vòi thì lỗi ném thẳng (FAULT) như trước."""
        dual = len(nozzles) > 1
        held = held or {}
        pump = self._has_pump()
        cmds: dict[str, int] = {}
        for n in nozzles:
            try:
                if dual and (why := self._blocked(n, target, pivots)):
                    raise LinkError("; ".join(why))
                # bù tia nước theo khoảng cách ngang (mặt phẳng XY) tới bia, đúng cho cả bảng lẫn nền. Bù là góc
                # HÌNH HỌC (độ thật của tia), còn lệnh tilt đo bằng độ lệnh: thật = tilt_gain x lệnh, nên cộng
                # bù/tilt_gain vào lệnh. Vòi không bơm thì không bù: laser thay cho tia nước và không rơi theo
                # quỹ đạo, bù sẽ làm chấm laser lệch khỏi bia đã CORRECT.
                water = (aiming.water_tilt(self.site, n, aiming.horizontal_distance(pivots[n], aim_points[n]))
                         if pump else 0.0)
                if n in held:
                    pan, tilt = held[n][0], held[n][1] + water / self._tilt_gain(n)
                else:
                    pan, tilt = self._cmd(n, pivots[n], aim_points[n], water)
                cmds[n] = self._aim(n, pan, tilt)
            except LinkError as e:
                if not dual:
                    raise
                self.events.emit("safety", f"vòi {n} hỏng ngay trước khi phun ({e}), bỏ vòi này",
                                 level="warn")
        if not cmds:
            raise LinkError("không vòi nào ngắm được trước khi phun")
        return cmds

    def _has_pump(self) -> bool:
        """Link có bơm không (IntegratedLink: không, chỉ laser). Link không khai báo thì coi là có."""
        return bool(getattr(self.link, "has_pump", True))

    def _tilt_gain(self, node: str) -> float:
        """Hệ số servo tilt (độ thật trên độ lệnh) đang dùng cho `node`; 1 khi không có pose tag (ngắm hình học thuần)."""
        if self._tag(node) is None:
            return 1.0
        g = self.cal(node).tilt_gain
        return g if abs(g) > 1e-6 else 1.0

    def _aim(self, node: str, pan: float, tilt: float) -> int:
        self._check()
        if not aiming.limits(self.site, node).ok(pan, tilt):
            raise Abort(f"góc ({pan:.1f}, {tilt:.1f}) ngoài giới hạn của {node}")
        if self.link.hb_age_ms(node) > HB_MAX_MS:
            raise LinkError(f"mất heartbeat của {node}")
        # link có giới hạn tốc độ servo (IntegratedLink) thì một lệnh có thể cần lâu hơn status_timeout_s
        wait_s = self.cfg.status_timeout_s + float(getattr(self.link, "max_aim_s", 0.0) or 0.0)
        cmd = self.link.aim(node, pan, tilt, self.acfg["aim_ttl_ms"])
        st = self.link.wait(cmd, timeout=wait_s)
        if st is None:
            # gửi lại tối đa một lần (kế hoạch mục 4) với id mới
            cmd = self.link.aim(node, pan, tilt, self.acfg["aim_ttl_ms"])
            st = self.link.wait(cmd, timeout=wait_s)
        if st is None:
            raise LinkError(f"{node} không trả lời lệnh ngắm")
        if st.st != "reached":
            raise LinkError(f"{node} {st.st} lệnh ngắm ({st.err or 'không rõ'})")
        self._check()
        return cmd

    def _laser_spot(self, node: str, cmd: int) -> np.ndarray | None:
        """Bật laser của `node` (đã ngắm xong lệnh `cmd`) và đo vết trên bảng/nền bằng cặp khung tắt/bật;
        None nếu không thấy vết."""
        off = self.hub.fresh()
        self.link.fire(node, cmd, "laser", self.cfg.laser_ms)
        self._sleep(0.15)  # chờ laser bật và khung mới
        on = self.hub.fresh()
        spot = self._find_spot(node, on, off)
        self.link.wait(cmd, ("done", "fault"), self.cfg.laser_ms / 1000 + 1)
        if spot is None:
            self.events.emit("vision", "không thấy vết laser", level="warn")
        return spot

    @staticmethod
    def _duplicate_spot(samples, pan: float, tilt: float, spot, tol_m: float = 0.01,
                        min_cmd_deg: float = 2.0) -> tuple | None:
        """(pan, tilt) của mẫu trước có vết cách `spot` dưới `tol_m` mà lệnh khác ít nhất `min_cmd_deg`: laser
        không thể rơi đúng chỗ cũ khi đã quay vài độ, nên đó là servo không quay hoặc khung chụp còn cũ."""
        p = np.asarray(spot, float)
        for _tag, p0, t0, s0 in samples:
            if abs(pan - p0) + abs(tilt - t0) >= min_cmd_deg and np.linalg.norm(p - np.asarray(s0, float)) < tol_m:
                return p0, t0
        return None

    def _find_spot(self, node: str, on, off, near=None, near_m: float = 0.15, on2=None, box=None):
        """Vết laser, bỏ qua vùng quanh đầu phát của mọi vòi (camera thấy đầu phát sáng đỏ khi bật) và chân trụ
        (đèn relay đỏ bật cùng laser). Bán kính che đầu `selfcal.head_mask_px` (mặc định 45; 0 là tắt), che chân
        `selfcal.foot_mask_px` (mặc định 60). `near` (điểm 3D dự đoán) và `near_m`: tìm quanh `near` (CORRECT, laser
        sáng liên tục, xem `Vision.find_spot`). `on2`: khung bật thứ hai để lọc theo độ ổn định; `box`: hộp pixel
        (u, v, w, h) của đám cháy, được che."""
        r = int(self.scfg.get("head_mask_px", 45))
        rf = int(self.scfg.get("foot_mask_px", 60))
        heads, feet = [], []
        for n in NOZZLES:
            tag = self._tag(n)
            if tag is None:
                continue
            if rf > 0 and getattr(tag, "position", None) is not None:
                feet.append(np.asarray(tag.position, float))
            if r > 0:
                try:
                    heads.append(self.cal(n).pivot_world(tag))
                except Exception as e:  # noqa: BLE001 - thiếu cal/pose thì không che, như trước
                    self.events.emit("calib", f"không che được đầu phát {n}: {e}", level="warn")
        kw = {}
        if heads:
            kw.update(exclude=heads, exclude_px=r)
        if near is not None:
            kw.update(near=near, near_m=near_m)
        if on2 is not None:
            kw.update(extra_on=on2)
            if feet:
                kw.update(foot=feet, foot_px=rf)
            if box is not None:
                kw.update(hide_boxes=[box])
        if not kw:
            return self.vision.find_spot(on, off)
        try:
            return self.vision.find_spot(on, off, **kw)
        except TypeError:  # vision giả trong test không nhận tham số che và vùng tìm
            return self.vision.find_spot(on, off)

    # --- laser sáng liên tục trong CORRECT và FIRE ---------------------------------------------

    def _laser_on(self, node: str, cmd: int) -> None:
        """Bật (hoặc gia hạn) laser của `node` thêm `laser_hold_ms`. Lệnh mới thay lệnh cũ mà không qua trạng thái
        tắt (link không được gửi OFF của lệnh cũ), nên gọi mỗi vòng CORRECT thì laser không chớp."""
        self.link.fire(node, cmd, "laser", self.cfg.laser_hold_ms)
        self._lit = self._lit | {node}

    def _laser_off(self, node: str, cmd: int) -> None:
        """Tắt laser mà không dừng servo (`stop` sẽ nhả servo): lệnh laser 1 ms thay hẹn giờ cũ và tắt ngay.
        Không làm gì nếu orchestrator không giữ laser của node này."""
        if node not in self._lit:
            return
        self._lit = self._lit - {node}
        self.link.fire(node, cmd, "laser", 1)

    def _drop_laser(self, node: str) -> None:
        """Vòi bị bỏ giữa lượt: tắt laser hết sức (không có lệnh ngắm hợp lệ để dùng `_laser_off`)."""
        if node in self._lit:
            self._lit = self._lit - {node}
            try:
                self.link.stop(node)
            except Exception as e:  # noqa: BLE001 - vòi đã hỏng; lượt kết thúc vẫn gọi link.stop()
                self.events.emit("fault", f"không tắt được laser {node}: {e}", level="warn")

    # --- CORRECT: visual servoing trong không gian lệnh ----------------------------------------

    def _forward(self, node: str, pivot, surface: str, c) -> np.ndarray | None:
        """Mô hình tiến của `node`: điểm 3D laser rơi trên mặt phẳng `surface` khi ra lệnh c = (pan, tilt), theo cal
        đang dùng (không có pose tag thì hình học thuần: tâm quay `pivot`, hướng `_yaw`, hệ số 1)."""
        surfaces = selfcal.surfaces_of(self.site)
        tag = self._tag(node)
        if tag is not None:
            return selfcal.forward_spot(self.cal(node), tag, float(c[0]), float(c[1]), surface, surfaces)
        return selfcal.plane_spot(pivot, selfcal.direction_of(self._yaw(node), float(c[0]), float(c[1])),
                                  surface, surfaces)

    def _model_jacobian(self, node: str, pivot, surface: str, idx, c, target, h: float = 1.0):
        """J (2x2, mét trên độ lệnh) của vết trên mặt `surface` theo (pan, tilt), bằng sai phân trung tâm của mô
        hình tiến quanh `c`; `idx` là hai thành phần của mặt (sàn: x, y; bảng: x, z). Tia tại `c` không chạm mặt thì
        lấy quanh lệnh hướng vào `target` (luôn chạm). None nếu vẫn không tính được hoặc không hữu hạn."""
        for c0 in (np.asarray(c, float), np.asarray(self._cmd(node, pivot, target), float)):
            cols = []
            for d in (np.array([h, 0.0]), np.array([0.0, h])):
                p, m = self._forward(node, pivot, surface, c0 + d), self._forward(node, pivot, surface, c0 - d)
                if p is None or m is None:
                    break
                cols.append((p - m)[idx] / (2 * h))
            else:
                J = np.column_stack(cols)
                if np.all(np.isfinite(J)):
                    return J
        return None

    @staticmethod
    def _ill(J) -> bool:
        """J suy biến hoặc điều kiện xấu: không đủ tin để đảo."""
        return not np.all(np.isfinite(J)) or abs(np.linalg.det(J)) < 1e-9 or np.linalg.cond(J) > 1e3

    def _observe(self, node: str, center, radius_m: float, off, on=None):
        """Một khung "bật" mới, tìm vết so với khung nền `off` trong vòng quanh `center`; không thấy thì nới bán
        kính 2 rồi 4 lần (cùng một khung). None nếu không thấy."""
        on = self.hub.fresh() if on is None else on
        self._sleep(getattr(self.cfg, "spot_pair_gap_s", 0.0))
        on2 = self.hub.fresh()  # khung bật thứ hai: chỉ pixel tăng ở cả hai khung mới là vết laser
        box = getattr(self, "_fire_box", None)
        for k in (1, 2, 4):
            spot = self._find_spot(node, on, off, near=center, near_m=radius_m * k, on2=on2, box=box)
            if spot is not None:
                return np.asarray(spot, float)
        return None

    def _aim_and_correct(self, run, node, pivot, target, aim_point, dual=False):
        """AIM rồi CORRECT bằng vết laser; trả (điểm ngắm hiệu dụng, độ lệch đo được cuối cùng cm, góc lệnh
        (pan, tilt) đang giữ laser). `dual`: lượt hai vòi, ghi tên vòi vào pha và dòng sự kiện.

        CORRECT là visual servoing trong không gian lệnh. Biến điều khiển là góc lệnh c = (pan, tilt), phép đo là
        vết laser s trên mặt của bia (sàn: x, y; bảng: x, z), sai lệch e = bia - s. Jacobian J = ds/dc (2x2, m/độ)
        khởi tạo bằng sai phân của mô hình tiến theo cal hiện tại, rồi cập nhật Broyden sau mỗi lần đo
        (J += ((ds - J dc) dc^T) / |dc|^2) nên học ngay hệ số servo và hướng thật của node dù cal sai nặng; bước
        dc = J^-1 e, kẹp `correct_step_max_deg` và giới hạn góc. (Cách cũ dời điểm ngắm 3D đúng bằng độ lệch
        đo, tức giả định mô hình góc đúng; cal sai thì chấm nhích một phần nhỏ mỗi vòng và không hội tụ.)

        Laser sáng liên tục từ vòng đo đầu tới hết FIRE (không có khung bật/tắt mỗi vòng): khung nền chụp một lần
        lúc laser tắt, mỗi vòng gia hạn laser, ngắm, chờ reached cộng `laser_settle_s`, chụp khung mới và tìm vết
        quanh chỗ J dự đoán (lửa chập chờn làm kênh đỏ đổi giữa các khung nên không tìm cả ảnh). Hai vòi: tắt laser
        vòi này khi xong để chấm hai vòi không lẫn nhau (hạn chế: giữa CORRECT vòi này và FIRE laser của vòi
        này tắt); FIRE bật lại cả hai."""
        target = np.asarray(target, float)
        self._set(Phase.AIM, node if dual else "", active=[node])
        c = np.array(self._cmd(node, pivot, aim_point), float)
        cmd = self._aim(node, *c)
        self._set(Phase.CORRECT, node if dual else "", active=[node])
        tc = self.tcfg
        max_iters = int(tc["correct_max_iters"])
        done_cm = float(tc["correct_done_m"]) * 100
        step_max = float(tc.get("correct_step_max_deg", 20.0))
        radius_m = float(tc.get("correct_search_m", 0.15))
        lim = aiming.limits(self.site, node)
        surface = self._surface_of(target)
        idx = np.flatnonzero(self._plane_mask(target))
        tag = self._tag(node)
        probe = float(tc.get("correct_probe_deg", 3.0))
        probes = [np.array([probe, 0.0]), np.array([0.0, probe])]  # J không dùng được: đo thật từng cột
        J = self._model_jacobian(node, pivot, surface, idx, c, target)
        if J is None:
            J = np.zeros((2, 2))  # Broyden sẽ điền dần từ hai bước dò
        center, prev, best = target, None, None  # prev: (c, s2); best: (c, s2, miss_cm)
        miss_cm: float | None = None
        aim_pt = np.asarray(aim_point, float)
        self._sleep(self.cfg.laser_settle_s)
        off = self.hub.fresh()  # nền: laser tắt, chụp trước khi bật lần đầu
        for i in range(max_iters):
            self._laser_on(node, cmd)  # lần đầu bật, các lần sau gia hạn; không tắt giữa các vòng
            self._sleep(self.cfg.laser_settle_s)
            spot = self._observe(node, center, radius_m * (2 if i == 0 else 1), off)
            if spot is not None and prev is not None and not self._ill(J):
                pred = prev[1] + J @ (c - prev[0])
                dev = float(np.linalg.norm(spot[idx] - pred))
                moved = float(np.linalg.norm(pred - prev[1]))
                if dev > 3 * moved and dev > 0.15:  # xa dự đoán quá: có thể là lửa chập chờn, đo lại một lần
                    self.events.emit("vision", f"vết lệch {dev * 100:.0f} cm so với dự đoán, đo lại", level="warn")
                    self._sleep(self.cfg.laser_settle_s)
                    again = self._observe(node, center, radius_m * 2, off)
                    if again is not None:
                        spot = again
            if spot is None:
                self.events.emit("vision", "không thấy vết laser", level="warn")
                run.shots.append({"round": i + 1, "miss_cm": None, "nozzle": node})
                break
            self._learn(node, tag, (float(c[0]), float(c[1])), spot)
            miss = target - spot
            miss_cm = float(np.linalg.norm(miss) * 100)  # 3D: bia và vết đều nằm trên bảng hoặc nền
            run.shots.append({"round": i + 1, "miss_cm": round(miss_cm, 2), "nozzle": node})
            self._mark("spots", node, spot)
            self.events.emit("correct", f"vòng {i + 1}: lệch {miss_cm:.1f} cm" + (f" ({node})" if dual else ""))
            if best is None or miss_cm < best[2]:
                best = (c.copy(), spot[idx].copy(), miss_cm)
            if prev is not None:  # Broyden: J mới khớp đúng cặp (dc, ds) vừa đo
                dc, ds = c - prev[0], spot[idx] - prev[1]
                if float(dc @ dc) > 0.02**2:
                    J = J + np.outer(ds - J @ dc, dc) / float(dc @ dc)
            prev = (c.copy(), spot[idx].copy())
            if miss_cm < done_cm or i == max_iters - 1:
                break
            ill = self._ill(J)
            if ill and probes:
                dc = probes.pop(0)
            elif ill:  # không có J: theo mô hình như cách cũ (dời điểm ngắm đúng bằng độ lệch)
                aim_pt = aim_pt + miss * self._plane_mask(target)
                dc = np.array(self._cmd(node, pivot, aim_pt), float) - c
            else:
                dc = np.linalg.solve(J, miss[idx])
            n = float(np.linalg.norm(dc))
            if n > step_max:
                dc = dc * (step_max / n)
            c_new = np.array([min(max(c[0] + dc[0], lim.pan[0]), lim.pan[1]),
                              min(max(c[1] + dc[1], lim.tilt[0]), lim.tilt[1])])
            if float(np.linalg.norm(c_new - c)) < 0.02:
                break  # kẹt ở giới hạn góc: không còn bước nào
            center = target.copy()
            center[idx] = spot[idx] + J @ (c_new - c) if not ill else target[idx]
            c = c_new
            cmd = self._aim(node, *c)  # laser vẫn sáng khi servo quay
        if best is not None and miss_cm is not None and best[2] < miss_cm \
                and float(np.linalg.norm(best[0] - c)) > 0.02:
            c, miss_cm = best[0], best[2]  # vòng cuối tệ hơn vòng tốt nhất: quay về lệnh đã đo tốt nhất
            cmd = self._aim(node, *c)
        # điểm ngắm hiệu dụng: chỗ mô hình tin lệnh c rơi vào (cho overlay và khoảng cách bù tia nước)
        fwd = self._forward(node, pivot, surface, c)
        aim_pt = target if fwd is None else fwd
        if dual:
            self._laser_off(node, cmd)  # lần lượt từng vòi: laser vòi này không được lẫn với vòi sau
        self._mark("aims", node, aim_pt)
        return aim_pt, miss_cm, (float(c[0]), float(c[1]))

    # --- tự hiệu chuẩn vòi ------------------------------------------------------------------

    def cal(self, node: str) -> NodeCal:
        """Cal đang dùng của `node`, hướng đầu lấy theo `yaw_deg` hiện tại của site (nếu có)."""
        cfg = (self.site["actuator"].get("nodes", {}) or {}).get(node) or {}
        yaw = cfg.get("yaw_deg")
        base = self.cals.get(node) or NodeCal.from_site(self.site, node)
        return replace(base, heading_deg=None if yaw is None else float(yaw))

    def _tag(self, node: str):
        return getattr(self.vision, "nodes", {}).get(node)

    def _cmd(self, node: str, pivot, point, extra_tilt: float = 0.0) -> tuple[float, float]:
        """Góc lệnh (pan, tilt) để vòi `node` chiếu tới `point`, theo cal đã học (không có pose tag thì theo
        hình học thuần như trước)."""
        tag = self._tag(node)
        if tag is None:
            pan, tilt = aiming.angles(pivot, point, self._yaw(node))
            return pan, tilt + extra_tilt
        return selfcal.command_for(self.cal(node), tag, point, extra_tilt)

    def _learn(self, node: str, tag, ang, spot) -> None:
        """Ghi một vết laser của CORRECT làm mẫu; đủ `refit_every` mẫu mới thì khớp lại ở luồng nền."""
        if not (self.selfcal_on and self.scfg.get("learn_from_correct", True)) or tag is None:
            return
        self._cal_samples[node].append((tag, float(ang[0]), float(ang[1]), np.asarray(spot, float).copy()))
        self._cal_new[node] += 1
        if self._cal_new[node] >= int(self.scfg.get("refit_every", 4)) and node not in self._cal_busy:
            self._cal_new[node] = 0
            self._cal_busy.add(node)
            threading.Thread(target=self._refit, args=(node, list(self._cal_samples[node])), daemon=True,
                             name=f"selfcal-{node}").start()

    def _refit(self, node: str, samples: list) -> None:
        try:
            cal = selfcal.fit(samples, NodeCal.from_site(self.site, node), self.site,
                              baseline=self.cals[node])
            if cal.fitted:
                self._offer_cal(node, cal, "CORRECT")
            elif "vô lý" in cal.note:
                self.events.emit("calib", f"{node}: {cal.note}", level="warn", node=node)
        except Exception as e:  # noqa: BLE001 - học nền hỏng không được ảnh hưởng lượt phun
            self.events.emit("calib", f"{node}: khớp lại lỗi {type(e).__name__}: {e}", level="warn", node=node)
        finally:
            self._cal_busy.discard(node)

    def _offer_cal(self, node: str, cal: NodeCal, source: str) -> None:
        """Áp dụng cal mới ngay nếu trạm rảnh, không thì để tới hết lượt (đổi cal giữa lượt làm điểm ngắm đã
        bù của lượt đó sai)."""
        with self._lock:
            if self.phase != Phase.IDLE or self._cal_state[node] == "sweeping":
                self._cal_pending[node] = cal
                return
        self._apply_cal(node, cal, source)

    def _apply_pending(self) -> None:
        for node, cal in list(self._cal_pending.items()):
            del self._cal_pending[node]
            self._apply_cal(node, cal, "CORRECT")

    def _apply_cal(self, node: str, cal: NodeCal, source: str) -> None:
        old = self.cals[node]
        prior = NodeCal.from_site(self.site, node)
        self.cals[node] = cal
        self._cal_state[node] = "ok"
        d_prior = (np.array(cal.pivot) - np.array(prior.pivot)) * 100
        try:
            selfcal.save_cals(self.site, self.cals)
            saved = ""
        except OSError as e:
            saved = f", lưu file lỗi: {e}"
        moved = float(np.linalg.norm((np.array(cal.pivot) - np.array(old.pivot)) * 100))
        if (source == "CORRECT" and moved < 0.5 and abs(cal.yaw0_deg - old.yaw0_deg) < 0.2
                and abs(cal.tilt0_deg - old.tilt0_deg) < 0.2):
            return  # đổi không đáng kể: không làm đầy nhật ký
        self.events.emit(
            "calib", f"{node} ({source}): rms {cal.rms_before_cm:.2f} -> {cal.rms_cm:.2f} cm với {cal.n_samples} mẫu, "
                     f"yaw0 {cal.yaw0_deg:+.2f}, tilt0 {cal.tilt0_deg:+.2f} độ, hệ số pan {cal.pan_gain:.3f} "
                     f"tilt {cal.tilt_gain:.3f}, pivot so với prior {d_prior[0]:+.1f}, {d_prior[1]:+.1f}, "
                     f"{d_prior[2]:+.1f} cm ({np.linalg.norm(d_prior):.1f} cm); {cal.note}{saved}",
            node=node, stage="result", source=source, rms_before_cm=cal.rms_before_cm, rms_cm=cal.rms_cm,
            n_samples=cal.n_samples, pivot=list(cal.pivot), pivot_delta_cm=[round(float(v), 2) for v in d_prior],
            yaw0_deg=cal.yaw0_deg, tilt0_deg=cal.tilt0_deg)

    def request_calibration(self, nodes: list[str]) -> None:
        """Xếp yêu cầu quét hiệu chuẩn; vòng chính chạy khi trạm rảnh và không có cảnh báo chờ."""
        for n in nodes:
            self._cal_requests.put(n)

    def _auto_calibrate(self) -> None:
        """Một lần sau commissioning, lúc rảnh: xếp yêu cầu quét cho mọi vòi."""
        if (self._startup_sweep_done or not self.selfcal_on or not self.scfg.get("startup_sweep", True)
                or not self.enabled or self.vision.state is None):
            return
        self._startup_sweep_done = True
        self.request_calibration(list(NOZZLES))

    def _run_calibrations(self) -> bool:
        """Chạy các yêu cầu quét đang chờ (ở luồng orchestrator); True nếu đã chạy gì đó."""
        ran = False
        while self._queue.empty() and not self._halt.is_set():
            try:
                node = self._cal_requests.get_nowait()
            except queue.Empty:
                break
            self.calibrate(node)
            ran = True
        return ran

    def _not_ready(self, node: str) -> list[str]:
        reasons = list(self.vision.blockers(node))
        if not reasons and self.pose_age(node) > POSE_MAX_S:
            reasons.append(f"pose của {node} quá cũ")
        if self.link.hb_age_ms(node) > HB_MAX_MS:
            reasons.append(f"mất heartbeat {node}")
        if self._tag(node) is None:
            reasons.append(f"chưa có pose của {node}")
        return reasons

    def _sweep_refs(self) -> dict:
        """Tâm (x, y) các tag tham chiếu trên sàn: auto lấy từ bản đồ tag đang nhìn thấy, fixed lấy từ
        site["tags"]["reference"]["positions"]."""
        rmap = getattr(self.vision, "reference_map", None)
        if getattr(self.vision, "reference_mode", "fixed") == "auto":
            return {} if rmap is None else {i: np.asarray(p, float)[:2] for i, p in rmap.references.items()}
        pos = (self.site.get("tags", {}).get("reference", {}) or {}).get("positions", {}) or {}
        return {i: np.asarray(p, float)[:2] for i, p in pos.items() if p is not None}

    def _sweep_hull(self) -> np.ndarray:
        """Vùng bao lồi (k x 2) của các tag tham chiếu; rỗng nếu chưa đủ 3 tag."""
        return selfcal.convex_hull(list(self._sweep_refs().values()))

    def _sweep_targets(self, node: str, hull: np.ndarray) -> list:
        """Đích quét: tâm từng tag tham chiếu (tag gần vòi trước), cộng tâm tag node khác nếu nằm trên sàn và
        trong khung (nới lề). Mỗi đích là (tên, điểm 3D)."""
        tag = self._tag(node)
        base = np.asarray(self.cal(node).pivot_world(tag), float)[:2]
        outside = float(self.scfg.get("sweep_outside_m", 0.05))
        cand = [(f"tag {i}", np.array([*p, 0.0])) for i, p in self._sweep_refs().items()]
        for n in NOZZLES:
            t = self._tag(n)
            if n != node and t is not None and getattr(t, "position", None) is not None:
                q = np.asarray(t.position, float)
                if abs(q[2]) <= 0.03 and selfcal.hull_margin(q[:2], hull) >= -outside:
                    cand.append((f"tag {getattr(t, 'tag_id', n)}", np.array([q[0], q[1], 0.0])))
        return sorted(cand, key=lambda t: float(np.linalg.norm(t[1][:2] - base)))

    def _guided_sweep(self, node: str, hull: np.ndarray, targets: list) -> list:
        """Quét dẫn đường tới tâm từng tag (`targets`: [(tên, điểm 3D)], gần vòi trước); laser chỉ đi trong khung tag tham chiếu
        (`hull`, nới `selfcal.sweep_outside_m` = 0,05).
        1) Điểm đầu: lệnh tới tâm khung theo cal hiện tại (bỏ qua nếu cal vô lý); không có vết trong khung thì
        thử số mồi (`NodeCal.from_site`) rồi hạ tilt 5, 10, 15 độ quanh pan đó; không bao giờ nâng tilt khi chưa
        có vết. 2) Từ vết đã thấy, đi tới từng đích bằng visual servoing (J từ mô hình, cập nhật Broyden, tối đa
        `selfcal.sweep_max_steps` = 8 bước mỗi tag, dừng khi lệch dưới 1,5 cm, kẹp `selfcal.sweep_step_max_deg` = 6 độ). Mỗi vết đo được là một mẫu. 3) Vết ra ngoài khung hoặc mất
        vết: về lệnh tốt gần nhất, giảm nửa bước, hai lần liền thì bỏ đích. Tilt không bao giờ vượt tilt lệnh tốt
        gần nhất quá 6 độ. Trả danh sách mẫu (tag, pan, tilt, vết)."""
        sc = self.scfg
        step_max = float(sc.get("sweep_step_max_deg", 6.0))
        outside = float(sc.get("sweep_outside_m", 0.05))
        lim = aiming.limits(self.site, node)
        tag = self._tag(node)
        prior = NodeCal.from_site(self.site, node)
        centre = np.array([*hull.mean(axis=0), 0.0])
        samples: list = []
        good: list = [None]  # [(c, spot)] của vết tốt gần nhất

        def clamp(c, tilt_cap):
            return np.array([min(max(float(c[0]), lim.pan[0]), lim.pan[1]),
                             min(max(float(c[1]), lim.tilt[0]), min(lim.tilt[1], tilt_cap))])

        def in_region(spot, tol):
            return spot is not None and abs(float(spot[2])) <= 0.05 and selfcal.hull_margin(spot[:2], hull) >= -tol

        radius_m = float(self.tcfg.get("correct_search_m", 0.15))
        self._sleep(self.cfg.laser_settle_s)
        off = self.hub.fresh()  # khung nền: laser tắt, chụp một lần trước khi bật

        def shoot(c, center=None):
            """Ngắm c (laser đang sáng liên tục, không tắt giữa các lần đo), chờ ổn định, đo vết bằng hai khung bật
            quanh `center` (None: cả ảnh); thêm mẫu nếu thấy. Trả vết hoặc None."""
            if not self._queue.empty():
                raise Abort("có cảnh báo, nhường cho xử lý cháy")
            cmd = self._aim(node, float(c[0]), float(c[1]))
            self._laser_on(node, cmd)  # lần đầu bật, các lần sau gia hạn (lệnh mới thay lệnh cũ, không OFF)
            self._sleep(self.cfg.laser_settle_s)
            spot = self._observe(node, center, radius_m, off)
            if spot is None:
                self.events.emit("vision", "không thấy vết laser", level="warn")
                return None
            spot = np.asarray(spot, float)
            if self._duplicate_spot(samples, float(c[0]), float(c[1]), spot):
                self.events.emit("calib", f"{node}: bỏ vết ({spot[0]:.3f}, {spot[1]:.3f}) trùng vết cũ dù lệnh khác "
                                 "(servo không quay hoặc khung cũ)", level="warn", node=node)
                return None
            samples.append((self._tag(node), float(c[0]), float(c[1]), spot))
            return spot

        # 1) điểm khởi đầu an toàn
        starts = []
        if not selfcal.cal_violations(self.cal(node), prior, self.site):
            starts.append(("cal", np.array(selfcal.command_for(self.cal(node), tag, centre), float)))
        else:
            self.events.emit("calib", f"{node}: cal hiện tại vô lý, bắt đầu từ số mồi", level="warn", node=node)
        pc = np.array(selfcal.command_for(prior, tag, centre), float)
        starts.append(("số mồi", pc))
        starts += [(f"số mồi hạ tilt {d} độ", pc - [0.0, d]) for d in (5, 10, 15)]
        tilt_cap = starts[0][1][1] + 6.0
        tried: list = []
        for label, c0 in starts:
            c = clamp(c0, tilt_cap)
            if any(np.linalg.norm(c - t) < 0.5 for t in tried):
                continue
            tried.append(c)
            spot = shoot(c)
            if in_region(spot, 0.0):
                good[0] = (c, spot)
                self.events.emit("calib", f"{node}: điểm khởi đầu ({label}) lệnh ({c[0]:+.1f}, {c[1]:+.1f}) vết "
                                 f"({spot[0]:.3f}, {spot[1]:.3f})", node=node, stage="shot", index=0)
                break
            self.events.emit("calib", f"{node}: điểm khởi đầu ({label}) lệnh ({c[0]:+.1f}, {c[1]:+.1f}) "
                             + ("không thấy vết" if spot is None else f"vết ({spot[0]:.3f}, {spot[1]:.3f}) ngoài khung"),
                             level="warn", node=node)
        if good[0] is None:
            raise Abort("không tìm được vết laser trong khung tag tham chiếu, dừng quét")

        # 2) đi tới từng đích
        max_steps = int(sc.get("sweep_max_steps", 8))
        remaining = list(targets)
        for k in range(1, len(targets) + 1):
            # tag đầu là tag gần vòi nhất (thứ tự của `targets`), các tag sau chọn tag gần vết hiện tại nhất
            if k == 1:
                tname, target = remaining.pop(0)
            else:
                j = int(np.argmin([np.linalg.norm(t[1][:2] - good[0][1][:2]) for t in remaining]))
                tname, target = remaining.pop(j)
            c, s = good[0][0].copy(), good[0][1].copy()
            J = self._model_jacobian(node, None, "floor", [0, 1], c, target)
            scale, fails, steps, dropped = 1.0, 0, 0, False
            for _ in range(max_steps):
                e = target[:2] - s[:2]
                if float(np.linalg.norm(e)) < 0.015:
                    break
                if J is None or self._ill(J):
                    dc = np.array(self._cmd(node, None, target), float) - c
                else:
                    dc = np.linalg.solve(J, e)
                n = float(np.linalg.norm(dc))
                lim_step = step_max * scale
                if n > lim_step:
                    dc = dc * (lim_step / n)
                c_new = clamp(c + dc, good[0][0][1] + 6.0)
                if float(np.linalg.norm(c_new - c)) < 0.02:
                    break
                steps += 1
                center = None
                if J is not None and not self._ill(J):
                    center = np.array([*(s[:2] + J @ (c_new - c)), 0.0])
                spot = shoot(c_new, center if center is not None else target)
                if not in_region(spot, outside):
                    fails += 1
                    scale /= 2
                    self.events.emit("calib", f"{node}: {tname} bước {steps} "
                                     + ("mất vết" if spot is None else f"vết ({spot[0]:.3f}, {spot[1]:.3f}) ngoài khung")
                                     + ", lùi về lệnh tốt gần nhất", level="warn", node=node)
                    if fails >= 2:
                        dropped = True
                        break
                    c, s = good[0][0].copy(), good[0][1].copy()
                    continue
                fails = 0
                if J is not None:  # Broyden: J khớp cặp (dc, ds) vừa đo
                    dcm, ds = c_new - c, spot[:2] - s[:2]
                    if float(dcm @ dcm) > 0.02**2:
                        J = J + np.outer(ds - J @ dcm, dcm) / float(dcm @ dcm)
                c, s = c_new, spot.copy()
                good[0] = (c.copy(), s.copy())
            miss = float(np.linalg.norm(target[:2] - good[0][1][:2]))
            self.events.emit("calib", f"{node}: {tname} ({target[0]:.2f}, {target[1]:.2f}) "
                             + ("bỏ tag: vết ra ngoài khung hoặc mất vết hai lần liền" if dropped else f"vết ({good[0][1][0]:.3f}, {good[0][1][1]:.3f}), "
                                f"lệch cuối {miss * 100:.1f} cm, {steps} bước"),
                             node=node, stage="shot", index=k, steps=steps, dropped=dropped,
                             miss_cm=None if dropped else round(miss * 100, 2))
        return samples

    def calibrate(self, node: str) -> dict:
        """Quét hiệu chuẩn một vòi: bắn laser tới vài góc, đo vết, khớp và lưu. Chỉ chạy lúc IDLE, orchestrator
        bật, vòi sẵn sàng; nhường (bỏ quét) khi có cảnh báo hoặc dừng khẩn cấp. Trả {"ok", "reason", ...}.
        Gọi từ luồng orchestrator (qua `request_calibration`) hoặc khi orchestrator không chạy."""
        res: dict = {"node": node, "ok": False}

        def fail(why: str) -> dict:
            if self._cal_state.get(node) == "sweeping":
                self._cal_state[node] = "failed"
            self.events.emit("calib", f"{node}: không hiệu chuẩn được: {why}", level="warn", node=node,
                             stage="failed", reason=why)
            return {**res, "reason": why}

        if node not in NOZZLES:
            return fail(f"không có vòi {node!r}")
        if not self.selfcal_on:
            return fail("tự hiệu chuẩn đang tắt (selfcal.enabled)")
        if not self.enabled:
            return fail("orchestrator đang tắt")
        with self._lock:
            if self.phase != Phase.IDLE or self._cal_state[node] == "sweeping" or not self._queue.empty():
                return fail("trạm đang bận hoặc có cảnh báo chờ")
            self._cal_state[node] = "sweeping"
        try:
            if why := self._not_ready(node):
                return fail("; ".join(why))
            self._abort.clear()
            hull = self._sweep_hull()
            if len(hull) < 3:
                return fail("không có khung tag tham chiếu (cần ít nhất 3 tag) để giới hạn vùng quét laser")
            targets = self._sweep_targets(node, hull)
            if len(targets) < 3:
                return fail(f"chỉ chọn được {len(targets)} tag làm đích quét")
            self.events.emit("calib", f"{node}: bắt đầu quét dẫn đường tới {len(targets)} tag: "
                             + ", ".join(t[0] for t in targets),
                             node=node, stage="start", points=len(targets))
            samples = self._guided_sweep(node, hull, targets)
            if len(samples) < 3:
                return fail(f"chỉ thấy {len(samples)} vết laser")
            self._cal_samples[node].clear()  # quét mới thay mẫu cũ (vòi có thể đã dời)
            self._cal_samples[node].extend(samples)
            self._cal_new[node] = 0
            cal = selfcal.fit(samples, NodeCal.from_site(self.site, node), self.site, baseline=self.cals[node])
            if not cal.fitted:
                return fail(cal.note)
            self._apply_cal(node, cal, "quét")
            return {**res, "ok": True, "rms_before_cm": cal.rms_before_cm, "rms_cm": cal.rms_cm,
                    "n_samples": cal.n_samples, "note": cal.note}
        except Abort as e:
            return fail(str(e))
        except (LinkError, TimeoutError) as e:
            return fail(str(e))
        finally:
            if self._cal_state[node] == "sweeping":
                self._cal_state[node] = "ok" if self.cals[node].fitted else "idle"
            self._lit = self._lit - {node}  # laser chỉ tắt ở đây: xong lượt quét, dừng hoặc lỗi
            self.link.stop(node)

    # --- tiện ích ---------------------------------------------------------------------------

    def _node_pos(self, n: str):
        p = self.vision.nodes.get(n)
        return None if p is None else p.position

    def _surface_of(self, point) -> str:
        """"floor" nếu điểm nằm trên mặt nền (Z ~ 0, xa mặt phẳng bảng), ngược lại "wall"."""
        _, y, z = (float(c) for c in point)
        return "floor" if abs(z) <= 0.02 and abs(y - self.site["board"]["plane_y"]) > 0.02 else "wall"

    def _plane_mask(self, point) -> np.ndarray:
        """Hướng được phép bù khi CORRECT: trên bảng chỉ x, z; trên nền chỉ x, y (giữ nguyên mặt)."""
        return np.array([1.0, 1.0, 0.0]) if self._surface_of(point) == "floor" else np.array([1.0, 0.0, 1.0])

    def _yaw(self, node: str) -> float:
        """Yaw (pan = 0) của node: site.yaml hoặc trục +Y của tag đế (aiming.node_yaw)."""
        return aiming.node_yaw(self.site, node, getattr(self.vision, "nodes", {}).get(node))

    def _reach_fn(self, node, pivot):
        if pivot is None:
            return lambda x, z, y=None: False
        lim = aiming.limits(self.site, node)
        plane_y = self.site["board"]["plane_y"]
        return lambda x, z, y=None: lim.ok(*self._cmd(node, pivot, (x, plane_y if y is None else y, z)))

    def _blocked(self, node: str, target, pivots: dict) -> list[str]:
        reasons = list(self.vision.blockers(node))
        if self.pose_age(node) > POSE_MAX_S and not reasons:
            reasons.append(f"pose của {node} quá cũ")
        if self.link.hb_age_ms(node) > HB_MAX_MS:
            reasons.append(f"mất heartbeat {node}")
        if not self.vision.on_board(np.asarray(target)):
            reasons.append("mục tiêu ngoài vùng bia")
        if node in pivots and not aiming.limits(self.site, node).ok(*self._cmd(node, pivots[node], target)):
            reasons.append(f"{node} không với tới bia")
        return reasons


def _num(v: float):
    return None if v == float("inf") else round(min(v, CAP_AGE), 1)


def _point_json(point, surface: str) -> dict:
    """Điểm 3D cho dashboard và log: x, y, z (m) và mặt ("wall" | "floor")."""
    return {"x": round(float(point[0]), 3), "y": round(float(point[1]), 3),
            "z": round(float(point[2]), 3), "surface": surface}


ZONE_MIN_M, ZONE_MAX_M = 0.04, 0.40  # kẹp bề ngang và chiều sâu vùng cháy


def sweep_pattern(zone: dict, step: float, serpentine: bool = False) -> list[tuple[int, int, np.ndarray]]:
    """Lưới quét vùng cháy: [(hàng, cột, điểm 3D)], hàng từ mép gần ra mép xa, mỗi hàng từ trái sang phải
    (nhìn từ vòi về lửa). `serpentine`: hàng lẻ đi ngược (phải sang trái); `cột` vẫn là vị trí ngang."""
    step = max(float(step), 1e-3)
    rows = max(1, round(zone["depth"] / step) + 1)
    cols = max(1, round(zone["width"] / step) + 1)
    depths = np.linspace(0.0, zone["depth"], rows) if rows > 1 else np.zeros(1)
    lats = np.linspace(-zone["width"] / 2, zone["width"] / 2, cols) if cols > 1 else np.zeros(1)
    m, fwd, right = np.asarray(zone["m"], float), np.asarray(zone["fwd"], float), np.asarray(zone["right"], float)
    out = []
    for r, b in enumerate(depths):
        order = range(cols - 1, -1, -1) if serpentine and r % 2 else range(cols)
        for c in order:
            p = m.copy()
            p[:2] = m[:2] + b * fwd + lats[c] * right
            out.append((r, c, p))
    return out


def zone_contains(zone: dict, p, tol: float = 0.0) -> bool:
    """Điểm `p` (x, y) nằm trong vùng cháy (nới `tol` mét mọi phía)."""
    d = np.asarray(p, float)[:2] - np.asarray(zone["m"], float)[:2]
    a, b = float(d @ np.asarray(zone["right"], float)), float(d @ np.asarray(zone["fwd"], float))
    return abs(a) <= zone["width"] / 2 + tol and -tol <= b <= zone["depth"] + tol


def _track_json(t) -> dict:
    return {**_point_json(t.position, t.surface), "conf": t.conf}
