"""Payload của hợp đồng CoAP (kế hoạch mục 4): key ngắn, JSON, mỗi bản tin dưới khoảng 60 byte.

Chỉ là phần mã hóa và kiểm tra, không đụng tới mạng; dùng chung cho CoAP thật và link mô phỏng.
"""

import json
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass

STATUS = ("accepted", "reached", "done", "rejected", "fault")
DEVICES = ("pump", "laser")


class PayloadError(ValueError):
    """Payload sai dạng: thiếu key, sai kiểu hoặc giá trị không hợp lệ."""


def _load(raw: bytes | str) -> dict:
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise PayloadError(f"không phải JSON: {e}") from None
    if not isinstance(obj, dict):
        raise PayloadError("payload phải là object JSON")
    return obj


def _get(obj: dict, key: str, kind, optional: bool = False):
    if key not in obj:
        if optional:
            return None
        raise PayloadError(f"thiếu key {key!r}")
    v = obj[key]
    if kind is float and isinstance(v, int) and not isinstance(v, bool):
        v = float(v)
    if not isinstance(v, kind) or isinstance(v, bool) and kind is not bool:
        raise PayloadError(f"key {key!r} sai kiểu: {v!r}")
    return v


def dumps(msg) -> bytes:
    """Mã hóa gọn (không khoảng trắng), bỏ key None."""
    d = {k: v for k, v in asdict(msg).items() if v is not None}
    return json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode()


@dataclass(frozen=True)
class Telemetry:
    """`POST /t`: sensor gửi định kỳ. n tên node, s số thứ tự, up giây từ lúc bật, t nhiệt, g gas."""

    n: str
    s: int
    up: int
    t: float
    g: float
    h: float | None = None  # độ ẩm %, nếu node có SHT31

    @classmethod
    def parse(cls, raw: bytes | str) -> "Telemetry":
        o = _load(raw)
        return cls(_get(o, "n", str), _get(o, "s", int), _get(o, "up", int), _get(o, "t", float),
                   _get(o, "g", float), _get(o, "h", float, optional=True))


@dataclass(frozen=True)
class Alert:
    """`POST /a`: cảnh báo. k loại (`alert` hoặc `clear`), chống lặp theo cặp (n, s)."""

    n: str
    s: int
    k: str
    t: float
    g: float

    @classmethod
    def parse(cls, raw: bytes | str) -> "Alert":
        o = _load(raw)
        a = cls(_get(o, "n", str), _get(o, "s", int), _get(o, "k", str), _get(o, "t", float),
                _get(o, "g", float))
        if a.k not in ("alert", "clear"):
            raise PayloadError(f"k phải là alert hoặc clear, nhận {a.k!r}")
        return a


@dataclass(frozen=True)
class Aim:
    """`POST /aim`: góc độ, ttl ms tính từ lúc node nhận lần đầu."""

    id: int
    pan: float
    tilt: float
    ttl: int

    def __post_init__(self):
        object.__setattr__(self, "pan", round(float(self.pan), 2))
        object.__setattr__(self, "tilt", round(float(self.tilt), 2))

    @classmethod
    def parse(cls, raw: bytes | str) -> "Aim":
        o = _load(raw)
        return cls(_get(o, "id", int), _get(o, "pan", float), _get(o, "tilt", float),
                   _get(o, "ttl", int))


@dataclass(frozen=True)
class Fire:
    """`POST /fire`: bật `dev` (pump hoặc laser) trong `ms`, chỉ nhận khi aim cùng id đã reached."""

    id: int
    dev: str
    ms: int

    @classmethod
    def parse(cls, raw: bytes | str) -> "Fire":
        o = _load(raw)
        f = cls(_get(o, "id", int), _get(o, "dev", str), _get(o, "ms", int))
        if f.dev not in DEVICES:
            raise PayloadError(f"dev phải là một trong {DEVICES}, nhận {f.dev!r}")
        return f


@dataclass(frozen=True)
class Stop:
    """`POST /stop`: tắt laser và bơm ngay."""

    id: int

    @classmethod
    def parse(cls, raw: bytes | str) -> "Stop":
        return cls(_get(_load(raw), "id", int))


@dataclass(frozen=True)
class Status:
    """`POST /status` từ node chấp hành: st thuộc STATUS, err mã lỗi nếu có."""

    id: int
    st: str
    pan: float | None = None
    tilt: float | None = None
    err: str | None = None
    n: str | None = None  # tên node; không có trong bản kế hoạch, Pi tự điền theo địa chỉ nguồn

    @classmethod
    def parse(cls, raw: bytes | str) -> "Status":
        o = _load(raw)
        s = cls(_get(o, "id", int), _get(o, "st", str), _get(o, "pan", float, optional=True),
                _get(o, "tilt", float, optional=True), _get(o, "err", str, optional=True),
                _get(o, "n", str, optional=True))
        if s.st not in STATUS:
            raise PayloadError(f"st phải là một trong {STATUS}, nhận {s.st!r}")
        return s


@dataclass(frozen=True)
class Info:
    """`GET /info`: node tự mô tả. fw phiên bản firmware, pan/tilt giới hạn góc (độ), hb thời gian không
    nghe heartbeat thì tắt bơm và laser (ms), fire thời gian bật tối đa mỗi lệnh (ms), srp có đăng ký SRP."""

    n: str
    fw: str
    pan: tuple[float, float]
    tilt: tuple[float, float]
    hb: int
    fire: int
    srp: bool

    @staticmethod
    def _range(o: dict, key: str) -> tuple[float, float]:
        v = o.get(key)
        ok = isinstance(v, list) and len(v) == 2 and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)
        if not ok or v[0] > v[1]:
            raise PayloadError(f"key {key!r} phải là [thấp, cao], nhận {v!r}")
        return float(v[0]), float(v[1])

    @classmethod
    def parse(cls, raw: bytes | str) -> "Info":
        o = _load(raw)
        return cls(_get(o, "n", str), _get(o, "fw", str), cls._range(o, "pan"), cls._range(o, "tilt"),
                   _get(o, "hb", int), _get(o, "fire", int), bool(_get(o, "srp", int)))


class Deduper:
    """Chống lặp theo khóa (ví dụ (n, s) của `/a`), nhớ tối đa `size` khóa trong `window_s` giây.

    Gửi lại của CoAP (CON) và sensor khởi động lại đều có thể làm trùng; khóa cũ hơn cửa sổ thì
    coi là mới (node khởi động lại đếm s từ 0).
    """

    def __init__(self, window_s: float = 60.0, size: int = 256, clock=time.monotonic) -> None:
        self.window_s = window_s
        self.size = size
        self.clock = clock
        self._seen: OrderedDict = OrderedDict()

    def seen(self, key) -> bool:
        """True nếu khóa đã gặp trong cửa sổ; nếu chưa thì ghi nhận và trả False."""
        now = self.clock()
        while self._seen:
            _, t = next(iter(self._seen.items()))
            if now - t <= self.window_s and len(self._seen) <= self.size:
                break
            self._seen.popitem(last=False)
        if key in self._seen and now - self._seen[key] <= self.window_s:
            return True
        self._seen[key] = now
        self._seen.move_to_end(key)
        return False
