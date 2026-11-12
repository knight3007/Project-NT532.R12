# NT532 CP6.4 — Thread Auto-Reconnect cho Node A/B

Ngày 2026-11-11 (Asia/Saigon). **Chuẩn bị PASS: unit test và build ESP-IDF v5.4.4 PASS cho A/B.** Chưa truy cập COM18/COM19/COM5, chưa flash, chưa thay đổi OTBR, NVS hoặc Thread dataset trên thiết bị. Runtime cold boot vẫn phải nghiệm thu sau flash được xác nhận riêng.

## Nguyên nhân gốc

Trong [probe A](../firmware/node-a-thread-probe/main/app_main.c) và [probe B](../firmware/node-b-thread-probe/main/app_main.c), `app_main()` gọi `esp_openthread_start()`, đăng ký bốn resource CoAP, rồi mở task Serial và kết thúc. Hàm `start_thread()` chỉ được gọi từ `process_provision_line()` khi nhận `START` hoặc `TLV:`; **không có lời gọi trong nhánh boot**. Vì vậy khởi động OpenThread instance không đồng nghĩa bật IPv6/Thread, và B chỉ attach sau khi nhập `START`. Cả A cũng có cùng thiếu sót trong source, dù chưa có báo cáo cold boot A thất bại.

Dấu hiệu B dùng lại Mesh-Local EID và không cần nhập lại `TLV:` cho thấy active dataset/settings của B đã tồn tại trên flash; đây là **suy luận từ quan sát người vận hành**, không phải đọc NVS của board trong checkpoint này. Source ESP-IDF v5.4.4 xác nhận `esp_openthread_start()` tạo task, chờ OpenThread instance và netif khởi tạo rồi trả về; settings port mở namespace `openthread` trên phân vùng `nvs` được cấu hình. Source probe dùng `nvs_flash_init()` (không erase) và `storage_partition_name = "nvs"`. Do đó có thể dùng `otDatasetIsCommissioned()` ngay sau khi start, dưới OpenThread lock.

Theo [OpenThread dataset API](https://openthread.io/reference/group/api-operational-dataset), `otDatasetIsCommissioned()` chỉ true khi Active Operational Dataset chứa mạng hợp lệ. Theo [OpenThread Thread API](https://openthread.io/reference/group/api-thread-general), phải bật IPv6 interface trước `otThreadSetEnabled(true)`. [ESP-IDF v5.4 OpenThread guide](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-guides/openthread.html) yêu cầu lock khi gọi OpenThread API từ task khác. [ESP-IDF v5.4 Thread API](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/network/esp_openthread.html) nêu `esp_openthread_auto_start(NULL)` có thể sinh dataset từ Kconfig; vì vậy **không dùng API đó**. Source ESP-IDF địa phương xác nhận nhánh NULL gọi `otDatasetCreateNewNetwork()` khi FTD bật.

## Thay đổi

| Node | Project mới | Version | Binary |
| --- | --- | --- | --- |
| A | [node-a-thread-autostart](../firmware/node-a-thread-autostart/) | `a-autostart-0.1.1` | [nt532_node_a_autostart.bin](../firmware/node-a-thread-autostart/build/nt532_node_a_autostart.bin) |
| B | [node-b-thread-autostart](../firmware/node-b-thread-autostart/) | `b-autostart-0.1.1` | [nt532_node_b_autostart.bin](../firmware/node-b-thread-autostart/build/nt532_node_b_autostart.bin) |

Từ mỗi probe gốc, chỉ sao chép source, CMake, `sdkconfig.defaults`, test và README sang project mới. Không sao chép `sdkconfig`, `build/`, NVS, flash image hoặc Thread dataset. Thêm `thread_start_existing()` dùng chung cho **boot** và Serial `START`/`TLV:`: nếu không có active dataset hợp lệ thì không bật IPv6/Thread; nếu có thì bật IPv6 rồi Thread. Gọi nó sau khi CoAP resources đã đăng ký và trước khi thả OpenThread lock. Khi không có dataset, task Serial vẫn nhận provisioning; CoAP resources đã đăng ký nhưng chưa truy cập được qua Thread. Không hardcode hoặc in dataset/Network Key. Không thêm driver GPIO, DHT11, MQ-2, servo hoặc relay; các đầu ra phần mềm tiếp tục không được điều khiển. Nguồn tải riêng và mạch kéo OFF vật lý vẫn phải bảo đảm an toàn.

## Bằng chứng build và test

| Check | Node A | Node B |
| --- | --- | --- |
| `idf.py --version` | `ESP-IDF v5.4.4`, exit 0 | Cùng phiên build |
| `idf.py --no-ccache set-target esp32h2` | exit 0 | exit 0 |
| `idf.py --no-ccache build` | **PASS**, exit 0 | **PASS**, exit 0 |
| App image | 746576 byte, `0xB6450` | 746656 byte, `0xB64A0` |
| SHA-256 app image | `1BB53A2CC42EB2FC477AC50ED5C304E7213FFDDC5DB3313CDF2A4B7F507D3E1A` | `CB8F0657D8E0105D3CD18BFCEB0069390E23FABD2E0DE0AEFFA10B6CAAEF4629` |
| `esptool image_info` | ESP32-H2, checksum/hash hợp lệ | ESP32-H2, checksum/hash hợp lệ |
| Host tests GCC 15.2.0, `-Wall -Wextra -Werror` | probe và autostart compile/run 0 | probe và autostart compile/run 0 |

[Log môi trường](./nt532_autostart_build_env_2026-11-11.log): Python `D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe` 3.11.15; `IDF_PATH=D:\esp\v5.4.4\esp-idf`; `IDF_TOOLS_PATH=D:\Espressif`. PowerShell phân loại dòng thông báo `Activating ESP-IDF 5.4` từ `export.ps1` là `NativeCommandError` khi ghi log; ngay sau đó dependency check `OK`, export hoàn tất, version/set-target/build đều exit 0. [Build A](./nt532_node_a_autostart_build_2026-11-11.log), [build B](./nt532_node_b_autostart_build_2026-11-11.log), [host tests](./nt532_autostart_tests_2026-11-11.log). PATH/Git safe.directory chỉ thay đổi ở process build. Không cài lại ESP-IDF.

Test mới được chạy RED khi helper chưa có, rồi GREEN. Các fake OpenThread API chỉ thay thế ranh giới phần cứng/stack; test kiểm tra boot có active dataset, boot không có dataset, boot lại với dataset cũ, `START` thủ công sau provisioning, `START` lặp và lỗi bật IPv6 không được tiếp tục bật Thread. Đây là **unit test**, không chứng minh radio/cold boot thật cho tới khi flash và kiểm tra.

Partition table binary của cả hai project mới có cùng SHA-256 `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820` với partition table của cả hai probe gốc. NVS `0x9000`, 24 KiB; app `0x10000`, 1 MiB. `flash_args` mới chỉ liệt kê bootloader `0x0`, partition table `0x8000`, app `0x10000`; không ghi vùng NVS. Không dùng `erase-flash`.

## Flash, nghiệm thu và rollback — hướng dẫn, chưa thực hiện

Trước mọi flash, kiểm tra lại PnP/MAC và cổng COM: A từng là COM19, MAC `48:31:b7:ff:fe:cd:5b:ce`; B từng là COM18, MAC `48:31:b7:ff:fe:cd:51:d5`. COM có thể thay đổi. Giữ nguồn riêng của tải ngắt, sao lưu/đối chiếu backup hiện có. Sau khi được cho phép flash riêng, từ PowerShell ESP-IDF 5.4.4, dùng đúng thư mục và đúng cổng đã xác minh:

```powershell
Set-Location 'D:\IOT Hien Dai\Project\Project-NT532.R12\firmware\node-a-thread-autostart'
idf.py --no-ccache -p COM19 flash
Set-Location 'D:\IOT Hien Dai\Project\Project-NT532.R12\firmware\node-b-thread-autostart'
idf.py --no-ccache -p COM18 flash
```

Mỗi board cần quyết định flash và nhận diện riêng. Không flash cả hai một cách mù quáng nếu COM đổi. Flash chuẩn này giữ NVS theo partition layout đã kiểm tra; xác nhận lại `flash_args` nếu build lại hay đổi cấu hình.

Sau flash, **không gửi START**: rút/cắm lại từng board, chờ attach, xác nhận OTBR vẫn Leader, A/B trở lại child/router với EID riêng; ping IPv6 và gọi `GET /info`, `POST /hb`, `GET /status`, `POST /stop`. `/info` phải báo đúng A/B và version Auto-Start; CoAP response/method giữ như probe. Lặp cold boot lần hai để xác nhận tự nối lại. `START` thủ công phải vẫn dùng được. Trường hợp không dataset được chứng minh bằng unit test; chỉ nghiệm thu phần cứng đó trên board thử nghiệm NVS trống, không xóa NVS A/B. [README A](../firmware/node-a-thread-autostart/README.md) và [README B](../firmware/node-b-thread-autostart/README.md) có bước cụ thể.

Nếu cần rollback sau quyết định flash riêng, quay lại `firmware/node-a-thread-probe` hoặc `firmware/node-b-thread-probe`, flash probe gốc vào **đúng board** bằng `idf.py -p <COM đã xác minh> flash`, không erase NVS. Bản sao lưu toàn flash A/B từ CP5.0.2 là firmware Serial cũ, không phải probe; restore toàn flash sẽ ghi đè NVS hiện tại nên không phải rollback mặc định cho CP6.4.

**Trạng thái:** dừng trước flash. Build/test PASS; cold boot thực tế, CoAP sau Auto-Start và mức OFF phần cứng chưa được đo trong checkpoint này.
