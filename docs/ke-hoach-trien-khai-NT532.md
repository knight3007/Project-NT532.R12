> Một phần kế hoạch này (một vòi trên ESP32-S3 qua Wi-Fi) đã bị thay bởi [thay-doi-hai-voi.md](thay-doi-hai-voi.md): hai vòi trên hai node H2 qua Thread.
> Thứ tự dựng phần cứng nằm ở [trien-khai-phan-cung.md](trien-khai-phan-cung.md).

# Kế hoạch triển khai chi tiết NT532

Oct 2, 2026 · @hunn

## 1. Tóm tắt và quyết định đã chốt

Hệ thống nhận cảnh báo từ node cảm biến ESP32-H2 qua Thread/CoAP, dùng Raspberry Pi 5 và một webcam cố định để xác minh và định vị mục tiêu trên bảng bia, rồi gửi góc ngắm tới ESP32-S3 điều khiển pan–tilt và vòi phun nước. Bản này thay cho kế hoạch ngày 2026-09-29 ở những điểm dưới đây.

| Hạng mục | Chốt | Khác gì so với bản 09-29 |
| --- | --- | --- |
| Cơ cấu chấp hành | Bơm nước phun vào bia; laser nhỏ gắn song song làm tâm ngắm để hiệu chỉnh và đo | Bản cũ chỉ dùng laser, bỏ bơm |
| Node chấp hành | ESP32-S3 qua Wi-Fi, giữ nguyên hợp đồng CoAP | Bản cũ dùng ESP32-H2 qua Thread |
| Node Thread | 1 H2 DevKit làm RCP, 2 H2 mini làm sensor | Bản cũ cần 4–5 H2 |
| Camera | Một webcam cố định | Không đổi |
| Định vị mục tiêu | Tia nhìn giao mặt phẳng bảng bia | Không đổi |
| Ngắm | Tính góc từ pose đế vòi, hiệu chỉnh vòng kín theo vết laser tâm ngắm, rồi bù tilt cho quỹ đạo nước | Bản cũ chỉ ngắm một lần, không sửa |
| Vai trò của Thread | Thêm multi-hop, báo động multicast khi mất Pi, tự phát hiện node bằng SRP | Bản cũ chỉ đo PDR |
| Sa bàn | Chữ L: mặt bàn 120 × 80 cm, bảng bia đứng cao 60 cm | Bản cũ chưa chốt |
| Dung sai ngắm | Laser tâm ngắm trong bán kính 3 cm quanh tâm bia ở cự ly 70 cm; tia nước trúng thẻ bia | Bản cũ chưa có số |
| Matter, Depth Anything, camera thứ hai | Gác lại | Depth Anything từng là nhánh mở rộng |

Dung sai 3 cm tương ứng khoảng 2,5° tổng sai số góc. Đây là con số đề xuất; nhóm chốt lại sau khi đo thử ở tuần 3.

## 2. Phần cứng

Đồ đang có vừa đủ cho MVP và không còn board dự phòng: hỏng một con H2 là thiếu node.

### Phân vai

| Thiết bị | Vai trò | Kết nối | Nguồn |
| --- | --- | --- | --- |
| Raspberry Pi 5 | OTBR, CoAP, YOLO, orchestrator, log | Wi-Fi/LAN, USB tới RCP | Adapter 5 V 5 A |
| ESP32-H2 DevKit | RCP cho OTBR | USB-UART vào Pi | USB từ Pi |
| ESP32-H2 mini #1 | Sensor 1, vai trò router | Thread | Sạc dự phòng |
| ESP32-H2 mini #2 | Sensor 2, vai trò router | Thread | Sạc dự phòng |
| ESP32-S3 | Node chấp hành: 2 servo, bơm nước, laser tâm ngắm | Wi-Fi, CoAP | USB cho board; 5 V 2–3 A riêng cho servo, 12 V riêng cho bơm, chung GND |

### Cần mua cho MVP

| Món | Số lượng | Ghi chú |
| --- | --- | --- |
| Webcam USB | 1 | Khóa được lấy nét và phơi sáng; bỏ qua nếu đã có |
| Khung pan–tilt kèm servo MG90S | 1 bộ, 2 servo | Bánh răng kim loại, ít rơ hơn SG90 |
| Module laser đỏ dưới 1 mW | 1 | Làm tâm ngắm; Class 2, không dùng loại 5 mW |
| Nguồn 5 V 2–3 A | 1 | Riêng cho servo |
| Cảm biến gas/khói MQ-2 | 2 | Chân analog qua cầu phân áp trước khi vào ADC |
| Cảm biến nhiệt SHT31 hoặc DS18B20 | 2 | Mỗi sensor node một cái |
| Còi hoặc LED báo động | 2 | Dùng cho chế độ mất Pi |
| Sạc dự phòng 5 V | 2 | Cấp nguồn sensor node |
| Transistor/MOSFET, điện trở, breadboard, dây | 1 bộ | Đóng cắt laser, cầu phân áp cho MQ-2 |
| Tấm formex hoặc mica mờ 120 × 60 cm | 1 | Bảng bia, màu sáng, không bóng |
| Chân máy hoặc giá kẹp bàn | 1 | Giữ webcam cố định |
| Giấy in tag, bìa cứng, nam châm hoặc băng dính gai | 1 bộ | Tag và thẻ bia; ép plastic để chịu nước |
| Bơm màng mini 12 V | 1 | Loại R385 hoặc tương đương; thử tầm phun trước khi chốt đầu vòi |
| Nguồn 12 V 2 A | 1 | Riêng cho bơm |
| MOSFET mức logic và diode | 1 bộ | Đóng cắt bơm từ GPIO của S3 |
| Ống silicone và đầu vòi 2–3 mm | 1 bộ | Vòi gắn song song với laser trên pan–tilt |
| Bình nước, khay hứng, hộp che mạch | 1 bộ | Khay đặt dọc chân bảng; hộp che cho sensor và S3 |

### Mua thêm khi có ngân sách, theo thứ tự

1. Một ESP32-H2 mini: mở được bài thử tự phục hồi mạng và làm dự phòng.
2. Thêm một ESP32-H2 mini nữa để chuyển node chấp hành từ Wi-Fi sang Thread.

## 3. Sa bàn và hệ tọa độ

Sa bàn hình chữ L: mặt bàn 120 × 80 cm làm sàn, bảng bia 120 × 60 cm dựng đứng ở mép sau làm mặt phẳng mục tiêu duy nhất. Các số đo là đề xuất, chỉnh theo bàn thật rồi ghi lại số đo thực.

&#91;embedded content: sa bàn · mặt bằng nhìn từ trên xuống\]

Webcam đứng sau và cao hơn cụm vòi phun nên không bị che; tia ngắm tới bảng gần vuông góc nên sai 1° chỉ lệch khoảng 1,2 cm.

| Thành phần | Vị trí | Ghi chú |
| --- | --- | --- |
| Bảng bia | Mép sau bàn, dựng đứng | Mặt phẳng Y = 0,80 m; mờ, màu sáng |
| Webcam | Chính giữa, cách mép trước khoảng 40 cm, cao khoảng 70 cm so với mặt bàn | Nhìn chéo xuống, thấy cả bảng và mọi tag; khóa lấy nét và phơi sáng |
| Cụm vòi phun | Giữa bàn phía trước, cách mép trước 15 cm, trên trụ cao 30 cm | Cách bảng khoảng 65 cm; vòi và laser tâm ngắm gắn song song, ngang tầm giữa bảng |
| Sensor 1 và 2 | Trên mặt bàn, cách bảng khoảng 25 cm, lệch trái và lệch phải | Mỗi sensor giữ một vùng; đặt trong hộp che nước |
| Tag tham chiếu | 4 tag dán cứng trên mặt bàn, tọa độ đo bằng thước | Định nghĩa gốc tọa độ; phát hiện camera bị xê dịch |
| Tag của node | Nằm ngửa trên nắp sensor và trên đế cố định của vòi phun | Không dán lên phần quay của pan–tilt; ép plastic để chịu nước |

### Quy ước dùng chung

- **Gốc tọa độ:** góc trước bên trái mặt bàn. X sang phải dọc mép trước, Y hướng về bảng bia, Z lên trên. Đơn vị mét và độ.
- **Góc ngắm:** pan = 0 và tilt = 0 khi tia laser vuông góc với bảng. Pan dương quay sang trái (nhìn từ sau vòi), tilt dương ngẩng lên.
- **Tag:** họ AprilTag 36h11. Cạnh 8 cm cho tag tham chiếu và đế vòi, 5–6 cm cho sensor. Đo lại cạnh bằng thước sau khi in.
- **Bia:** thẻ in hình lửa cỡ 6–8 cm, ép plastic, gắn bằng nam châm hoặc băng dính gai để đổi vị trí nhanh.
- **Ghép cảnh báo với mục tiêu:** khi một sensor báo động, chỉ nhận bia có tọa độ X cách sensor đó không quá 0,35 m. Vị trí sensor lấy từ tag của nó.
- **Vùng phun và chiếu:** giới hạn góc để tia nước và tia laser không ra khỏi bảng bia. Không đặt gương, kính hay vật bóng sau và quanh bảng.

Nước và điện: đặt khay hứng dọc chân bảng. Pi, S3, nguồn và ổ cắm nằm phía sau vòi phun và có che. Lau khô bảng giữa các lượt đo.

## 4. Mạng và giao thức

Thread mang dữ liệu cảm biến, Wi-Fi mang lệnh điều khiển, và cả hai dùng chung một hợp đồng CoAP. Nhờ vậy sau này chuyển node chấp hành sang Thread chỉ phải đổi phần khởi tạo mạng.

&#91;embedded content: kiến trúc · 2 sensor, RCP, Pi, S3, webcam\]

Sensor gửi cảnh báo qua Thread tới Pi; Pi nhận diện, định vị rồi gửi góc ngắm qua Wi-Fi tới S3. Hai sensor còn báo động trực tiếp cho nhau bằng multicast, không cần Pi.

### Vai trò mạng

- **Pi 5 và H2 DevKit (RCP):** border router của mạng Thread. Pi chạy CoAP server cho sensor và CoAP client tới S3.
- **Sensor 1 và 2:** thiết bị Thread đầy đủ (FTD), có thể làm router, luôn bật thu.
- **S3:** vào Wi-Fi cùng mạng LAN với Pi, quảng bá dịch vụ qua mDNS để Pi tìm theo tên.
- **Tự phát hiện sensor:** từ tuần 4, sensor đăng ký dịch vụ qua SRP với border router và Pi tìm bằng DNS-SD. Trước đó dùng địa chỉ khai trong file cấu hình.

### Hợp đồng CoAP

| Hướng | Resource | Kiểu | Payload | Xử lý |
| --- | --- | --- | --- | --- |
| Sensor → Pi | `POST /t` | Không xác nhận | `n, s, up, t, g` | Telemetry mỗi 5–10 s; Pi ghi thời điểm nhận |
| Sensor → Pi | `POST /a` | Có xác nhận | `n, s, k, t, g` | Cảnh báo; chống lặp theo cặp `(n, s)` |
| Sensor → mọi node | `POST /alarm` tới `ff03::1` | Multicast, không xác nhận | `n, s` | Node nhận hú còi hoặc nháy LED; chạy cả khi không có Pi |
| Pi → S3 | `POST /aim` | Có xác nhận | `id, pan, tilt, ttl` | Kiểm tra giới hạn góc, trả `accepted` hoặc `rejected` |
| Pi → S3 | `POST /fire` | Có xác nhận | `id, dev, ms` | Chỉ nhận khi lệnh `aim` cùng `id` đã `reached`; tự tắt sau `ms` |
| Pi → S3 | `POST /stop` | Có xác nhận | `id` | Tắt laser và bơm ngay, ưu tiên cao nhất |
| Pi → S3 | `POST /hb` | Không xác nhận | rỗng | Mỗi 500 ms; mất 3 nhịp liên tiếp thì S3 tự tắt bơm và laser |
| S3 → Pi | `POST /status` | Có xác nhận | `id, st, pan, tilt, err` | `st` là `accepted`, `reached`, `done`, `rejected` hoặc `fault` |
|  |  |  |  |  |

Ví dụ payload, dùng key ngắn để mỗi bản tin dưới khoảng 60 byte và không bị phân mảnh trên Thread:

```json
{"n":"s1","s":124,"k":"alert","t":52.1,"g":832}
{"id":42,"pan":31.8,"tilt":-7.4,"ttl":1500}
{"id":42,"dev":"pump","ms":2000}
{"id":42,"st":"reached","pan":31.5,"tilt":-7.2}
```

Quy tắc thời gian: `ttl` tính từ lúc S3 nhận lệnh lần đầu, lệnh trùng `id` bị bỏ. Với `/aim`, Pi chỉ gửi lại tối đa một lần rồi báo lỗi, không dùng cơ chế gửi lại mặc định vì nó có thể kéo dài hàng chục giây.

### Logic trên sensor node

- Đọc cảm biến mỗi giây. Báo động khi vượt ngưỡng 3 mẫu liên tiếp; hủy báo động khi xuống dưới ngưỡng 5 mẫu liên tiếp.
- Ngưỡng nhiệt và gas đặt sau khi đo thực ở tuần 2, lưu trong NVS cùng `node_id`.
- Bỏ qua số đọc MQ-2 trong vài phút đầu sau khi cấp nguồn.
- Khi báo động, gửi `/a` cho Pi và `/alarm` multicast cùng lúc.

### Trình tự xử lý của orchestrator

1. **IDLE:** chờ cảnh báo; kiểm tra camera, heartbeat và pose còn hợp lệ.
2. **ALERT:** nhận `/a`, chống lặp, xác định sensor và vị trí của nó.
3. **LOCALIZE:** chụp ảnh, chạy YOLO, lọc theo độ tin cậy và khoảng cách tới sensor, giao tia nhìn với bảng bia để ra tọa độ mục tiêu. Không thấy bia sau 3 s thì ghi log và về IDLE.
4. **AIM:** tính pan/tilt từ pose đế vòi, gửi `/aim`, chờ `reached`.
5. **CORRECT:** bật laser ngắn, camera tìm vết sáng, tính độ lệch so với tâm bia rồi gửi `/aim` bù. Lặp tối đa 3 lần hoặc đến khi lệch dưới 1 cm.
6. **FIRE và VERIFY:** cộng góc bù tilt cho quỹ đạo nước, bật bơm 2 s, chụp lại bia để ghi trúng hay trượt, tắt, về IDLE.
7. **FAULT:** lỗi ở bất kỳ bước nào thì gửi `/stop`, ghi nguyên nhân.

Các trường hợp chặn ngắm: không thấy tag đế vòi, pose quá cũ, tag tham chiếu lệch (camera bị xê dịch), mục tiêu ngoài bảng, góc ngoài giới hạn, mất heartbeat, lệnh quá hạn.

## 5. Lịch theo tuần

MVP hoàn chỉnh vào cuối tuần 4; tuần 5 để đo và làm phần mở rộng, tuần 6 để báo cáo, tuần 7 dự phòng. Ngày tháng giả định tuần 1 bắt đầu thứ Hai 05/10/2026; nếu lệch thì dời cả dãy.

&#91;embedded content: lộ trình · 5 giai đoạn, mỗi giai đoạn một cổng kiểm tra\]

Mỗi hình thoi là cổng kiểm tra cuối giai đoạn; không qua cổng thì chưa sang việc của giai đoạn sau.

### Tuần 0 (02–04/10): chốt phạm vi và đặt hàng

- **Cả nhóm:** xin giảng viên xác nhận phạm vi (phun nước thật lên bia mô phỏng, node chấp hành chạy Wi-Fi). Chốt danh sách mua và đặt hàng. Thống nhất quy ước ở mục 3 và hợp đồng CoAP ở mục 4.
- **Hậu:** cài ESP-IDF, nạp firmware RCP vào H2 DevKit, cài OTBR trên Pi.
- **Hùng:** cài Python, OpenCV và YOLO trên Pi; in bảng hiệu chuẩn camera và các tag.
- **Hiếu:** vẽ đế vòi phun và trụ, sơ đồ dây và nguồn cho S3 và bơm.

Cổng kiểm tra: giảng viên đồng ý phạm vi và đơn hàng đã đặt.

### Tuần 1 (05–11/10): khung giao tiếp

- **Hậu:** hai H2 mini vào mạng Thread, gửi `/t` và `/a` với dữ liệu giả. CoAP server trên Pi ghi log và chống lặp.
- **Hùng:** hiệu chuẩn nội tại webcam, lưu ma trận camera, hệ số méo và sai số chiếu lại. Phát hiện AprilTag và in ra pose. Bắt đầu chụp ảnh bia.
- **Hiếu:** firmware S3 có Wi-Fi và CoAP (`/aim`, `/stop`, `/hb`, `/status`) với servo giả lập bằng log. Tách ba module: mạng, CoAP, chấp hành.

Cổng kiểm tra: cảnh báo giả từ sensor tới Pi, Pi gửi lệnh ngắm giả tới S3, S3 trả trạng thái, tất cả có trong log của Pi.

### Tuần 2 (12–18/10): phần cứng thật và lát cắt dọc đầu tiên

- **Hậu:** gắn MQ-2 và cảm biến nhiệt, đo rồi đặt ngưỡng, cài logic 3 mẫu liên tiếp. Thêm `/alarm` multicast và còi.
- **Hùng:** cùng Hiếu dựng sa bàn. Đo pose camera từ 4 tag tham chiếu. Viết hàm `localize(pixel)` giao tia nhìn với bảng bia và kiểm tra bằng 5 điểm đo thước.
- **Hiếu:** lắp pan–tilt, vòi và laser tâm ngắm lên trụ. Làm mạch MOSFET cho bơm, thử tầm phun và chọn đầu vòi. Điều khiển servo bằng LEDC, giới hạn góc, watchdog heartbeat, bơm và laser mặc định tắt. Lập bảng quy đổi góc sang độ rộng xung.

Cổng kiểm tra: gõ tay tọa độ một điểm trên bảng, Pi tính góc, S3 quay, vết laser nằm trong 5 cm quanh điểm đó.

### Tuần 3 (19–25/10): nhận diện và ngắm

- **Hậu:** state machine đầy đủ theo mục 4. Hàm tính pan/tilt từ tọa độ mục tiêu và pose đế vòi. Ghép cảnh báo với mục tiêu theo khoảng cách tới sensor.
- **Hùng:** gán nhãn 200–300 ảnh bia chụp trên đúng sa bàn, train YOLO bản nano, đo thời gian suy luận trên Pi. Ước lượng pose tag của đế vòi và sensor. Tìm vết laser bằng hiệu của ảnh bật và ảnh tắt.
- **Hiếu:** đo offset từ tag tới tâm quay và tới đầu laser. Hiệu chỉnh điểm 0 và hệ số servo bằng 9 điểm trên bảng. Phun thử ở 9 điểm để lập bảng bù tilt cho tia nước. Cùng Hậu chỉnh công thức góc.

Cổng kiểm tra: kích cảm biến thật, YOLO thấy bia, laser tâm ngắm chỉ vào bia và tia nước chạm bảng (chưa cần vòng kín). Chốt con số dung sai.

### Tuần 4 (26/10–01/11): vòng kín, độ bền và Thread

- **Hậu:** ép multi-hop bằng `macfilter` hoặc giảm công suất phát. Rút Pi để kiểm tra `/alarm`. Thêm SRP và DNS-SD. Đo PDR và độ trễ theo số hop.
- **Hùng:** cùng Hậu làm bước CORRECT. Chạy lại commissioning khi dời node, đánh dấu pose cũ là hết hạn. Dùng tag tham chiếu để phát hiện camera bị lệch.
- **Hiếu:** tiêm lỗi: mất Wi-Fi, mất heartbeat, lệnh trễ, lệnh trùng, góc ngoài giới hạn, `/stop` giữa chừng. Cố định cơ khí, đi dây gọn, che nước cho mạch.

Cổng kiểm tra (MVP): 10 lượt liên tiếp từ cảm biến tới phun nước, ít nhất 8 lượt tia nước trúng thẻ bia. Mọi lỗi tiêm vào đều kết thúc với bơm và laser tắt.

### Tuần 5 (02–08/11): đo và mở rộng

- **Hậu:** chạy đủ số lượt cho các bài thử mạng và end-to-end, xuất bảng số liệu. Nếu đã có con H2 thứ tư thì làm bài tự phục hồi.
- **Hùng:** đo sai số pose tag, sai số tọa độ mục tiêu và sai số điểm chạm theo từng vị trí bia. Vẽ biểu đồ.
- **Hiếu:** đo ngắm một lần, ngắm có hiệu chỉnh và tỷ lệ trúng của tia nước ở 9 vị trí. Xử lý rò nước và bắn tóe.

Cổng kiểm tra: đủ số liệu cho mọi thử nghiệm ở mục 7.

### Tuần 6 (09–15/11): báo cáo và demo

- **Cả nhóm:** báo cáo, README dựng lại hệ thống, sơ đồ dây, file hiệu chuẩn, dataset và model, video demo. Đóng băng code từ giữa tuần.

Cổng kiểm tra: demo chạy trọn hai lần liên tiếp mà không phải sửa tay.

### Tuần 7 (16–22/11): dự phòng

- Chỉ sửa lỗi, không thêm tính năng. Nếu còn thời gian thì chuyển node chấp hành sang H2 chạy Thread và đo so sánh với bản Wi-Fi.

## 6. Phân công

Hùng nhận phần model và thị giác. Hậu và Hiếu chia phần Thread với điều phối và phần chấp hành; hai bạn có thể đổi mảng cho nhau.

| Người | Mảng | Việc chính | Bàn giao sớm để tích hợp |
| --- | --- | --- | --- |
| Hùng | Model và thị giác | Dataset, train YOLO, hiệu chuẩn camera, pose tag, định vị tia–mặt phẳng, tìm vết laser, đo sai số | Các hàm `detect(frame)`, `localize(pixel)`, `tag_poses(frame)`, `find_spot(on, off)` |
| Hậu | Thread và điều phối | Firmware sensor H2, RCP và OTBR, CoAP server, state machine, tính pan/tilt, log, các bài thử Thread | Server và client giả lập, cảnh báo thật, định dạng log |
| Hiếu | Chấp hành và cơ khí | Sa bàn, đế và trụ vòi phun, firmware S3, servo, bơm, laser tâm ngắm, an toàn điện, nước và quang | Node nhận `/aim` và trả trạng thái, bảng hiệu chỉnh servo, số đo offset cơ khí |

Ba chỗ giao nhau cần hai người cùng làm:

- **Offset từ tag tới đế vòi:** Hiếu đo cơ khí, Hùng kiểm tra lại bằng ảnh.
- **Bước CORRECT:** Hậu viết vòng lặp, Hùng cung cấp `find_spot`.
- **Công thức góc:** Hậu viết hàm, Hiếu cung cấp bảng hiệu chỉnh servo.

Phần của Hùng nặng nhất ở tuần 2–3. Nếu cổng tuần 2 trễ, Hậu nhận phần pose tag để Hùng tập trung vào model.

## 7. Thử nghiệm và chỉ số

Mỗi thử nghiệm chạy nhiều lượt và báo trung bình, trung vị, độ phân tán. Mọi mốc thời gian lấy theo đồng hồ của Pi; không lấy hiệu giữa đồng hồ ESP và Pi.

| Thử nghiệm | Cách đo | Số lượt | Kết quả ghi lại | Người làm |
| --- | --- | --- | --- | --- |
| Thread một hop | Telemetry liên tục | 30 phút | PDR theo `s`, độ trễ khứ hồi | Hậu |
| Thread hai hop | Ép sensor 2 đi qua sensor 1 | 30 phút | PDR và độ trễ so với một hop | Hậu |
| Mất Pi | Rút nguồn Pi rồi kích sensor 1 | 10 | Sensor 2 có báo động không, sau bao lâu; thời gian nối lại khi cắm Pi | Hậu |
| Tự phát hiện node | Reset node, chờ Pi thấy dịch vụ | 10 | Thời gian từ lúc cấp nguồn tới lúc Pi thấy | Hậu |
| Pose tag | Đặt node ở 9 điểm đo bằng thước, lặp lại sau khi dời | 9 điểm × 3 | Sai số vị trí (cm), tỷ lệ phát hiện tag | Hùng |
| Nhận diện bia | Tập ảnh kiểm tra chụp riêng, đổi ánh sáng | Từ 50 ảnh | Precision, recall, thời gian suy luận trên Pi | Hùng |
| Định vị mục tiêu | Bia ở 9 vị trí trên bảng, tọa độ đo độc lập | 9 × 3 | Sai số tổng và theo từng trục (cm) | Hùng |
| Ngắm một lần | Cùng 9 vị trí, không hiệu chỉnh | 9 × 3 | Sai số điểm chạm của laser tâm ngắm (mm) | Hiếu |
| Ngắm có hiệu chỉnh | Cùng 9 vị trí, có bước CORRECT | 9 × 3 | Sai số sau hiệu chỉnh, số bước lặp | Hiếu, Hùng |
| Phun nước | Cùng 9 vị trí, sau hiệu chỉnh và bù tilt | 9 × 3 | Trúng hoặc trượt thẻ bia; độ lệch của tâm vệt nước (cm) | Hiếu |
| End-to-end | Kích cảm biến, 20 lượt có bia và 10 lượt không có | 30 | Thời gian từ lúc Pi nhận cảnh báo tới `reached` và tới FIRE; tỷ lệ trúng; số lần báo nhầm; số lần dừng an toàn | Cả nhóm |
| Tiêm lỗi | 6 lỗi của tuần 4 | Mỗi lỗi 3 | Bơm và laser có tắt không, sau bao lâu | Hiếu |
| Tải của Pi | Ghi trong lúc chạy end-to-end | 30 | CPU, RAM, nhiệt độ | Hùng |

Các ngưỡng trong đề cương cũ (PDR từ 98%, suy luận dưới 100 ms) là mục tiêu, chưa phải kết quả. Ghi số đo thật, kể cả khi không đạt.

## 8. Phần mở rộng theo thứ tự ưu tiên

Chỉ bắt đầu phần mở rộng sau khi MVP qua cổng tuần 4.

1. **Con H2 thứ tư.** Làm sensor 3 kiêm node trung gian: rút node đang chuyển tiếp và đo thời gian mạng tự đổi đường. Đồng thời là board dự phòng.
2. **Node chấp hành chạy Thread.** Thay S3 bằng một H2, chỉ đổi phần khởi tạo mạng. So sánh độ trễ lệnh và tỷ lệ thành công giữa bản Wi-Fi và bản Thread.
3. **Sensor ngủ chạy pin.** Một node chỉ đo nhiệt ở chế độ sleepy end device, so dòng tiêu thụ và độ trễ với node router.

Để mục 2 chỉ mất khoảng một buổi, firmware S3 phải giữ bốn nguyên tắc ngay từ tuần 1:

- CoAP viết bằng thư viện chung (libcoap), không dùng API riêng của Wi-Fi hay OpenThread.
- Chỉ dùng ngoại vi mà H2 cũng có: LEDC cho servo, số chân GPIO khai trong file cấu hình.
- Payload ngắn; `ttl` và số lần gửi lại là tham số.
- Pi tìm node theo tên dịch vụ, không gõ cứng địa chỉ.

Gác lại trong đồ án này: Matter, camera thứ hai, Depth Anything.

## 9. Rủi ro và đường lui

| Rủi ro | Dấu hiệu sớm | Đường lui |
| --- | --- | --- |
| Bơm phun yếu hoặc tia nước tỏa rộng | Thử tầm phun ở tuần 2 không tới bảng | Đổi đầu vòi nhỏ hơn, đưa trụ lại gần bảng, hoặc đổi bơm áp cao hơn |
| Hỏng một con H2 | Board không nạp được hoặc không vào mạng | Mua ngay một H2 mini; trong lúc chờ, chạy với một sensor |
| Laser lệch quá dung sai | Cổng tuần 2 không đạt 5 cm | Dựa vào bước CORRECT; nếu vẫn lệch, hiệu chỉnh trực tiếp từ pixel sang góc bằng 9 điểm trên bảng, bỏ qua chuỗi pose tag |
| Pose tag nhiễu hoặc camera không thấy tag | Sai số vị trí trên 2 cm | Tăng cỡ tag, lấy trung bình nhiều khung; với sensor chỉ dùng vị trí, không dùng hướng |
| YOLO nhận diện không ổn định | Recall thấp ở tuần 3 | Thêm ảnh chụp đúng sa bàn; đường lui là dán tag lên bia hoặc lọc theo màu, ghi rõ chế độ đang chạy |
| Hùng quá tải ở tuần 2–3 | Cổng tuần 2 trễ | Hậu nhận phần pose tag |
| OTBR khó cài trên Pi | Kẹt quá 2 ngày ở tuần 0–1 | Chạy OTBR bằng Docker |
| Hàng về trễ | Chưa có servo hoặc cảm biến khi vào tuần 2 | Tuần 1 đã chạy bằng dữ liệu giả; kéo dài thêm và dồn việc phần mềm lên trước |
| Wi-Fi ở nơi demo không ổn định | S3 rớt kết nối khi thử tại chỗ | Pi tự phát Wi-Fi riêng cho S3; cáp USB nối thẳng làm đường dự phòng |
| Mất mạng hoặc treo giữa lệnh | Thấy trong bài tiêm lỗi | Bơm và laser mặc định tắt, `ttl`, heartbeat, `/stop` |
| Dời node sau khi thiết lập | Tag tham chiếu hoặc tag đế lệch | Pose bị đánh dấu hết hạn, chặn ngắm cho tới khi commissioning lại |
| Nước bắn vào mạch hoặc ổ cắm | Có vệt nước ngoài khay sau các lượt thử | Hộp che cho sensor và S3, nguồn đặt sau vòi, rút ngắn thời gian phun |
| Tia nước lệch khỏi điểm laser tâm ngắm | Phun thử ở tuần 3 lệch đều một hướng | Chỉnh bảng bù tilt theo khoảng cách; cố định vòi cứng hơn; giữ mực nước trong bình ổn định |

## 10. Việc còn phải chốt

- [ ] Giảng viên xác nhận hai điểm: phun nước thật lên bia mô phỏng (không có lửa thật), và node chấp hành chạy Wi-Fi có phù hợp với đề tài Thread hay không.
- [ ] Hạn nộp và ngày demo chính thức, để dời lịch ở mục 5.
- [ ] Hậu và Hiếu xác nhận mảng của mình hoặc đổi cho nhau.
- [ ] Đã có webcam chưa và là loại nào.
- [ ] Chỗ đặt sa bàn cố định trong suốt 6 tuần.
- [ ] Ngân sách cho danh sách mua ở mục 2 và con H2 dự phòng.
- [ ] Dung sai ngắm chính thức (đề xuất 3 cm ở 70 cm), chốt ở cuối tuần 3.
- [ ] Cập nhật file kiến trúc cũ: node chấp hành là S3 qua Wi-Fi, thêm bước CORRECT và các resource `/fire`, `/hb`, `/alarm`.
