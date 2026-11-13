# NT532 Node B v0.2.3 — khôi phục dải xung Pan/Tilt hiệu chỉnh

Ngày: 2026-11-13. Firmware: `firmware/node-b-integrated`, ESP32-H2, ESP-IDF v5.4.4. Nền 0.2.2 đã được người dùng flash và xác nhận Pan/Tilt, relay LED, HOME, STOP hoạt động trên phần cứng. Trong lần làm việc này **không flash, không kích servo/relay thật, không xóa NVS, không đổi Thread dataset, không commit/push**.

## Kết quả

| Hạng mục | Trạng thái | Bằng chứng / giới hạn |
| --- | --- | --- |
| Dải Pan 500–2500 µs và Tilt 600–2500 µs độc lập | **PASS** | Cùng hằng số hiệu chỉnh dùng ở kiểm tra CoAP, parser, driver LEDC và JSON. Host test nhận bốn biên, từ chối bốn giá trị vượt biên. |
| HOME và ramp | **PASS host** | HOME 1500/1500 µs; mỗi trục thay đổi tối đa 20 µs mỗi bước; đổi target tiếp tục từ vị trí hiện tại. |
| STOP, timeout, Thread detach, relay LED | **PASS host** | State test và fake LEDC/GPIO xác nhận PWM dừng, GPIO4/5 LOW, GPIO10 LOW; ON/OFF relay vẫn hoạt động. |
| `GET /pantilt` | **PASS host/build** | Kiểm JSON trạng thái không PWM và có PWM; bốn trường giới hạn trục đúng. Chưa gọi CoAP qua radio trên bản 0.2.3. |
| Toàn bộ host tests | **PASS** | 5/5 suite: probe, dht11, thread, control, servo_relay; GCC 15.2.0, `-std=c11 -Wall -Wextra -Werror`. |
| Build ESP-IDF | **PASS** | ESP-IDF v5.4.4, target esp32h2, `PROJECT_VER=b-integrated-0.2.3`; app 772496 byte, còn `0x43670` byte (26%) trong phân vùng app 1 MiB. `esptool image_info` báo checksum và validation hash hợp lệ. |
| Nghiệm thu dải cơ khí trên Node B | **BLOCKED** | Cần người dùng flash app và kiểm từng bước trên cụm lắp ráp thật. Host tests không xác nhận giới hạn cơ khí. |

## Thay đổi source

| File | Dòng | Thay đổi |
| --- | --- | --- |
| `firmware/node-b-integrated/main/integrated_control.h` | 8–14, 49 | Giữ bốn giới hạn hiệu chỉnh riêng, bỏ `CONTROL_TEST_MIN_US/MAX_US`; khai báo hàm tạo JSON `/pantilt`. |
| `firmware/node-b-integrated/main/integrated_control.c` | 6–10, 57–98, 153–169 | Assert HOME nằm trong cả hai dải; parser và validation dùng giới hạn riêng; tạo JSON trạng thái và bốn trường giới hạn. |
| `firmware/node-b-integrated/main/servo_output.c` | 12, 77–81, 126 | `servo_output_set_us()` và HOME dùng cùng hằng số với CoAP. Không đổi LEDC 50 Hz hoặc đường STOP. |
| `firmware/node-b-integrated/main/app_main.c` | 242–248 | `GET /pantilt` gọi formatter đã test; lỗi format trả 5.00 không kèm JSON cắt dở. |
| `firmware/node-b-integrated/test/test_integrated_control.c` | 11–15, 32–34, 50–83 | JSON, biên hợp lệ/không hợp lệ, HOME, ramp, đổi target. Test STOP/watchdog/detach và relay cũ giữ nguyên. |
| `firmware/node-b-integrated/test/test_servo_output.c` | 98–106 | Kiểm duty ở biên hợp lệ, từ chối vượt biên và không đổi duty khi lỗi. Test GPIO LOW/relay cũ giữ nguyên. |
| `firmware/node-b-integrated/CMakeLists.txt` | 3 | Tăng version lên `b-integrated-0.2.3`. |
| `firmware/node-b-integrated/README.md` | 3, 19, 29–37, 41–57, 75–77 | Cập nhật contract CoAP, build/flash, tình trạng 0.2.2 đã nghiệm thu và rollback về 0.2.2. |

| Trục | Giới hạn POST 0.2.2 | Giới hạn POST 0.2.3 | HOME |
| --- | --- | --- | --- |
| Pan GPIO4 | 1300–1700 µs | **500–2500 µs** | 1500 µs |
| Tilt GPIO5 | 1300–1700 µs | **600–2500 µs** | 1500 µs |

`POST /pantilt` vẫn nhận `ARM`, `HOME`, hoặc cặp số nguyên `pan_us,tilt_us`. Các payload `500,1500`, `2500,1500`, `1500,600`, `1500,2500`, `500,600`, `2500,2500` được chấp nhận **sau** heartbeat → ARM → HOME. Các payload `499,1500`, `2501,1500`, `1500,599`, `1500,2501` bị từ chối bằng **4.00 Bad Request**; target cũ không thay đổi. Không clamp giá trị vượt biên. Điều kiện 4.03 Forbidden cho lệnh hợp lệ khi thiếu authorization vẫn giữ nguyên. `GET /pantilt` giữ `armed`, `pwm_active`, `pan_us`, `tilt_us`, `target_pan_us`, `target_tilt_us` và thay hai trường `test_*` bằng `pan_min_us:500`, `pan_max_us:2500`, `tilt_min_us:600`, `tilt_max_us:2500`.

## Kiểm thử và binary

Đã chạy cả năm host suite từ thư mục gốc bằng GCC với `-std=c11 -Wall -Wextra -Werror`, include/source thuộc `firmware/node-b-integrated`. Kết quả: `probe: PASS`, `dht11: PASS`, `thread: PASS`, `control: PASS`, `servo_relay: PASS`. Test `control` mới thất bại ở biên 500 µs trên source cũ và qua sau khi thay giới hạn; test formatter mới thất bại ở bước link trước khi triển khai. Host tests kiểm state/driver fake, không đo xung điện thực tế.

Lệnh build từ `firmware/node-b-integrated` trong môi trường ESP-IDF v5.4.4:

```powershell
idf.py --no-ccache -B ../../build-output/node-b-integrated-0.2.3 build
```

Build cuối cùng **PASS** sau khi sửa đường lỗi JSON. CMake xác nhận `App ... version: b-integrated-0.2.3`; `esptool image_info` xác nhận ảnh ESP32-H2, checksum và validation hash hợp lệ. Có cảnh báo `git dubious ownership` khi CMake xem thư mục ESP-IDF của sandbox, nhưng cấu hình và build hoàn thành, version được đặt trực tiếp trong `CMakeLists.txt`.

Binary mới: `build-output/node-b-integrated-0.2.3/nt532_node_b_integrated.bin`

Kích thước: **772496 byte**

SHA-256: **`07A913214400722C5089FF2177D176DA6F8777778014F58BA18B3ED768ECFE51`**
`app-flash_args`: `0x10000 nt532_node_b_integrated.bin`; mode `dio`, 48 MHz, flash 4 MB.

Binary 0.2.2 được giữ nguyên tại `build-output/node-b-integrated-0.2.2/nt532_node_b_integrated.bin`; SHA-256 sau build vẫn là **`63A8B8AAAC029EC7A9E3A0FCBD27711D0516E31440340F939A642227B64CC13B`**. Hai partition table 0.2.2/0.2.3 có cùng SHA-256 `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820`.

## App-only flash và kiểm CoAP — hướng dẫn, chưa chạy

Xác định đúng COM của Node B, giữ laser thật và pump ngắt. Từ `firmware/node-b-integrated`, với môi trường ESP-IDF v5.4.4:

```powershell
idf.py --no-ccache -B ../../build-output/node-b-integrated-0.2.3 -p COM_PORT app-flash
```

Chỉ ghi app ở offset `0x10000`; không dùng `flash` toàn bộ hay xóa NVS. Sau khi flash, xác nhận `/info.fw` là `b-integrated-0.2.3`, Thread Auto-Start, DHT11 và relay GPIO10 OFF. Lấy EID hiện tại của B. Các lệnh dưới chạy từ Raspberry Pi có `coap-client`:

```sh
NODE_B_EID='fd..:..'
coap-client -m get "coap://[$NODE_B_EID]/info"
coap-client -m get "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e '499,1500' "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e '2501,1500' "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e '1500,599' "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e '1500,2501' "coap://[$NODE_B_EID]/pantilt"
```

`GET` phải có bốn giới hạn riêng nêu trên. Bốn POST vượt biên phải là `4.00 Bad Request`, không kích chuyển động. Khi bắt đầu nghiệm thu chuyển động, gửi `/hb` định kỳ mỗi 400–500 ms; sau heartbeat mới gửi ARM và HOME:

```sh
coap-client -m post -e '' "coap://[$NODE_B_EID]/hb"
coap-client -m post -e ARM "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e HOME "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e '1550,1500' "coap://[$NODE_B_EID]/pantilt"
coap-client -m get "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e '' "coap://[$NODE_B_EID]/stop"
```

## Nghiệm thu phần cứng theo mức nhỏ

1. Trước khi cấp 5 V cho servo, kiểm GPIO4/GPIO5 bằng oscilloscope hoặc logic analyzer: boot/STOP LOW, HOME 1500/1500 µs ở 50 Hz, các bước thay duty không vượt 20 µs mỗi 20 ms. Kiểm GPIO10 LOW khi boot và STOP. Nguồn servo 5 V riêng có chung GND; relay chỉ điều khiển LED mô phỏng.
2. Sau khi trace ổn, cấp 5 V servo, bảo đảm cụm Pan/Tilt không vướng cơ khí tại HOME. Giữ heartbeat mỗi 400–500 ms. Di chuyển **một trục mỗi lần**, trục kia giữ 1500 µs: Pan thử 1550 rồi 1600, về HOME, thử 1450 rồi 1400; Tilt thử tương tự 1550/1600 và 1450/1400. Dừng ở mỗi bước để quan sát khoảng hở, rung, tiếng kẹt và dòng nguồn. Giảm bước nếu cần.
3. Chỉ mở rộng dần sau khi từng mức qua: ví dụ thêm 50 µs mỗi lần về hai phía, HOME giữa các lượt. Không tự động quét hoặc gửi ngay mục tiêu 500/2500 µs trong lần đầu. Dải xung hiệu chỉnh không thay thế giới hạn cơ khí của cụm lắp ráp. Nếu kẹt, nhấn `/stop`, ngắt nguồn servo khi cần và ghi lại mức xung cuối an toàn.
4. Kiểm `/laser` ON/OFF với **LED mô phỏng**: ON chỉ sau heartbeat → ARM → HOME; OFF, `/stop`, timeout >1500 ms và Thread detach phải đưa relay/GPIO10 LOW. Sau STOP hoặc timeout, muốn tiếp tục phải gửi heartbeat mới → ARM → HOME. Không có lệnh tự HOME khi emergency STOP.
5. Chỉ sau khi cụm cơ khí cho phép, kiểm bốn biên hợp lệ `500,1500`, `2500,1500`, `1500,600`, `1500,2500` từng lệnh một, vẫn giữ heartbeat và khả năng STOP. Nếu cơ khí không cho phép, ghi **BLOCKED** cho phần nghiệm thu biên đó; host tests đã xác nhận API nhận biên.

## Rollback về 0.2.2 — hướng dẫn, chưa chạy

Nếu 0.2.3 không đạt nghiệm thu, STOP và ngắt nguồn servo; xác nhận đúng COM của Node B và đối chiếu SHA-256 0.2.2 ở trên. Từ `firmware/node-b-integrated`, ghi **chỉ app** cũ tại `0x10000`:

```powershell
python -m esptool --chip esp32h2 -p COM_PORT write_flash --flash_mode dio --flash_freq 48m --flash_size 4MB 0x10000 ../../build-output/node-b-integrated-0.2.2/nt532_node_b_integrated.bin
```

Không ghi bootloader/partition, không xóa NVS/dataset. Sau rollback, kiểm `/info.fw` là `b-integrated-0.2.2`, Thread tự nối và các chức năng đã được nghiệm thu ở 0.2.2 trước khi cấp lại nguồn servo.
