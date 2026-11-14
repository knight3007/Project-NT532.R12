"""Cấu hình từ biến môi trường."""

import logging
import os
import secrets
from dataclasses import dataclass

from .security import hash_password

log = logging.getLogger("nt532.config")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    pi_url: str = "http://127.0.0.1:8080"
    secret: str = ""
    admin_user: str = "admin"
    admin_hash: str = ""
    db_path: str = "data/control.sqlite"
    poll_s: float = 1.0
    retention_days: int = 14
    https: bool = False  # đặt cookie `Secure` khi chạy sau HTTPS
    session_days: int = 7
    pi_access_id: str = ""  # Cloudflare Access service token (khi Pi nằm sau tunnel)
    pi_access_secret: str = ""
    pi_token: str = ""  # khác rỗng = chế độ push: Pi tự POST lên /api/pi/push, VM không gọi vào Pi
    cmd_timeout_s: float = 5.0  # hạn chờ Pi nhận và chạy một lệnh (chế độ push)


def load(env: dict | None = None) -> Settings:
    e = os.environ if env is None else env
    pw_hash = e.get("NT532_ADMIN_PASSWORD_HASH", "").strip()
    if not pw_hash:
        plain = e.get("NT532_ADMIN_PASSWORD", "")
        if not plain:
            raise ConfigError(
                "Chưa đặt mật khẩu quản trị. Tạo chuỗi băm bằng `python -m app.hash_password` rồi điền vào "
                "NT532_ADMIN_PASSWORD_HASH (hoặc đặt tạm NT532_ADMIN_PASSWORD khi chạy thử)."
            )
        log.warning("Đang dùng NT532_ADMIN_PASSWORD dạng chữ thường; nên chuyển sang NT532_ADMIN_PASSWORD_HASH.")
        pw_hash = hash_password(plain)
    secret = e.get("NT532_SECRET", "")
    if not secret:
        secret = secrets.token_urlsafe(32)
        log.warning("Thiếu NT532_SECRET: dùng khóa ngẫu nhiên, phiên đăng nhập sẽ mất khi khởi động lại.")
    access_id = e.get("NT532_PI_ACCESS_CLIENT_ID", "").strip()
    access_secret = e.get("NT532_PI_ACCESS_CLIENT_SECRET", "").strip()
    if bool(access_id) != bool(access_secret):
        raise ConfigError(
            "Cloudflare Access cần đủ cả hai biến NT532_PI_ACCESS_CLIENT_ID và NT532_PI_ACCESS_CLIENT_SECRET; "
            "hiện mới đặt một trong hai."
        )
    return Settings(
        pi_url=e.get("NT532_PI_URL", "http://127.0.0.1:8080").rstrip("/"),
        secret=secret,
        admin_user=e.get("NT532_ADMIN_USER", "admin"),
        admin_hash=pw_hash,
        db_path=e.get("NT532_DB", "data/control.sqlite"),
        poll_s=float(e.get("NT532_POLL_S", "1.0")),
        retention_days=int(e.get("NT532_RETENTION_DAYS", "14")),
        https=e.get("NT532_HTTPS", "").lower() in ("1", "true", "yes"),
        pi_access_id=access_id,
        pi_access_secret=access_secret,
        pi_token=e.get("NT532_PI_TOKEN", "").strip(),
        cmd_timeout_s=float(e.get("NT532_CMD_TIMEOUT_S", "5.0")),
    )
