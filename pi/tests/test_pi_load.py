import pytest

from nt532.sysload import (
    cpu_percent,
    parse_meminfo_used_mb,
    parse_proc_stat,
    parse_temp_c,
    parse_throttled,
)

PROC_STAT = """cpu  100 20 30 800 40 5 5 0 0 0
cpu0 25 5 7 200 10 1 1 0 0 0
intr 12345
"""

MEMINFO = """MemTotal:        8192000 kB
MemFree:          500000 kB
MemAvailable:    4096000 kB
Buffers:          100000 kB
"""


def test_parse_proc_stat_lay_dong_cpu_tong():
    total, idle = parse_proc_stat(PROC_STAT)
    assert total == 1000
    assert idle == 840  # idle + iowait


def test_parse_proc_stat_thieu_dong_cpu():
    with pytest.raises(ValueError):
        parse_proc_stat("intr 1\n")


def test_cpu_percent():
    assert cpu_percent((1000, 840), (2000, 1340)) == pytest.approx(50.0)
    assert cpu_percent((1000, 840), (1000, 840)) == 0.0


def test_parse_meminfo_used_mb():
    assert parse_meminfo_used_mb(MEMINFO) == pytest.approx(4000.0)


def test_parse_meminfo_thieu_truong():
    with pytest.raises(ValueError):
        parse_meminfo_used_mb("MemTotal: 100 kB\n")


def test_parse_temp_va_throttled():
    assert parse_temp_c("54321\n") == pytest.approx(54.321)
    assert parse_throttled("throttled=0x50005\n") == 0x50005
