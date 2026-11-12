"""Tạo chuỗi băm mật khẩu để điền vào NT532_ADMIN_PASSWORD_HASH.

    uv run python -m app.hash_password
"""

import getpass
import sys

from .security import hash_password


def main() -> int:
    pw = getpass.getpass("Mật khẩu: ")
    if len(pw) < 8:
        print("Mật khẩu cần ít nhất 8 ký tự.", file=sys.stderr)
        return 1
    if pw != getpass.getpass("Nhập lại: "):
        print("Hai lần nhập không khớp.", file=sys.stderr)
        return 1
    print(hash_password(pw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
