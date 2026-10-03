# Model

Trọng số YOLO không đưa lên git. Đặt tên theo ngày train và ghi ở đây bản nào đang dùng.

`fire-n.pt` là bản `detect()` nạp mặc định; hiện là bản sao của `mix26-neg-20261003.pt`.

Lớp: 0 = smoke, 1 = fire. Chưa bản nào thấy ảnh sa bàn; cần tinh chỉnh bằng ảnh thẻ bia tự chụp trước khi dùng cho demo.

## Các bản đã train

| File | Nền | Dữ liệu train | Epoch | Ghi chú |
| --- | --- | --- | --- | --- |
| `mix26-neg-20261003.pt` | `mix26-n` | Như trên, thêm 1.031 ảnh không lửa: 202 ảnh vật giống lửa (Mendeley Artificial Fire) và 829 ảnh phòng ở (COCO val2017) | 15 | Đang dùng. Ít báo nhầm hơn hẳn |
| `mix26-n-20261003.pt` | YOLO26n (COCO), 640 px | Bộ gộp `data/dataset/fire-mix/`: D-Fire + Home-fire + Indoor Fire Smoke, 21.522 ảnh | 35 | Chọn theo val của hai bộ trong nhà |
| `dfire-n-20261003.pt` | YOLO11n (COCO), 640 px | D-Fire train, 14.122 ảnh | 30 | Giữ lại để so sánh |

## Kết quả trên tập test

Đánh giá bằng `pi/scripts/eval_detect.py --split test`, dữ liệu mà model chưa từng thấy.

| Model | Tập test | Lửa P | Lửa R | Lửa mAP50 | Khói mAP50 | Tất cả mAP50 | Tất cả mAP50-95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `dfire-n` (YOLO11n) | Trong nhà (2.050 ảnh) | 0,61 | 0,42 | 0,44 | 0,20 | 0,32 | 0,15 |
| `mix26-n` (YOLO26n) | Trong nhà (2.050 ảnh) | 0,90 | 0,83 | 0,90 | 0,87 | 0,89 | 0,56 |
| `mix26-neg` (YOLO26n) | Trong nhà (2.050 ảnh) | 0,90 | 0,81 | 0,90 | 0,86 | 0,88 | 0,55 |
| `dfire-n` (YOLO11n) | D-Fire (4.306 ảnh) | 0,70 | 0,60 | 0,68 | 0,80 | 0,74 | 0,42 |
| `mix26-n` (YOLO26n) | D-Fire (4.306 ảnh) | 0,74 | 0,63 | 0,71 | 0,83 | 0,77 | 0,44 |

"Trong nhà" là tập test của Home-fire và Indoor Fire Smoke gộp lại (`data/dataset/fire-mix/data.yaml`).

Giữa hai bản đổi cả kiến trúc (YOLO11n sang YOLO26n) lẫn dữ liệu train, nên mức cải thiện không tách riêng được cho từng yếu tố. Phần lớn mức tăng trên tập trong nhà nhiều khả năng đến từ dữ liệu.

## Báo nhầm

Phần trăm ảnh không có lửa mà model vẫn báo lửa, đo bằng `pi/scripts/eval_false_alarms.py` trên phần test của mẫu âm tính (model chưa thấy các ảnh này).

| Model | Loại ảnh | Số ảnh | conf ≥ 0,25 | conf ≥ 0,35 | conf ≥ 0,5 | conf ≥ 0,7 |
| --- | --- | --- | --- | --- | --- | --- |
| `mix26-n` | Vật giống lửa | 33 | 57,6% | 42,4% | 39,4% | 27,3% |
| `mix26-neg` | Vật giống lửa | 33 | 6,1% | 0% | 0% | 0% |
| `mix26-n` | Phòng ở (COCO) | 181 | 7,7% | 3,9% | 2,8% | 0% |
| `mix26-neg` | Phòng ở (COCO) | 181 | 2,8% | 2,2% | 0,6% | 0% |

Mẫu vật giống lửa nhỏ (33 ảnh) và cùng nguồn với ảnh âm tính đã đưa vào train, nên con số 0% lạc quan hơn thực tế. Cái giá phải trả là recall lửa trên tập test trong nhà giảm từ 0,83 xuống 0,81.

## Bản cho Raspberry Pi

`pi/scripts/export_model.py` xuất `fire-n.pt` sang `fire-n_ncnn_model/` (NCNN, 640 px) và `fire-n.onnx`. Trên 20 ảnh lửa của tập test Home-fire, cả ba bản bắt cùng 16/20 ảnh, hộp lệch nhau dưới 1 px, độ tin cậy chênh trung bình 0,008.

## Tốc độ

| Máy | Trọng số | imgsz | Trung vị (ms) | p95 (ms) |
| --- | --- | --- | --- | --- |
| Laptop, GPU RTX 4050 | `fire-n.pt` | 640 | khoảng 1,4 | |
| Laptop, CPU x86 | `fire-n.pt` | 640 | 22,6 | 37,0 |
| Laptop, CPU x86 | NCNN | 640 | 47,5 | 54,3 |
| Laptop, CPU x86 | ONNX | 640 | 24,7 | 35,6 |
| Laptop, CPU x86 | `fire-n.pt` | 320 | 12,1 | 14,0 |
| Laptop, CPU x86 | NCNN (xuất ở 320) | 320 | 13,4 | 16,6 |
| Laptop, CPU x86 | ONNX (xuất ở 320) | 320 | 9,7 | 20,7 |
| Raspberry Pi 5 | | | chưa đo | |

Đo bằng `pi/scripts/bench_detect.py`, 50 lượt. NCNN được tối ưu cho ARM nên trên x86 chậm hơn PyTorch; phải đo trên Pi rồi mới chọn bản chạy thật.

## Phân loại lớp đám cháy theo chất liệu

`cof26-n-cls-20261004.pt`: YOLO26n-cls, 224 px, 40 epoch trên ClassesOfFire (`data/dataset/classesoffire/`, chia bằng `prepare_classesoffire.py`), chọn bản tốt nhất theo val. Train bằng `pi/scripts/train_classify.py`. Chưa ghép vào hệ thống.

Trên tập test (661 ảnh):

| Lớp | Số ảnh | P | R |
| --- | --- | --- | --- |
| A chất rắn | 366 | 0,97 | 0,98 |
| B chất lỏng và khí | 27 | 0,85 | 0,85 |
| C điện | 61 | 0,90 | 0,89 |
| D kim loại | 25 | 0,96 | 0,92 |
| F dầu ăn | 24 | 0,89 | 0,96 |
| Không cháy | 158 | 0,99 | 0,98 |

Độ chính xác chung 96%. Con số có thể lạc quan: ảnh gom từ nhiều nguồn trên mạng, nhiều ảnh là khung liền nhau của cùng video, mà bước lọc chỉ bỏ ảnh trùng hoàn toàn. Model dựa nhiều vào bối cảnh (ổ điện, chảo, bếp), nên thẻ bia muốn được phân loại đúng phải in cả bối cảnh.
