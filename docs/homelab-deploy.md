# Triển khai NT532 control trên VM trong mạng LAN

VM, Pi và máy mở dashboard nằm chung mạng LAN. `control` chạy bằng Docker trên VM, cổng 8800. Pi chỉ là HTTP client: mỗi giây gửi `POST http://<ip-vm>:8800/api/pi/push` (trạng thái, sự kiện, kết quả lệnh), lệnh từ dashboard đi về Pi ngay trong response. Không cloudflared, không VPN, không Caddy.

```
Trình duyệt ──http──> <ip-vm>:8800
Pi          ──http──> <ip-vm>:8800/api/pi/push   (lệnh trả về trong response)
```

## 1. VM: một lệnh

Cần Docker có `docker compose`. Trong repo đã clone:

```bash
git pull && bash homelab/deploy/install-vm.sh
```

Script tự sinh `homelab/deploy/.env` (khóa phiên, token Pi, mật khẩu admin), build, chạy, chờ app lên rồi in ra:

- địa chỉ dashboard `http://<ip-vm>:8800` và mật khẩu `admin` (chỉ in lần đầu, nhớ ghi lại);
- lệnh chạy trên Pi, có sẵn token.

Muốn đặt mật khẩu tự chọn: `ADMIN_PASSWORD='...' bash homelab/deploy/install-vm.sh`. Cổng 8800 bận: thêm `NT532_PORT=8810` trước lệnh ở lần chạy đầu (giá trị được ghi vào `.env`).

## 2. Pi: một lệnh

Trong thư mục `pi/` của repo trên Pi, dán đúng dòng script vừa in, dạng:

```bash
NT532_PI_TOKEN=<token> uv run --no-sync python scripts/run_station.py --decider rules --control-url http://<ip-vm>:8800
```

Thấy dòng `Đẩy lên control: http://<ip-vm>:8800` là đang gửi. Sai token thì log báo `HTTP 401 (sai token)`; mất mạng thì Pi thử lại, lùi dần tới 10 s, rồi tự nối lại. Pi chết thì dashboard báo "Mất kết nối với Pi" sau vài giây, vẫn giữ lịch sử; lệnh gửi lúc đó bị từ chối, lệnh Pi không nhận trong 5 s bị hủy, không chạy muộn. Hình camera chưa có ở chế độ này.

Nên đặt IP tĩnh (hoặc DHCP reservation trên router) cho VM để URL không đổi.

## Vận hành

Tất cả chạy trong `homelab/deploy/`.

| Việc | Lệnh |
|---|---|
| Cập nhật sau `git pull` | `bash install-vm.sh` (giữ nguyên `.env` và dữ liệu) |
| Xem log | `docker compose -f compose.vm.yaml logs -f control` |
| Dừng / chạy lại | `docker compose -f compose.vm.yaml stop` / `start` |
| Đặt lại mật khẩu admin | xóa dòng `NT532_ADMIN_PASSWORD_HASH` trong `.env`, chạy lại `bash install-vm.sh` |
| Đổi token Pi | xóa dòng `NT532_PI_TOKEN` trong `.env`, chạy lại script, chạy lại Pi với token mới |
| Sao lưu dữ liệu (SQLite trong volume `nt532_data`) | `docker run --rm -v nt532_data:/d -v "$PWD":/b alpine tar czf /b/nt532-data-$(date +%F).tgz -C /d .` |

VM bật `ufw` thì mở cổng cho LAN: `sudo ufw allow from 192.168.1.0/24 to any port 8800 proto tcp` (đổi theo dải mạng của bạn).

## An ninh

- Mọi thứ đi HTTP thường trong LAN: ai trong mạng nhà bắt gói được thì thấy token và cookie. Chấp nhận được với LAN tin cậy; đừng mở cổng 8800 ra internet.
- Dashboard điều khiển được trạm: giữ mật khẩu admin dài.
- `.env` chứa bí mật, đã nằm trong `.gitignore`, script đặt quyền 600.
- Cơ chế an toàn phần cứng (giới hạn thời gian, dừng khẩn) nằm ở Pi, không phụ thuộc VM.

`compose.yaml`, `Caddyfile`, `setup-lxc.sh` trong `homelab/deploy/` là bộ cũ (Caddy, Tailscale), không dùng cho cách này.
