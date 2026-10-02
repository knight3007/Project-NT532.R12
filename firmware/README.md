# Firmware

Mỗi thư mục con là một project ESP-IDF riêng, tạo bằng `idf.py create-project` ngay trong thư mục đó.

| Thư mục | Board | Mạng | Ghi chú |
| --- | --- | --- | --- |
| `sensor-h2/` | ESP32-H2 mini | Thread (FTD) | Gửi `/t`, `/a`, `/alarm`; `node_id` và ngưỡng lưu trong NVS |
| `actuator-s3/` | ESP32-S3 | Wi-Fi | Nhận `/aim`, `/fire`, `/stop`, `/hb`; trả `/status` |
| `rcp/` | ESP32-H2 DevKit | USB-UART vào Pi | Dùng ví dụ `ot_rcp` của ESP-IDF; ghi lại lệnh nạp và cấu hình OTBR |

Firmware S3 giữ bốn nguyên tắc ở mục 8 của kế hoạch để sau này chuyển sang H2 chạy Thread: CoAP qua libcoap, chỉ dùng ngoại vi H2 cũng có, `ttl` và số lần gửi lại là tham số, Pi tìm node theo tên dịch vụ.

Đưa `sdkconfig.defaults` lên git; `sdkconfig` và `build/` đã nằm trong `.gitignore`.
