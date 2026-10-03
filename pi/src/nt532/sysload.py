"""Đọc tải của Raspberry Pi từ /proc và /sys; các hàm parse_* là hàm thuần để dễ kiểm thử."""


def parse_proc_stat(text: str) -> tuple[int, int]:
    """Từ nội dung /proc/stat trả về (tổng jiffy, jiffy rảnh) của dòng `cpu` tổng."""
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            values = [int(v) for v in parts[1:]]
            # Các trường: user nice system idle iowait irq softirq steal ...; guest đã nằm trong user.
            total = sum(values[:8])
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return total, idle
    raise ValueError("không có dòng cpu trong /proc/stat")


def cpu_percent(prev: tuple[int, int], cur: tuple[int, int]) -> float:
    """Phần trăm CPU bận giữa hai lần đọc parse_proc_stat."""
    total = cur[0] - prev[0]
    if total <= 0:
        return 0.0
    return 100.0 * (1 - (cur[1] - prev[1]) / total)


def parse_meminfo_used_mb(text: str) -> float:
    """RAM đã dùng (MB) = MemTotal - MemAvailable, từ nội dung /proc/meminfo."""
    kb = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            kb[key] = int(rest.split()[0])
    if len(kb) != 2:
        raise ValueError("thiếu MemTotal hoặc MemAvailable trong /proc/meminfo")
    return (kb["MemTotal"] - kb["MemAvailable"]) / 1024


def parse_temp_c(text: str) -> float:
    """Nhiệt độ °C từ nội dung thermal_zone*/temp (đơn vị mili độ)."""
    return int(text.strip()) / 1000


def parse_throttled(text: str) -> int:
    """Từ đầu ra `vcgencmd get_throttled` (dạng `throttled=0x50005`) lấy mặt nạ bit."""
    return int(text.strip().split("=")[-1], 16)
