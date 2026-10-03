# Model

Trọng số YOLO không đưa lên git. Đặt tên theo ngày train và ghi ở đây bản nào đang dùng.

`fire-n.pt` là bản `detect()` nạp mặc định; hiện là bản sao của `mix26-n-20261003.pt`.

Lớp: 0 = smoke, 1 = fire. Chưa bản nào thấy ảnh sa bàn; cần tinh chỉnh bằng ảnh thẻ bia tự chụp trước khi dùng cho demo.

## Các bản đã train

| File | Nền | Dữ liệu train | Epoch | Ghi chú |
| --- | --- | --- | --- | --- |
| `mix26-n-20261003.pt` | YOLO26n (COCO), 640 px | Bộ gộp `data/dataset/fire-mix/`: D-Fire + Home-fire + Indoor Fire Smoke, 21.522 ảnh | 35 | Đang dùng. Chọn theo val của hai bộ trong nhà |
| `dfire-n-20261003.pt` | YOLO11n (COCO), 640 px | D-Fire train, 14.122 ảnh | 30 | Giữ lại để so sánh |

## Kết quả trên tập test

Đánh giá bằng `pi/scripts/eval_detect.py --split test`, dữ liệu mà model chưa từng thấy.

| Model | Tập test | Lửa P | Lửa R | Lửa mAP50 | Khói mAP50 | Tất cả mAP50 | Tất cả mAP50-95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `dfire-n` (YOLO11n) | Trong nhà (2.050 ảnh) | 0,61 | 0,42 | 0,44 | 0,20 | 0,32 | 0,15 |
| `mix26-n` (YOLO26n) | Trong nhà (2.050 ảnh) | 0,90 | 0,83 | 0,90 | 0,87 | 0,89 | 0,56 |
| `dfire-n` (YOLO11n) | D-Fire (4.306 ảnh) | 0,70 | 0,60 | 0,68 | 0,80 | 0,74 | 0,42 |
| `mix26-n` (YOLO26n) | D-Fire (4.306 ảnh) | 0,74 | 0,63 | 0,71 | 0,83 | 0,77 | 0,44 |

"Trong nhà" là tập test của Home-fire và Indoor Fire Smoke gộp lại (`data/dataset/fire-mix/data.yaml`).

Giữa hai bản đổi cả kiến trúc (YOLO11n sang YOLO26n) lẫn dữ liệu train, nên mức cải thiện không tách riêng được cho từng yếu tố. Phần lớn mức tăng trên tập trong nhà nhiều khả năng đến từ dữ liệu.

Tốc độ trên GPU RTX 4050 laptop: khoảng 1,4 ms mỗi ảnh. Chưa đo trên Raspberry Pi 5.
