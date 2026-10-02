# NT532 — Hệ thống phát hiện và dập lửa mô phỏng qua Thread

Sensor ESP32-H2 báo động qua Thread/CoAP. Raspberry Pi 5 dùng một webcam cố định để xác minh và định vị bia trên bảng, rồi gửi góc ngắm qua Wi-Fi tới ESP32-S3 điều khiển pan–tilt, bơm nước và laser tâm ngắm.

Kế hoạch đầy đủ: [docs/ke-hoach-trien-khai-NT532.md](docs/ke-hoach-trien-khai-NT532.md). Hợp đồng CoAP ở mục 4, quy ước tọa độ ở mục 3.

## Cấu trúc

| Thư mục | Nội dung | Người phụ trách |
| --- | --- | --- |
| `pi/src/nt532/vision/` | YOLO, hiệu chuẩn camera, pose tag, định vị, tìm vết laser | Hùng |
| `pi/src/nt532/net/` | CoAP server cho sensor, CoAP client tới S3 | Hậu |
| `pi/src/nt532/orchestrator/` | State machine, tính pan/tilt, log | Hậu |
| `firmware/sensor-h2/` | Firmware sensor node (ESP-IDF, Thread) | Hậu |
| `firmware/rcp/` | Ghi chú nạp RCP cho H2 DevKit và cài OTBR | Hậu |
| `firmware/actuator-s3/` | Firmware node chấp hành (ESP-IDF, Wi-Fi) | Hiếu |
| `config/site.yaml` | Số đo sa bàn, ID và cỡ tag, giới hạn góc, ngưỡng | Cả nhóm |
| `calibration/` | Kết quả hiệu chuẩn camera và servo | Hùng, Hiếu |
| `data/`, `models/` | Ảnh, dataset, trọng số (không đưa lên git) | Hùng |

## Chạy phần Python

Cần Python 3.11 trở lên và [uv](https://docs.astral.sh/uv/).

```bash
cd pi
uv sync
uv run pytest
```

Thêm YOLO khi cần train hoặc suy luận:

```bash
uv sync --extra yolo
```

## Firmware

Mỗi thư mục con trong `firmware/` là một project ESP-IDF riêng. Xem [firmware/README.md](firmware/README.md).

## Quy ước

- Đơn vị mét và độ. Gốc tọa độ ở góc trước bên trái mặt bàn: X sang phải, Y về phía bảng bia, Z lên trên.
- Mọi số đo sa bàn nằm trong `config/site.yaml`, không gõ cứng trong code.
- Mốc thời gian lấy theo đồng hồ của Pi.
