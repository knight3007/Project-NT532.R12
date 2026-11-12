# Triển khai NT532 control dashboard lên homelab

Dashboard chạy trong một LXC Ubuntu 24.04 trên Proxmox. Caddy lấy chứng chỉ HTTPS tự động và làm reverse proxy tới app `control` (cổng 8800, không mở ra ngoài). Raspberry Pi nằm ở mạng khác, LXC gọi Pi qua Tailscale. Colab worker kết nối ra ngoài bằng WebSocket tới URL HTTPS công khai.

```
Internet ──443──> Router ──> LXC [Caddy] ──> [control:8800] ──Tailscale──> Raspberry Pi
Colab worker ──wss──> (cùng URL HTTPS)
```

Bộ file nằm ở `homelab/deploy/`: `compose.yaml`, `Caddyfile`, `.env.example`, `setup-lxc.sh`.

## 1. Tạo LXC trên Proxmox

- Template Ubuntu 24.04, 2 vCPU, 2 GB RAM, đĩa 16 GB là đủ.
- Unprivileged container, bật Options > Features: `nesting=1` và `keyctl=1` (Docker cần).
- IP LAN cố định, ví dụ `192.168.1.50`.
- Tailscale cần thiết bị TUN. Trên host Proxmox, sửa `/etc/pve/lxc/<CTID>.conf`, thêm:

```
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
```

Khởi động lại LXC sau khi sửa.

## 2. DNS và router

- Có tên miền: tạo bản ghi A trỏ về IP public nhà bạn. Chưa có thì dùng DuckDNS (`ten.duckdns.org`).
- Router: chuyển tiếp cổng 80 và 443 (TCP, thêm 443/UDP nếu muốn HTTP/3) về IP LAN của LXC.
- Cổng 80 bắt buộc mở để Let's Encrypt xác thực và để chuyển hướng sang HTTPS.
- IP public đổi thường xuyên thì cài cập nhật DuckDNS (cron `curl`) hoặc DDNS của router.

## 3. Chạy script cài đặt

Trong LXC, bằng root:

```bash
apt-get update && apt-get install -y git curl
git clone -b feature/homelab-logs https://github.com/knight3007/Project-NT532.R12.git /opt/nt532
sudo bash /opt/nt532/homelab/deploy/setup-lxc.sh
```

Script cài Docker, compose plugin, Tailscale, bật ufw (22, 80, 443), tạo user `nt532`, kéo mã nguồn về `/opt/nt532`, tạo `.env` từ mẫu rồi dừng để bạn điền. Đổi nguồn bằng biến `NT532_REPO_URL`, `NT532_BRANCH`. Script chạy lại an toàn.

## 4. Tailscale

Script không tự chạy `tailscale up` vì cần đăng nhập tương tác.

```bash
# Trên LXC
sudo tailscale up
# Trên Pi
sudo tailscale up
tailscale ip -4      # ghi lại IP của Pi, dạng 100.x.y.z
```

Kiểm tra từ LXC: `curl http://<pi-tailscale-ip>:8080/` phải trả lời. Pi chỉ nên nghe trên Tailscale (hoặc LAN của nó), không port-forward ra Internet.

## 5. Điền `.env`

```bash
sudo -u nt532 nano /opt/nt532/homelab/deploy/.env
```

Sinh bí mật: `openssl rand -hex 32` (mỗi biến một chuỗi riêng).

| Biến | Ý nghĩa |
|---|---|
| `DOMAIN`, `ACME_EMAIL` | Tên miền và email Let's Encrypt |
| `NT532_PI_URL` | `http://<pi-tailscale-ip>:8080` |
| `NT532_SECRET` | Khóa ký phiên |
| `NT532_ADMIN_USER`, `NT532_ADMIN_PASSWORD_HASH` | Tài khoản admin, mật khẩu đã băm theo hướng dẫn của app control |
| `NT532_DB` | Đường dẫn DB trong volume `data/` |
| `NT532_WORKER_TOKEN` | Token cho Colab worker |
| `NT532_DECIDER_TOKEN` | Token cho bộ ra quyết định |

Nếu hash chứa `$` và compose báo lỗi nội suy, gấp đôi thành `$$`.

## 6. Khởi động và kiểm tra

Chạy lại script (hoặc chạy tay):

```bash
sudo bash /opt/nt532/homelab/deploy/setup-lxc.sh
# hoặc
cd /opt/nt532/homelab/deploy && docker compose up -d --build
```

Kiểm tra:

```bash
docker compose ps                       # control phải "healthy"
docker compose logs -f caddy            # thấy "certificate obtained"
curl -I https://DOMAIN                  # 200/302 và có Strict-Transport-Security
curl -I http://DOMAIN                   # phải chuyển hướng 308 sang https
curl https://DOMAIN/healthz
```

Nếu Caddy không lấy được chứng chỉ: kiểm tra DNS trỏ đúng IP public, port 80/443 đã forward, ISP không chặn cổng 80. Thử trước bằng CA staging (thêm `acme_ca https://acme-staging-v02.api.letsencrypt.org/directory` vào khối global của Caddyfile) để tránh bị giới hạn tần suất.

## 7. Cập nhật

```bash
cd /opt/nt532 && sudo -u nt532 git pull --ff-only
cd homelab/deploy && docker compose up -d --build
```

## 8. Sao lưu

- Dữ liệu app: thư mục `homelab/control/data/` (SQLite, log). Với SQLite nên dừng container hoặc dùng `sqlite3 .backup` trước khi chép:

```bash
cd /opt/nt532/homelab/deploy
docker compose stop control
tar czf ~/nt532-data-$(date +%F).tgz -C ../control data
docker compose start control
```

- Chứng chỉ Caddy (tránh xin lại, bị giới hạn): volume `caddy_data`.

```bash
docker run --rm -v nt532_caddy_data:/d -v "$PWD":/b alpine tar czf /b/caddy_data.tgz -C /d .
```

- Tốt nhất là dùng Proxmox backup (vzdump) cho cả LXC theo lịch, thêm `.env` và các bản `.tgz` ở trên sao chép ra máy khác.

## 9. Lưu ý an ninh

- Dashboard công khai: đặt mật khẩu admin dài, ngẫu nhiên; đừng dùng lại mật khẩu khác.
- Chỉ lộ 80/443. Cổng 22 chỉ mở trong LAN (ufw cho phép toàn bộ ở mức mặc định của script, nên giới hạn thêm: `ufw delete allow 22/tcp; ufw allow from 192.168.1.0/24 to any port 22`). Dùng khóa SSH, tắt đăng nhập mật khẩu.
- Pi chỉ truy cập qua Tailscale; không port-forward Pi. Có thể dùng Tailscale ACL để chỉ cho LXC gọi cổng 8080 của Pi.
- Quay vòng token (`NT532_WORKER_TOKEN`, `NT532_DECIDER_TOKEN`, `NT532_SECRET`) định kỳ hoặc khi nghi lộ: sinh mới, sửa `.env`, `docker compose up -d` rồi cập nhật phía Colab. Đổi `NT532_SECRET` sẽ đăng xuất mọi phiên.
- Giới hạn tần suất đăng nhập nên làm ở app (app control chịu trách nhiệm); Caddy mặc định không có rate-limit, cần plugin riêng nếu muốn thêm.
- Tùy chọn: `fail2ban` đọc log của Caddy để chặn IP dò mật khẩu; tự động cập nhật bảo mật bằng `unattended-upgrades`.
- Không commit `.env`. File `.gitignore` trong `homelab/deploy/` đã loại trừ.
- Điều khiển phần cứng (vòi phun) qua Internet: nếu dashboard bị chiếm, kẻ tấn công điều khiển được trạm. Giữ cơ chế an toàn ở phía Pi (giới hạn thời gian, nút dừng khẩn).
