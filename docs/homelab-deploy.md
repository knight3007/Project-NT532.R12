# Triển khai NT532 control lên homelab

App `control` chạy bằng Docker trên một VM. cloudflared trên VM public nó ra một domain HTTPS. Pi ở mạng khác và chỉ là HTTP client: mỗi giây Pi gửi `POST https://<domain>/api/pi/push` (trạng thái, sự kiện, kết quả lệnh), lệnh từ trang web đi về Pi ngay trong response. Pi không mở cổng, không cài thêm gì; không dùng VPN, không cần Caddy hay port-forward.

```
Trình duyệt ──https──> <domain> (cloudflared trên VM) ──> control :8800
Pi          ──https──> <domain>/api/pi/push  (lệnh trả về trong response)
```

Pi chết thì `control` báo "Mất kết nối với Pi" sau vài giây và vẫn giữ toàn bộ lịch sử đã nhận. Lệnh gửi khi Pi mất kết nối bị từ chối ngay; lệnh Pi không nhận trong `NT532_CMD_TIMEOUT_S` (mặc định 5 s) bị hủy, không chạy muộn. Hình camera chưa có ở chế độ này.

## 1. Trên VM

Cần Docker và cloudflared đã chạy sẵn. Từ thư mục repo đã clone (ví dụ `/opt/nt532`):

```bash
cd /opt/nt532 && git pull
cd homelab/deploy
cp .env.example .env
```

Điền `.env` (sinh bí mật bằng `openssl rand -hex 32`, mỗi biến một chuỗi riêng):

| Biến | Giá trị |
|---|---|
| `DOMAIN` | domain public của control (compose bắt buộc có) |
| `NT532_SECRET` | khóa ký phiên |
| `NT532_PI_TOKEN` | token Pi gửi kèm mỗi lần push; bật chế độ push |
| `NT532_HTTPS` | `true` |
| `NT532_WORKER_TOKEN`, `NT532_DECIDER_TOKEN` | cho worker Colab và bộ quyết định từ xa |
| `NT532_ADMIN_PASSWORD_HASH` | điền ở bước dưới, đặt trong nháy đơn |

`NT532_PI_URL` không dùng ở chế độ push, để nguyên.

Tạo `compose.override.yaml` cạnh `compose.yaml` (compose tự đọc): mở cổng cho riêng VM và cho app đọc IP thật của người dùng từ header proxy, để giới hạn đăng nhập sai tính theo từng người.

```bash
cat > compose.override.yaml <<'EOF'
services:
  control:
    ports:
      - "127.0.0.1:8800:8800"
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800", "--forwarded-allow-ips", "*"]
EOF
```

Quyền ghi dữ liệu (container chạy bằng uid 10001), build, tạo mật khẩu admin:

```bash
sudo mkdir -p ../control/data && sudo chown -R 10001:10001 ../control/data
docker compose build control
docker compose run --rm control python -m app.hash_password
```

Dán chuỗi `scrypt$...` vào `.env` dạng `NT532_ADMIN_PASSWORD_HASH='scrypt$...'`. Chạy (chỉ `control`; `docker compose up -d` trần sẽ bật cả Caddy và chiếm 80/443):

```bash
docker compose up -d control
curl http://127.0.0.1:8800/healthz
```

Trong cloudflared, thêm Public Hostname `<domain>` → `HTTP` → `localhost:8800` (dashboard Zero Trust), hoặc một mục `ingress` với `service: http://localhost:8800` trong `config.yml` rồi `sudo systemctl restart cloudflared`. Kiểm tra `curl https://<domain>/healthz`, mở `https://<domain>` và đăng nhập `admin`.

## 2. Trên Pi

```bash
cd ~/nt532/pi   # thư mục pi/ của repo trên Pi
NT532_PI_TOKEN=<cùng token với VM> uv run --no-sync python scripts/run_station.py --decider rules --control-url https://<domain>
```

Dòng `Đẩy lên control: https://<domain>` xuất hiện là đang gửi. Dashboard LAN của Pi (`http://<ip-pi>:8080/`) vẫn chạy như cũ. Sai token thì log báo `HTTP 401 (sai token)`; mất mạng thì Pi thử lại, lùi dần tới 10 s, và tự nối lại.

## 3. Cập nhật

```bash
cd /opt/nt532 && git pull && cd homelab/deploy && docker compose up -d --build control
docker compose logs -f control
```

## 4. Sao lưu

Dữ liệu ở `homelab/control/data/` (SQLite). Dừng container trước khi chép:

```bash
docker compose stop control
tar czf ~/nt532-data-$(date +%F).tgz -C ../control data
docker compose start control
```

## 5. An ninh

- Mật khẩu admin dài, ngẫu nhiên: trang này điều khiển được trạm.
- `NT532_PI_TOKEN` lộ thì người khác giả được Pi và nhận lệnh: sinh mới, sửa `.env` ở VM, `docker compose up -d control`, chạy lại Pi với token mới.
- Không commit `.env` (đã có trong `homelab/deploy/.gitignore`).
- Cơ chế an toàn phần cứng (giới hạn thời gian, dừng khẩn) nằm ở phía Pi, không phụ thuộc VM.
