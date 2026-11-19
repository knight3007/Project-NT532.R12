# HỆ THỐNG PHÁT HIỆN VÀ ĐỊNH VỊ SỰ CỐ CHÁY SỬ DỤNG THREAD VÀ EDGE AI

NT532 dùng Raspberry Pi 5 xử lý hình ảnh và hai node ESP32-H2 A/B trao đổi qua OTBR/Thread/CoAP. Bản trình diễn dùng mục tiêu lửa hiển thị trên màn hình laptop để đánh giá phát hiện, định vị và hướng ngắm; không phải hệ thống dập lửa thật. Firmware A/B tích hợp hiện điều khiển pan–tilt và relay/LED mô phỏng, không điều khiển bơm.

## Project Overview

Camera Android truyền video qua MediaMTX tới Pi; Pi định vị mục tiêu và tính góc pan–tilt rồi gửi lệnh qua CoAP/Thread. Hai kịch bản trình bày dùng Node A làm primary và Node B làm backup. Dashboard phục vụ demo local/LAN.

## System Architecture

![Sơ đồ kiến trúc NT532: camera Android, Raspberry Pi 5, Thread và hai node ESP32-H2](docs/picture%20and%20video/nt532_system_architecture.png)

*Sơ đồ thành phần và luồng dữ liệu của bản trình bày; không thể hiện mức nghiệm thu phần cứng.*

## Hardware Design & GPIO Mapping

Hai bảng dưới đây mô tả **ánh xạ tín hiệu theo source firmware tích hợp** trên ESP32-H2 Super Mini, chưa xác nhận toàn bộ dây nối và mức điện áp trên cụm phần cứng thực tế.

### Node A — Primary Node

| Thành phần | Tín hiệu | GPIO | Trạng thái trong firmware |
| --- | --- | --- | --- |
| Pan servo | PWM | GPIO4 | Điều khiển pan |
| Tilt servo | PWM | GPIO5 | Điều khiển tilt |
| Relay | IN | GPIO10 | Active-HIGH; đầu ra dùng cho LED mô phỏng |
| MQ-2 | AOUT / ADC | GPIO1 | ADC1_CH0 qua mạch chia áp phù hợp |
| MQ-2 | DOUT | GPIO12 | Đã khai báo nhưng **đang tắt** (`MQ2_DO_ENABLED = 0`) |

![Sơ đồ minh họa các thành phần và chân tín hiệu Node A](docs/picture%20and%20video/nodeA.png)

*Hình minh họa thiết kế, chưa phải sơ đồ đấu nối đã nghiệm thu. Hình vẽ MQ-2 AOUT/DOUT nối tới GPIO nhưng firmware hiện tắt DOUT; AOUT cần mạch chia áp, DOUT cần chuyển mức và cả hai phải được đo trước khi nối. Module laser trong hình không thuộc đầu ra đang được firmware điều khiển.*

### Node B — Backup Node

| Thành phần | Tín hiệu | GPIO | Trạng thái trong firmware |
| --- | --- | --- | --- |
| Pan servo | PWM | GPIO4 | Điều khiển pan |
| Tilt servo | PWM | GPIO5 | Điều khiển tilt |
| Relay | IN | GPIO10 | Active-HIGH; đầu ra dùng cho LED mô phỏng |
| DHT11 | DATA | GPIO11 | Tín hiệu cảm biến |

![Sơ đồ minh họa các thành phần và chân tín hiệu Node B](docs/picture%20and%20video/nodeB.png)

*Hình minh họa thiết kế, chưa xác nhận nguồn servo, relay và dây nối thực tế. Module laser trong hình không thuộc đầu ra đang được firmware điều khiển.*

Servo cần nguồn riêng phù hợp với tải và **common GND** với ESP32-H2; GPIO4/5 chỉ cấp tín hiệu PWM. GPIO10 điều khiển **đầu vào module relay**, không cấp điện trực tiếp cho cuộn relay; nguồn cho module/tải phải được kiểm tra theo phần cứng thực tế. Relay cần giữ OFF khi khởi động/reset; firmware kéo GPIO10 LOW và tài liệu firmware yêu cầu điện trở kéo xuống ngoài. MQ-2 phải được cấp nguồn theo đặc tả module; đo điện áp AOUT sau mạch chia áp và DOUT sau mạch chuyển mức trước khi nối ESP32-H2. Không dùng cấu hình ADC để thay thế bảo vệ quá áp.

## Core Technologies & Features

- **Edge AI và định vị:** YOLO phát hiện mục tiêu; ChArUco hiệu chuẩn camera; AprilTag và Dynamic Mapping hỗ trợ Localization.
- **Điều khiển:** Pi tính góc pan–tilt, trao đổi với hai node qua OTBR/RCP, Thread và CoAP.
- **Trình diễn:** Node A/B điều khiển servo và relay/LED mô phỏng với mục tiêu lửa trên màn hình laptop; không xác nhận dập cháy thật.

## Performance & Experimental Results

Hai biểu đồ thực nghiệm dưới đây là nguồn cho kết quả này: **11 lượt được ghi nhận; 9 lượt đủ mốc dùng để tính trung bình**, 2 lượt thiếu mốc do định dạng log cũ được loại khỏi phép tính. Thời gian trung bình của 9 lượt hợp lệ là **74,15 giây**.

| Giai đoạn | Trung bình | Tỷ trọng |
| --- | ---: | ---: |
| LOCALIZE | 7,83 s | 10,6% |
| AIM | 26,17 s | 35,3% |
| FIRE | 39,77 s | 53,6% |
| VERIFY | 0,37 s | 0,5% |
| **Tổng** | **74,15 s** | **100%** |

Các khoảng lần lượt là báo động → DECIDE, DECIDE → trúng mục tiêu (gồm quét/chỉnh), trúng mục tiêu → log `đã dập`, và log `đã dập` → kết thúc. FIRE/`đã dập`/VERIFY là nhãn log với mục tiêu lửa trên màn hình laptop, không chứng minh dập cháy thật.

![Thời gian trung bình theo từng giai đoạn của 9 lượt đủ mốc](docs/picture%20and%20video/nt532-average-phase-durations.png)

[Xem biểu đồ timeline của 6 lượt đại diện](docs/picture%20and%20video/nt532-representative-trial-timelines.png) và [tài liệu kết quả chi tiết](docs/README.md#experimental-results--media).

## Technology Stack & Repository Structure

| Thành phần | Vai trò | Vị trí |
| --- | --- | --- |
| Raspberry Pi 5: Python, YOLO, MediaMTX, OTBR/CoAP | Xử lý ảnh, định vị, điều phối và dashboard | `pi/src/nt532/` |
| ESP32-H2: ESP-IDF, Thread | Node A/B tích hợp và RCP | `firmware/node-a-integrated/`, `firmware/node-b-integrated/`, `firmware/rcp/` |
| Cấu hình và hiệu chuẩn | Hình học sa bàn, giới hạn, kết quả hiệu chuẩn | `config/site.yaml`, `calibration/` |
| Tài liệu | Giao thức, kết quả và quy trình | `docs/`, `firmware/README.md` |

## Demo

[Xem thư mục tư liệu hình ảnh](docs/picture%20and%20video/). Mô hình dùng cụm phần cứng thật và mục tiêu lửa hiển thị trên màn hình laptop. Video được giữ local, không đưa lên GitHub.

## Current Status & Limitations

[Runbook E2E](docs/nt532-e2e-runbook.md) ghi nhận software E2E với node giả đã đạt. Các kiểm tra từng cụm phần cứng không thay cho nghiệm thu physical hardware E2E toàn hệ thống. Bảng GPIO mô tả source hiện tại; dây nối và mức điện áp trên mô hình cần được đo đối chiếu trước khi vận hành.

## Documentation & Future Work

[Mục lục tài liệu](docs/README.md) dẫn tới firmware, định vị, thực nghiệm và runbook. Jev2 là hướng nghiên cứu tiếp theo, chưa thuộc đường chạy tạo ra số liệu hiệu năng ở trên.
