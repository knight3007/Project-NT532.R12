"""Băm và kiểm tra mật khẩu quản trị bằng scrypt (thư viện chuẩn), định dạng `scrypt$N$r$p$salt$hash`."""

import base64
import hashlib
import hmac
import os

N, R, P = 2**14, 8, 1


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, want = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                            dklen=len(base64.b64decode(want)), maxmem=128 * 1024 * 1024)
        return hmac.compare_digest(dk, base64.b64decode(want))
    except (ValueError, TypeError):
        return False
