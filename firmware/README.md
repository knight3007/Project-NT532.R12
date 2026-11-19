# Firmware

Hướng source A/B hiện tại dùng hai node ESP32-H2 tích hợp cảm biến, pan–tilt và relay/LED mô phỏng laser, trao đổi CoAP qua Thread với Pi 5 và RCP/OTBR. Firmware A/B không điều khiển bơm. Xem [runbook E2E](../docs/nt532-e2e-runbook.md): software E2E với node giả đã đạt, physical hardware E2E đầy đủ còn chờ xác minh; không suy ra binary nào đang nạp trên từng board.

| Thư mục | Board | Mạng | Ghi chú |
| --- | --- | --- | --- |
| `node-a-integrated/` | ESP32-H2 Super Mini, A | Thread | Source A/B hiện tại: MQ-2, `/info`, `/status`, `/mq2`, `/pantilt`, `/laser`, `/hb`, `/stop`; bản 0.1.2 được mô tả trong README riêng |
| `node-b-integrated/` | ESP32-H2 Super Mini, B | Thread | Source A/B hiện tại: DHT11, pan–tilt, relay/LED; bản 0.2.5 được mô tả trong README riêng |
| `sensor-h2/` | ESP32-H2 Super Mini | Thread | Thiết kế tham chiếu với `/aim` và `/fire`; có build CI và host test lõi C, chưa dùng làm bằng chứng firmware đang chạy trên A/B |
| `rcp/` | ESP32-H2 DevKit | USB-UART vào Pi | Hướng dẫn ví dụ `ot_rcp` và OTBR; kiểm trạng thái triển khai theo runbook |

## Nguyên tắc

- **Giao thức theo firmware:** A/B tích hợp dùng `/pantilt`, `/laser`, `/hb`, `/stop`, `/info`, `/status` và endpoint cảm biến theo node; `sensor-h2` tham chiếu dùng hợp đồng `/aim`, `/fire` của kế hoạch cũ. Không gửi lệnh profile cũ trực tiếp tới A/B; xem runbook.
- **An toàn mặc định tắt:** A/B giữ relay/LED và PWM tắt khi khởi động, STOP, mất heartbeat hoặc rời Thread theo README từng node; chưa coi mô tả source là kiểm chứng phần cứng hiện tại.
- **Tìm node:** địa chỉ cấu hình và SRP tùy đường chạy; đối chiếu địa chỉ thực và profile trước khi vận hành.
- **Phạm vi:** nguồn, chân, giới hạn xung và kiểm tra riêng từng node nằm trong README của A/B.
- **Cấu hình:** `sdkconfig.defaults` nằm trong git; `sdkconfig`, `build/`, `managed_components/` đã được `.gitignore`.
- **Bằng chứng build:** `sensor-h2` đã qua CI; README A/B ghi build và host test theo từng phiên bản. Build hoặc host test không thay cho nghiệm thu trên phần cứng.
