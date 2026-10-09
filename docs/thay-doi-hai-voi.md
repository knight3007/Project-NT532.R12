# Thay đổi so với kế hoạch 2026-10-02: hai cụm vòi trên hai node H2

Nhóm chốt ngày 2026-10-02: mỗi node ESP32-H2 Super Mini vừa đọc cảm biến vừa điều khiển một cụm pan–tilt, bơm và laser. Tổng cộng hai cụm vòi đặt tại hai vị trí sensor, thay cho một cụm trên ESP32-S3.

File kế hoạch gốc chưa sửa; các mục dưới đây ghi đè lên nó.

## Điều thay đổi

| Hạng mục | Kế hoạch gốc | Bây giờ |
| --- | --- | --- |
| Node chấp hành | 1 ESP32-S3 qua Wi-Fi | 2 ESP32-H2 qua Thread, cùng board với sensor |
| Số cụm pan–tilt, bơm, laser | 1 | 2 |
| Vị trí vòi | Giữa bàn phía trước, cách bảng 65 cm | Tại hai vị trí sensor |
| Tag | Tag sensor 5–6 cm và tag đế vòi 8 cm | Một tag 8 cm trên đế cố định của mỗi node |
| Chọn vòi | Chỉ có một | Bộ quyết định chọn `s1`, `s2` hoặc cả hai cùng phun (mục dưới); không còn mặc định node báo động |
| Hợp đồng CoAP | `/aim`, `/fire`, `/stop`, `/hb`, `/status` tới S3 | Giữ nguyên, gửi tới node H2 tương ứng qua Thread |

## Việc phát sinh

- Mua thêm: 1 bộ pan–tilt, 1 bơm, 1 laser, 1 nguồn 5 V cho servo, 1 nguồn 12 V cho bơm, 1 bộ MOSFET.
- Node không còn chạy được bằng sạc dự phòng đơn thuần: servo và bơm cần nguồn riêng, chung GND với H2.
- Hiệu chuẩn (offset cơ khí, bảng servo, bảng bù tilt) và các bài đo 9 vị trí làm riêng cho từng vòi.
- Tách nguồn và thêm tụ lọc để servo, bơm không gây nhiễu lên chân ADC đọc MQ-2 hay làm H2 reset.
- Che nước cho cảm biến vì vòi nằm ngay trên node.
- Bài thử multi-hop: lệnh ngắm tới sensor 2 cũng đi qua hai hop, cần đo lại độ trễ và `ttl`.

## Gán chân đề xuất cho ESP32-H2 Super Mini

Đọc từ ảnh mặt sau board, cần đối chiếu lại với sơ đồ chân của người bán trước khi hàn.

| Chức năng | GPIO | Ghi chú |
| --- | --- | --- |
| MQ-2 (analog, qua cầu phân áp) | 1 | ADC1 chỉ có trên GPIO1–5 |
| Servo pan | 4 | LEDC |
| Servo tilt | 5 | LEDC |
| I2C SDA, SCL (SHT31) | 10, 11 | DS18B20 thì chỉ cần một chân |
| MOSFET bơm | 12 | Điện trở kéo xuống ở cổng để mặc định tắt |
| MOSFET laser | 14 | Điện trở kéo xuống |
| Còi hoặc LED báo động | 13 | |
| Dự phòng | 0, 22 | 22 là pad ở giữa board |

Tránh dùng: GPIO8 và 9 (chân strapping, thường nối LED và nút BOOT), GPIO2, 3 và 25 (strapping), GPIO23 và 24 (UART0), GPIO26 và 27 (USB).

## Phun một hay hai vòi

Câu hỏi `nozzle` của bộ quyết định có ba đáp án: `s1`, `s2`, `both (s1 + s2)`. Mô hình Jev được dạy chọn `both` cho đám cháy lớn mà cả hai vòi khỏe và với tới bia; baseline luật không bao giờ trả `both`, chỉ chọn vòi đơn gần bia nhất (theo `dist3`). Orchestrator thực thi đúng đáp án:

- Hai vòi bị chặn thì chỉ báo động (`alarm_only`). Một vòi bị chặn lúc quyết định thì ghi cảnh báo `safety` và chạy như lượt một vòi bằng vòi còn lại.
- AIM và CORRECT làm lần lượt từng vòi (mỗi lúc chỉ một laser, vì `find_spot` so khung tắt/bật). Mỗi vòi giữ điểm ngắm và độ lệch riêng; mỗi vòng CORRECT ghi tên vòi. Vòi nào gặp `LinkError` lúc AIM hoặc CORRECT thì bị bỏ (cảnh báo `safety`, "hỏng lúc ngắm") và lượt chạy tiếp bằng vòi còn lại; vòi cuối cùng hỏng thì FAULT như lượt một vòi. `Abort` (dừng khẩn cấp, góc ngoài giới hạn) vẫn dừng cả lượt.
- Ngay trước FIRE, orchestrator gửi `/aim` cuối cho cả hai vòi rồi mới gửi hai lệnh `/fire pump` liền nhau để hai bơm chạy cùng lúc. Vòi nào lúc đó mất heartbeat, bị chặn hoặc không trả lời lệnh ngắm thì bị bỏ (cảnh báo `safety`) và lượt chạy tiếp bằng vòi còn lại; hết vòi thì FAULT như lượt một vòi.
- VERIFY coi hai vòi là một lần phun: nhiệt sau phun lấy trung bình hai node, độ lệch `mark_cm` lấy giá trị lớn hơn của hai vòi, `nozzle` trong obs là `both (s1 + s2)`. `done`, `re-aim` (làm lại AIM + CORRECT cả hai vòi), `spray more` (phun lại, không ngắm lại) và `call human` xử lý như lượt một vòi, vẫn tối đa 3 lần.
- Dừng khẩn cấp gửi `/stop` tới mọi node nên tắt cả hai bơm và laser.
- Sa bàn ảo có lửa lớn (`SimWorld.ignite(size="large")`: thẻ to hơn, tín hiệu mạnh và lan xa hơn, nhiều máu hơn) để thử đường này. Trên phần cứng thật, phun cả hai bơm cùng lúc được thử ở tuần 4–5 cùng với nguồn (xem [kế hoạch](ke-hoach-trien-khai-NT532.md) và [trien-khai-phan-cung.md](trien-khai-phan-cung.md)).

## Chưa quyết

- ESP32-S3 còn dùng làm đường lui hay bỏ hẳn.
- Vị trí và độ cao đặt node để góc bắn tới bia không quá xiên và không che tầm nhìn camera.
