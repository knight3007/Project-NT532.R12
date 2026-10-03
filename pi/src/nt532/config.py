import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SITE_CONFIG = REPO_ROOT / "config" / "site.yaml"


def read_site(path: str | Path | None = None) -> dict[str, Any]:
    """Đọc nguyên file cấu hình sa bàn, không qua bước nào khác."""
    path = Path(path or DEFAULT_SITE_CONFIG)
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_site(path: str | Path | None = None, source: str | int | None = None) -> dict[str, Any]:
    """Đọc file cấu hình sa bàn. Thứ tự ưu tiên: tham số, biến NT532_CONFIG, config/site.yaml.

    Với sa bàn ảo (`source == "sim"` hoặc NT532_CONFIG=sim) trả site của sim: vị trí tag tham
    chiếu đã điền, hiệu chuẩn và commissioning nằm trong data/sim/ thay vì calibration/.
    """
    chosen = path or os.environ.get("NT532_CONFIG")
    if source == "sim" or str(chosen) == "sim":
        from .sim import sim_site

        return sim_site(read_site(None if str(chosen) == "sim" else chosen))
    return read_site(chosen)
