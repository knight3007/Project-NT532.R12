# NT532 control

Trung tâm điều khiển web cho trạm chữa cháy NT532. Chạy trên homelab (Docker, sau Caddy) và nói chuyện với dashboard của Raspberry Pi qua Tailscale. Người dùng chỉ chạm vào dịch vụ này, không bao giờ vào thẳng Pi.

## M1 gồm những gì

- Trang "Tổng quan": dải pha, camera trực tiếp (proxy MJPEG), thẻ node s1/s2, bộ quyết định, các lệnh, nhật ký.
- Đăng nhập một tài khoản quản trị (scrypt, cookie phiên ký, giới hạn 5 lần sai mỗi phút theo IP).
- Poller hỏi Pi mỗi giây, lưu sự kiện, telemetry và các lượt xử lý vào SQLite, dọn dữ liệu cũ mỗi ngày.
- `/api/cmd` chỉ chuyển tiếp các lệnh trong danh sách cho phép (xem `COMMANDS` trong `app/main.py`), ghi lại thành sự kiện `cmd` kèm tên người dùng.
- Chưa làm (các mốc sau): worker Colab, cấu hình Caddy.

## M2 gồm những gì

- Thanh điều hướng: Tổng quan · Nhật ký · Lượt · Bộ quyết định · Cài đặt (sắp có).
- `/history` (Nhật ký): lọc theo node, loại, mức, khoảng thời gian, từ khóa; biểu đồ nhiệt độ và gas theo node (SVG thuần, dải min–max, ngưỡng, vùng lượt xử lý); bảng 100 dòng mỗi trang, bấm dòng để xem JSON thêm; nút "Xuất CSV".
- `/runs` (Lượt): ô tổng hợp theo kết quả, bảng các lượt, JSON gốc, liên kết sang nhật ký đúng khoảng thời gian của lượt.
- API mới: `/api/events` (thêm `from`, `to`, `q`, `level`, `kind` lặp lại, `page`/`offset`, header `X-Total-Count`), `/api/events.csv`, `/api/telemetry` (gộp ô, tối đa 600 điểm), `/api/telemetry.csv`, `/api/runs`.
- CSDL tự nâng lên phiên bản 2 khi mở (chỉ mục, cột phẳng của `runs`); CSDL của M1 dùng tiếp được. Chi tiết ở `docs/homelab-control.md`.

## Chạy khi phát triển với sa bàn ảo

1. Chạy trạm ảo (cửa sổ riêng), từ `D:\code\IoT\pi`:

   ```
   uv run --no-sync python scripts/run_station.py --source sim --port 8080
   ```

2. Chạy dịch vụ, từ `homelab/control`:

   ```
   uv sync
   $env:NT532_ADMIN_PASSWORD = "mat-khau-thu-nghiem"   # PowerShell; bash: export ...
   uv run uvicorn app.main:app --port 8800
   ```

3. Mở http://127.0.0.1:8800, đăng nhập bằng `admin` và mật khẩu vừa đặt.

Khi chạy thật, dùng mật khẩu băm thay cho `NT532_ADMIN_PASSWORD`:

```
uv run python -m app.hash_password
```

rồi dán kết quả vào `NT532_ADMIN_PASSWORD_HASH`.

## Pi tự đẩy lên (khuyên dùng khi Pi khác mạng)

Khi Pi ở sau NAT, VM không gọi vào được Pi. Đặt `NT532_PI_TOKEN` (sinh bằng `openssl rand -hex 32`, đặt cùng giá trị ở Pi) thì chế độ push bật: VM không còn gọi `NT532_PI_URL`, Pi là HTTP client.

- Pi `POST /api/pi/push` với `Authorization: Bearer <NT532_PI_TOKEN>`, body `{"boot", "state", "events", "results"}`. `boot` là chuỗi ngẫu nhiên mỗi lần tiến trình Pi khởi động; `state` và `events` y hệt `GET /api/state` và `GET /api/events` của Pi; `results` là `[{"id", "reply"}]` của các lệnh nhận ở lần trước.
- Response `{"ack": <seq lớn nhất đã nhận trong boot này>, "cmds": [{"id", "cmd", ...}]}`. Pi chỉ gửi lại các sự kiện có seq lớn hơn `ack`. Boot đổi = Pi khởi động lại, VM đồng bộ lại seq.
- Lệnh từ `/api/cmd` và đổi bộ quyết định vào hàng đợi, chỉ trao cho Pi đúng một lần qua response, kết quả về ở lần push sau. Quá `NT532_CMD_TIMEOUT_S` mà Pi chưa nhận thì lệnh bị xóa (không chạy muộn) và trả 504; Pi mất kết nối trả 502.
- Sai hoặc thiếu token trả 401; `NT532_PI_TOKEN` rỗng thì endpoint trả 503. Body tối đa 2 MB, tối đa 1000 sự kiện mỗi lần.
- Hình camera: Pi `POST /api/pi/frame` (cùng Bearer token, body là JPEG thô, tối đa 1 MB). `/camera/snapshot.jpg` và `/camera/stream.mjpg` phát lại khung mới nhất; quá 10 s không có khung mới thì trả 503.
- Mạng Thread: `pi/scripts/thread_monitor.py` (dịch vụ `nt532-thread`) `POST /api/pi/thread` mỗi ~10 s (cùng Bearer token, JSON tối đa 256 KB): OTBR, ping/CoAP/vai trò từng node, topology. Tổng quan có panel "Mạng Thread"; mỗi mẫu ghi một dòng mỗi node vào bảng `thread` (giữ theo `retention_days`). Lịch sử `GET /api/thread/history?hours=` và CSV `GET /api/thread.csv?hours=` (cần đăng nhập); trang Nhật ký có biểu đồ ping, mất gói, CoAP theo node.
- Không có push trong `max(5, 3 x NT532_POLL_S)` giây thì ghi "Mất kết nối với Pi".
- Camera chưa có ở chế độ này: `/camera/stream.mjpg` và `/camera/snapshot.jpg` trả 503.

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `NT532_PI_URL` | `http://127.0.0.1:8080` | Địa chỉ dashboard của Pi |
| `NT532_PI_ACCESS_CLIENT_ID` | (trống) | Client ID của service token Cloudflare Access, dùng khi Pi ở sau Access; phải đặt cùng `..._SECRET` |
| `NT532_PI_ACCESS_CLIENT_SECRET` | (trống) | Client Secret của service token Cloudflare Access (gửi kèm mọi request tới Pi) |
| `NT532_SECRET` | sinh ngẫu nhiên (có cảnh báo) | Khóa ký cookie phiên; đặt cố định để phiên sống qua lần khởi động lại |
| `NT532_ADMIN_USER` | `admin` | Tên đăng nhập |
| `NT532_ADMIN_PASSWORD_HASH` | (bắt buộc, hoặc `NT532_ADMIN_PASSWORD`) | Chuỗi scrypt do `app.hash_password` tạo |
| `NT532_ADMIN_PASSWORD` | | Mật khẩu thường, chỉ để chạy thử |
| `NT532_HTTPS` | `false` | `true` khi chạy sau HTTPS: cookie có cờ `Secure` |
| `NT532_DB` | `data/control.sqlite` | Đường dẫn SQLite |
| `NT532_POLL_S` | `1.0` | Chu kỳ hỏi Pi (giây) |
| `NT532_PI_TOKEN` | (trống: chế độ kéo) | Token Bearer Pi dùng khi tự đẩy lên `/api/pi/push`; khác rỗng thì bật chế độ push |
| `NT532_CMD_TIMEOUT_S` | `5.0` | Hạn chờ Pi nhận và chạy một lệnh (chế độ push) |
| `NT532_RETENTION_DAYS` | `14` | Số ngày giữ sự kiện, telemetry và các lượt |

Thiếu cả hai biến mật khẩu thì dịch vụ từ chối khởi động và in hướng dẫn.

## Docker

```
cp .env.example .env    # điền NT532_SECRET, NT532_ADMIN_PASSWORD_HASH, NT532_PI_URL, NT532_HTTPS=true
docker compose up -d --build
```

Cổng 8800, dữ liệu ở `./data`. Dịch vụ không tự lo HTTPS: đặt Caddy phía trước.

## Kiểm thử

```
uv run pytest -q
uv run ruff check .
```

Test dùng một Pi giả chạy trong tiến trình, không cần trạm thật.

## Cấu trúc

- `app/main.py`: FastAPI, xác thực, whitelist lệnh, proxy camera, SSE `/api/stream`.
- `app/pi_client.py`: poller, phát hiện Pi khởi động lại (seq về nhỏ), theo dõi `pi_online`.
- `app/store.py`: SQLite (`events`, `telemetry`, `runs`), migration có phiên bản, truy vấn lọc và gộp ô.
- `app/history.py`: router `/api/events`, `/api/telemetry`, `/api/runs` và các file CSV.
- `app/static/`: HTML/CSS/JS thuần, không cần bước build (`history.html`, `runs.html`, `js/chart.js`, `js/shell.js` cho M2).

## M3/M4: bộ quyết định và hub Colab

Trang `/decider` (đổi bộ quyết định trên Pi, theo dõi worker GPU) và hub trong `app/hub.py`. Pi vẫn dùng `RemoteDecider`, chỉ trỏ vào hub; Colab mở WebSocket RA hub (xem `homelab/colab/protocol.md`).

```
Pi --POST /decide (Tailscale)--> hub <--WebSocket /api/worker/ws (worker mở ra)-- Colab (GPU, Jev)
```

Gắn vào `app/main.py` (trong `create_app`):

```python
from .hub import hub, router as hub_router
...
app.include_router(hub_router)         # sau khi tạo app, trước app.mount("/static", ...)
# trong lifespan: await hub.startup(app) sau khi tạo app.state.pi; await hub.shutdown() khi tắt
```

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `NT532_WORKER_TOKEN` | (trống: tắt cổng worker) | Token worker Colab gửi trong `Authorization: Bearer` (hoặc `?token=`) |
| `NT532_DECIDER_TOKEN` | (trống) | Token Pi phải gửi tới `/decide`, `/health`. Trống thì chỉ nhận IP Tailscale `100.64.0.0/10` hoặc loopback |
| `NT532_DECIDE_DEADLINE_MS` | `2500` | Hạn chờ worker; nên nhỏ hơn timeout phía Pi (3 s) |
| `NT532_PUBLIC_DECIDE_URL` | (trống) | Địa chỉ hub mà Pi gọi được, vd `http://<ip-tailscale-của-LXC>:8800`; cần cho lựa chọn "Jev qua homelab" |

- `/decide` và `/health` không dùng cookie phiên; mọi lỗi (không worker, quá hạn, worker báo lỗi) trả HTTP 503 để `RemoteDecider` của Pi rơi về luật.
- Sau Caddy/proxy, IP máy gọi là IP của proxy, nên nên đặt `NT532_DECIDER_TOKEN` (và đặt cùng giá trị ở Pi) thay vì dựa vào dải Tailscale.
- Worker mới nối sẽ thay worker cũ (đóng cũ với lý do `replaced`). Hub gửi `ping` mỗi 20 s, 15 s không thấy `pong` thì ngắt.
- API cho trang (cần đăng nhập): `GET /api/decider/status`, `POST /api/decider/switch`, `POST /api/decider/test`, `POST /api/decider/worker/shutdown`. Sự kiện của hub được ghi vào nhật ký với `kind: "hub"`.
- Caddy cần cho WebSocket đi qua `/api/worker/ws` (reverse_proxy mặc định đã hỗ trợ).
