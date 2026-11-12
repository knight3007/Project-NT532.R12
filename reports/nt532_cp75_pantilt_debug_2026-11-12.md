# NT532 CP7.5 — Node B Pan/Tilt HOME disarm (2026-11-12)

## Kết luận

Source xác định lỗi tất định trên đường khởi động PWM của bản `b-integrated-0.2.0`: `servo_output_set_us()` gọi `ledc_set_duty_and_update()` nhưng firmware không cài LEDC fade service. Trong ESP-IDF v5.4.4, `components/esp_driver_ledc/src/ledc.c:1601` gọi `ledc_fade_channel_init_check()`; tại dòng 1309, hàm này log `Fade service not installed` và trả `ESP_FAIL` khi chưa `ledc_fade_func_install()`. Do đó `POST /pantilt` `HOME` trả 2.04 khi mới đặt `home_requested`; servo task sau đó khởi tạo LEDC, gặp lỗi khi đặt xung HOME và gọi `control_stop()`. Kết quả `armed:false`, `pwm_active:false`, trong khi `stop_count` không tăng. Việc chưa cấp 5 V riêng cho servo không tác động đến lỗi API LEDC này.

Đây là nguyên nhân xác định từ source và được tái hiện bằng host test với giả lập lỗi fade service. Chưa có USB Serial/JTAG log hoặc phép đo GPIO từ Node B để xác nhận đường lỗi cụ thể đã xảy ra trên board. Nếu firmware mới vẫn disarm sau HOME, cần lấy log `Servo start failed: <esp_err>`, `Servo update failed: <esp_err>`, `Actuator heartbeat expired`, `Thread role: ...` và log `ledc` trong khoảng 2 giây trước/sau HOME. Các vị trí log ở `app_main.c` servo task, dòng 86, 96, 126 và callback Thread dòng 254; `ledc.c` của IDF tự log lỗi fade/timer.

## Truy vết trạng thái

- `s_state.stopped` khởi tạo `true` ở `app_main.c:31` và chỉ được ghi `true` bởi `probe_apply(PROBE_STOP)` ở `probe_state.c:18`; `stop_count` chỉ tăng ngay tại đó. Servo task không đọc cờ này. `/status.stopped` thực tế được tính từ `!control.pwm_active` ở `app_main.c:196`, nên `stopped:true` trước ARM hoặc trước HOME không chứng minh STOP đang chặn ARM.
- `/hb` cập nhật `s_state.last_hb_ms/has_hb` và `s_control.hb_seen/hb_ms` ở `app_main.c:150-155`. `ARM` chỉ được nhận khi `attached` và heartbeat của `s_control` còn trong 1500 ms; nó đặt `armed=true` và không phát PWM (`integrated_control.c:82-85`).
- `HOME` cần attached, heartbeat còn hạn và `armed`; nó đặt đích 1500/1500 và `home_requested=true`, trả 2.04 trước khi phần cứng khởi động (`integrated_control.c:87-94`, `app_main.c:60-71`). Servo task mới gọi LEDC và `control_pwm_started()` đặt `pwm_active=true` (`app_main.c:94-112`, `integrated_control.c:117-124`).
- `control_stop()` xóa `armed`, `home_requested`, `pwm_active`, `hb_seen` ở `integrated_control.c:13-25`. Nó được gọi khi boot, `/stop`, Thread detach, watchdog quá 1500 ms, hoặc LEDC start/update lỗi. `servo_output_stop()` đưa GPIO4/5 LOW và relay GPIO10 LOW. STOP/timeout/detach yêu cầu heartbeat mới và ARM lại.
- LEDC timer/channel lỗi và duty update lỗi đều trả `esp_err_t` lên servo task. Servo task log lỗi và disarm; STOP trong lúc LEDC đang cập nhật được kiểm lại sau thao tác. Bản vá thêm mã lỗi vào log update để dễ phân biệt với lỗi khởi tạo.

## Bản vá

- `firmware/node-b-integrated/main/servo_output.c:55-65`: thay API cần fade service bằng `ledc_set_duty()` rồi `ledc_update_duty()` cho mỗi kênh. Chỉ servo task ghi LEDC; mỗi lỗi vẫn gọi `servo_output_stop()`.
- `firmware/node-b-integrated/main/app_main.c:124-128`: log mã lỗi khi duty update thất bại.
- `firmware/node-b-integrated/CMakeLists.txt:3`: version `b-integrated-0.2.1`.
- `firmware/node-b-integrated/test/test_integrated_control.c:45-71` và `test/test_servo_output.c`: regression STOP → heartbeat mới → ARM giữ qua năm chu kỳ 20 ms → HOME → PWM 1500/1500; STOP hoặc hết hạn 1500 ms hủy PWM và HOME cần ARM lại. Test fake LEDC cũng kiểm lỗi cấu hình kênh/duty update đưa xung về LOW.

Giữ nguyên Thread Auto-Start, DHT11, CoAP contract, relay OFF, laser disabled, kiểm tra attached, watchdog 1500 ms, dải thử 1300–1700 µs và bước 20 µs/20 ms. Không sửa Node A/RCP/NVS.

## Kiểm chứng

| Kiểm tra | Trước vá | Sau vá |
| --- | --- | --- |
| Host `test_servo_output` với fake LEDC v5.4.4 (fade service chưa cài) | Compile PASS; FAIL ở `servo_output_start_home() == ESP_OK` | Compile/run PASS; PWM 1500/1500, lỗi LEDC đưa LOW |
| Host `test_integrated_control` (STOP, ARM qua nhiều chu kỳ, HOME, timeout, detach) | PASS; chứng minh STOP cũ không trực tiếp hủy ARM | PASS |
| Host probe, DHT11, Thread Auto-Start | Không chạy lại trước vá; bản 0.2.0 có kết quả PASS cũ | Cả 3 compile/run PASS |
| ESP-IDF v5.4.4 build `esp32h2` | Binary 0.2.0 có sẵn | PASS; `esptool image_info` checksum và validation hash valid |

Lệnh build: `idf.py --no-ccache -B ../../build-output/node-b-integrated-cp75 build` từ `firmware/node-b-integrated`. Build log: `reports/nt532_cp75_build_2026-11-12.log`. App 771856 bytes (`0xbc710`), trong partition 1 MiB còn `0x438f0` bytes. Image `b-integrated-0.2.1` tại `build-output/node-b-integrated-cp75/nt532_node_b_integrated.bin`, SHA-256:

`726879A7BBFA6D4DBA5B2A20C526F8098C8748D787013C630B4ED86EE5074E3F`

Binary 0.2.0 trong `firmware/node-b-integrated/build` không được ghi đè (timestamp 2026-11-11 21:58:38), nhưng hash hiện tại là `01469BDCD845FF81048D931584DF664F682D56E431E479A1BA6F5A4B7714E6A9`, khác hash `FF3F...` của báo cáo 2026-11-11. Vì không có bản dump sau lần flash 0.2.0, không xác nhận file 0.2.0 hiện tại đúng từng byte với image đã flash. Partition table mới, bản 0.2.0 và bản Node B Auto-Start trùng SHA-256 `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820`.

## Flash app-only và rollback (chỉ hướng dẫn; chưa thực hiện)

Sau khi xác minh PnP/MAC đúng Node B và cổng COM hiện tại, trong môi trường ESP-IDF v5.4.4, từ `firmware/node-b-integrated` chạy:

```powershell
idf.py --no-ccache -B ../../build-output/node-b-integrated-cp75 -p COM_PORT app-flash
```

`app-flash_args` của build mới chỉ ghi app ở offset `0x10000`; không ghi bootloader, partition table hay NVS. Không dùng `flash` hoặc `erase-flash`.

Rollback an toàn về Node B Auto-Start đã được nghiệm thu trên phần cứng: từ `firmware/node-b-thread-autostart`, sau khi xác minh cổng Node B, chạy `idf.py --no-ccache -p COM_PORT app-flash`. Binary Auto-Start hiện có SHA-256 `225B010387ACFDE5930E5A952F5A68D8C368889A027E1D22EC07BA986E13548E`; partition table cùng hash như trên. Nếu cần quay về chính xác image `b-integrated-0.2.0` đang chạy, cần trước hết đối chiếu bản binary đã flash với bản lưu tin cậy; hash báo cáo cũ và file hiện tại không khớp.

Không flash, không erase NVS, không thay đổi Node A/RCP và không commit/push trong CP7.5.
