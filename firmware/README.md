# Firmware

Thiết kế hiện hành (xem [docs/thay-doi-hai-voi.md](../docs/thay-doi-hai-voi.md)): hai node ESP32-H2, mỗi node vừa đọc cảm biến vừa điều khiển một cụm vòi (pan–tilt, bơm, laser), nói CoAP qua Thread. Pi 5 cùng một H2 DevKit làm RCP là border router OpenThread và chạy orchestrator. Mỗi thư mục con là một project ESP-IDF riêng.

| Thư mục | Board | Mạng | Ghi chú |
| --- | --- | --- | --- |
| `sensor-h2/` | ESP32-H2 Super Mini (x2) | Thread (FTD) | Firmware chính của node: gửi `/t`, `/a`, `/alarm`; nhận `/aim`, `/fire`, `/stop`, `/hb`, `/alarm`; trả `/status`. Lõi C thuần trong `main/core/` test trên máy. `node_id`, ngưỡng, hiệu chuẩn servo ghi đè bằng NVS |
| `rcp/` | ESP32-H2 DevKit | USB-UART vào Pi | Dùng ví dụ `ot_rcp` của ESP-IDF; ghi lại lệnh nạp và cấu hình OTBR |
| `actuator-s3/` | ESP32-S3 | Wi-Fi | Chỉ còn là đường lui nếu node H2 không đáp ứng được; hiện để trống |

## Nguyên tắc

- **Hợp đồng CoAP là giao diện duy nhất** (kế hoạch mục 4): `/t`, `/a`, `/status` từ node lên Pi; `/aim`, `/fire`, `/stop`, `/hb` từ Pi xuống node; `/alarm` multicast `ff03::1` giữa hai node. Payload JSON key ngắn, dưới khoảng 60 byte. Mọi node nói cùng hợp đồng nên đổi mạng vận chuyển chỉ đổi phần khởi tạo.
- **An toàn mặc định tắt:** bơm và laser về mức thấp trước mọi thứ khác; mất heartbeat 1,5 s thì tự tắt; Pi không phải là chỗ duy nhất giữ an toàn.
- **Tìm node:** hiện Pi dùng địa chỉ khai trong `config/site.yaml` (`network.nodes`); từ tuần 4 node đăng ký SRP và Pi tìm bằng DNS-SD.
- **Một chủ cho mỗi hệ con:** task CoAP giữ libcoap, task chấp hành giữ trạng thái vòi, giao tiếp qua hàng đợi FreeRTOS.
- **Cấu hình:** `sdkconfig.defaults` nằm trong git; `sdkconfig`, `build/`, `managed_components/` đã được `.gitignore`.
- **Chưa build được trên máy nhóm** (không có ESP-IDF): các chỗ chưa kiểm tra đánh dấu `CHƯA KIỂM`, danh sách trong [sensor-h2/README.md](sensor-h2/README.md).
