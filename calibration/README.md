# Hiệu chuẩn

Code chỉ đọc hai file trong thư mục này (qua `Vision`, đường dẫn khai ở `config/site.yaml`):

- `camera.yaml`: ma trận camera, hệ số méo và sai số chiếu lại (`calibrate_camera.py`, tuần 1). Đổi độ phân giải hoặc lấy nét thì hiệu chuẩn lại.
- `commissioning.yaml`: pose camera và pose tag từng node trên sa bàn (`commission.py`). Làm lại mỗi khi dời camera hoặc node.

Các giá trị hiệu chuẩn khác không nằm ở đây:

- Servo (xung, điểm 0, đảo chiều) và giới hạn góc phía node: NVS/Kconfig của firmware node. Giới hạn góc phía Pi ở `config/site.yaml` → `actuator.nodes.*`.
- Offset tâm quay `actuator.pivot_offset` và góc bù tia nước `actuator.water_tilt_deg`: `config/site.yaml`.

Các file sau là chỗ ghi chép tay (kèm ngày đo), **chưa dùng**: chưa có code nào đọc chúng.

- `servo.yaml`: bảng quy đổi góc sang độ rộng xung, điểm 0 và hệ số (tuần 2–3).
- `offsets.yaml`: offset từ tag đế vòi tới tâm quay và tới đầu laser (tuần 3).
- `water.yaml`: bảng bù tilt cho tia nước theo khoảng cách (tuần 3).
