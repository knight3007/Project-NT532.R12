# Bàn giao tích hợp Node A/B — NT532

Ngày: 2026-11-13. Tài liệu này ghi trạng thái firmware đã kiểm trên host và kết quả nghiệm thu phần cứng do người vận hành báo lại. Giá trị không có log hoặc phép đo đi kèm được ghi đúng mức bằng chứng, không suy thêm.

## A. Tóm tắt điều hành

NT532 hướng tới phát hiện, định vị và xử lý mục tiêu trên sa bàn bằng Raspberry Pi 5 và hai cụm ESP32-H2. Node A đọc MQ-2, Node B đọc DHT11; cả hai điều khiển Pan/Tilt và relay bật LED mô phỏng laser qua CoAP trên Thread. Bản đang bàn giao là A `a-integrated-0.1.0` và B `b-integrated-0.2.3`.

Người vận hành đã xác nhận chuỗi lệnh CoAP và chuyển động/relay trên A, cũng như dải servo đầy đủ và relay trên B. Hai binary build trước đó còn tồn tại và khớp SHA-256; host tests được chạy lại khi bàn giao. Chưa chốt ngưỡng cảnh báo MQ-2, phép đo điện áp AO/DO, bản đồ góc–xung theo hình học thật, hay tích hợp Vision → Localization → Orchestrator → node trên hai cụm đồng thời. Relay hiện **chỉ bật LED**; không có laser phát thực hoặc bơm được điều khiển trong hai firmware tích hợp này.

## B. Kiến trúc hệ thống

```text
Camera → Vision/Localization → Orchestrator trên Raspberry Pi 5
                                   │
                         OTBR native + CoAP
                                   │
                               Thread mesh
                           ┌───────┴───────┐
                      Node A ESP32-H2   Node B ESP32-H2
                      MQ-2 ADC          DHT11
                      Pan/Tilt          Pan/Tilt
                      Relay → LED       Relay → LED
```

Mỗi node tự bật Thread từ **active dataset của chính nó trong NVS** sau cold boot; thiếu dataset thì không tự tạo mạng. CoAP nhận heartbeat, ARM, HOME, target xung, relay và STOP. Servo cấp 5 V riêng, chung GND với H2; PWM 50 Hz, ramp tối đa 20 µs mỗi 20 ms. Heartbeat dự kiến khoảng 400–500 ms, timeout 1500 ms; STOP, timeout hoặc Thread detach tắt PWM/relay và xóa quyền ARM. Pi hiện có các mô-đun Vision, Localization và Orchestrator, nhưng giao thức Orchestrator `/aim` + `/fire` khác giao thức node thực `/pantilt` + `/laser`; chưa có adapter đã nghiệm thu. Xem [giao thức Pi](../pi/src/nt532/net/coap.py) và [Orchestrator](orchestrator.md). Không coi demo trên sa bàn ảo là nghiệm thu đường điều khiển phần cứng.

## C. Pinout và yêu cầu điện

### Node A

| Thiết bị | GPIO | Yêu cầu |
|---|---:|---|
| MQ-2 AO | 1 | ADC1_CH0. Module/heater dùng nguồn theo thông số module, không cấp qua GPIO. Đo mức AO **sau mạch chia áp** tại chân H2 trước khi đấu; ADC attenuation không chống quá áp. |
| MQ-2 DO | 12 | Firmware đang **disabled**; nếu đấu phải xác nhận mức điện áp, chuyển mức và polarity. Kế hoạch cũ từng dùng GPIO12 cho tải khác. |
| SG90 Pan | 4 | Tín hiệu PWM 50 Hz; nguồn servo 5 V riêng, GND chung. |
| SG90 Tilt | 5 | Như Pan. |
| Relay → LED | 10 | Ngõ điều khiển active-HIGH: HIGH ON, LOW OFF; relay chỉ mô phỏng laser. Xác nhận module nhận HIGH 3,3 V, có trạng thái OFF an toàn lúc reset. |

### Node B

| Thiết bị | GPIO | Yêu cầu |
|---|---:|---|
| DHT11 DATA | 11 | VCC 3V3, GND chung, pull-up 4,7 kΩ lên 3V3 theo [README B](../firmware/node-b-integrated/README.md). |
| SG90 Pan | 4 | PWM 50 Hz; nguồn servo 5 V riêng, GND chung. |
| SG90 Tilt | 5 | Như Pan. |
| Relay → LED | 10 | Active-HIGH; LOW là OFF. Không nối laser thật vào đường điều khiển này. |

Không suy pinout vật lý của Super Mini từ sơ đồ DevKitM-1. Không có biên bản đo điện áp AO/DO hoặc trace GPIO4/5/10 trong bằng chứng bàn giao hiện tại.

## D. Phiên bản, build và bản phục hồi

| Bản | Source | App binary cục bộ, không đưa lên Git | SHA-256 đã đọc lại |
|---|---|---|---|
| A `a-integrated-0.1.0` | [Node A integrated](../firmware/node-a-integrated/) | `build-output/node-a-integrated-0.1.0/nt532_node_a_integrated.bin` | `80F44A0DFF4D6F374CCAA9A7541E39468003346419FA47492614761B2DA378AE` |
| B `b-integrated-0.2.3` | [Node B integrated](../firmware/node-b-integrated/) | `build-output/node-b-integrated-0.2.3/nt532_node_b_integrated.bin` | `07A913214400722C5089FF2177D176DA6F8777778014F58BA18B3ED768ECFE51` |
| A Auto-Start `a-autostart-0.1.1` | [Node A Auto-Start](../firmware/node-a-thread-autostart/) | `firmware/node-a-thread-autostart/build/nt532_node_a_autostart.bin` | `958E8CE231414702B2BBB5E7C4CE225CAC29AA989B312B30385A9CF369CC6D37` |
| B rollback `b-integrated-0.2.2` | Lịch sử trước 0.2.3 | `build-output/node-b-integrated-0.2.2/nt532_node_b_integrated.bin` | `63A8B8AAAC029EC7A9E3A0FCBD27711D0516E31440340F939A642227B64CC13B` |

Target là ESP32-H2, project dùng ESP-IDF v5.4.4 theo cấu hình/build report. `project_description.json` của hai build xác nhận tên, target và version; `app-flash_args` đặt app ở `0x10000`. Báo cáo build gốc: [A](../reports/nt532_node_a_integrated_2026-11-13.md), [B 0.2.3](../reports/nt532_node_b_full_servo_range_2026-11-13.md), [B STOP/relay 0.2.2](../reports/nt532_node_b_stop_relay_2026-11-12.md). Hai báo cáo tích hợp được viết **trước** lần nghiệm thu phần cứng người dùng vừa xác nhận, nên trạng thái BLOCKED về flash/CoAP trong chúng là mốc lịch sử; mục G dưới đây ghi kết quả mới hơn. Lượt bàn giao này **không rebuild ESP-IDF**; hash được đối chiếu với binary có sẵn, không đưa build output vào commit.

## E. Hiệu chỉnh servo

| Tham số | Pan | Tilt |
|---|---:|---:|
| MIN | 500 µs | 600 µs |
| HOME | 1500 µs | 1500 µs |
| MAX | 2500 µs | 2500 µs |

Hai trục có biên độc lập; parser từ chối target ngoài biên, không clamp. Đây là **dải xung đã chạy**, không phải phép đo góc, hệ số °/µs, giới hạn tránh va chạm hay tọa độ ngắm thế giới. Không lấy 1500 µs làm góc 0° nếu chưa đo mốc. Trước khi chuyển target Vision thành xung, dùng số đo góc, chiều quay, offset và hình học đúng cụm A/B; không thay bằng quan hệ tuyến tính đoán trước. Xem [hướng dẫn hiệu chuẩn](../calibration/README.md) và [cấu hình góc/offset hiện tại của Pi](../config/site.yaml). Hai file local `calibration/pantilt_calibration.json` và `calibration/servo_measurements.csv` đang **chưa được theo dõi trên nhánh này**; chủ sở hữu cần đồng bộ/kiểm nguồn riêng trước khi dùng chúng làm dữ liệu cho adapter. Tài liệu này không sửa chúng.

## F. Lệnh CoAP trên Raspberry Pi

Trên Pi cần `coap-client-notls`; script nghiệm thu A còn cần `jq`. Xác định mesh-local EID của **từng** node từ log/OTBR, không dùng EID của node kia. Không điền Thread dataset hoặc Network Key vào tài liệu/lệnh.

```bash
NODE_A_EID='DIA_CHI_IPV6_CUA_A'
NODE_B_EID='DIA_CHI_IPV6_CUA_B'
NODE_EID="$NODE_A_EID"       # đổi sang "$NODE_B_EID" khi kiểm B
ping -6 -c 3 "$NODE_EID"
coap-client-notls -m get "coap://[$NODE_EID]/info"
coap-client-notls -m get "coap://[$NODE_EID]/status"
coap-client-notls -m get "coap://[$NODE_EID]/pantilt"
coap-client-notls -m get "coap://[$NODE_EID]/mq2"  # chỉ A; B đọc DHT11 qua /status
```

Khi đã kiểm vùng chuyển động và chỉ nối relay LED, giữ heartbeat chạy nền trong lúc ARM/HOME/điều khiển; luôn OFF và STOP lúc kết thúc:

```bash
( while :; do coap-client-notls -m post -e '' "coap://[$NODE_EID]/hb" >/dev/null; sleep 0.4; done ) &
HB_PID=$!
coap-client-notls -m post -e ARM "coap://[$NODE_EID]/pantilt"
coap-client-notls -m post -e HOME "coap://[$NODE_EID]/pantilt"
coap-client-notls -m post -e '1500,1500' "coap://[$NODE_EID]/pantilt"
coap-client-notls -m get "coap://[$NODE_EID]/pantilt"
coap-client-notls -m post -e ON "coap://[$NODE_EID]/laser"
coap-client-notls -m get "coap://[$NODE_EID]/laser"
coap-client-notls -m post -e OFF "coap://[$NODE_EID]/laser"
coap-client-notls -m post -e '' "coap://[$NODE_EID]/stop"
kill "$HB_PID"; wait "$HB_PID" 2>/dev/null || true
```

Để thử watchdog **riêng**, giữ heartbeat, ARM, HOME, bật LED như trên; dừng heartbeat bằng `kill "$HB_PID"; wait "$HB_PID" 2>/dev/null || true`, đợi `sleep 2`, rồi `GET /status` và `GET /laser`: kỳ vọng `stopped:true`, `servo_pwm:false`, `relay_on:false`, `hb_fresh:false`. Gửi `OFF`/`STOP` cuối lượt kể cả khi lệnh trước lỗi. Script [nghiệm thu A](../firmware/node-a-integrated/test/node_a_coap_acceptance.sh) giữ heartbeat và cleanup OFF/STOP; yêu cầu EID, `--allow-outputs`, cùng `--allow-extremes` hoặc `--watchdog-only`. Nó chỉ kiểm trạng thái CoAP, không thay thế đo điện/kiểm vùng cơ khí.

## G. Kết quả nghiệm thu và mức bằng chứng

### Node A

| Hạng mục | Kết quả | Bằng chứng và giới hạn |
|---|---|---|
| Source/build | **PASS** | Source `a-integrated-0.1.0` hiện diện; build trước đó thành công, app hash khớp; 7/7 nhóm host test chạy lại: MQ-2 logic/ADC, response A, control, servo, probe, Thread. |
| CoAP/Thread | **PASS do người dùng báo** | `/info` nhận A và đúng version; role `router`. Không có log nguyên vẹn được lưu trong repo cho lượt này. |
| Pan/Tilt | **PASS do người dùng báo** | HOME 1500/1500; Pan 500/1500 → 2500/1500 → 1500/1500; Tilt 1500/600 → 1500/2500 → 1500/1500. Người dùng xác nhận hoạt động tốt. Không có trace PWM hoặc phép đo góc. |
| Relay/STOP | **PASS do người dùng báo** | `/laser` ON `on:true`, OFF `on:false`; sau STOP `stopped:true`, `servo_armed:false`, `servo_pwm:false`, `relay_on:false`, `laser_on:false`. Không suy ra điện áp GPIO từ JSON. |
| MQ-2 | **PASS một phần / BLOCKED hiệu chuẩn** | `mq2_sensor_ok:true` được quan sát; terminal output bị cắt mất giá trị ADC. Chưa có ngưỡng alarm, DO disabled; `alarm:null` là đúng. Chưa có số đo AO/DO hay ppm hợp lệ. |
| Điện và fault injection trên thiết bị | **BLOCKED** | Chưa có bằng chứng đo AO/DO, GPIO LOW/trace STOP, watchdog timeout hoặc detach trên cụm A trong lượt bàn giao. Host test đã kiểm logic tương ứng. |

### Node B

| Hạng mục | Kết quả | Bằng chứng và giới hạn |
|---|---|---|
| Source/build | **PASS** | Source `b-integrated-0.2.3` hiện diện; build trước đó thành công, app hash khớp; 5/5 nhóm host test chạy lại: probe, DHT11, Thread, control, servo/relay. |
| CoAP và Pan/Tilt | **PASS do người dùng báo** | Người dùng xác nhận đã thử trên cụm thật dải Pan 500–2500, Tilt 600–2500, HOME và relay. Bản 0.2.2 đã xử lý hiện tượng Pan di chuyển sau STOP; người dùng báo hoạt động đạt sau đó. Chưa có bảng log CoAP chi tiết cho B 0.2.3 trong lần bàn giao này. |
| DHT11 | **PASS host / BLOCKED số đo hiện trường** | Driver/test có trong source; không có giá trị nhiệt/ẩm thực đo được cung cấp cho lần bàn giao. |
| Điện và fault injection trên thiết bị | **BLOCKED** | Không có oscilloscope trace hoặc số đo GPIO10/4/5; không khẳng định STOP liên tục LOW bằng phép đo điện. Watchdog/detach đã có host test, chưa có biên bản fault injection phần cứng kèm theo. |

Không có kết quả **FAIL** được người vận hành báo cho chuỗi nghiệm thu A/B này; các mục thiếu bằng chứng được giữ là BLOCKED.

## H. Vấn đề còn mở

1. MQ-2 cần đo baseline, đáp ứng, thời gian warm-up thực và hysteresis trên phần cứng trước khi đặt alarm. `alarm:null` phải được giữ cho tới khi có tiêu chí; không suy ppm từ ADC raw.
2. Kiểm tra chính xác ADC so với điện áp **tại GPIO1**, mạch bảo vệ/chia áp AO và mức DO nếu dự định đấu. Chưa được khẳng định điện áp an toàn từ kết quả CoAP.
3. GPIO12 DO đang tắt; cần xác nhận đấu dây, mức logic và polarity, đồng thời tránh xung đột với thiết kế cũ.
4. Dải xung không cho góc/gain/hình học ngắm. Cần đo lại/đối chiếu hồ sơ đo hiện có theo cụm A/B và quy ước tọa độ trước khi chuyển Vision target thành lệnh servo.
5. Firmware node nhận `/pantilt` và `/laser`; Pi Orchestrator dùng `/aim` và `/fire` cùng schema lệnh khác. Cần adapter/chuyển đổi và chặn an toàn rõ ràng; `/fire` bơm/laser trong mô hình Pi **không có** tương đương tải thật ở firmware này.
6. Chưa hoàn thành thử nghiệm Vision → Localization → Orchestrator → hai node vật lý, và chưa có thử đồng thời hai node trên OTBR với xử lý lỗi mạng.

## I. Checkpoint tiếp theo

1. **CP-A/B — đóng băng mốc đã nghiệm thu:** lưu version, hash, source và log thử của từng board; không thay đổi NVS/dataset hoặc ghi đè bản rollback.
2. Đo và hiệu chuẩn MQ-2: AO/DO điện, ADC, warm-up, ngưỡng/hysteresis theo dữ liệu thực; chỉ bật DO khi xác nhận đấu nối.
3. Xác nhận telemetry sensor từ A/B đến Pi: hiện firmware hỗ trợ `GET /mq2` và `/status`, còn Pi thiết kế server `/t`, `/a`; chốt polling/adapter/push và schema trước khi dùng trong quyết định.
4. Thiết kế và kiểm adapter Pi ↔ node cho `/aim`/`/fire` sang `/pantilt`/`/laser`, giữ ARM/HOME/heartbeat/STOP và không ánh xạ `/fire pump` vào relay LED.
5. Dùng hồ sơ đo góc, offset, pose và chiều quay để nối Vision/Localization với Pan/Tilt; kiểm trên target không tải trước.
6. Thử A và B đồng thời trên cùng OTBR: phân biệt EID/Node ID, độ trễ, mất gói và trạng thái sensor.
7. Fault injection có kiểm soát: hết heartbeat, detach, STOP trong lúc PWM/relay ON; ghi log CoAP và trace điện khi có dụng cụ.
8. Demo end-to-end sau khi adapter, sensor calibration và các chặn an toàn đã nghiệm thu; không kích hoạt laser/bơm thật chỉ từ kết quả LED mô phỏng.

## J. Phục hồi và rollback

Trước mọi lần flash, xác nhận đúng board/cổng COM bằng nhận diện riêng, sao lưu dữ liệu cần thiết và kiểm SHA-256 app. Chỉ ghi app ở `0x10000` với ESP32-H2; **không** `erase_flash`, không ghi bootloader/partition/NVS, không dùng dataset của node kia. Các lệnh dưới là hướng dẫn, **không được chạy trong lượt bàn giao này**:

```powershell
$py = 'D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe'
# Thay COM_A / COM_B bằng cổng đã xác minh; chạy tại repo root.
& $py -m esptool --chip esp32h2 -p COM_A write_flash 0x10000 'build-output\node-a-integrated-0.1.0\nt532_node_a_integrated.bin'
& $py -m esptool --chip esp32h2 -p COM_B write_flash 0x10000 'build-output\node-b-integrated-0.2.3\nt532_node_b_integrated.bin'
# Rollback từng node bằng app CỦA CHÍNH NODE đó:
& $py -m esptool --chip esp32h2 -p COM_A write_flash 0x10000 'firmware\node-a-thread-autostart\build\nt532_node_a_autostart.bin'
& $py -m esptool --chip esp32h2 -p COM_B write_flash 0x10000 'build-output\node-b-integrated-0.2.2\nt532_node_b_integrated.bin'
```

Binary rollback chỉ là đường dẫn **local**; Git lưu source, test và hash trong tài liệu, không lưu build artifacts. Nếu clone mới không có binary thì cần build đúng source/version hoặc lấy artifact đã lưu nội bộ và xác minh hash trước khi flash. Sau rollback, kiểm `/info`, Thread Auto-Start, relay OFF và trạng thái NVS của đúng node.

## K. Tham chiếu repository

- [Firmware A](../firmware/node-a-integrated/), [README A](../firmware/node-a-integrated/README.md), [test A](../firmware/node-a-integrated/test/run_host_tests.ps1), [script CoAP A](../firmware/node-a-integrated/test/node_a_coap_acceptance.sh), [báo cáo kỹ thuật A](../reports/nt532_node_a_integrated_2026-11-13.md).
- [Firmware B](../firmware/node-b-integrated/), [README B](../firmware/node-b-integrated/README.md), [test B](../firmware/node-b-integrated/test/), [báo cáo dải xung B](../reports/nt532_node_b_full_servo_range_2026-11-13.md), [báo cáo STOP/relay B](../reports/nt532_node_b_stop_relay_2026-11-12.md).
- [Node A Auto-Start](../firmware/node-a-thread-autostart/), [báo cáo Thread Auto-Start](../reports/nt532_thread_autostart_2026-11-11.md), [hướng dẫn hiệu chuẩn](../calibration/README.md), [cấu hình Pi](../config/site.yaml), [giao thức Pi](../pi/src/nt532/net/protocol.py), [tài liệu Orchestrator](orchestrator.md).
