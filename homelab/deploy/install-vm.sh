#!/usr/bin/env bash
# Cài và bật NT532 control trên VM bằng một lệnh:
#   bash homelab/deploy/install-vm.sh
# Lần đầu tự sinh .env (khóa, token, mật khẩu admin), build, chạy, rồi in lệnh cho Pi.
# Chạy lại để cập nhật sau `git pull`; biến đã có trong .env được giữ nguyên.
# Đặt lại mật khẩu admin: xóa dòng NT532_ADMIN_PASSWORD_HASH trong .env rồi chạy lại.
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Cần cài Docker trước." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Cần Docker Compose v2 (docker compose)." >&2; exit 1; }
DC=(docker compose -f compose.vm.yaml)

rand() { openssl rand -hex "$1" 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex($1))"; }
has() { grep -q "^$1=." .env 2>/dev/null; }
put() { has "$1" || { sed -i "/^$1=/d" .env; echo "$1=$2" >> .env; }; }

touch .env && chmod 600 .env
put NT532_PORT "${NT532_PORT:-8800}"  # cổng trên VM; compose đọc từ .env, đổi sau thì sửa ở đây
export NT532_PORT="$(grep '^NT532_PORT=' .env | cut -d= -f2-)"
put NT532_SECRET "$(rand 32)"
put NT532_PI_TOKEN "$(rand 32)"
put NT532_WORKER_TOKEN "$(rand 32)"
put NT532_DECIDER_TOKEN "$(rand 32)"
put NT532_ADMIN_USER admin
put NT532_DB /app/data/nt532.db
put NT532_HTTPS false  # http trong LAN; true thì trình duyệt bỏ cookie đăng nhập

"${DC[@]}" build control

new_pw=""
if ! has NT532_ADMIN_PASSWORD_HASH; then
  new_pw="${ADMIN_PASSWORD:-$(rand 10)}"
  hash=$(PW="$new_pw" "${DC[@]}" run --rm --no-deps -T -e PW control \
    python -c "import os; from app.security import hash_password; print(hash_password(os.environ['PW']))")
  sed -i "/^NT532_ADMIN_PASSWORD_HASH=/d" .env
  echo "NT532_ADMIN_PASSWORD_HASH='$hash'" >> .env  # nháy đơn: compose không nội suy dấu $
fi

"${DC[@]}" up -d control

printf "Chờ control lên"
for _ in $(seq 60); do
  if curl -fsS http://127.0.0.1:$NT532_PORT/healthz >/dev/null 2>&1; then ok=1; break; fi
  printf "."; sleep 1
done
echo
[ "${ok:-}" = 1 ] || { echo "control chưa lên, xem: ${DC[*]} logs control" >&2; exit 1; }

ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
token=$(grep '^NT532_PI_TOKEN=' .env | cut -d= -f2-)
url="http://${ip:-<ip-vm>}:$NT532_PORT"
cat <<EOF

control đã chạy: $url

1. Mở $url trên máy cùng LAN, đăng nhập: admin / ${new_pw:-<mật khẩu đã đặt lần trước>}
2. Trên Pi, trong thư mục pi/ của repo:

NT532_PI_TOKEN=$token uv run --no-sync python scripts/run_station.py --decider rules --control-url $url

EOF
[ -n "$new_pw" ] && echo "Ghi lại mật khẩu admin ở trên, script không lưu bản chữ thường."
exit 0
