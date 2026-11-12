# NT532 CP7 Extended — Node B integrated firmware (2026-11-11)

## Kết quả

**PASS phần chuẩn bị, chưa flash.** Tạo `firmware/node-b-integrated` từ bản DHT11 + Auto-Start đã build PASS. Firmware `b-integrated-0.2.0` cho `esp32h2` giữ Thread dataset trong NVS, Serial `TLV:`/`START`, DHT11 GPIO11 và bốn endpoint probe; bổ sung Pan/Tilt có khóa an toàn. Laser **vô hiệu hóa** vì chưa xác minh chân/mạch, relay active-HIGH GPIO10 luôn LOW, bơm không được điều khiển. Không kết nối COM18, COM19, COM5; không thao tác OTBR hoặc nguồn tải.

| Checkpoint | Kết quả | Bằng chứng / giới hạn |
| --- | --- | --- |
| ESP-IDF / target | PASS | `D:\esp\v5.4.4\esp-idf`, v5.4.4; `esp32h2`; Python `D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe` 3.11.15 |
| Probe endpoint regression | PASS host | `test_probe_state.c`, compile/run 0/0; chưa gọi endpoint trên firmware mới qua radio |
| Auto-Start cold boot / thiếu dataset | PASS host | `test_thread_start.c`, compile/run 0/0; bản Auto-Start B gốc đã PASS trên phần cứng theo nghiệm thu trước, bản tích hợp chưa flash |
| DHT11 có/không có dữ liệu | PASS host | `test_dht11.c`, compile/run 0/0; checksum, timeout, JSON `null`; chưa đấu cảm biến thật |
| Pan/Tilt HOME, dải, dữ liệu sai | PASS host | `test_integrated_control.c`, compile/run 0/0; chưa đo xung PWM hoặc chuyển động thật |
| Laser OFF, STOP, timeout | PASS logic an toàn | `/laser` không gán GPIO và luôn từ chối POST; STOP/timeout không thể làm laser bật; chưa có mạch để thử tải |
| Heartbeat mất / Thread detach / reboot | PASS host | Control disarm và PWM yêu cầu OFF; thời gian dừng thực tế chưa đo trên board |
| GPIO conflict | PASS source audit | Firmware dùng GPIO4 Pan, 5 Tilt, 10 relay LOW, 11 DHT11; không khởi tạo laser, pump, MQ-2 hay SHT31 |
| Build / image | PASS | `idf.py --no-ccache set-target esp32h2` và `build` exit 0; `esptool.py v4.13.0 image_info` exit 0, checksum/validation hash valid |

Binary: `firmware/node-b-integrated/build/nt532_node_b_integrated.bin`, **773600 bytes** (`0xbcde0`), SHA-256 **`FF3F1C2F429191A74852C7C6FDF489A675819129780E1C82BF0477A3D8AC6A79`**. App partition 1 MiB, còn `0x43220` bytes (26%). Build log `reports/nt532_node_b_integrated_build_2026-11-11.log`; host test log `reports/nt532_node_b_integrated_tests_2026-11-11.log`. Các log và `build/*.bin` bị `.gitignore` loại khỏi commit; không commit/push.

Lần build đầu dừng vì `app_main.c` so sánh hai enum khác kiểu (`PROBE_CONTENT` với `otCoapCode`) dưới `-Werror=enum-compare`. Đã đổi sang `OT_COAP_CODE_CONTENT`; các build incremental sau sửa đều exit 0. Lượt review cuối bổ sung ngắt LEDC **và** trả GPIO4/5 về output LOW khi STOP hoặc khởi tạo PWM lỗi, rồi build lại PASS. Không sửa ESP-IDF, Python hay toolchain.

## Audit phần cứng và nguồn

Nguồn trong bảng: [hiệu chuẩn xung](../calibration/pantilt_calibration.json), [quan sát servo](../calibration/servo_measurements.csv), [đề xuất hai vòi cũ](../docs/thay-doi-hai-voi.md), [roadmap](../docs/NT532_CODEX_PHASE_A_Z.md), tài liệu firmware DHT11 và thông số người vận hành cung cấp trong checkpoint này. Tài liệu cũ là **đề xuất**, không thay cho sơ đồ dây as-built.

| Cụm / thiết bị | GPIO | Nguồn và mức logic | Mức xác minh / xung đột |
| --- | --- | --- | --- |
| A = s1/tag 21, ESP32-H2 Super Mini | — | USB/board H2 3.3 V logic | Ánh xạ A theo tài liệu hiệu chuẩn; firmware A không đổi trong checkpoint này |
| A Pan/Tilt | GPIO4 / GPIO5 | Servo dùng nguồn 5 V riêng, GND chung H2; PWM tín hiệu 3.3 V cần kiểm servo nhận được | GPIO và 50 Hz từ báo cáo test; HOME 1500/1500 µs; cần kiểm lại dây A thực tế |
| A MQ-2, SHT31 | MQ-2 GPIO1 ADC qua cầu phân áp; SHT31 SDA/SCL GPIO10/11 | MQ-2 heater 5 V, ADC phải ≤3.3 V; SHT31 theo điện áp module | **Chỉ là thiết kế cũ**, chưa xác minh lắp thực; không đưa vào firmware B |
| A pump, laser | GPIO12 MOSFET pump và GPIO14 MOSFET laser được **đề xuất** | Pump nguồn 12 V riêng; laser qua mạch đóng/ngắt, không cấp từ GPIO | Chưa có sơ đồ as-built; không được xem GPIO14 là chân laser thật |
| A relay | Chưa rõ | Chưa rõ | Không suy từ B sang A |
| B = s2/tag 22, ESP32-H2 Super Mini | — | USB/board H2 3.3 V logic | COM18/MAC trong báo cáo backup là dấu nhận dạng lịch sử; phải kiểm lại trước flash |
| B DHT11 | DATA GPIO11 | VCC 3V3, GND chung, DATA kéo lên **3V3** bằng 4.7 kΩ | Đã xác nhận chân trong yêu cầu, driver đã build/test; chưa kiểm dây/sensor thật. Xung đột với SDA GPIO11 của thiết kế SHT31 cũ, nên SHT31 không khởi tạo |
| B Pan/Tilt | GPIO4 / GPIO5 | Servo nguồn 5 V riêng, đủ dòng (tài liệu đề xuất 2–3 A cho cụm), GND chung; tín hiệu H2 3.3 V | GPIO xác nhận trong yêu cầu; GPIO4 từng dùng laser, **phải tháo dây laser cũ khỏi GPIO4** trước khi cấp nguồn servo |
| B relay active-HIGH | GPIO10 | Firmware giữ LOW; nguồn cuộn relay/logic input và ngưỡng 3.3 V của module cần đo | GPIO/polarity xác nhận trong yêu cầu; xung đột với SDA GPIO10 của SHT31 cũ. Cần pull-down phần cứng để OFF cả trong reset |
| B laser | **Chưa xác định** | Chỉ được dùng mạch đóng/ngắt phù hợp, nguồn riêng theo module; GPIO không cấp nguồn trực tiếp | Tài liệu cũ đề xuất GPIO14 nhưng chưa có bằng chứng as-built. Firmware không cấu hình chân laser; `/laser` POST luôn 4.03 |
| B pump | GPIO12 MOSFET là đề xuất cũ | Nguồn 12 V riêng, GND chung theo thiết kế cũ | Không khởi tạo/điều khiển; giữ nguồn ngắt |

Phạm vi hiệu chuẩn xung **cả A/B**: Pan 500–2500 µs, Tilt 600–2500 µs, HOME 1500/1500 µs. Đây là biên xung đã báo cáo, **không phải** giới hạn góc hoặc vùng không va chạm. Quan sát góc trong CSV chỉ là ước lượng bằng mắt; hướng/offset cơ khí từng cụm chưa đo đủ. Firmware B giới hạn thử đầu ở **1300–1700 µs** cho cả hai trục, nằm trong phạm vi đã báo cáo. Servo tiến tối đa 20 µs mỗi 20 ms; lệnh đầu `HOME` cấp ngay xung 1500 µs nên vẫn có thể làm trục ở vị trí không biết trước chuyển động đột ngột. Người vận hành phải kiểm clearance/cơ khí và nguồn trước khi thử.

Sơ đồ nguồn kiểm tra trên giấy: USB cấp H2; DHT11 dùng 3V3/GND H2; servo lấy 5 V **nguồn riêng**, nối chung GND với H2, chỉ dây tín hiệu vào GPIO4/5; relay cần nguồn theo module và pull-down input về GND; pump 12 V và laser vẫn **ngắt nguồn**. Không cấp servo/laser từ GPIO. Chưa có sơ đồ đấu dây as-built, ảnh board hoặc phép đo điện áp/độ nhạy input relay; các mục này là blocker trước kích hoạt tải.

## Phần code được chuyển và phần mới

DHT11 (`dht11_logic.*`, `dht11_sensor.*`), `probe_state.*`, `thread_start.*`, ba test cũ và `sdkconfig.defaults` trong project mới có SHA-256 **giống hệt** `node-b-thread-dht11-autostart`. Auto-Start vẫn kiểm active dataset có sẵn rồi bật IPv6/Thread; khi thiếu dataset vẫn chờ `TLV:`/`START`, không tạo mạng. NVS không bị xóa trong code. Driver DHT11 vẫn chỉ dùng GPIO11 và task đọc cách nhau ít nhất 2 s, checksum/timeout; CoAP lấy snapshot không chờ sensor.

Các file mới: `main/integrated_control.c/.h` (parse/validate, ARM/HOME, ramp, watchdog), `main/servo_output.c/.h` (GPIO LOW, LEDC 50 Hz low-speed 14 bit), `test/test_integrated_control.c`. Chỉ `main/app_main.c` và hai CMakeLists thay đổi so với bản nền để thêm task/CoAP/driver và version. `README.md` mới mô tả hợp đồng và checklist. LEDC API được kiểm với header/source ESP-IDF v5.4.4 tại `D:\esp\v5.4.4\esp-idf\components\esp_driver_ledc`; [tài liệu Espressif LEDC ESP32-H2](https://docs.espressif.com/projects/esp-idf/en/v5.1/esp32h2/api-reference/peripherals/ledc.html) ghi H2 chỉ có low-speed mode và `ledc_set_duty_and_update()` an toàn qua task. [API Thread v5.4](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/network/esp_openthread.html) xác nhận NVS partition và khóa OpenThread. Không có dependency ngoài ESP-IDF.

Review cuối bổ sung test cho trường hợp STOP xảy ra khi đang khởi tạo LEDC và yêu cầu heartbeat mới sau khi Thread rời mạng rồi nối lại; `integrated_control_final` compile/run 0/0. Source firmware không đổi sau build cuối, nên không build lại chỉ vì thay test host/báo cáo.

## CoAP mới và giữ nguyên

| Endpoint | Hành vi |
| --- | --- |
| `GET /info` | `n=B`, `fw=b-integrated-0.2.0`, chế độ integrated |
| `GET /status` | Thread role, heartbeat, stop count và các trường DHT11 `sensor_ok`, `temperature_c`, `humidity_pct`, `sample_age_ms`, `last_attempt_age_ms`, `sensor_error`; thêm `servo_armed`, `servo_pwm`, `relay_on:false`, `laser_on:false` |
| `POST /hb` | Giữ phản hồi 2.04, làm mới heartbeat 1500 ms |
| `POST /stop` | Giữ phản hồi 2.04, disarm/stop servo PWM; relay/laser vẫn OFF |
| `GET /pantilt` | JSON trạng thái ARM/PWM, xung hiện tại/đích và giới hạn thử |
| `POST /pantilt` | Payload ASCII `ARM`, `HOME` hoặc `pan_us,tilt_us`; cần Thread attached và heartbeat mới. ARM không phát PWM; HOME đầu tiên phát 1500/1500; lệnh số chỉ nhận sau HOME, trong 1300–1700 µs. Dữ liệu sai 4.00; thiếu điều kiện 4.03; quá 16 byte 4.13 |
| `GET /laser` | `available:false`, `on:false`, `hardware_unverified` |
| `POST /laser` | Luôn 4.03, không có GPIO laser trong image |

Đầu vào từ CoAP được chặn độ dài và parse nghiêm ngặt trước khi đổi target; không in payload. Servo task riêng cập nhật LEDC, không đọc DHT11 hoặc chờ mạng trong handler CoAP. STOP, heartbeat timeout và role rời khỏi CHILD/ROUTER/LEADER disarm; PWM dừng khi task được đánh thức (chu kỳ kiểm tối đa 20 ms cộng lịch FreeRTOS, **chưa đo độ trễ thực**). Laser không thể bật nên thử STOP/timeout đối với laser chỉ chứng minh logic disabled, chưa kiểm mạch thật. Không có xác thực ứng dụng riêng cho lệnh servo: bất kỳ thiết bị có quyền vào Thread network có thể gửi `ARM`/`HOME`; chỉ dùng bản này để thử nghiệm với tải được giám sát.

## Flash args, NVS và rollback

Partition table bản tích hợp và bản B Auto-Start đã PASS trùng SHA-256 `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820` (3072 bytes). Parser ESP-IDF: NVS `0x9000`, 24 KiB; phy `0xf000`, 4 KiB; factory app `0x10000`, 1 MiB. `build/app-flash_args` chỉ ghi `0x10000 nt532_node_b_integrated.bin` với `dio`, 48 MHz, flash 4 MB; khác bản Auto-Start chỉ ở tên app. Lệnh full `flash` còn ghi bootloader `0x0` và partition table `0x8000`, nên **chỉ dùng `app-flash` sau khi được phép**. Theo mã `components/esptool_py/CMakeLists.txt` của IDF v5.4.4, app-flash dùng app partition offset và không nhắm NVS. CP này **không ghi flash**.

Backup B hiện có ở `backups/serial-2026-11-11/B_COM18_full_flash.bin`, SHA-256 checkpoint trước `4B181EA6A619105C223A2650260161A05B5AC36FC188877ED6F2406A2EB3F36A`; không thay đổi backup. Sau khi xác minh lại đúng board/MAC/cổng COM và được phép flash, từ thư mục `firmware/node-b-integrated` chạy `idf.py --no-ccache -p COM18 app-flash` (thay COM18 nếu đã đổi). **Rollback**: từ `firmware/node-b-thread-autostart` đã PASS thực tế, chạy cùng lệnh `idf.py --no-ccache -p COM18 app-flash` sau xác nhận riêng. Cả hai là app-only, không erase NVS; không dùng full-flash backup để rollback thông thường.

## Checklist nghiệm thu tích hợp sau flash riêng

1. Xác minh PnP/MAC đúng Node B và cổng hiện tại; xác nhận dây GPIO4/5 không còn nối laser cũ, GPIO10 relay có pull-down và nguồn tải ngắt. Đo mức 3.3 V/5 V, chung GND; không cho bơm/laser nguồn.
2. Flash app-only sau phê duyệt, cold boot **không gửi START/TLV**; xác nhận B tự vào Thread. Không có dataset thì xác nhận chờ provisioning, không tạo mạng.
3. Chưa cắm DHT11/servo 5 V: `GET /info`, `/status`, `/pantilt`, `/laser`; kỳ vọng DHT11 `sensor_ok:false`, nhiệt/ẩm `null`, servo PWM OFF, relay/laser OFF. Kiểm GPIO10 LOW bằng đồng hồ. Gửi lệnh sai và thiếu heartbeat; xác nhận 4.00/4.03.
4. Tắt nguồn, đấu DHT11 đúng 3V3/GND/DATA GPIO11 với pull-up 4.7 kΩ lên 3V3. Cold boot; kiểm `sensor_ok:true` với giá trị hợp lý và tuổi mẫu thay đổi theo nhịp ít nhất 2 s. Thử tháo cảm biến khi board tắt nguồn rồi boot lại để kiểm lỗi.
5. Chỉ sau kiểm cơ khí và có người ngắt 5 V: đấu Pan GPIO4, Tilt GPIO5, cấp servo từ nguồn 5 V riêng chung GND. Gửi `/hb` mỗi 500 ms, `ARM`, `HOME`, rồi đích nhỏ như `1520,1500`; quan sát xung/movement và GET `/pantilt`. Thử STOP, ngừng heartbeat và mất Thread; đo thời gian PWM về idle LOW. Không dùng đầu dải hiệu chuẩn trong image này.
6. `POST /laser` phải luôn 4.03 cả khi heartbeat/ARM hợp lệ. Không nối laser thật hoặc bơm trong checkpoint này. Kiểm A và RCP vẫn vận hành độc lập.

## Blocker và Node A

- **Laser B/A:** chưa có chân/mạch đóng-ngắt as-built, điện áp/nguồn, pull-down và đường dây hiện tại. GPIO14 chỉ là đề xuất cũ; GPIO4 từng dùng laser nhưng nay dành Pan. Laser thật không thể nghiệm thu hay đưa vào cùng lần flash này cho đến khi có sơ đồ và kiểm điện. Có thể cần bản firmware tiếp theo sau khi xác minh; không tuyên bố mục tiêu một lần flash đã bao gồm laser.
- **Servo và relay:** chưa đo nguồn 5 V, GND chung, mức điều khiển 3.3 V, pull-down relay hoặc khoảng không va chạm cơ khí. Giới hạn 1300–1700 µs chỉ là mức thử bảo thủ; không chứng minh an toàn cơ khí.
- **Node A:** tài liệu dự kiến A cũng có Pan/Tilt GPIO4/5, laser, bơm và MQ-2/SHT31, nhưng bản lắp thật của sensor/relay/laser chưa được chứng thực. Khi cần bản A tương ứng, lấy `node-a-thread-autostart` đã PASS làm nền, thêm driver sensor **đúng với phần cứng A đã xác minh**, Pan/Tilt có giới hạn riêng và chốt OFF; giữ laser disabled tới khi rõ mạch. Không thay đổi firmware A trong checkpoint này.
- **Tích hợp Pi:** endpoint `POST /pantilt` dùng µs cho thử trực tiếp; orchestrator cũ dùng `/aim` theo góc và `/fire`. Chưa thể thay thế firmware chính của trạm hoặc đánh giá một flash cho toàn bộ quy trình camera/laser/phun khi hiệu chuẩn góc–xung và mạch laser chưa được xác nhận.
