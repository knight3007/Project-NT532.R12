# NT532 — Hệ thống phát hiện và dập lửa mô phỏng qua Thread

Hai node ESP32-H2 vừa đọc cảm biến vừa mang một cụm pan–tilt, bơm nước và laser tâm ngắm. Node báo động qua Thread/CoAP; Raspberry Pi 5 dùng một webcam cố định để xác minh và định vị bia trên bảng, rồi gửi góc ngắm qua Thread về đúng node đó.

Kế hoạch đầy đủ: [docs/ke-hoach-trien-khai-NT532.md](docs/ke-hoach-trien-khai-NT532.md). Hợp đồng CoAP ở mục 4, quy ước tọa độ ở mục 3. Kế hoạch gốc dùng một vòi trên ESP32-S3; phần đã đổi ghi ở [docs/thay-doi-hai-voi.md](docs/thay-doi-hai-voi.md).

## Cấu trúc

| Thư mục | Nội dung | Người phụ trách |
| --- | --- | --- |
| `pi/src/nt532/vision/` | YOLO, hiệu chuẩn camera, pose tag, định vị, tìm vết laser | Hùng |
| `pi/src/nt532/net/` | Payload CoAP, CoAP server cho sensor, CoAP client tới node chấp hành | Hậu |
| `pi/src/nt532/orchestrator/` | State machine, gộp dữ liệu cho bộ quyết định, tính pan/tilt, log | Hậu |
| `pi/src/nt532/decider/` | Bộ kịch bản tổng hợp, baseline luật, mô hình Jev | Hùng |
| `pi/src/nt532/dashboard/` | Dashboard web của trạm | Hùng |
| `pi/src/nt532/sim/` | Sa bàn ảo: camera, thế giới ảo có cảm biến và node chấp hành | Hùng |
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

Sau khi cài `--extra yolo`, chạy lệnh bằng `uv run --extra yolo ...`; `uv run` trơn sẽ gỡ torch ra khỏi môi trường. Tương tự, mô hình quyết định Jev cần `--extra decider` (torch, torchvision, transformers, peft); orchestrator chạy bằng luật thì không cần.

Các script trong `pi/scripts/`:

| Script | Việc |
| --- | --- |
| `make_print_sheets.py` | Sinh PDF in tag và bảng ChArUco vào `docs/print/` |
| `make_target_cards.py` | Sinh PDF thẻ bia hình lửa từ ảnh D-Fire mà model nhận rõ |
| `calibrate_camera.py` | Chụp ảnh bảng ChArUco và xuất `calibration/camera.yaml` |
| `live_tags.py` | Xem tag, pose camera, pose node và viền bảng bia; phím C để commissioning |
| `commission.py` | Commissioning hoặc kiểm tra xê dịch, không cần màn hình (cho Pi) |
| `measure.py` | Các bài đo: click điểm, định vị bia, pose tag, vết laser; ghi CSV vào `runs/measure/` |
| `report_errors.py` | Thống kê sai số từ CSV của `measure.py` và vẽ hình |
| `live_detect.py` | Xem trực tiếp model phát hiện lửa trên webcam, video hoặc ảnh |
| `capture_frames.py` | Chụp khung hình vào thư mục, bằng phím hoặc tự động |
| `autolabel.py` | Tạo nhãn nháp YOLO cho ảnh thẻ bia tự chụp, để sửa tay rồi train |
| `prepare_fire_mix.py` | Gộp D-Fire với hai bộ lửa trong nhà và ảnh không lửa về cùng thứ tự lớp |
| `prepare_negatives.py` | Lấy ảnh không lửa từ COCO và bộ Fire Recognition (Mendeley) |
| `prepare_classesoffire.py` | Lọc ảnh trùng và chia ClassesOfFire thành train/val/test |
| `train_detect.py` | Train YOLO phát hiện lửa |
| `train_classify.py` | Train YOLO phân loại lớp đám cháy theo chất liệu (A/B/C/D/F) |
| `eval_detect.py` | Đánh giá trọng số YOLO trên tập val hoặc test |
| `eval_false_alarms.py` | Đo tỷ lệ báo nhầm trên ảnh không có lửa |
| `export_model.py` | Xuất model sang NCNN hoặc ONNX cho Pi |
| `bench_detect.py` | Đo thời gian suy luận bằng CPU |
| `log_pi_load.py` | Ghi CPU, RAM, nhiệt độ của Pi ra CSV |
| `sim_demo.py` | Chạy trọn kịch bản trên sa bàn ảo bằng YOLO thật và in sai số từng bước |
| `run_station.py` | Chạy trạm (orchestrator, bộ quyết định, link node) kèm dashboard web ở cổng 8080 |

Chưa có sa bàn thì dùng sa bàn ảo trong `pi/src/nt532/sim/`: thêm `--source sim` vào bất kỳ script nào có `--source` (camera ảo, 4 tag tham chiếu mặc định, hai node, thẻ bia và laser theo pan/tilt).

Chạy cả trạm kèm dashboard trên sa bàn ảo: `uv run python scripts/run_station.py --source sim` rồi mở http://localhost:8080/. Kiến trúc, luồng xử lý và các việc còn mở ở [docs/orchestrator.md](docs/orchestrator.md).

Orchestrator dùng phần thị giác qua lớp `Vision`, mô tả ở [docs/vision-api.md](docs/vision-api.md). Cách đưa lên Pi và chạy các bài đo ở [docs/trien-khai-pi.md](docs/trien-khai-pi.md).

Dataset và nguồn tải ghi ở [docs/datasets.md](docs/datasets.md).

## Firmware

Mỗi thư mục con trong `firmware/` là một project ESP-IDF riêng. Xem [firmware/README.md](firmware/README.md).

## Quy ước

- Đơn vị mét và độ. Gốc tọa độ ở góc trước bên trái mặt bàn: X sang phải, Y về phía bảng bia, Z lên trên.
- Mọi số đo sa bàn nằm trong `config/site.yaml`, không gõ cứng trong code.
- Mốc thời gian lấy theo đồng hồ của Pi.
