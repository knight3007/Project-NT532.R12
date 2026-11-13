# NT532 — Node A integrated firmware 0.1.0

Ngày: 2026-11-13. Phạm vi: tạo firmware mới cho ESP32-H2; không flash, không chạy tải vật lý, không sửa Node A Auto-Start hoặc Node B.

## 1. Kết quả và kiến trúc

**PASS trên host/build:** `firmware/node-a-integrated` là ứng dụng riêng, version `a-integrated-0.1.0`, build ESP-IDF v5.4.4 cho `esp32h2`. Main task khởi tạo GPIO relay/servo ở LOW, NVS, OpenThread và CoAP; khôi phục Thread từ active dataset đã có trong NVS. Servo task chạy tick 20 ms, dùng lại control/watchdog của B 0.2.3. MQ-2 task ưu tiên thấp lấy mẫu ADC riêng mỗi 1 s và ghi snapshot được khóa; đường CoAP chỉ đọc snapshot, không chờ ADC. Không có đường sensor tự bật relay.

**BLOCKED trên thiết bị:** chưa xác minh đấu dây, điện áp AO/DO, cơ khí cụm A, dữ liệu MQ-2 hoặc chạy CoAP trên Node A. Build và host test không chứng minh hoạt động vật lý.

## 2. Pin và căn cứ

| GPIO | Chức năng dự kiến | Trạng thái |
|---:|---|---|
| 1 | MQ-2 AO qua mạch chia áp thích hợp; ADC1_CH0 | Mapping chip xác nhận trong [ESP-IDF GPIO ESP32-H2](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/gpio.html) và [ADC OneShot](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/adc_oneshot.html); board/harness chưa đo |
| 12 | MQ-2 DO qua chuyển mức | Mặc định tắt; chưa xác nhận có đấu, polarity và xung đột với chân pump trong kế hoạch cũ |
| 4, 5 | SG90 Pan/Tilt PWM 50 Hz | Cần kiểm tra chiều quay, khoảng trống, chân board và nguồn 5 V riêng, GND chung |
| 10 | Relay active-HIGH, LED mô phỏng laser | LOW khi boot/STOP/timeout/detach; cần đo trên cụm A |

MQ-2 có thể cần nguồn 5 V cho heater. Không cấp nguồn qua GPIO. **Phải đo điện áp AO và DO tại đầu vào ESP32-H2 sau chia áp/chuyển mức trước khi đấu vào H2; không giả định module 5 V xuất mức an toàn.** ADC attenuation chỉ đặt thang đo, không bảo vệ chân khỏi quá áp. Board ESP32-H2 Super Mini và dây thực tế chưa được kiểm tra trực tiếp.

## 3. Mã nguồn và tái sử dụng

- Tạo `firmware/node-a-integrated/CMakeLists.txt`, `sdkconfig.defaults`, `main/CMakeLists.txt`, `main/app_main.c`, README và test scripts.
- Sao chép nguyên byte `thread_start.c/.h` và `sdkconfig.defaults` từ Node A Auto-Start; sao chép nguyên byte `integrated_control.c/.h`, `servo_output.c/.h`, `probe_state.c/.h` từ Node B **0.2.3 trong workspace**. Đã so sánh SHA cho từng file: khớp nguồn tương ứng. Không lấy giới hạn từ 0.2.2.
- Tạo `main/mq2_config.h`, `mq2_adc.c/.h`, `mq2_logic.c/.h`, `mq2_sensor.c/.h`, `node_a_response.c/.h`; tích hợp `/mq2` và identity A trong `app_main.c`. Không có DHT11 trong project mới.
- Tạo `test/test_mq2_logic.c`, `test/test_mq2_adc.c`, `test/test_node_a_response.c` và fake ADC headers. `test/run_host_tests.ps1` dùng lại test nguồn Node B và Node A Auto-Start, không nhân bản bộ test đó.
- Tạo `test/node_a_coap_acceptance.sh` cho Pi; **chỉ kiểm tra cú pháp `bash -n`, chưa chạy script với thiết bị**.

## 4. MQ-2

`mq2_config.h` tập trung GPIO, ADC attenuation `ADC_ATTEN_DB_12`, độ rộng 12 bit, 8 mẫu trung bình mỗi 1000 ms, warm-up 300000 ms, freshness 3000 ms, DO disabled, DO active level dự kiến 0, debounce 3 mẫu, ngưỡng alarm `-1` (disabled), hysteresis 0. Mapping GPIO1→ADC1_CH0 được kiểm tra lại bằng `adc_oneshot_io_to_channel` lúc khởi tạo; mismatch làm init lỗi. [ADC Calibration ESP32-H2](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/adc_calibration.html) hỗ trợ curve fitting: chỉ trả `adc_mv` khi driver hiệu chuẩn thành công; đó là điện áp **tại GPIO1 sau chia áp**, không phải điện áp trực tiếp của AO module. Raw vẫn có thể đọc khi thiếu calibration. Không có công thức ppm hoặc ngưỡng báo cháy tự đặt.

`GET /mq2` trả `sensor`, `sensor_ok`, `warming_up`, `adc_raw`, `adc_mv`, `do_level`, `do_active`, `alarm`, `sample_age_ms`, `error`. Trước mẫu hợp lệ hoặc khi lỗi ADC, dữ liệu không được báo thành phép đo mới. DO không đấu là `null`. `alarm:null` là chưa có tiêu chí hiệu chuẩn, kể cả khi có ADC raw; warm-up chỉ là trạng thái theo thời gian, không xác nhận sensor đã ổn định thực tế. Không có hành động relay tự động từ MQ-2.

## 5. CoAP và điều khiển

| API | Nội dung |
|---|---|
| `GET /info` | `n:A`, `fw:a-integrated-0.1.0`, `mode:integrated`, `sensor:mq2` |
| `GET /status` | Thread role, `stopped`, `hb_age_ms`, `hb_fresh`, `stop_count`, `servo_armed`, `servo_pwm`, `relay_on`, `laser_on:false`, `mq2_sensor_ok`, `mq2_adc_raw`, `mq2_alarm` |
| `GET /mq2` | Trạng thái cảm biến ở mục 4 |
| `GET /pantilt`; `POST /pantilt` | ARM, HOME 1500/1500, target Pan 500–2500 và Tilt 600–2500 µs; reject vượt giới hạn |
| `GET /laser`; `POST /laser` | Relay LED ON/OFF, không điều khiển laser thật |
| `POST /hb`; `POST /stop` | Heartbeat, emergency STOP |

Kế thừa watchdog 1500 ms, ramp tối đa 20 µs mỗi tick 20 ms, PWM 50 Hz, boot OFF, STOP không HOME, servo GPIO LOW sau STOP và relay LOW. ARM cần attached/fresh heartbeat; HOME khởi động PWM trước khi target/relay ON. OFF/STOP vẫn nhận khi heartbeat cũ. Mất Thread hoặc heartbeat xóa authorization và tắt tải; reboot không phục hồi ARM/relay. Payload điều khiển dài hơn 16 byte trả `4.13`, sai định dạng/vượt giới hạn `4.00`, chưa đủ điều kiện `4.03`, như B. JSON `/status` và `/mq2` dùng buffer 512 byte với kiểm tra tràn.

## 6. Thread và phân vùng

`thread_start_existing()` của A chỉ bật IPv6/Thread nếu active dataset đã commissioned; khi thiếu dataset, Thread giữ disabled và **không tự tạo network**. Luồng USB serial `START`/`TLV:` còn nguyên, không log nội dung TLV; không nhúng dataset/Network Key trong logic ứng dụng. `sdkconfig.defaults` giống Node A Auto-Start. Partition table binary của bản mới, Node A Auto-Start và Node B 0.2.3 cùng SHA-256 `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820`; app offset `0x10000`, NVS giữ nguyên. Do đó chỉ đề xuất flash app-only, tuyệt đối không erase NVS.

## 7. Kiểm thử host và build

Chạy `powershell -ExecutionPolicy Bypass -File firmware/node-a-integrated/test/run_host_tests.ps1` từ repo root: **PASS 7/7** nhóm `mq2_logic`, `mq2_adc`, `node_a_response`, `integrated_control`, `servo_output`, `probe_state`, `thread_start` với GCC `-Wall -Wextra -Werror`. Các test kiểm tra ADC init/read/failure và calibration, raw/mV/null, warm-up/freshness/DO/alarm, identity A, giới hạn servo/ramp, relay safety, STOP/LOW, timeout kể cả fault-injection relay ON với PWM inactive, và Thread Auto-Start có/không có dataset. Không chạy các phép đo thiết bị.

`idf.py --no-ccache -B ../../build-output/node-a-integrated-0.1.0 build` từ project mới: **PASS** với ESP-IDF v5.4.4, target ESP32-H2, app version đúng. Image `esptool image_info`: checksum và validation hash valid. App 784000 byte (`0xBF680`), vừa partition 1 MiB, còn `0x40980` byte (25%). `bash -n test/node_a_coap_acceptance.sh`: **PASS** cú pháp. Cảnh báo `git dubious ownership` từ cây IDF thuộc tài khoản khác xuất hiện trong log nhưng không chặn build.

Binary: `build-output/node-a-integrated-0.1.0/nt532_node_a_integrated.bin`

SHA-256: `80F44A0DFF4D6F374CCAA9A7541E39468003346419FA47492614761B2DA378AE`

Binary cũ còn nguyên theo hash: Node A Auto-Start `958E8CE231414702B2BBB5E7C4CE225CAC29AA989B312B30385A9CF369CC6D37`; Node B 0.2.2 `63A8B8AAAC029EC7A9E3A0FCBD27711D0516E31440340F939A642227B64CC13B`; Node B 0.2.3 `07A913214400722C5089FF2177D176DA6F8777778014F58BA18B3ED768ECFE51`.

## 8. Flash app-only và rollback (chỉ cho người nghiệm thu, chưa thực thi)

Sau khi xác minh điện áp, pinout và nguồn, chạy từ repo root trên Windows với đúng cổng Node A. Các lệnh này chỉ ghi app ở `0x10000`, không ghi bootloader/partition/NVS:

```powershell
& 'D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe' -m esptool --chip esp32h2 -p COM_A -b 460800 --before default_reset --after hard_reset write_flash 0x10000 'build-output\node-a-integrated-0.1.0\nt532_node_a_integrated.bin'
```

Rollback app Node A Auto-Start đã nghiệm thu:

```powershell
& 'D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe' -m esptool --chip esp32h2 -p COM_A -b 460800 --before default_reset --after hard_reset write_flash 0x10000 'firmware\node-a-thread-autostart\build\nt532_node_a_autostart.bin'
```

Thay `COM_A` bằng cổng thật của Node A; xác nhận board trước khi chạy. Không chạy `idf.py flash` vì lệnh build gợi ý sẽ ghi cả bootloader và partition table.

## 9. Checklist Pi/PuTTY sau flash

1. Trước khi cắm AO/DO, đo điện áp đầu ra qua chia áp/chuyển mức ở mọi trạng thái có thể có. Kiểm tra chân Super Mini và GPIO12 không trùng tải khác. Xác nhận nguồn servo 5 V riêng, GND chung, relay chỉ nối LED mô phỏng; ngắt laser thật.
2. Khởi động A với nguồn tải tách riêng. PuTTY USB serial: ghi version, role, mesh-local EID, có/không khôi phục Thread từ NVS. Không nhập dataset mới nếu Auto-Start đã chạy. Kiểm tra relay GPIO10 và servo GPIO4/5 LOW lúc boot.
3. Pi/OTBR: `GET /info` nhận `n:A`, đúng version; `GET /status` có MQ-2, không có DHT11; `GET /mq2` phản ánh warm-up/raw và `alarm:null`. Nếu calibration không có, `adc_mv:null` là hợp lệ. So sánh ADC với điện áp chân đo bằng đồng hồ; đánh giá độ nhiễu và thời gian ổn định trước khi đặt ngưỡng.
4. Kiểm tra trước ARM: PWM OFF; ON bị từ chối. Gửi heartbeat mỗi 400–500 ms, ARM, HOME rồi kiểm tra 1500/1500 và tốc độ ramp. Dùng logic analyzer/oscilloscope xác nhận PWM 50 Hz, Pan 500–2500, Tilt 600–2500, STOP đưa GPIO4/5 LOW liên tục không glitch, GPIO10 LOW. Sau đó mới gắn servo để xác nhận hướng quay và khoảng trống cơ khí của A.
5. Kiểm tra relay LED ON/OFF, STOP, timeout và detach đều OFF; reboot vẫn OFF. Timeout khi relay ON và servo PWM inactive đã được test fault-injection trên host; kiểm chứng tín hiệu vật lý sau này nếu có cách tái tạo an toàn.
6. Kiểm tra Node B vẫn hoạt động độc lập trên cùng OTBR, không nhầm EID/identity. Ghi kết quả đo/ảnh trace và chỉ sau đó hiệu chuẩn ngưỡng raw/mV, hysteresis, DO polarity nếu DO thực sự đấu.

Script Pi (copy `test/node_a_coap_acceptance.sh` lên Pi; **chưa chạy ở đây**). Script bắt buộc xác nhận cho phép output, giữ heartbeat nền, cleanup OFF/STOP khi thoát. Chạy sweep chỉ sau xác nhận cơ khí; watchdog là lần chạy riêng:

```bash
bash node_a_coap_acceptance.sh --eid 'NODE_A_MESH_LOCAL_EID' --allow-outputs --allow-extremes
bash node_a_coap_acceptance.sh --eid 'NODE_A_MESH_LOCAL_EID' --allow-outputs --watchdog-only
```

## 10. BLOCKED còn lại

- Điện áp AO/DO, mạch chia áp/chuyển mức, board pinout và trạng thái đấu DO chưa đo; firmware để DO disabled.
- Polarity DO và ngưỡng cảnh báo/hysteresis chưa hiệu chuẩn; `alarm:null` chủ ý, không có ppm.
- Biên độ, chiều quay, va chạm cơ khí servo cụm A, relay LED và mức LOW sau STOP chưa được kiểm chứng bằng thiết bị.
- Chưa flash Node A, chưa chạy script CoAP trên Pi, chưa xác nhận Auto-Start/OTBR thực tế hay ảnh hưởng tới Node B trên mạng thật.
