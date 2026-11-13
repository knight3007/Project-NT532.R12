#!/usr/bin/env bash
# Cài đặt NT532 trên Ubuntu 24.04 (LXC). Chạy bằng root: sudo bash setup-lxc.sh
# Chạy lại nhiều lần vẫn an toàn.
# Biến tùy chọn: NT532_REPO_URL, NT532_BRANCH, NT532_USER, NT532_DIR
set -euo pipefail

NT532_REPO_URL="${NT532_REPO_URL:-https://github.com/knight3007/Project-NT532.R12.git}"
NT532_BRANCH="${NT532_BRANCH:-feature/homelab-logs}"
NT532_USER="${NT532_USER:-nt532}"
NT532_DIR="${NT532_DIR:-/opt/nt532}"

log() { printf '\n==> %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Hãy chạy bằng root: sudo bash $0" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

log "Cập nhật apt và cài gói cơ bản"
apt-get update -y
apt-get install -y ca-certificates curl gnupg git ufw openssl

log "Cài Docker (repo chính thức)"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  echo "Docker đã có: $(docker --version)"
fi
systemctl enable --now docker

log "Cài Tailscale (trên host, không trong Docker)"
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "Tailscale đã có."
fi

log "Cấu hình tường lửa ufw (22, 80, 443)"
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

log "Tạo người dùng $NT532_USER"
if ! id "$NT532_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$NT532_USER"
fi
usermod -aG docker "$NT532_USER"

log "Lấy mã nguồn vào $NT532_DIR"
git config --global --add safe.directory "$NT532_DIR" || true
if [ ! -d "$NT532_DIR/.git" ]; then
  mkdir -p "$(dirname "$NT532_DIR")"
  git clone --branch "$NT532_BRANCH" "$NT532_REPO_URL" "$NT532_DIR"
else
  git -C "$NT532_DIR" fetch origin "$NT532_BRANCH"
  git -C "$NT532_DIR" checkout "$NT532_BRANCH"
  git -C "$NT532_DIR" pull --ff-only origin "$NT532_BRANCH"
fi
chown -R "$NT532_USER:$NT532_USER" "$NT532_DIR"

DEPLOY="$NT532_DIR/homelab/deploy"
if [ ! -f "$DEPLOY/compose.yaml" ]; then
  echo "Không thấy $DEPLOY/compose.yaml. Kiểm tra NT532_BRANCH (hiện: $NT532_BRANCH)." >&2
  exit 1
fi
mkdir -p "$NT532_DIR/homelab/control/data"

NEED_ENV=0
if [ ! -f "$DEPLOY/.env" ]; then
  cp "$DEPLOY/.env.example" "$DEPLOY/.env"
  chown "$NT532_USER:$NT532_USER" "$DEPLOY/.env"
  chmod 600 "$DEPLOY/.env"
  NEED_ENV=1
fi

TS_STATE="$(tailscale status 2>&1 || true)"
if echo "$TS_STATE" | grep -qiE 'logged out|needs login|stopped|not running|failed to connect'; then
  cat <<MSG

==> Tailscale chưa đăng nhập. Chạy tay lệnh sau rồi mở link xác thực:
      sudo tailscale up
    Trên LXC cần /dev/net/tun (xem docs/homelab-deploy.md). Sau đó lấy IP của Pi
    bằng 'tailscale status' và điền vào NT532_PI_URL.
MSG
fi

if [ "$NEED_ENV" -eq 1 ]; then
  cat <<MSG

==> Đã tạo $DEPLOY/.env từ mẫu. Hãy điền (nano $DEPLOY/.env):
    DOMAIN, ACME_EMAIL, NT532_PI_URL, NT532_SECRET, NT532_ADMIN_PASSWORD_HASH, các token.
    Sau đó chạy lại: sudo bash $0
MSG
  exit 0
fi

if grep -qE '^(NT532_SECRET|NT532_ADMIN_PASSWORD_HASH)=$' "$DEPLOY/.env"; then
  echo "File .env còn thiếu NT532_SECRET hoặc NT532_ADMIN_PASSWORD_HASH. Điền rồi chạy lại." >&2
  exit 1
fi

log "Build và khởi động"
cd "$DEPLOY"
docker compose up -d --build
docker compose ps

echo
echo "Xong. Kiểm tra: curl -I https://<DOMAIN>   |   docker compose -f $DEPLOY/compose.yaml logs -f"
