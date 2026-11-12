# homelab/deploy

Bộ triển khai production cho NT532 control dashboard (Caddy + control trong Docker Compose, Tailscale trên host LXC).

| File | Vai trò |
|---|---|
| `compose.yaml` | Dịch vụ `control` (không publish port) và `caddy` (80/443) |
| `Caddyfile` | HTTPS tự động, header bảo mật, không đệm cho `/camera/*` và `/api/stream` |
| `.env.example` | Mẫu biến môi trường; sao chép thành `.env` |
| `setup-lxc.sh` | Cài Docker, Tailscale, ufw, user `nt532`, kéo mã, chạy compose; chạy lại an toàn |

Tóm tắt nhanh trên Ubuntu 24.04 (root):

```bash
git clone -b feature/homelab-logs https://github.com/knight3007/Project-NT532.R12.git /opt/nt532
sudo bash /opt/nt532/homelab/deploy/setup-lxc.sh   # lần 1: tạo .env rồi dừng
sudo tailscale up                                   # đăng nhập tương tác
nano /opt/nt532/homelab/deploy/.env                 # điền biến
sudo bash /opt/nt532/homelab/deploy/setup-lxc.sh   # lần 2: build và chạy
```

Hướng dẫn đầy đủ (Proxmox, DNS, router, sao lưu, an ninh): `docs/homelab-deploy.md`.
