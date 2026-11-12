# Node B Thread/CoAP + DHT11 Auto-Start (CP7)

Firmware riêng cho ESP32-H2 B, phiên bản `b-dht11-auto-0.1.1`. Dựa trên `node-b-thread-dht11`: giữ nguyên driver DHT11, task lấy mẫu, Serial provisioning, `/info`, `/status`, `/hb`, `/stop`. Thêm đúng logic Auto-Start đã chạy trên Node B từ `node-b-thread-autostart`: lúc boot, nếu active dataset còn trong NVS thì bật IPv6 và Thread. Nếu chưa có dataset, chờ `TLV:<dataset-hex>` hoặc `START`; không tạo mạng mới. Dataset và Network Key không được in log.

Chỉ GPIO11 dành cho DHT11 DATA. Firmware không khởi tạo GPIO10, GPIO4/5, relay, servo, bơm hay laser. Mọi tải phải tiếp tục ngắt nguồn riêng.

## DHT11

| Chân cảm biến rời | Nối vào Node B |
| --- | --- |
| VCC (1) | 3V3 |
| DATA (2) | GPIO11, kéo lên 3V3 bằng 4.7 kΩ |
| NC (3) | Để hở |
| GND (4) | GND chung |

Với module có PCB, xác nhận ký hiệu chân và điện trở kéo lên trên module thực tế. Không đưa 5 V vào GPIO11. Xác minh chân GPIO11 trên **board B thực tế** trước khi đấu. GPIO11 là I/O theo [hướng dẫn ESP32-H2 DevKitM-1](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32h2/esp32-h2-devkitm-1/user_guide.html) và [datasheet ESP32-H2](https://documentation.espressif.com/esp32-h2_datasheet_en.html); repo trước đây cũng gọi board là H2 Super Mini nên cần kiểm tra đúng board vật lý.

Task DHT11 thử đọc cách nhau ít nhất 2 giây, có timeout, kiểm tra timing, checksum và dải giá trị. CoAP chỉ lấy snapshot, không đợi cảm biến. `/status` xuất `sensor_ok`, `temperature_c`, `humidity_pct`, `sample_age_ms`, `last_attempt_age_ms`, `sensor_error`. Khi chưa cắm cảm biến hoặc có lỗi, `sensor_ok:false`, nhiệt/ẩm và `sample_age_ms` bằng `null`.

## Build và test

Trong PowerShell đã export ESP-IDF v5.4.4 và dùng Python `D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe`, tại thư mục này:

```powershell
idf.py --no-ccache set-target esp32h2
idf.py --no-ccache build
```

Host tests dùng GCC UCRT64 trong PATH của process: `test_probe_state.c`, `test_dht11.c`, `test_thread_start.c`. Test tích hợp cuối kiểm tra auto-join khi đã có dataset và `/status` khi DHT11 chưa cắm. Kết quả và lệnh build của lần này nằm trong báo cáo CP7 ở `reports/`.

## Flash sau khi nghiệm thu, chưa thực hiện

Xác minh lại Node B qua Windows PnP và MAC đã ghi trong backup là `48:31:b7:ff:fe:cd:51:d5`; COM18 chỉ là cổng ở lần kiểm tra trước. Giữ nguồn tải ngắt, xác nhận backup B còn nguyên, rồi dùng **app-flash** từ thư mục firmware này:

```powershell
idf.py --no-ccache -p COM18 app-flash
```

Lệnh này dùng `build/app-flash_args` và chỉ ghi image ứng dụng ở `0x10000`; partition NVS ở `0x9000` không nằm trong danh sách ghi. Không dùng `erase-flash` hay lệnh `flash` toàn bộ dự án. Không chuyển dataset từ A sang B. Nếu B chưa có active dataset, dùng cơ chế `TLV:<dataset-hex>` và `START` trên USB Serial/JTAG của chính B, truyền trực tiếp và không lưu dataset vào source, log hoặc Git.

## Nghiệm thu thực tế sau flash

1. Khi **chưa đấu DHT11**, cấp nguồn B và không nhập `START`/`TLV:`. Chờ B tự vào mạng Thread cũ; từ Pi truy vấn `GET /info` để xác nhận `n=B`, `fw=b-dht11-auto-0.1.1`, rồi `GET /status` để xác nhận `sensor_ok:false` và hai giá trị đo `null`. Kiểm tra `POST /hb` và `POST /stop`; không cấp nguồn cho tải.
2. Tắt nguồn B, đấu DHT11 như bảng trên rồi cấp nguồn lại. B phải tự nối Thread. Truy vấn `/status` nhiều lần để thấy `sensor_ok:true`, nhiệt/ẩm hợp lý và `sample_age_ms` thay đổi theo chu kỳ tối thiểu 2 giây. Kiểm tra A và RCP vẫn hoạt động.
3. Nếu B chưa có dataset, xác nhận nó không tự tạo mạng; provisioning qua Serial bằng dataset của mạng hiện tại, rồi thử cold boot lại.

Ví dụ lệnh trên Pi sau khi lấy đúng Mesh-Local EID của B:

```sh
NODE_B_EID='fd..:..'
coap-client -m get "coap://[$NODE_B_EID]/info"
coap-client -m get "coap://[$NODE_B_EID]/status"
coap-client -m post -N -e '' "coap://[$NODE_B_EID]/hb"
coap-client -m post -e '' "coap://[$NODE_B_EID]/stop"
```

## Rollback, chỉ hướng dẫn

Sau khi xác minh lại đúng B và được phép ghi firmware, mở thư mục `firmware/node-b-thread-autostart` đã PASS trên phần cứng và chạy `idf.py --no-ccache -p COM18 app-flash` từ đó. Lệnh app-only giữ NVS hiện tại. Không khôi phục full-flash backup trừ khi thực sự cần khôi phục cả NVS và có xác nhận riêng.
