# Node A Thread/CoAP Auto-Start (CP6.4)

Firmware `nt532_node_a_autostart`, phiên bản `a-autostart-0.1.1`, tạo riêng từ `node-a-thread-probe`. Giữ `GET /info`, `GET /status`, `POST /hb`, `POST /stop`, Serial provisioning và NVS của **chính Node A**. Không khởi tạo GPIO hay tải; giữ nguồn riêng của relay, laser, bơm, servo ở trạng thái ngắt.

Sau `esp_openthread_start()`, firmware kiểm tra active dataset bằng `otDatasetIsCommissioned()` dưới OpenThread lock. Có dataset hợp lệ thì bật IPv6 rồi Thread; không có thì để Thread disabled và chờ `TLV:`/`START` qua Serial. Lệnh `START` vẫn dùng cùng đường khởi động. Không gọi `esp_openthread_auto_start(NULL)` vì API đó có thể tạo dataset từ Kconfig. Xem [báo cáo CP6.4](../../reports/nt532_thread_autostart_2026-11-11.md).

## Build

Trong PowerShell, đặt môi trường **chỉ cho process** và dùng Python ESP-IDF:

```powershell
$env:IDF_PATH = 'D:\esp\v5.4.4\esp-idf'
$env:IDF_TOOLS_PATH = 'D:\Espressif'
$env:PATH = 'D:\Espressif\python_env\idf5.4_py3.11_env\Scripts;' + $env:PATH
. "$env:IDF_PATH\export.ps1"
& python "$env:IDF_PATH\tools\idf.py" --no-ccache set-target esp32h2
& python "$env:IDF_PATH\tools\idf.py" --no-ccache build
```

Không sao chép `sdkconfig`, `build/`, NVS hay Thread dataset từ board khác. `sdkconfig.defaults` giữ cấu hình probe gốc, bao gồm 4 MB flash, USB Serial/JTAG và OpenThread native FTD.

## Flash sau xác nhận riêng — không chạy trong CP6.4

COM19 là cổng của A lúc sao lưu, có thể đã đổi. Xác minh lại board vật lý bằng PnP và MAC `48:31:b7:ff:fe:cd:5b:ce`, bảo đảm các tải ngắt nguồn, rồi dùng cổng đã xác nhận:

```powershell
# Chạy trong thư mục firmware/node-a-thread-autostart, sau khi export ESP-IDF.
& python "$env:IDF_PATH\tools\idf.py" --no-ccache -p COM19 flash
```

Không chạy `erase-flash`. Build này dùng cùng partition table SHA-256 với probe gốc: NVS ở `0x9000..0xEFFF`; lệnh flash của IDF ghi bootloader `0x0`, partition table `0x8000`, app `0x10000`, không ghi vùng NVS. Tuy nhiên phải kiểm tra lại flash args trước khi thao tác nếu build lại hoặc đổi cấu hình phân vùng.

## Nghiệm thu sau flash

Đóng Serial monitor, rút/cắm lại **chỉ board A**, không gửi `START`. Sau khi OTBR đã là Leader, đợi board attach rồi kiểm tra bằng EID của A: IPv6 ping, `GET /info` đúng `n=A` và `fw=a-autostart-0.1.1`, `POST /hb`, `GET /status`, `POST /stop`. Lặp cold boot lần hai; xác nhận cả hai lần trở lại child/router và CoAP hoạt động mà không nhập dataset/START. Kiểm tra board còn lại và RCP tiếp tục online.

Nếu `START` thủ công được gửi, firmware vẫn dùng dataset đã có; không cần gửi lại `TLV:`. Khi chưa có dataset, firmware không tự tạo mạng và vẫn chờ `TLV:<active-dataset-hex>` hoặc `START` qua USB Serial/JTAG (LF hoặc CRLF); không ghi lại giá trị đầu vào. Chỉ thử trường hợp NVS trống trên board thử nghiệm riêng, **không xóa NVS của A/B** để làm bài test.

## Rollback — hướng dẫn, không thực thi

Nếu Auto-Start không đạt nghiệm thu, xác minh lại A bằng MAC/PnP, giữ tải ngắt nguồn, rồi sau quyết định flash riêng, build/flash lại **probe A gốc** từ `firmware/node-a-thread-probe` qua cổng A vừa xác nhận:

```powershell
Set-Location 'D:\IOT Hien Dai\Project\Project-NT532.R12\firmware\node-a-thread-probe'
& python "$env:IDF_PATH\tools\idf.py" --no-ccache -p COM19 flash
```

Rollback probe bằng `idf.py flash` dùng cùng partition table và không xóa NVS; probe gốc cần `START` sau boot. Bản backup toàn flash từ giai đoạn Serial có sẵn nhưng khôi phục nó sẽ thay thế NVS/dataset hiện tại, không dùng để rollback CP6.4 trừ khi có quyết định riêng. Không flash hoặc rollback trong checkpoint này.

Nguồn API: [ESP-IDF v5.4 Thread](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/network/esp_openthread.html), [ESP-IDF OpenThread guide](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-guides/openthread.html), [OpenThread active dataset](https://openthread.io/reference/group/api-operational-dataset), [OpenThread Thread enable](https://openthread.io/reference/group/api-thread-general).
