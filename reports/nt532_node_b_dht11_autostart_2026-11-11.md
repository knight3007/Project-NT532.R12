# NT532 CP7 — Tích hợp Auto-Start vào Node B DHT11 (2026-11-11)

## Kết luận

**PASS phần chuẩn bị:** firmware `b-dht11-auto-0.1.1` build thành công cho `esp32h2` bằng ESP-IDF v5.4.4; ba nhóm host test PASS. **Chưa flash, chưa thử firmware tích hợp trên board.** Bản Auto-Start gốc đã PASS trên Node B theo kết quả nghiệm thu trước đó; việc giữ đúng hành vi trong bản tích hợp được kiểm tra qua mã nguồn, test giả lập OpenThread và build, cần nghiệm thu cold boot trên phần cứng sau khi được phép flash.

| Hạng mục | Kết quả |
| --- | --- |
| Project mới | `firmware/node-b-thread-dht11-autostart` |
| Target / phiên bản | `esp32h2` / `b-dht11-auto-0.1.1` |
| ESP-IDF / Python | `D:\esp\v5.4.4\esp-idf` v5.4.4 / `D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe` 3.11.15 |
| `idf.py --version` | PASS, `ESP-IDF v5.4.4` |
| `set-target` / `build` | PASS / PASS, exit code 0 / 0 |
| Probe/CoAP regression | PASS, compile/run 0/0 |
| DHT11 unit test | PASS, compile/run 0/0 |
| Auto-Start + DHT11 integration | PASS, compile/run 0/0 |
| Binary | `firmware/node-b-thread-dht11-autostart/build/nt532_node_b_dht11_autostart.bin` |
| Binary size / SHA-256 | 755056 bytes (`0xb8570`) / `2B89F3B32E4D8640CF0C6DB5CEF4D96051EF9F3ACA04EE193B492D89A1CADA48` |
| Image validation | `esptool.py v4.13.0 image_info`: ESP32-H2, checksum và validation hash hợp lệ, exit 0 |

Log: `reports/nt532_node_b_dht11_autostart_tests_2026-11-11.log` và `reports/nt532_node_b_dht11_autostart_build_2026-11-11.log` (đều bị `.gitignore` loại khỏi commit).

## Thay đổi so với `node-b-thread-dht11`

| File trong project mới | Thay đổi |
| --- | --- |
| `CMakeLists.txt` | Đổi version và tên project để nhận dạng firmware mới. |
| `main/CMakeLists.txt` | Thêm `thread_start.c` vào component. |
| `main/app_main.c` | Include helper; dùng helper trong `start_thread()` và gọi `start_thread(instance)` khi boot, sau đăng ký CoAP resource và trước khi nhả khóa OpenThread. Đường `TLV:`/`START` vẫn gọi hàm này như trước. |
| `main/thread_start.c`, `.h` | Sao chép nguyên byte từ bản B Auto-Start đã PASS; SHA-256 của `thread_start.c` ở cả hai project là `0919075AED13BA94304D7E2C4C36069E5694670BA63D1C635BCC52F9778ADD74`. |
| `test/test_thread_start.c`, `test/fake_openthread/` | Chuyển test cold boot/reboot/START/lỗi IPv6 của Auto-Start; thêm kiểm tra tích hợp rằng auto-join với DHT11 chưa cắm trả `sensor_ok:false`, `temperature_c:null`, `humidity_pct:null`. |
| `README.md` | Cập nhật hướng dẫn build, đấu DHT11, app-only flash, nghiệm thu, rollback. |

`main/dht11_logic.c/.h`, `main/dht11_sensor.c/.h`, `main/probe_state.c/.h`, `test/test_dht11.c`, `test/test_probe_state.c`, `sdkconfig.defaults` trong project mới có SHA-256 **giống hệt** bản DHT11 gốc. Driver, task lấy mẫu GPIO11 (tối thiểu 2000 ms), checksum/timeout, snapshot CoAP và bốn endpoint `/info`, `/status`, `/hb`, `/stop` không bị thay đổi. Không thêm GPIO10, GPIO4/5 hoặc chức năng tải; `/stop` và heartbeat giữ nguyên. Không đưa dataset hay Network Key vào source/log. Hai project nguồn không bị chỉnh sửa.

Binary DHT11 gốc vẫn có SHA-256 `A8EEA3300A1E51F7AB196254E695493A73141B6535DAF76AA11EEECE9842AE44` như checkpoint trước.

## Hành vi Auto-Start và kiểm thử

- `esp_openthread_start()` dùng partition `nvs` như bản gốc. Khi có active dataset hợp lệ, helper gọi `otIp6SetEnabled(true)` rồi `otThreadSetEnabled(true)` khi đang giữ khóa OpenThread. Không gọi `otDatasetSetActiveTlvs` lúc boot.
- Khi chưa có active dataset, helper trả về mà không bật IPv6/Thread; Serial `TLV:` và `START` vẫn sẵn sàng, không tạo dataset mới.
- Test host giả lập đã kiểm tra boot với dataset, cold boot lại, không có dataset, `START` sau provisioning, `START` lặp lại và lỗi bật IPv6. Test DHT11 cũ kiểm tra checksum/timing/timeout, JSON `null` lúc chưa có mẫu hoặc lỗi cảm biến. Test tích hợp mới chạy hai logic trong cùng binary test.
- Giới hạn: host test không mô phỏng phần cứng NVS/radio hay dây DHT11. Phải nghiệm thu thực tế sau flash riêng.

## Partition và NVS

Partition table của bản mới và bản `node-b-thread-autostart` có cùng SHA-256: `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820` (3072 bytes). ESP-IDF parser xác nhận `nvs` ở `0x9000`, 24 KiB; `phy_init` ở `0xf000`, 4 KiB; `factory` app ở `0x10000`, 1 MiB. Binary mới `0xb8570` bytes, nằm trong app partition.

`build/app-flash_args` ở cả hai project có cùng chế độ `dio`, 48 MHz, flash 4 MB và **chỉ** một offset ứng dụng `0x10000`; chỉ khác tên `.bin`. `build/flash_args` có ba offset `0x0` bootloader, `0x8000` partition table, `0x10000` app, nên không dùng lệnh full `flash` cho lần nâng cấp này. Mã ESP-IDF v5.4.4 tại `components/esptool_py/CMakeLists.txt` tạo target `app-flash` từ riêng `${app_partition_offset}`. Vì vậy, **nếu sau này chạy đúng `app-flash`**, lệnh ghi chỉ nhắm phân vùng app và không ghi đè NVS. CP7 này chưa kết nối COM hoặc ghi flash.

Backup B còn nguyên: `backups/serial-2026-11-11/B_COM18_full_flash.bin`, 4194304 bytes, SHA-256 `4B181EA6A619105C223A2650260161A05B5AC36FC188877ED6F2406A2EB3F36A`. File `.bin` trong `build/` bị `.gitignore` loại khỏi commit; không commit/push.

## Nghiệm thu trên Node B sau khi cho phép flash

1. Xác minh lại qua Windows PnP rằng cổng đang chọn là **Node B**, đối chiếu MAC `48:31:b7:ff:fe:cd:51:d5` từ báo cáo backup. COM18 là ánh xạ lần trước, không coi là bằng chứng hiện tại. Giữ tải relay/servo/bơm/laser ngắt nguồn. Từ thư mục project mới, dùng ESP-IDF v5.4.4 đã export và chỉ chạy `idf.py --no-ccache -p COM18 app-flash` sau khi xác nhận đúng cổng; thay COM18 nếu cổng đã đổi. Không erase NVS.
2. **Chưa cắm DHT11:** cold boot B, không gửi `START` hoặc `TLV:`. Đợi B tự vào mạng hiện có. Trên Pi dùng EID của B để gọi `GET /info` (`n=B`, `fw=b-dht11-auto-0.1.1`), `GET /status` (`sensor_ok:false`, nhiệt/ẩm `null`), `POST /hb`, `POST /stop`. Không cấp nguồn cho tải.
3. **Có DHT11:** tắt nguồn B, đấu VCC 3V3, GND, DATA GPIO11 kéo lên 3V3 bằng 4.7 kΩ (kiểm tra chân trên board/module thực tế), rồi cold boot. Không gửi `START`. Kiểm tra B tự vào Thread, `/status` chuyển `sensor_ok:true`, nhiệt/ẩm hợp lý và `sample_age_ms` tăng rồi được làm mới với nhịp đọc tối thiểu 2 giây. Xác nhận Node A và RCP vẫn hoạt động.
4. Nếu B không có dataset, xác nhận không tự tạo mạng; dùng USB Serial/JTAG của B để provisioning trực tiếp bằng dataset mạng hiện có, không in hoặc lưu dataset vào source/log. Lặp lại cold boot. Nếu cảm biến lỗi, các giá trị đo phải `null`.

## Rollback, chỉ hướng dẫn

Sau khi xác minh đúng board B và được phép ghi, mở `firmware/node-b-thread-autostart` đã PASS thực tế, dùng cùng môi trường ESP-IDF rồi chạy `idf.py --no-ccache -p COM18 app-flash` (thay COM theo cổng đã xác minh). Bản này dùng cùng partition table, app-only flash giữ NVS. Không thực hiện rollback trong checkpoint này.

## Ghi chú build

ESP-IDF/Python/toolchain đã được dùng từ bản cài hiện có, không cài đặt lại. CMake in một cảnh báo Git `dubious ownership` khi truy vấn metadata của submodule OpenThread trong `D:\esp\v5.4.4\esp-idf`; quá trình cấu hình, biên dịch, link, tạo image và kiểm tra image đều exit 0. Không thay đổi Git config toàn hệ thống. Cảnh báo này không làm hỏng artifact, nhưng có thể xử lý bằng `safe.directory` trong process nếu cần build lại.
