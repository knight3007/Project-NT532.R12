# NT532 Node B v0.2.2 — Servo STOP và relay mô phỏng laser

Ngày: 2026-11-12. Target ESP32-H2, ESP-IDF v5.4.4. Đây là bản build từ firmware `b-integrated-0.2.1` đang chạy trên Node B. **Chưa flash, chưa cấp lệnh kích servo/relay trên phần cứng, chưa xóa NVS, chưa commit/push.**

## Kết quả

| Hạng mục | Kết quả | Bằng chứng/giới hạn |
| --- | --- | --- |
| Phân tích source và sửa đường STOP | PASS ở mức source | Bỏ khoảng tắt output/bật pull-up của `gpio_reset_pin()`; host fake driver kiểm tra đường gọi, chưa đo điện GPIO4/5. |
| STOP, timeout 1500 ms, Thread detach, ARM lại | PASS host | Control và fake LEDC/GPIO test. |
| Relay LED qua GPIO10 active-HIGH, CoAP `/laser` | PASS host/build | GPIO driver calls và state test; handler CoAP đã compile nhưng chưa thử request trực tiếp trên bo mạch, chưa đấu/kích relay thật. |
| ESP-IDF v5.4.4 build ESP32-H2 | PASS | Build log và esptool `image_info` checksum/hash hợp lệ. |
| Pan không chuyển động sau STOP trên SG90 thật | BLOCKED | Cần oscilloscope/logic analyzer và thử nghiệm bo mạch sau quyết định flash riêng. |
| Mức LOW/HIGH GPIO10, module nhận HIGH 3.3 V, relay OFF lúc reset | BLOCKED | Cần đo và kiểm tra pull-down ngoài. |

## Giai đoạn A: nguyên nhân và đường tín hiệu STOP

`POST /stop` đi qua `probe_apply()` (tăng `stop_count`) rồi `control_stop()` (xóa ARM, HOME, PWM, relay và cờ heartbeat), áp relay LOW, đánh thức servo task. Task kiểm tra trạng thái mỗi tối đa một chu kỳ 20 ms theo lịch, gọi `servo_output_stop()` nếu PWM đã chạy. Watchdog cũng chạy trong task mỗi chu kỳ, kể cả khi relay ON mà PWM không chạy. Thread detach gọi `control_set_attached(false)`, xóa cùng các cờ, áp relay LOW và đánh thức task. LEDC timer 50 Hz, độ phân giải 14 bit; HOME viết duty 1500/1500 µs; mục tiêu được chặn 1300–1700 µs và dịch tối đa 20 µs/chu kỳ. Không có lệnh tự HOME khi STOP.

**Lỗi source đã xác nhận:** bản 0.2.1 gọi `ledc_stop(..., idle_level=0)` rồi `gpio_reset_pin(GPIO4/5)` trong `drive_low()`. Mã `gpio_reset_pin()` của IDF v5.4.4 đặt `GPIO_MODE_DISABLE`, bật pull-up trên chân output và tắt output trước khi code cũ gọi `gpio_set_direction(..., OUTPUT)`. Vì vậy lệnh STOP có một cửa sổ chân servo không còn được điều khiển LOW. [Tài liệu GPIO ESP32-H2 v5.4](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/gpio.html) mô tả cùng hành vi của reset pin. Mã `ledc_stop()` trong IDF đặt idle level rồi tắt tín hiệu LEDC; [tài liệu LEDC](https://docs.espressif.com/projects/esp-idf/en/v5.1/esp32h2/api-reference/peripherals/ledc.html) mô tả idle level. Không thấy đảo cực PWM hay lệnh duty ngoài task servo. Các chỗ ghi GPIO4/5 chỉ nằm trong `servo_output.c`.

Bản vá nạp sẵn latch LOW, cấu hình GPIO output với pull-up tắt và pull-down bật, rồi nối GPIO matrix về GPIO output khi bàn giao từ LEDC. `servo_output_stop()` ghi log `STOP output error: LEDC pan=... tilt=... GPIO pan=... tilt=...` **chỉ khi** driver báo lỗi. Lỗi tạo PWM đã được app log `Servo start failed`/`Servo update failed`, sau đó `control_stop()` và đưa output về LOW. Lỗi LEDC có thể giải thích trạng thái HOME bị hủy; ở lỗi Pan chuyển động sau STOP hiện tại, chưa có log Serial/JTAG hay trace chân nên chưa thể quy kết chuyển động vật lý cho riêng cửa sổ pull-up.

Các khả năng còn mở: SG90 phản ứng với mất PWM, xung ngắn trong bàn giao chân, nhiễu tín hiệu, sụt áp 5 V hoặc sai khác ground. Host fake không mô phỏng thời gian chuyển mạch hay phản ứng servo. Nếu GPIO4 được đo LOW liên tục mà Pan vẫn quay, cần kiểm tra nguồn/ground/tín hiệu và hành vi servo trước khi đổi thêm control logic.

## Giai đoạn B: relay GPIO10

`POST /laser` `ON` chỉ được nhận khi Thread attached, heartbeat còn trong 1500 ms, đã ARM và HOME đã kích PWM. `OFF` ghi GPIO10 LOW ngay trong handler, kể cả khi stale/disarmed/detached nếu request tới được thiết bị. STOP và Thread detach ghi LOW trong handler/callback; watchdog ghi LOW trong servo task ngay cả khi PWM không active. Boot gọi `servo_output_init_safe()` trước NVS/Thread và đặt GPIO10 LOW. Relay và trạng thái authorization được cập nhật dưới cùng control lock. GPIO driver báo lỗi thì control bị STOP và app thử ghi LOW, trả CoAP 5.00 cho request bị lỗi. `GET /laser` trả `available:true`, `mode:"relay_led_simulation"`, `on` theo lệnh GPIO10; `/status.relay_on` cũng là **trạng thái ra lệnh**, không phải phản hồi tiếp điểm/ánh sáng. `/status.laser_on` vẫn `false`: firmware không điều khiển laser thật.

GPIO10 chỉ là input logic của module relay active-HIGH, không cấp dòng trực tiếp cho cuộn relay. Cần pull-down ngoài để giữ OFF trong reset trước khi firmware chạy; cần xác nhận module thực sự nhận 3.3 V là HIGH. Chỉ dùng LED mô phỏng, không nối laser thật.

## File và dòng chính

| File | Dòng | Thay đổi |
| --- | --- | --- |
| `firmware/node-b-integrated/main/servo_output.c` | 27–74 | LOW và GPIO matrix khi STOP/boot; GPIO10 relay; log lỗi LEDC/GPIO STOP. |
| `firmware/node-b-integrated/main/servo_output.h` | 8 | API GPIO10 relay. |
| `firmware/node-b-integrated/main/integrated_control.c` | 13–55, 107–121 | STOP/watchdog xóa relay; `/laser` ON/OFF và authorization. |
| `firmware/node-b-integrated/main/app_main.c` | 56–105, 106–174, 188–258, 288–300, 379–405 | Đồng bộ relay với CoAP, STOP, timeout, Thread và status; boot LOW. |
| `firmware/node-b-integrated/CMakeLists.txt` | 3 | Version `b-integrated-0.2.2`. |
| `firmware/node-b-integrated/test/test_integrated_control.c` | 9–86 | Regression state, ARM qua nhiều chu kỳ, STOP/timeout/detach. |
| `firmware/node-b-integrated/test/test_servo_output.c` | 10–177 | Fake LEDC/GPIO: duty HOME, STOP LOW, không reset pin, relay HIGH/LOW và lỗi driver. |
| `firmware/node-b-integrated/test/fake_driver/` | nhiều | Fake GPIO/ROM matrix/log cho host test. |
| `firmware/node-b-integrated/README.md` | 1–95 | Hợp đồng CoAP, safety và acceptance. |

## Test trước/sau và build

| Bước test-first | Trước sửa | Sau sửa |
| --- | --- | --- |
| STOP không `gpio_reset_pin()` sau PWM | FAIL tại `reset_after_pwm == 0` | PASS; STOP gọi LEDC idle LOW rồi GPIO output LOW, không reset pin. |
| Relay chỉ ON khi attached + heartbeat + ARM + HOME | FAIL ở assertion đầu của `control_laser_request()` | PASS; malformed/rejected không có lệnh GPIO10 HIGH. |
| Driver GPIO10 theo lệnh hợp lệ | Link FAIL: `servo_output_set_relay` chưa tồn tại | PASS; ON/HIGH, OFF/LOW, STOP/timeout/detach/reboot LOW. |
| Host GCC `-std=c11 -Wall -Wextra -Werror` | — | PASS: probe, dht11, thread, control, servo_relay; [log](nt532_node_b_0.2.2_host_tests_2026-11-12.log). |
| ESP-IDF v5.4.4 `idf.py --no-ccache -B ../../build-output/node-b-integrated-0.2.2 build` | — | PASS; ESP32-H2 image 772368 bytes, 1 MiB app slot, [log](nt532_node_b_0.2.2_build_2026-11-12.log). |

Regression bao gồm ARM giữ ít nhất năm chu kỳ 20 ms trước HOME, HOME duty 1500/1500 µs, target hợp lệ, STOP hoặc timeout hủy PWM và đòi ARM lại. Có fault injection `pwm_active=false` trong lúc relay vẫn ON để bảo đảm watchdog tắt relay dù PWM inactive. Tình huống này không tạo được qua CoAP hợp lệ do `ON` yêu cầu HOME. Test cũng xác nhận lỗi cấu hình kênh LEDC/duty update dừng output. Không có test host nào chứng minh không có xung ngắn điện hoặc chuyển động Pan thật.

Binary mới: `build-output/node-b-integrated-0.2.2/nt532_node_b_integrated.bin`
SHA-256: `63A8B8AAAC029EC7A9E3A0FCBD27711D0516E31440340F939A642227B64CC13B`
`esptool image_info`: detected ESP32-H2, checksum và validation hash valid.
Binary 0.2.1 giữ nguyên: `build-output/node-b-integrated-cp75/nt532_node_b_integrated.bin`, SHA-256 `726879A7BBFA6D4DBA5B2A20C526F8098C8748D787013C630B4ED86EE5074E3F`. Partition table hai build có cùng SHA-256 `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820`.

## CoAP từ Raspberry Pi sau khi xác định EID của Node B

Gửi `/hb` mỗi 400–500 ms trong lúc test. `coap-client` dưới đây là ví dụ; `NODE_B_EID` phải là EID thực từ bo mạch.

```sh
NODE_B_EID='fd..:..'
coap-client -m post -e '' "coap://[$NODE_B_EID]/hb"
coap-client -m post -e ARM "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e HOME "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e '1550,1500' "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e '1500,1550' "coap://[$NODE_B_EID]/pantilt"
coap-client -m post -e ON "coap://[$NODE_B_EID]/laser"
coap-client -m get "coap://[$NODE_B_EID]/laser"
coap-client -m post -e OFF "coap://[$NODE_B_EID]/laser"
coap-client -m post -e '' "coap://[$NODE_B_EID]/stop"
coap-client -m get "coap://[$NODE_B_EID]/status"
```

Để xác nhận watchdog: sau một chuỗi hợp lệ, dừng sender `/hb`, chờ hơn 1500 ms rồi đọc `/status`, `/pantilt`, `/laser` và đo GPIO4/5/10. Phải thấy PWM và relay OFF, `armed:false`; HOME và ON tiếp theo bị từ chối cho đến heartbeat mới, ARM, HOME. `stop_count` chỉ tăng do POST `/stop`, không phải timeout. Nếu cần log chẩn đoán, ghi USB Serial/JTAG từ 2 giây trước tới 2 giây sau STOP/timeout: `Thread role`, `Actuator heartbeat expired`, `Servo start failed`, `Servo update failed`, `STOP output error` và log `ledc`.

## Acceptance phần cứng (chưa thực hiện)

1. Xác định đúng Node B/COM bằng MAC hoặc PnP. Chỉ nối LED mô phỏng với tiếp điểm relay, để laser thật/pump ngắt. GPIO4/5 chỉ nối tín hiệu servo, GPIO10 chỉ nối relay input; kiểm tra chung GND và nguồn servo 5 V riêng.
2. Trước flash mới, kiểm tra pull-down ngoài ở GPIO10 và đo trạng thái reset: LOW, relay OFF. Kiểm tra input relay nhận 3.3 V HIGH, không lấy nguồn cuộn relay từ ESP32-H2 GPIO.
3. Sau khi được phép flash app, **chưa cấp 5 V servo**, dùng oscilloscope/logic analyzer đo đồng thời GPIO4, GPIO5, GND và đường 5 V, mẫu đủ phân giải xung ngắn (gợi ý ≥10 MHz), ghi nhiều chu kỳ 20 ms trước/sau `/stop`. Với heartbeat + ARM + HOME + mục tiêu nhỏ, xác nhận 50 Hz, 1500 µs HOME; sau STOP tín hiệu liên tục LOW, không HIGH spike/khoảng floating/pull-up. Lặp với timeout và Thread detach. Nếu thấy xung lạ, giữ trạng thái BLOCKED và đối chiếu log STOP/LEDC.
4. Chỉ sau khi trace tín hiệu an toàn mới cấp 5 V servo, lặp cùng chuỗi và quan sát Pan. Nếu Pan còn quay dù GPIO4 LOW liên tục, đo 5 V tại servo, common GND, dây tín hiệu; xem xét điện trở kéo xuống/bộ đệm tín hiệu hoặc đặc tính SG90 sau mất PWM, không thêm chuyển động HOME tự động trong STOP.
5. Với relay LED, kiểm tra ON bị từ chối trước heartbeat/ARM/HOME; sau trình tự hợp lệ GPIO10 HIGH, relay và LED ON. OFF, STOP, timeout, detach và reboot phải đưa GPIO10 LOW, relay/LED OFF. So `/laser.on` và `/status.relay_on` với lệnh; xác nhận tiếp điểm bằng đo thực tế.

## App-only flash và rollback — hướng dẫn, **chưa chạy**

Từ `firmware/node-b-integrated` với môi trường IDF v5.4.4, sau khi quyết định kiểm thử phần cứng riêng và xác nhận `COM_PORT` là Node B:

```powershell
idf.py --no-ccache -B ../../build-output/node-b-integrated-0.2.2 -p COM_PORT app-flash
```

`app-flash_args` của build mới ghi duy nhất `0x10000 nt532_node_b_integrated.bin` với mode dio, 48m, 4MB; không ghi bootloader/partition/NVS. Nếu v0.2.2 không đạt acceptance, rollback app 0.2.1 bằng binary đã đối chiếu SHA-256 ở trên:

```powershell
python -m esptool --chip esp32h2 -p COM_PORT write_flash --flash_mode dio --flash_freq 48m --flash_size 4MB 0x10000 ../../build-output/node-b-integrated-cp75/nt532_node_b_integrated.bin
```

Đã đối chiếu cú pháp `esptool write_flash --help`, offset app của `app-flash_args`, hash partition table và hash binary cũ. **Chưa thực thi flash/rollback trên bo mạch**; xác nhận rollback vận hành và Thread Auto-Start vẫn cần thử sau khi có quyền thao tác phần cứng. Không dùng `idf.py flash`, `erase_flash`, hoặc erase NVS cho quy trình này.
