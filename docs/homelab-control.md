# Trung tâm điều khiển trên homelab

Dashboard của Pi (`pi/src/nt532/dashboard/server.py`) không có xác thực và chỉ phù hợp mạng LAN của sa bàn. Để dùng được từ xa (điện thoại, lúc demo), ta thêm một dịch vụ riêng chạy trên homelab, nằm ở `homelab/control/`.

## Kiến trúc

```
Trình duyệt --HTTPS--> Caddy --> control (FastAPI, :8800) --Tailscale--> Pi (dashboard :8080)
                                      |
                                      +-- SQLite: sự kiện, telemetry, các lượt xử lý
```

- Người dùng chỉ nói chuyện với `control`: đăng nhập, xem trạng thái, gửi lệnh. Pi không bao giờ lộ ra internet.
- `control` hỏi Pi mỗi giây (`/api/state`, `/api/events`), giữ trạng thái mới nhất trong bộ nhớ, ghi lịch sử vào SQLite và đẩy cho trình duyệt qua SSE. Hình camera là proxy MJPEG.
- Lệnh đi qua một danh sách cho phép; lệnh sa bàn ảo chỉ được nhận khi Pi báo `sim = true`. Mọi lệnh được ghi lại với tên người dùng.
- Pi khởi động lại thì `seq` của sự kiện về nhỏ: poller phát hiện và đồng bộ lại.

## Các mốc

- M1 (xong): trang Tổng quan chạy được với sa bàn ảo.
- M2 (xong): trang Nhật ký (lọc, biểu đồ telemetry, CSV) và trang Lượt, thanh điều hướng chung.
- M3: trang bộ quyết định (đổi và theo dõi jev/hybrid/remote).
- M4: worker Colab cho bộ quyết định từ xa.
- M5: cấu hình Caddy và triển khai Docker trên homelab.

## Chạy M1 trên máy phát triển

Hai tiến trình: trạm ảo và `control`. Chi tiết, biến môi trường và Docker ở `homelab/control/README.md`.

```
# cửa sổ 1, từ pi/
uv run --no-sync python scripts/run_station.py --source sim --port 8080

# cửa sổ 2, từ homelab/control/
uv sync
$env:NT532_ADMIN_PASSWORD = "mat-khau-thu-nghiem"
uv run uvicorn app.main:app --port 8800
```

Mở http://127.0.0.1:8800 và đăng nhập bằng `admin`.

## M2: Nhật ký và Lượt

Thanh điều hướng ở mọi trang: Tổng quan · Nhật ký · Lượt · Bộ quyết định · Cài đặt (chưa làm).

- `/history`: bộ lọc node, loại sự kiện, mức, khoảng thời gian (15 phút, 1 giờ, 24 giờ, 7 ngày hoặc tùy chọn) và tìm trong nội dung. Bộ lọc nằm trong URL nên chia sẻ được. Phía trên bảng là biểu đồ nhiệt độ, gas (và độ ẩm nếu bật) của từng node: đường trung bình, dải min–max, ngưỡng nét đứt màu ember, các lượt xử lý tô nền. Biểu đồ vẽ bằng SVG thuần (`static/js/chart.js`), mỗi chuỗi tối đa 600 điểm do máy chủ gộp theo ô thời gian. Bảng sự kiện 100 dòng mỗi trang, bấm một dòng để xem JSON thêm.
- `/runs`: ô tổng hợp (số lượt, dập được, bỏ qua, chỉ báo động, gọi người, lỗi) và bảng các lượt mới nhất trước; mở một lượt để xem JSON gốc và đường dẫn "Xem nhật ký của lượt" (mở `/history` đúng khoảng thời gian của lượt, rộng thêm 5 giây hai đầu).
- API (cần đăng nhập, `from`/`to` là epoch giây): `GET /api/events` (`node`, `kind` lặp lại được, `level`, `q`, `from`, `to`, `limit`, `page` hoặc `offset`; tổng số dòng ở header `X-Total-Count`), `GET /api/events.csv`, `GET /api/telemetry?node&from&to&points` (min/avg/max theo ô, kèm ngưỡng và các lượt giao khoảng đó), `GET /api/telemetry.csv`, `GET /api/runs?from&to&outcome&limit` (trả `{runs, summary}`; `summary` không bị lọc theo `outcome`). CSV có BOM UTF-8 để Excel đọc đúng tiếng Việt.
- SQLite: lược đồ có phiên bản (`PRAGMA user_version = 2`). Lần mở đầu tiên trên CSDL của M1 tự thêm chỉ mục `(node,time)`, `(kind,time)` cho `events`, các cột phẳng của `runs` (kết thúc, mục tiêu, tọa độ, mặt, vòi, bộ quyết định, số lần, số lần phun, sai số) và điền chúng từ `data_json`. Chạy lại bao nhiêu lần cũng an toàn. Mỗi lượt một dòng theo khóa (id Pi, lúc bắt đầu), cập nhật khi kết quả đổi; dọn dữ liệu cũ (`NT532_RETENTION_DAYS`) áp dụng cho cả `runs`.
