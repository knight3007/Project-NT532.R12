#!/usr/bin/env bash
# Cài môi trường trạm NT532 trên Raspberry Pi 5 (Raspberry Pi OS Bookworm 64-bit). Chạy lại nhiều lần không hại.
#
#   bash pi/deploy/setup_pi.sh                    # apt, uv, uv sync, mediamtx
#   bash pi/deploy/setup_pi.sh --yolo             # thêm --extra yolo và ncnn
#   bash pi/deploy/setup_pi.sh --install-services # chép và bật nt532-station + mediamtx (systemd)
#   bash pi/deploy/setup_pi.sh --mosquitto        # thêm gói mqtt (paho) và cài broker mosquitto cho Home Assistant
#   NT532_PI_TOKEN=<token> bash pi/deploy/setup_pi.sh --control-url http://<ip-vm>:8800
#                                                 # dịch vụ trạm đẩy lên control (ghi ~/.config/nt532/station.env)
#
# Chạy bằng user thường (không sudo); script tự gọi sudo khi cần. Không cài OTBR: xem firmware/rcp/README.md.
set -euo pipefail

YOLO=0
SERVICES=0
MOSQUITTO=0
CONTROL_URL=""
MEDIAMTX_DIR="${MEDIAMTX_DIR:-$HOME/mediamtx}"
while [ $# -gt 0 ]; do
  case "$1" in
    --yolo) YOLO=1 ;;
    --install-services) SERVICES=1 ;;
    --mosquitto) MOSQUITTO=1 ;;
    --control-url) CONTROL_URL="${2:?--control-url cần URL}"; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Tham số lạ: $1 (xem --help)" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$CONTROL_URL" ] && [ -z "${NT532_PI_TOKEN:-}" ]; then
  echo "--control-url cần biến NT532_PI_TOKEN (token in ra lúc chạy homelab/deploy/install-vm.sh)." >&2
  exit 2
fi

if [ "$(id -u)" -eq 0 ]; then
  echo "Đừng chạy bằng root/sudo: uv và mediamtx cài vào thư mục của user." >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PI_DIR="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$PI_DIR/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"

log() { printf '\n==> %s\n' "$*"; }

if [ "$(uname -m)" != "aarch64" ]; then
  echo "CẢNH BÁO: máy này là $(uname -m), không phải aarch64 (Pi 5 + OS 64-bit). Tiếp tục." >&2
fi

# --- 1. apt ---------------------------------------------------------------
log "Cài gói apt"
sudo apt-get update
# libgl1, libglib2.0-0: opencv-python (bản có GUI) cần khi import cv2. ffmpeg: có ffplay để xem thử RTSP.
# v4l-utils: v4l2-ctl cho webcam. git/curl/tar/jq: tải mediamtx.
sudo apt-get install -y --no-install-recommends \
  git curl ca-certificates tar jq ffmpeg v4l-utils libgl1 libglib2.0-0 \
  python3 build-essential

# coap-client để thử tay: tên gói đổi theo bản Debian (CHƯA KIỂM tên đúng trên Bookworm), thử lần lượt.
if ! command -v coap-client >/dev/null 2>&1; then
  for pkg in libcoap3-bin libcoap2-bin libcoap-bin; do
    if apt-cache show "$pkg" >/dev/null 2>&1; then
      sudo apt-get install -y "$pkg" && break
    fi
  done
fi
command -v coap-client >/dev/null 2>&1 || echo "CẢNH BÁO: chưa có coap-client; tìm bằng: apt-cache search coap" >&2

# --- 2. uv ------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  log "Cài uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv --version

# --- 3. môi trường Python ---------------------------------------------------
log "uv sync"
cd "$PI_DIR"
if [ "$YOLO" -eq 1 ]; then
  uv sync --extra yolo $( [ "$MOSQUITTO" -eq 1 ] && echo --extra mqtt )
  # ncnn không nằm trong pyproject.toml, nên sau đó mọi lệnh phải chạy bằng `uv run --no-sync`.
  uv pip install ncnn
else
  if [ "$MOSQUITTO" -eq 1 ]; then uv sync --extra mqtt; else uv sync; fi
fi

# --- 3b. mosquitto (tùy chọn, xem docs/home-assistant.md) --------------------------
if [ "$MOSQUITTO" -eq 1 ]; then
  log "Cài mosquitto"
  sudo apt-get install -y --no-install-recommends mosquitto mosquitto-clients
  echo "Tạo user và bật cấu hình: xem pi/deploy/homeassistant/mosquitto.conf và docs/home-assistant.md"
fi

# --- 4. mediamtx (bản mới nhất, linux_arm64) ---------------------------------
if [ -x "$MEDIAMTX_DIR/mediamtx" ]; then
  log "mediamtx đã có ở $MEDIAMTX_DIR (xóa thư mục này nếu muốn tải lại bản mới)"
else
  log "Tải mediamtx mới nhất"
  api="https://api.github.com/repos/bluenviron/mediamtx/releases/latest"
  url="$(curl -fsSL "$api" | jq -r '.assets[].browser_download_url | select(endswith("linux_arm64.tar.gz"))' | head -n1)"
  [ -n "$url" ] || { echo "Không tìm thấy file linux_arm64.tar.gz trong $api" >&2; exit 1; }
  echo "$url"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$url" -o "$tmp/mtx.tar.gz"
  mkdir -p "$MEDIAMTX_DIR"
  tar xzf "$tmp/mtx.tar.gz" -C "$MEDIAMTX_DIR" mediamtx
fi
"$MEDIAMTX_DIR/mediamtx" --version || true

# --- 4b. đẩy lên control (tùy chọn) ----------------------------------------------
if [ -n "$CONTROL_URL" ]; then
  log "Ghi ~/.config/nt532/station.env (đẩy lên $CONTROL_URL)"
  mkdir -p "$HOME/.config/nt532"
  (umask 077; printf 'NT532_PI_TOKEN=%s\nNT532_STATION_ARGS=--control-url %s\n' \
    "$NT532_PI_TOKEN" "$CONTROL_URL" > "$HOME/.config/nt532/station.env")
fi

# --- 5. systemd -----------------------------------------------------------------
if [ "$SERVICES" -eq 1 ]; then
  log "Cài dịch vụ systemd"
  for unit in mediamtx nt532-station nt532-camera nt532-thread; do
    # Unit mẫu viết cho user pi, repo /home/pi/nt532, mediamtx /home/pi/mediamtx; sửa theo máy thật.
    sed -e "s|^User=pi|User=$(id -un)|" \
        -e "s|/home/pi/nt532|$REPO|g" \
        -e "s|/home/pi/.config|$HOME/.config|g" \
        -e "s|/home/pi/mediamtx|$MEDIAMTX_DIR|g" \
        -e "s|/home/pi/.local/bin/uv|$(command -v uv)|g" \
        "$HERE/$unit.service" | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
  done
  sudo systemctl daemon-reload
  sudo systemctl enable --now mediamtx
  # Trạm cần config/site.yaml, calibration/ và network.nodes xong mới chạy được: chỉ bật, chưa khởi động.
  # Unit trạm tự bỏ qua khi chưa có calibration/camera.yaml; lúc đó nt532-camera đẩy hình để ngắm và hiệu chuẩn.
  sudo systemctl enable nt532-station
  if [ -n "$CONTROL_URL" ] || [ -f "$HOME/.config/nt532/station.env" ]; then
    sudo systemctl enable --now nt532-camera
    sudo systemctl enable --now nt532-thread
  fi
  echo "mediamtx đã chạy. nt532-station mới được bật (khởi động cùng máy), chưa start."
fi

# --- 6. bước tiếp ------------------------------------------------------------------
cat <<MSG

Xong. Bước tiếp (docs/trien-khai-phan-cung.md):
  1. Kiểm tra:      cd $PI_DIR && uv run --no-sync pytest -q
  2. RCP + OTBR:    firmware/rcp/README.md
  3. Điền config/site.yaml: network.nodes, tags.reference.positions, camera.*
  4. Chép model:    models/fire-n.pt và models/fire-n_ncnn_model/ vào $REPO/models/ (nếu dùng --yolo)
  5. Chạy thử trạm: cd $PI_DIR && uv run --no-sync python scripts/run_station.py --decider rules
  6. Chạy nền:      bash pi/deploy/setup_pi.sh --install-services; sudo systemctl start nt532-station
MSG
