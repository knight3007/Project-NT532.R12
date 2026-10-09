# Dùng điện thoại làm camera

Camera sa bàn có thể là điện thoại thay cho webcam. Điện thoại phát RTMP lên mediamtx chạy trên Pi, Pi đọc lại bằng RTSP. Điểm cần cẩn thận là độ trễ của stream (0,5–2 s) và việc điện thoại tự xử lý ảnh (chống rung, lấy nét, phơi sáng).

## 1. Sơ đồ

```
Điện thoại (Larix Broadcaster, Android/iOS)
   │ RTMP, Wi-Fi 5 GHz
   ▼
mediamtx trên Pi 5 (:1935, path cam)
   │ RTSP/TCP, localhost:8554
   ▼
StreamCapture (nt532/vision/stream.py) → FrameHub → orchestrator
```

## 2. Cài mediamtx trên Pi 5

Vào https://github.com/bluenviron/mediamtx/releases, lấy bản mới nhất, file `mediamtx_<phiên bản>_linux_arm64.tar.gz`.

```bash
mkdir -p ~/mediamtx && cd ~/mediamtx
```

```bash
tar xzf ~/Downloads/mediamtx_*_linux_arm64.tar.gz
```

Chạy thử với cấu hình của repo (RTMP :1935, RTSP :8554, path `cam`, tắt HLS/WebRTC/SRT):

```bash
~/mediamtx/mediamtx ~/nt532/pi/deploy/mediamtx.yml
```

Chạy nền bằng systemd, `/etc/systemd/system/mediamtx.service` (sửa `User` và đường dẫn cho khớp):

```ini
[Unit]
Description=mediamtx (camera điện thoại)
After=network-online.target
Wants=network-online.target

[Service]
User=pi
ExecStart=/home/pi/mediamtx/mediamtx /home/pi/nt532/pi/deploy/mediamtx.yml
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now mediamtx
```

## 3. Cài ứng dụng trên điện thoại

Dùng Larix Broadcaster (Android, iOS). Thêm kết nối mới:

- URL: `rtmp://<IP Pi>:1935/cam`.
- Độ phân giải 1280x720, khớp `camera.width/height` trong `site.yaml`. 30 fps. Bitrate 4–6 Mbps. Khoảng cách keyframe 1 s. Khóa chiều ngang.
- **Tắt chống rung (EIS/OIS điện tử)**: nó làm méo khung hình, hỏng thông số nội tại và pose tag.
- Khóa lấy nét và phơi sáng (AF/AE lock), không dùng zoom số.
- Bật giữ màn hình sáng và cắm sạc, kẻo điện thoại ngủ giữa chừng.

Điện thoại phải gắn cứng vào giá. Nhích điện thoại là commissioning hết đúng; phần thị giác có kiểm tra xê dịch, khi báo lệch thì chạy lại `commission.py`.

## 4. Cấu hình site.yaml

```yaml
camera:
  stream:
    url: "rtsp://127.0.0.1:8554/cam"
```

Đặt xong, `run_station.py` và các script dùng camera tự đọc stream. Cũng có thể truyền thẳng `--source rtsp://127.0.0.1:8554/cam` cho script nào có `--source`.

## 5. Hiệu chuẩn nội tại trên chính stream này

Thông số nội tại của webcam không dùng lại cho điện thoại. Hiệu chuẩn lại trên stream, giữ đúng độ phân giải và ống kính (không đổi sang ống góc rộng hay zoom giữa chừng):

```bash
uv run --no-sync python scripts/calibrate_camera.py --capture   # đọc camera.stream.url trong site.yaml
```

Trên Pi, mọi lệnh `uv run` phải có `--no-sync` (các lệnh dưới đây cũng vậy): `ncnn` cài bằng `uv pip` không nằm trong `pyproject.toml`, `uv run` trơn sẽ đồng bộ lại môi trường và gỡ nó.

## 6. Đo độ trễ

```bash
uv run --no-sync python scripts/stream_latency.py --node s1
```

Script bật laser của node `s1` nhiều lần và đo từ lúc gửi lệnh tới khi stream hiện vệt laser. Laser phải rọi vào vùng camera nhìn thấy. Chép giá trị gợi ý vào `camera.stream.latency_s`.

Việc này quan trọng ở bước CORRECT: orchestrator bật laser 700 ms, chờ 0,15 s rồi chỉ nhận khung chụp sau lúc đó. Khung chụp được tính bằng lúc nhận trừ `latency_s`. Nếu `latency_s` nhỏ hơn độ trễ thật thì khung cũ, chụp trước khi bật laser, bị coi là mới và vết laser không bao giờ được tìm thấy. Đặt hơi lớn thì chỉ chờ lâu thêm một chút.

## 7. Kiểm tra nhanh

- Từ laptop cùng mạng: `ffplay rtsp://<IP Pi>:8554/cam` hoặc mở URL đó bằng VLC.
- Xem tag và pose qua stream: `uv run --no-sync python scripts/live_tags.py --source rtsp://127.0.0.1:8554/cam`.

## 8. Sự cố thường gặp

- **Điện thoại ngủ hoặc mất Wi-Fi**: `StreamCapture` tự kết nối lại sau mỗi `reconnect_s`; trong lúc đó dashboard báo lỗi camera. Tắt chế độ tiết kiệm pin cho ứng dụng và giữ màn hình sáng.
- **Độ trễ cao**: giảm bitrate hoặc độ phân giải, dùng Wi-Fi 5 GHz (tốt nhất là router riêng cho sa bàn), để keyframe 1 s. Đo lại bằng `stream_latency.py` sau mỗi thay đổi.
- **Ảnh vỡ khối làm tag mất, vết laser mờ**: tăng bitrate, giữ phơi sáng cố định, tránh cảnh quá tối.
