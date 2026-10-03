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

Tốc độ trên GPU RTX 4050 laptop: khoảng 1,4 ms mỗi ảnh. Chưa đo trên Raspberry Pi 5.
