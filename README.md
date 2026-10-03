# NT532 — Hệ thống phát hiện và dập lửa mô phỏng qua Thread

Hai node ESP32-H2 vừa đọc cảm biến vừa mang một cụm pan–tilt, bơm nước và laser tâm ngắm. Node báo động qua Thread/CoAP; Raspberry Pi 5 dùng một webcam cố định để xác minh và định vị bia trên bảng, rồi gửi góc ngắm qua Thread về đúng node đó.

Kế hoạch đầy đủ: [docs/ke-hoach-trien-khai-NT532.md](docs/ke-hoach-trien-khai-NT532.md). Hợp đồng CoAP ở mục 4, quy ước tọa độ ở mục 3. Kế hoạch gốc dùng một vòi trên ESP32-S3; phần đã đổi ghi ở [docs/thay-doi-hai-voi.md](docs/thay-doi-hai-voi.md).

## Cấu trúc

| Thư mục | Nội dung | Người phụ trách |
| --- | --- | --- |
| `pi/src/nt532/vision/` | YOLO, hiệu chuẩn camera, pose tag, định vị, tìm vết laser | Hùng |
| `pi/src/nt532/net/` | CoAP server cho sensor, CoAP client tới node chấp hành | Hậu |
| `pi/src/nt532/orchestrator/` | State machine, tính pan/tilt, log | Hậu |
| `pi/scripts/` | Sinh file in, hiệu chuẩn camera, chuẩn bị dataset, train | Hùng |
| `firmware/sensor-h2/` | Firmware node H2: cảm biến và chấp hành (ESP-IDF, Thread) | Hậu, Hiếu |
| `firmware/rcp/` | Ghi chú nạp RCP cho H2 DevKit và cài OTBR | Hậu |
| `firmware/actuator-s3/` | Firmware chấp hành trên S3, đường lui nếu H2 không kham nổi | Hiếu |
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

Sau khi cài `--extra yolo`, chạy lệnh bằng `uv run --extra yolo ...`; `uv run` trơn sẽ gỡ torch ra khỏi môi trường.

Các script trong `pi/scripts/`:

| Script | Việc |
| --- | --- |
| `make_print_sheets.py` | Sinh PDF in tag và bảng ChArUco vào `docs/print/` |
| `calibrate_camera.py` | Chụp ảnh bảng ChArUco và xuất `calibration/camera.yaml` |
| `prepare_classesoffire.py` | Lọc ảnh trùng và chia ClassesOfFire thành train/val/test |
| `prepare_fire_mix.py` | Gộp D-Fire với hai bộ lửa trong nhà về cùng thứ tự lớp |
| `train_detect.py` | Train YOLO phát hiện lửa |
| `eval_detect.py` | Đánh giá trọng số YOLO trên tập val hoặc test |

Dataset và nguồn tải ghi ở [docs/datasets.md](docs/datasets.md).

## Firmware

Mỗi thư mục con trong `firmware/` là một project ESP-IDF riêng. Xem [firmware/README.md](firmware/README.md).

## Quy ước

- Đơn vị mét và độ. Gốc tọa độ ở góc trước bên trái mặt bàn: X sang phải, Y về phía bảng bia, Z lên trên.
- Mọi số đo sa bàn nằm trong `config/site.yaml`, không gõ cứng trong code.
- Mốc thời gian lấy theo đồng hồ của Pi.
