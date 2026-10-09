"""Kịch bản demo đầu cuối trên sa bàn ảo: dàn một tình huống, để trạm tự xử lý, đo và chấm điểm.

    station = build_sim()
    station.start()
    for scene in SCENES.values():
        kq = run_scene(station, scene)          # dict JSON được, có kq["verdict"]

Mỗi cảnh chấm hai thứ tách bạch:
- an toàn (`safety_ok`): cuối cảnh không còn bơm hay laser nào bật, node bị cấm không bao giờ phun hay
  bật laser, sau nút dừng khẩn cấp mọi thứ tắt trong 1 s. Vi phạm là lỗi nghiêm trọng.
- quyết định (`decision_ok`): có phun đúng lúc, đúng vòi, lửa có tắt không. Sai là thông tin để đánh giá
  bộ quyết định (luật hay Jev), không phải lỗi chương trình.

`score` là hàm thuần, chấm từ số đã quan sát nên thử được mà không cần trạm. `run_scene` không ném lỗi vì
cảnh hỏng: lỗi dàn cảnh, hết giờ hay trạm kẹt đều thành ghi chú trong verdict.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..orchestrator.machine import Phase, Settings

STOP_OFF_S = 1.0  # sau nút dừng khẩn cấp, bơm và laser phải tắt hết trong chừng này giây
STOP_WINDOW_S = 3.0  # chỉ xét khoảng này sau lúc dừng (lượt mới sau đó không bị tính là "còn bật")
UNEXPECTED = ("fault", "stopped", None)  # kết cục không ai mong khi cảnh không nói gì thêm


def fast_settings() -> Settings:
    """Chu kỳ ngắn cho test và chạy nhanh (cùng số với tests/test_orchestrator.py)."""
    return Settings(frame_gap_s=0.05, verify_wait_s=1.0)


@dataclass
class Scene:
    name: str  # tên dùng trong --scenes
    title: str  # tiêu đề hiển thị cho khán giả
    expect: str  # hành vi mong đợi, ngắn
    setup: Callable  # setup(world, station): dàn cảnh trên sa bàn ảo
    during: Callable | None = None  # during(station, world) -> dict | None: chạy sau setup (vd. bấm dừng)
    timeout_s: float = 120.0
    should_spray: bool | None = None  # True phải phun, False không được phun, None không chấm
    expect_out: bool = False  # lửa của cảnh phải tắt hẳn
    expect_nozzles: set[str] | None = None  # vòi được phép phun (None: không chấm)
    forbid_nodes: set[str] = field(default_factory=set)  # node không bao giờ được phun hay bật laser
    expect_outcomes: set[str] | None = None  # mọi lượt của cảnh phải kết thúc trong tập này


# --- ghi lại những gì xảy ra ----------------------------------------------------------------------


class Recorder:
    """Luồng nền lấy mẫu bơm và laser của sa bàn mỗi `period` giây (chỉ nhớ lúc trạng thái đổi) và gom
    sự kiện mới của trạm, để không mất sự kiện khi cảnh dài hơn bộ đệm 500 sự kiện."""

    def __init__(self, world, events, period: float = 0.05) -> None:
        self.world, self.log, self.period = world, events, period
        self.cursor = events.last_seq
        self.events: list[dict] = []
        self.changes: list[tuple] = []  # (thời điểm, các bơm bật, các laser bật) mỗi lần đổi
        self.pumped: set[str] = set()
        self.lasered: set[str] = set()
        self.dual = False  # hai bơm từng chạy cùng lúc
        self.first_pump: float | None = None
        self.t_end: float | None = None
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="demo-recorder")

    def _sample(self) -> None:
        new = self.log.since(self.cursor, 10_000)
        if new:
            self.cursor = new[-1]["seq"]
            self.events.extend(new)
        tr = self.world.truth()
        now = time.time()
        pumps = frozenset(n for n, v in tr["pumping"].items() if v)
        lasers = frozenset(n for n, v in tr["lasers"].items() if v)
        if not self.changes or self.changes[-1][1:] != (pumps, lasers):
            self.changes.append((now, pumps, lasers))
        self.pumped |= pumps
        self.lasered |= lasers
        self.dual |= len(pumps) > 1
        if pumps and self.first_pump is None:
            self.first_pump = now

    def _run(self) -> None:
        while not self._halt.wait(self.period):
            self._sample()

    def start(self) -> "Recorder":
        self._sample()
        self._thread.start()
        return self

    def stop(self) -> None:
        self._halt.set()
        self._thread.join(2)
        self._sample()
        self.t_end = time.time()

    @property
    def alert_time(self) -> float | None:
        """Lúc cảnh báo đầu tiên của cảnh (cảnh báo thật, không tính "hết báo động")."""
        return next((e["time"] for e in self.events if e["kind"] == "alert" and e["level"] == "warn"), None)


def on_after(changes: list[tuple], t_end: float, since: float, window: float = STOP_WINDOW_S) -> float:
    """Từ `since`, bơm hoặc laser còn bật tới giây thứ mấy (tối đa `window`); 0 nếu đã tắt hết ngay."""
    end, last = since + window, 0.0
    for i, (t, pumps, lasers) in enumerate(changes):
        t_next = changes[i + 1][0] if i + 1 < len(changes) else t_end
        if (pumps or lasers) and t_next > since and t < end:
            last = max(last, min(t_next, end) - since)
    return last


# --- chấm điểm ----------------------------------------------------------------------------------


def score(scene: Scene, observed: dict) -> dict:
    """Chấm một cảnh từ số quan sát: {"decision_ok": bool | None, "safety_ok": bool, "notes": [..]}.

    `observed`: ready, timed_out, error, runs (Run.to_json), sprayed, lasered, fire_out (None nếu cảnh
    không có lửa), end_pumps, end_lasers, on_after_stop_s (None nếu cảnh không bấm dừng)."""
    notes: list[str] = []
    sprayed, lasered = set(observed.get("sprayed", ())), set(observed.get("lasered", ()))

    safety_ok = True
    left = sorted(set(observed.get("end_pumps", ())) | set(observed.get("end_lasers", ())))
    if left:
        safety_ok = False
        notes.append(f"an toàn: hết cảnh mà {', '.join(left)} vẫn còn bật bơm hoặc laser")
    if bad := sorted(scene.forbid_nodes & (sprayed | lasered)):
        safety_ok = False
        notes.append(f"an toàn: node bị cấm {', '.join(bad)} đã phun hoặc bật laser")
    after = observed.get("on_after_stop_s")
    if after is not None and after > STOP_OFF_S:
        safety_ok = False
        notes.append(f"an toàn: sau nút dừng khẩn cấp vẫn còn bật {after:.1f} s (giới hạn {STOP_OFF_S:g} s)")

    runs = observed.get("runs") or []
    outcomes = [r.get("outcome") for r in runs]
    checks: list[bool] = []

    def check(ok: bool, why: str) -> None:
        checks.append(ok)
        if not ok:
            notes.append("quyết định: " + why)

    if observed.get("error"):
        check(False, f"lỗi khi dàn cảnh: {observed['error']}")
    if not observed.get("ready", True):
        check(False, "trạm chưa về trạng thái rảnh trước khi dàn cảnh")
    if observed.get("timed_out"):
        check(False, f"hết giờ ({scene.timeout_s:g} s) khi trạm chưa xử lý xong")
    if not runs:
        check(False, "không có lượt xử lý nào (cảm biến không báo động?)")
    if scene.should_spray is True:
        check(bool(sprayed), "đáng lẽ phải phun mà không phun")
    elif scene.should_spray is False:
        check(not sprayed, f"phun nhầm bằng {', '.join(sorted(sprayed))} khi không có cháy thật")
    if scene.expect_nozzles is not None and sprayed:
        wrong = sorted(sprayed - scene.expect_nozzles)
        check(not wrong, f"phun bằng vòi không mong đợi {', '.join(wrong)} "
                         f"(mong đợi {', '.join(sorted(scene.expect_nozzles))})")
    if scene.expect_out:
        check(observed.get("fire_out") is True, "lửa chưa tắt")
    if scene.expect_outcomes is not None:
        wrong = sorted({str(o) for o in outcomes if o not in scene.expect_outcomes})
        check(not wrong, f"kết cục lượt {', '.join(wrong)} ngoài mong đợi "
                         f"({', '.join(sorted(scene.expect_outcomes))})")
    else:
        wrong = sorted({str(o) for o in outcomes if o in UNEXPECTED})
        check(not wrong, f"có lượt kết thúc {', '.join(wrong)}")

    gradable = (scene.should_spray is not None or scene.expect_out or scene.expect_outcomes is not None
                or scene.expect_nozzles is not None)
    return {"decision_ok": all(checks) if gradable else None, "safety_ok": safety_ok, "notes": notes}


# --- chạy một cảnh ---------------------------------------------------------------------------------


def _idle(orch) -> bool:
    return orch.phase == Phase.IDLE and not orch.snapshot()["queue"]


def prepare(station, cap_s: float = 60.0) -> bool:
    """Dọn sa bàn, đưa mọi node về online, rồi chờ trạm rảnh: IDLE, hàng đợi trống, không node nào
    còn báo động, và quên các lượt xử lý lại còn treo. Quá `cap_s` thì trả False (trạm kẹt), không chờ mãi."""
    world, orch = station.world, station.orch
    world.clear()
    for n in world.nodes:
        world.set_online(n, True)
    end = time.time() + cap_s
    while time.time() < end:
        if _idle(orch) and not any(ns.alarm for ns in world.node_sensors.values()):
            orch.forget_followups()  # lượt "xử lý lại" của cảnh trước không lan sang cảnh này
            return True
        time.sleep(0.1)
    return False


def _fires_out(world, fire_ids: set[int]) -> bool | None:
    if not fire_ids:
        return None
    return all(s["out"] for s in world.truth()["sources"] if s["id"] in fire_ids)


def _wait_done(station, scene: Scene, rec: Recorder, fire_ids: set[int], t_setup: float,
               deadline: float, settle_s: float, alert_wait_s: float) -> bool:
    """Chờ tới khi trạm đã IDLE với hàng đợi trống đủ `settle_s` giây sau cảnh báo đầu tiên (cảnh báo
    xử lý lại có thể mở lượt mới nên không dừng ngay ở lượt đầu). Cảnh cần lửa tắt mà lửa chưa tắt thì chờ
    thêm cho tới lúc trạm kịp tự xử lý lại. True nếu xong trước hạn, False nếu hết giờ."""
    orch = station.orch
    idle_since = None
    while True:
        now = time.time()
        if now >= deadline:
            return False
        if rec.alert_time is None:
            if now - t_setup > alert_wait_s:
                return True  # không có cảnh báo nào: không chờ nữa, score ghi lại
        elif _idle(orch):
            idle_since = idle_since or now
            need = settle_s
            if scene.expect_out and not _fires_out(station.world, fire_ids):
                need = max(settle_s, orch.cfg.realert_s + 5.0)
            if now - idle_since >= need:
                return True
        else:
            idle_since = None
        time.sleep(0.1)


def run_scene(station, scene: Scene, settle_s: float = 4.0, sample_s: float = 0.05,
              ready_cap_s: float = 60.0, alert_wait_s: float = 40.0) -> dict:
    """Chạy một cảnh trên `station` (sa bàn ảo đã start) và trả kết quả JSON được."""
    world, orch, events = station.world, station.orch, station.events
    ready = prepare(station, ready_cap_s)
    base_id = max((r.id for r in orch.runs), default=0)  # orch.runs chỉ giữ 20 lượt nên đếm theo id
    events.emit("demo", f"cảnh {scene.name}: {scene.title}", scene=scene.name)
    rec = Recorder(world, events, sample_s).start()
    t0 = time.time()
    extra: dict = {}
    error = None
    fire_ids: set[int] = set()
    timed_out = False
    try:
        try:
            scene.setup(world, station)
            fire_ids = {s["id"] for s in world.truth()["sources"] if s["kind"] == "fire"}
            if scene.during is not None:
                extra = scene.during(station, world) or {}
        except Exception as e:  # noqa: BLE001 - cảnh hỏng thì báo trong verdict, không làm chết cả buổi demo
            error = f"{type(e).__name__}: {e}"
        if error is None:  # dàn cảnh hỏng thì không chờ gì nữa
            timed_out = not _wait_done(station, scene, rec, fire_ids, time.time(), t0 + scene.timeout_s,
                                       settle_s, alert_wait_s)
        if timed_out:  # dừng hẳn để cảnh sau bắt đầu sạch; bơm còn bật sau đó mới là vi phạm
            orch.emergency_stop("hết giờ cảnh demo")
            end = time.time() + 5
            while time.time() < end and not _idle(orch):
                time.sleep(0.05)
        time.sleep(0.3)
        end_state = world.truth()
        fire_out = _fires_out(world, fire_ids)
    finally:
        rec.stop()
        for n in world.nodes:  # node bị cắt liên lạc trong cảnh thì nối lại
            world.set_online(n, True)

    runs = [r.to_json() for r in list(orch.runs) if r.id > base_id]
    stop_t = extra.get("stopped_at")
    after = None if stop_t is None else round(on_after(rec.changes, rec.t_end, stop_t), 2)
    alert_t = rec.alert_time
    observed = {
        "ready": ready, "timed_out": timed_out, "error": error, "runs": runs,
        "sprayed": sorted(rec.pumped), "lasered": sorted(rec.lasered), "dual_pump": rec.dual,
        "fire_out": fire_out,
        "end_pumps": sorted(n for n, v in end_state["pumping"].items() if v),
        "end_lasers": sorted(n for n, v in end_state["lasers"].items() if v),
        "on_after_stop_s": after,
    }
    result = {
        "name": scene.name, "title": scene.title, "expect": scene.expect,
        "started": t0, "duration_s": round(time.time() - t0, 1),
        "outcomes": [r["outcome"] for r in runs],
        "alert_s": None if alert_t is None else round(alert_t - t0, 2),
        "latency_s": None if alert_t is None or rec.first_pump is None
        else round(rec.first_pump - alert_t, 2),
        **observed,
    }
    result["verdict"] = score(scene, observed)
    events.emit("demo", f"cảnh {scene.name} xong: quyết định "
                        f"{_word(result['verdict']['decision_ok'])}, "
                        f"{'an toàn' if result['verdict']['safety_ok'] else 'VI PHẠM AN TOÀN'}",
                scene=scene.name, level="info" if result["verdict"]["safety_ok"] else "error")
    return result


def _word(ok: bool | None) -> str:
    return "không chấm" if ok is None else "đạt" if ok else "không đạt"


def summary_entry(result: dict) -> dict:
    """Một cảnh trong summary.json (và mục "Kịch bản demo" của báo cáo): lượt rút gọn, vòi đã phun,
    độ trễ, lửa có tắt không, verdict."""
    keys = ("name", "title", "expect", "outcomes", "sprayed", "lasered", "dual_pump", "alert_s", "latency_s",
            "fire_out", "end_pumps", "end_lasers", "on_after_stop_s", "timed_out", "ready", "error",
            "duration_s", "verdict")
    return {"runs": [compact_run(r) for r in result["runs"]], **{k: result.get(k) for k in keys}}


def compact_run(run: dict) -> dict:
    """Một lượt rút gọn để ghi summary.json: bỏ đoạn STATE dài, chi tiết đã có trong log của trạm."""
    first = (run.get("decisions") or [{}])[0]
    ans = first.get("answers") or {}
    t0 = (run.get("phases") or [[None, run.get("started")]])[0][1]
    fire = next((p[1] for p in run.get("phases") or [] if p[0] == "FIRE"), None)
    return {
        "id": run.get("id"), "node": run.get("node"), "outcome": run.get("outcome"),
        "nozzle": run.get("nozzle"), "attempts": run.get("attempts"), "target": run.get("target"),
        "decider": first.get("decider"), "decide_ms": first.get("latency_ms"),
        "real_fire": (ans.get("real_fire") or {}).get("answer"),
        "action": (ans.get("action") or {}).get("answer"),
        "miss_cm": next((s["miss_cm"] for s in reversed(run.get("shots") or [])
                         if s.get("miss_cm") is not None), None),
        "alert_to_fire_s": None if fire is None or t0 is None else round(fire - t0, 2),
    }


# --- các cảnh -------------------------------------------------------------------------------------


def _estop_during(station, world) -> dict:
    """Chờ trạm tới CORRECT hoặc FIRE (laser hoặc bơm sắp chạy) rồi bấm dừng khẩn cấp."""
    orch = station.orch
    end = time.time() + 60
    while time.time() < end:
        if orch.phase in (Phase.CORRECT, Phase.FIRE):
            t = time.time()
            orch.emergency_stop("demo")
            return {"stopped_at": t}
        time.sleep(0.02)
    return {}


def _scenes() -> dict[str, Scene]:
    scenes = [
        Scene(
            "fire_s1", "Đám cháy nhỏ gần vòi s1",
            "s1 báo động, camera thấy bia lửa, bộ quyết định chọn vòi s1 (gần nhất); trạm ngắm, chỉnh bằng "
            "laser rồi phun. Lửa tắt, bơm và laser tắt hết.",
            lambda w, st: w.ignite(0.32, 0.30), timeout_s=150,
            should_spray=True, expect_out=True, expect_nozzles={"s1"}),
        Scene(
            "fire_s2", "Đám cháy nhỏ gần vòi s2",
            "s2 báo động, bộ quyết định chọn vòi s2; trạm ngắm, chỉnh bằng laser rồi phun. Lửa tắt, bơm và "
            "laser tắt hết.",
            lambda w, st: w.ignite(0.90, 0.30), timeout_s=150,
            should_spray=True, expect_out=True, expect_nozzles={"s2"}),
        Scene(
            "fire_large", "Đám cháy lớn giữa bảng",
            "Cả hai node báo động. Cần nhiều nước hơn nên có thể phải qua vài lượt (trạm tự xử lý lại khi cảm "
            "biến vẫn vượt ngưỡng). Hai vòi chỉ phun cùng lúc khi Jev trả lời ở DECIDE (--decider hybrid "
            "--model-stages decide,verify hoặc --decider remote); baseline luật dùng một vòi gần nhất.",
            lambda w, st: w.ignite(0.60, 0.30, size="large"), timeout_s=300,
            should_spray=True, expect_out=True),
        Scene(
            "lamp", "Đèn nóng giả làm lửa",
            "Cảm biến báo nhiệt cao nhưng không có khí, camera thấy thẻ đèn. Không được phun. Baseline luật "
            "chỉ nhìn độ tin cậy của camera nên thỉnh thoảng phun nhầm: đó là điểm yếu cổ điển của luật.",
            lambda w, st: w.add_lamp(0.32, 0.30), timeout_s=90, should_spray=False),
        Scene(
            "steam_object", "Vật màu cam và hơi nước nấu ăn",
            "Camera thấy vật màu cam, cảm biến thấy nóng và ẩm nhưng gần như không có khí. Không được phun.",
            lambda w, st: (w.add_object(0.88, 0.30), w.add_steam(0.90)), timeout_s=90,
            should_spray=False),
        Scene(
            "spike", "Nhiễu cảm biến, không có gì trên bảng",
            "Một xung khí ngắn trên s1 gây báo động nhưng camera không thấy bia nào. Trạm bỏ qua (ignored) "
            "và không phun.",
            lambda w, st: w.add_spike("s1", duration=5.0), timeout_s=90,
            should_spray=False, expect_outcomes={"ignored"}),
        Scene(
            "node_offline", "Mất liên lạc với s2 khi có cháy gần s2",
            "s2 mất heartbeat nên không được ngắm hay phun bằng s2. Cảnh báo duy nhất đến từ s1 (xung nhiễu); "
            "s1 chỉ phun nếu với tới. Trạm kết thúc an toàn, lửa gần s2 có thể không tắt.",
            lambda w, st: (w.set_online("s2", False), w.ignite(0.90, 0.30), w.add_spike("s1", duration=5.0)),
            timeout_s=90, forbid_nodes={"s2"},
            expect_outcomes={"ignored", "alarm_only", "extinguished", "human"}),
        Scene(
            "estop", "Nút dừng khẩn cấp giữa lượt phun",
            "Đám cháy nhỏ; khi trạm tới CORRECT hoặc FIRE thì bấm dừng khẩn cấp. Lượt kết thúc \"stopped\" "
            "và mọi bơm, laser tắt trong 1 s.",
            lambda w, st: w.ignite(0.32, 0.30), during=_estop_during, timeout_s=120,
            expect_outcomes={"stopped"}),
    ]
    return {s.name: s for s in scenes}


SCENES = _scenes()
