# Node B Thread/CoAP + DHT11 (CP7)

Firmware riêng cho ESP32-H2 **Node B**, phiên bản `b-dht11-0.1.0`. Kế thừa Thread join, USB Serial provisioning và bốn endpoint CoAP của `node-b-thread-probe`. Chỉ cấu hình **GPIO11** để đọc DHT11; không khởi tạo GPIO10, servo, relay, bơm hay laser. Giữ nguồn riêng của mọi tải ở trạng thái ngắt.

## Đấu dây và kiểm tra trước flash

| DHT11 rời | ESP32-H2 Node B |
| --- | --- |
| VCC (chân 1) | 3V3 |
| DATA (chân 2) | GPIO11, kéo lên **3V3** bằng 4.7 kΩ |
| NC (chân 3) | Không nối |
| GND (chân 4) | GND chung |

Với module DHT11 có PCB, **đọc ký hiệu chân trên chính module** trước khi đấu; thứ tự chân có thể khác cảm biến rời. Kiểm tra module có sẵn điện trở kéo lên và điện áp kéo lên của DATA; tuyệt đối không đưa 5 V vào GPIO11. Kiểm tra bằng đồng hồ rằng 3V3, GND, DATA và GPIO11 đúng trên **board B thực tế** trước khi flash. Repo mô tả hai node là H2 Super Mini, chưa có sơ đồ của board vật lý để xác nhận đầu nối. Tài liệu Espressif cho DevKitM-1 và datasheet H2 liệt kê GPIO11 là I/O; đây là kiểm tra theo tài liệu, chưa phải kiểm tra dây thực tế. Giữ dây DATA ngắn, ưu tiên dưới 1 m ở 3.3 V.

Nguồn và timing theo [Aosong DHT11 manual](https://www.aosong.com/userfiles/files/media/DHT11%E6%B8%A9%E6%B9%BF%E5%BA%A6%E4%BC%A0%E6%84%9F%E5%99%A8%E8%AF%B4%E6%98%8E%E4%B9%A6%EF%BC%88%E4%B8%AD%EF%BC%89%20A0-1208.pdf). Pin GPIO11 trên [ESP32-H2 DevKitM-1](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32h2/esp32-h2-devkitm-1/user_guide.html) và [datasheet H2](https://documentation.espressif.com/esp32-h2_datasheet_en.html). Driver dùng [GPIO ISR](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/gpio.html) và [esp_timer](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/system/esp_timer.html) có sẵn trong ESP-IDF v5.4.4.

## CoAP

| Request | Kết quả |
| --- | --- |
| `GET /info` | JSON `n=B`, `fw=b-dht11-0.1.0`, outputs disabled |
| `GET /status` | Vai trò Thread, heartbeat, stop count và trạng thái DHT11 |
| `POST /hb` | 2.04, ghi heartbeat |
| `POST /stop` | 2.04, tăng stop count; **không điều khiển tải** |

`/status` có `temperature_c`, `humidity_pct`, `sensor_ok`, `sample_age_ms`, `last_attempt_age_ms`, `sensor_error`. Khi chưa có mẫu hoặc lỗi checksum/timing/timeout, `sensor_ok=false`, các giá trị đo và `sample_age_ms` là `null`. `last_attempt_age_ms` là tuổi của lần thử gần nhất, kể cả lần thất bại. Ví dụ khi thành công:

```json
{"n":"B","role":"router","stopped":true,"hb_age_ms":250,"hb_fresh":true,"stop_count":0,"sensor_ok":true,"temperature_c":24.00,"humidity_pct":55.00,"sample_age_ms":800,"last_attempt_age_ms":800,"sensor_error":null}
```

Task cảm biến đợi ít nhất 2 giây sau lần đọc trước, bắt cạnh bằng ngắt GPIO, chờ kết quả trong cửa sổ hữu hạn rồi kiểm tra 40 bit, checksum và dải đo. Handler CoAP chỉ sao chép snapshot dưới khóa ngắn; không chờ DHT11. Không thêm component ngoài ESP-IDF.

## Build/test trên Windows

Trong PowerShell đã `export.ps1` ESP-IDF v5.4.4 với `IDF_PATH=D:\esp\v5.4.4\esp-idf`, `IDF_TOOLS_PATH=D:\Espressif`, Python `D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe`, tại thư mục firmware này:

```powershell
idf.py --no-ccache set-target esp32h2
idf.py --no-ccache build
```

Host tests dùng GCC UCRT64 trong PATH **chỉ của process**:

```powershell
$oldPath = $env:PATH
$env:PATH = 'C:\msys64\ucrt64\bin;C:\Windows\System32;C:\Windows'
gcc -std=c11 -Wall -Wextra -Werror -Imain main/probe_state.c test/test_probe_state.c -o build/test_probe_state.exe
& build/test_probe_state.exe
gcc -std=c11 -Wall -Wextra -Werror -Imain main/dht11_logic.c test/test_dht11.c -o build/test_dht11.exe
& build/test_dht11.exe
$env:PATH = $oldPath
```

Không sao chép `sdkconfig`, `build/`, NVS hoặc dataset từ probe A/B. Firmware dùng MAC, Thread identity và NVS riêng của board B.

## Flash và provisioning — chỉ sau xác nhận riêng

**CP7 chỉ build; chưa chạy lệnh dưới đây.** Xác minh lại board B bằng PnP và MAC `48:31:b7:ff:fe:cd:51:d5`; COM18 chỉ là cổng đã ghi nhận trước đây. Kiểm tra backup B `backups/serial-2026-11-11/B_COM18_full_flash.bin`, SHA-256 `4B181EA6A619105C223A2650260161A05B5AC36FC188877ED6F2406A2EB3F36A`. Giữ tải ngắt nguồn. Không chọn COM19 (A) hoặc COM5 (RCP). Sau khi người vận hành xác nhận flash B, thay COM18 bằng cổng vừa xác minh nếu đổi:

```powershell
idf.py --no-ccache -p COM18 flash
```

Không dùng `erase-flash`. Firmware giữ cơ chế USB Serial/JTAG `TLV:<active-dataset-hex>` hoặc `START` với CR/LF/CRLF. `START` dùng dataset đã lưu trên **chính B**. Nếu cần nạp dataset mạng Thread hiện có từ Pi, xác minh đúng USB Serial/JTAG của B rồi truyền trực tiếp mà không in/ghi dataset:

```sh
sudo ot-ctl state
ls -l /dev/serial/by-id/
sudo sh -c 'tlv=$(ot-ctl dataset active -x | sed -n "1p"); [ ${#tlv} -gt 0 ] || exit 1; printf "TLV:%s\n" "$tlv" > /dev/serial/by-id/<B-USB-serial-JTAG>'
```

Không dùng `dataset init new`; không đưa Network Key vào source, chat, báo cáo hoặc log. Kiểm tra từ Pi bằng **EID của B**:

```sh
NODE_B_EID='fd..:..'
coap-client -m get "coap://[$NODE_B_EID]/info"
coap-client -m post -N -e '' "coap://[$NODE_B_EID]/hb"
coap-client -m get "coap://[$NODE_B_EID]/status"
coap-client -m post -e '' "coap://[$NODE_B_EID]/stop"
```

Xác nhận `n=B`, version mới, role hợp lệ, `sensor_ok=true`, nhiệt/ẩm hợp lý và `sample_age_ms` thay đổi theo chu kỳ ít nhất 2 s. Kiểm tra lỗi cảm biến sau khi **tắt nguồn board và tháo DATA**, rồi khởi động lại: `sensor_ok=false`, nhiệt/ẩm `null`, `sensor_error` cho biết lỗi. A và RCP vẫn hoạt động độc lập.

## Rollback — hướng dẫn, không thực thi

Sau khi xác minh lại B và được phép ghi flash, có thể flash lại image probe B đã PASS tại `firmware/node-b-thread-probe/build/`, hoặc khôi phục nguyên trạng backup B. Khôi phục backup ghi đè toàn bộ flash/NVS hiện tại; xác minh hash trước khi làm. Lệnh cho image backup đầy đủ (thay COM18 bằng cổng B thực tế):

```powershell
$py = 'D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe'
$backup = 'D:\IOT Hien Dai\Project\Project-NT532.R12\backups\serial-2026-11-11\B_COM18_full_flash.bin'
Get-FileHash $backup -Algorithm SHA256
& $py -m esptool --chip esp32h2 --port COM18 write_flash --flash_freq keep --flash_mode keep --flash_size keep 0x0 $backup
```

Không chạy rollback hay flash trong CP7.
