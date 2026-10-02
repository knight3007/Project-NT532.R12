import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SITE_CONFIG = REPO_ROOT / "config" / "site.yaml"


def load_site(path: str | Path | None = None) -> dict[str, Any]:
    """Đọc file cấu hình sa bàn. Thứ tự ưu tiên: tham số, biến NT532_CONFIG, config/site.yaml."""
    path = Path(path or os.environ.get("NT532_CONFIG") or DEFAULT_SITE_CONFIG)
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)
