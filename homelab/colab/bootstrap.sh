#!/usr/bin/env bash
# Dựng worker NT532 trên runtime Colab mới (Ubuntu + GPU). Chạy lại nhiều lần vẫn an toàn.
#
# Biến môi trường:
#   NT532_REPO_URL      (bắt buộc) URL git của repo
#   NT532_BRANCH        nhánh, mặc định main
#   NT532_HUB_WS        (bắt buộc) wss://.../api/worker/ws
#   NT532_WORKER_TOKEN  (bắt buộc) token worker
#   NT532_JEV_RUN       tên run Jev, mặc định jev2
#   NT532_WEIGHTS_URL   tar.gz chứa runs/decider/jev/<run> (Drive share dùng gdown) -- hoặc
#   NT532_WEIGHTS_DIR   thư mục trọng số có sẵn (vd /content/drive/MyDrive/nt532/jev2)
#   NT532_WORKDIR       nơi clone, mặc định /content/nt532
#   NT532_LOG           file log, mặc định /content/worker.log
#   NT532_FOREGROUND=1  chạy worker ở foreground (mặc định: nền, ghi log)
set -euo pipefail

: "${NT532_REPO_URL:?thiếu NT532_REPO_URL}"
: "${NT532_HUB_WS:?thiếu NT532_HUB_WS}"
: "${NT532_WORKER_TOKEN:?thiếu NT532_WORKER_TOKEN}"
BRANCH="${NT532_BRANCH:-main}"
RUN="${NT532_JEV_RUN:-jev2}"
WORKDIR="${NT532_WORKDIR:-/content/nt532}"
LOG="${NT532_LOG:-/content/worker.log}"
export PATH="$HOME/.local/bin:$PATH"

# 1. uv
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. mã nguồn
if [ -d "$WORKDIR/.git" ]; then
  git -C "$WORKDIR" fetch --depth 1 origin "$BRANCH"
  git -C "$WORKDIR" checkout -q -B "$BRANCH" FETCH_HEAD
else
  git clone --depth 1 --branch "$BRANCH" "$NT532_REPO_URL" "$WORKDIR"
fi
cd "$WORKDIR/pi"

# 3. thư viện (torch lấy bản CUDA từ PyPI trên Linux)
uv sync --extra decider

# 4. trọng số Jev -> runs/decider/jev/<run> (đường dẫn tương đối repo pi/)
DEST="runs/decider/jev/$RUN"
if [ ! -d "$DEST" ] || [ -z "$(ls -A "$DEST" 2>/dev/null)" ]; then
  mkdir -p "$DEST"
  if [ -n "${NT532_WEIGHTS_DIR:-}" ]; then
    cp -r "$NT532_WEIGHTS_DIR"/. "$DEST"/
  elif [ -n "${NT532_WEIGHTS_URL:-}" ]; then
    TMP="$(mktemp -d)"
    if [[ "$NT532_WEIGHTS_URL" == *drive.google.com* ]]; then
      uv tool run gdown --fuzzy "$NT532_WEIGHTS_URL" -O "$TMP/w.tar.gz"
    else
      curl -L --fail "$NT532_WEIGHTS_URL" -o "$TMP/w.tar.gz"
    fi
    # tar có thể chứa thư mục <run>/ hoặc file trải phẳng: bỏ thư mục đầu nếu trùng tên run
    if tar -tzf "$TMP/w.tar.gz" | head -1 | grep -q "^\(\./\)\?$RUN/"; then
      tar -xzf "$TMP/w.tar.gz" -C "$DEST" --strip-components=1
    else
      tar -xzf "$TMP/w.tar.gz" -C "$DEST"
    fi
    rm -rf "$TMP"
  else
    echo "Thiếu trọng số: đặt NT532_WEIGHTS_URL hoặc NT532_WEIGHTS_DIR" >&2
    exit 1
  fi
fi

# 5. dừng worker cũ (chạy lại bootstrap) rồi chạy worker mới
pkill -f scripts/colab_worker.py 2>/dev/null || true
CMD=(uv run --no-sync python scripts/colab_worker.py --hub-ws "$NT532_HUB_WS"
     --token "$NT532_WORKER_TOKEN" --jev-run "$RUN" --device cuda)
if [ "${NT532_FOREGROUND:-0}" = "1" ]; then
  exec "${CMD[@]}"
fi
nohup "${CMD[@]}" >"$LOG" 2>&1 &
echo "worker pid $! ; log: $LOG"
