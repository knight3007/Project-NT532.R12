# Triển khai phần thị giác lên Raspberry Pi 5

Code thị giác đã chạy và có test trên laptop. Các bước dưới đây đưa nó lên Pi rồi đo những con số mà plan cần: thời gian suy luận và tải của Pi.

## 1. Chuẩn bị trên laptop

- Xuất model sang NCNN, định dạng chạy nhanh nhất trên CPU ARM:
  ```bash
  uv run --no-sync python scripts/export_model.py
  ```
  Kết quả nằm ở `models/fire-n_ncnn_model/`. Muốn thử cỡ ảnh nhỏ hơn cho nhanh thì xuất thêm bằng `--imgsz 480` hoặc `--imgsz 320`. Cỡ ảnh lúc xuất phải khớp `vision.imgsz` lúc chạy.
- Hiệu chuẩn webcam có thể làm ngay trên laptop. Thông số nội tại thuộc về con webcam chứ không thuộc về máy tính, nên chép `calibration/camera.yaml` sang Pi là dùng được, miễn là giữ nguyên độ phân giải và lấy nét.
- Thư mục `models/` và `data/` không nằm trong git. Chép sang Pi các file `models/fire-n.pt`, `models/fire-n_ncnn_model/` và, nếu cần, `models/cof26-n-cls-20261004.pt`.

## 2. Cài trên Pi

Dùng Raspberry Pi OS 64-bit (Bookworm, Python 3.11). Pi 5 nên có quạt tản nhiệt chủ động, vì chạy YOLO liên tục sẽ làm Pi giảm xung khi nóng.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
git clone <repo> ~/nt532 && cd ~/nt532/pi
```

```bash
uv sync --extra yolo
```

```bash
uv pip install ncnn
```

- Trên Pi, `torch` lấy bản CPU từ PyPI; nguồn CUDA trong `pyproject.toml` chỉ áp cho Windows.
- `ncnn` không có trong `pyproject.toml`, nên sau khi cài phải chạy mọi lệnh bằng `uv run --no-sync`. `uv run` trơn sẽ đồng bộ lại môi trường và gỡ `ncnn`.

Chép model từ laptop sang Pi:

```bash
scp -r models/fire-n.pt models/fire-n_ncnn_model pi@<ip-pi>:~/nt532/models/
```

Kiểm tra:

```bash
uv run --no-sync pytest -q
```

## 3. Webcam

```bash
v4l2-ctl --list-devices
```

```bash
v4l2-ctl -d /dev/video0 -l
```

- Lấy số thứ tự thiết bị điền vào `camera.index` trong `config/site.yaml`.
- Tìm các điều khiển kiểu `focus_absolute` và `exposure_time_absolute`, thử vài giá trị rồi điền vào `camera.focus` và `camera.exposure`. Để `null` thì camera tự chỉnh, nhưng khi đó lấy nét có thể trôi và vết laser dễ bị cháy sáng.

## 4. Đo tốc độ suy luận

```bash
uv run --no-sync python scripts/bench_detect.py --weights ../models/fire-n.pt ../models/fire-n_ncnn_model --n 100
```

- Kết quả ghi vào `runs/bench/bench-<tên máy>.csv`. Chép bảng vào `models/README.md`.
- Nếu NCNN 640 chậm quá mức chấp nhận (plan đặt mục tiêu dưới 100 ms), xuất bản 480 hoặc 320 trên laptop rồi đo lại.
- Chọn được bản nào thì sửa `vision.weights` và `vision.imgsz` trong `config/site.yaml` rồi commit.

Số đo tham khảo trên CPU laptop x86 (không phải Pi), tính bằng ms:

| Trọng số | imgsz | Trung vị | p95 |
| --- | --- | --- | --- |
| `fire-n.pt` | 640 | 22,6 | 37,0 |
| NCNN | 640 | 47,5 | 54,3 |
| ONNX | 640 | 24,7 | 35,6 |
| `fire-n.pt` | 320 | 12,1 | 14,0 |
| NCNN | 320 | 13,4 | 16,6 |
| ONNX | 320 | 9,7 | 20,7 |

Trên x86, NCNN chậm hơn PyTorch vì NCNN được tối ưu cho ARM. Thứ tự trên Pi có thể đảo lại, nên phải đo trên Pi rồi mới chọn.

## 5. Commissioning khi đã có sa bàn

1. Đo tâm 4 tag tham chiếu bằng thước, điền vào `tags.reference.positions` trong `config/site.yaml`.
2. Chạy commissioning, không cần màn hình:
   ```bash
   uv run --no-sync python scripts/commission.py
   ```
   Lệnh lưu `calibration/commissioning.yaml` khi kết quả đạt. Thiếu node hoặc sai số quá ngưỡng thì không ghi đè file cũ.
3. Kiểm tra lại sau khi có ai chạm vào sa bàn:
   ```bash
   uv run --no-sync python scripts/commission.py --check
   ```
4. Nếu Pi có màn hình (hoặc chạy trên laptop), `live_tags.py` hiện tag, viền bảng bia và pose node theo thời gian thực.

## 6. Các bài đo của Hùng

| Bài (plan mục 7) | Lệnh | File kết quả |
| --- | --- | --- |
| Kiểm tra 5 điểm (tuần 2) | `measure.py click --label p1 --truth 0.30,0.20` | `runs/measure/click.csv` |
| Định vị mục tiêu, 9 vị trí × 3 | `measure.py target --label p1 --truth 0.30,0.20 --repeat 3` | `runs/measure/target.csv` |
| Pose tag, 9 điểm × 3 | `measure.py tag --node s1 --truth 0.40,0.58 --repeat 3` | `runs/measure/tag.csv` |
| Vết laser | `measure.py spot --label p1 --target 0.30,0.20` | `runs/measure/spot.csv` |
| Tải của Pi | `log_pi_load.py --interval 1 --out runs/pi_load.csv` | `runs/pi_load.csv` |

- `--truth` là tọa độ X và Z trên mặt bảng; riêng bài `tag` là X và Y trên mặt bàn.
- Thêm `--headless` khi chạy trên Pi không màn hình.
- Thống kê và vẽ hình:
  ```bash
  uv run --no-sync python scripts/report_errors.py --csv runs/measure/target.csv
  ```

## 7. Dataset thẻ bia (tuần 3)

1. In `docs/print/target-cards.pdf` ở tỷ lệ 100% và ép plastic. Nguồn ảnh ghi ở `docs/print/target-cards.txt` (D-Fire, CC0).
2. Gắn thẻ ở nhiều vị trí trên bảng, đổi ánh sáng, rồi chụp:
   ```bash
   uv run --no-sync python scripts/capture_frames.py --out data/raw/sa-ban --every 1
   ```
3. Tạo nhãn nháp bằng model hiện tại:
   ```bash
   uv run --no-sync python scripts/autolabel.py --images data/raw/sa-ban --out data/dataset/sa-ban
   ```
4. Mở bằng công cụ gán nhãn (CVAT, Label Studio hoặc makesense.ai) để sửa nhãn, rồi tinh chỉnh model trên laptop có GPU.

## Những gì chưa chạy thử

- Phần cửa sổ, chuột và bàn phím của `live_tags.py` và `measure.py`, và việc đọc webcam thật.
- `log_pi_load.py` mới được test phần đọc chuỗi mẫu; phần đọc `/proc` và `vcgencmd` chưa chạy trên Pi.
- Tốc độ YOLO trên Pi 5.
