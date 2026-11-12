# NT532 — Sao lưu flash Serial A/B, CP5.0.2

Ngày: 2026-11-11 (Asia/Saigon). Kết quả: **PASS cả CP5-A và CP5-B**. Chỉ dùng thao tác đọc/đối chiếu flash trên COM19 và COM18. Không truy cập COM5, không ghi hay xóa flash, không chỉnh firmware Serial A/B.

## Checkpoint

| Checkpoint | Thiết bị | COM | Dung lượng flash | File backup | SHA-256 | PASS/FAIL |
| --- | --- | --- | ---: | --- | --- | --- |
| CP5-A | ESP32-H2 A | COM19 | 4 MB (4.194.304 byte) | [`A_COM19_full_flash.bin`](../backups/serial-2026-11-11/A_COM19_full_flash.bin) | `4DFAE7C5F0FDA19B6DC37E3F4A5FC3BE586B105BC682D283ADA6B1CC6A1F1E21` | **PASS** |
| CP5-B | ESP32-H2 B | COM18 | 4 MB (4.194.304 byte) | [`B_COM18_full_flash.bin`](../backups/serial-2026-11-11/B_COM18_full_flash.bin) | `4B181EA6A619105C223A2650260161A05B5AC36FC188877ED6F2406A2EB3F36A` | **PASS** |

Mỗi file có đúng 4.194.304 byte, bằng dung lượng `flash_id` nhận diện. Mỗi file được kiểm tra lại bằng `esptool verify_flash` trên chính board tương ứng: **toàn bộ 0x400000 byte tại offset 0x0 khớp digest của flash**, exit code 0. SHA-256 trên đây tính lại từ file cuối cùng sau khi kiểm tra.

## Môi trường và nguyên nhân lỗi Python

- ESP-IDF source: `D:\esp\v5.4.4\esp-idf`; tools: `D:\Espressif`.
- Python/esptool đã dùng: `D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe` — Python 3.11.15, `esptool.py v4.13.0`. Lệnh `python.exe -m esptool version` bằng đường dẫn tuyệt đối: **exit code 0**.
- PowerShell mặc định chọn `C:\Users\dotha\anaconda3\python.exe` (Python 3.13.9); `python -m esptool version` bằng Python mặc định báo `No module named esptool`, **exit code 1**. Đây là nguyên nhân lỗi esptool trong shell hiện tại. Dùng trực tiếp Python của ESP-IDF venv đã hoạt động, không cài esptool vào Anaconda và không đổi PATH hệ thống hay PATH process.
- [Báo cáo RCP trước đó](./nt532_rcp_flash_2026-11-11.md) xác nhận cùng venv này đã dùng khi flash RCP.

## Nhận diện board và điều kiện an toàn

Người vận hành đã xác nhận tải relay, laser, bơm, servo ở cả A/B được ngắt nguồn riêng và cho phép esptool reset hai board. `docs/NT532_CODEX_PHASE_A_Z.md` (dòng 1024–1025) và yêu cầu hiện tại cùng ánh xạ **A→COM19, B→COM18**. PnP tại thời điểm sao lưu:

| Board | Windows PnP | Instance ID | Chip / MAC | Flash |
| --- | --- | --- | --- | --- |
| A | `USB Serial Device (COM19)`, `OK` | `USB\VID_303A&PID_1001&MI_00\6&30CE3D84&0&0000` | ESP32-H2FH4 rev v0.1; `48:31:b7:ff:fe:cd:5b:ce` | GD 4 MB; manufacturer `c8`, device `4016` |
| B | `USB Serial Device (COM18)`, `OK` | `USB\VID_303A&PID_1001&MI_00\6&D5F696D&0&0000` | ESP32-H2FH4 rev v0.1; `48:31:b7:ff:fe:cd:51:d5` | GD 4 MB; manufacturer `c8`, device `4016` |

Có tiến trình PuTTY nhưng không có bằng chứng nó dùng COM nào. Kiểm tra mở độc quyền COM19 và COM18 bằng pyserial đều thành công trước khi chạy esptool; `flash_id`, `read_flash` và `verify_flash` sau đó cũng mở đúng cổng và exit code 0. Số COM có thể thay đổi khi cắm lại; dùng MAC và PnP để nhận diện lại trước mọi thao tác sau này.

## Lệnh đã chạy và log

Mọi lệnh esptool đều gọi Python venv bằng đường dẫn tuyệt đối, với `--chip esp32h2`, đúng `--port`, `--before default_reset` và `--after hard_reset`. Theo cú pháp esptool v4.13.0, quy trình mỗi board là:

1. `flash_id` → đọc chip, MAC, flash size; **exit 0** cho A và B.
2. `read_flash 0 0x400000 <file>.partial` → đọc toàn bộ flash 4 MB; **exit 0** cho A và B. B dùng thêm `--no-progress` để log gọn. Chỉ đổi tên `.partial` thành `.bin` sau khi kiểm tra đúng 4.194.304 byte. Nếu tên backup đã tồn tại, quy trình chọn tên có hậu tố mới để tránh ghi đè.
3. `verify_flash --flash_freq keep --flash_mode keep --flash_size keep 0 <file>.bin` → so digest với toàn bộ flash; **exit 0**, `verify OK (digest matched)` cho A và B. Mã nguồn `esptool` v4.13.0 cho thấy lệnh này gọi `flash_md5sum`; không ghi flash.

| Board | `flash_id` | `read_flash` | `verify_flash` |
| --- | --- | --- | --- |
| A | [log](../backups/serial-2026-11-11/logs/A_COM19_flash_id.log) (0) | [log](../backups/serial-2026-11-11/logs/A_COM19_read_flash.log) (0) | [log](../backups/serial-2026-11-11/logs/A_COM19_verify_flash.log) (0) |
| B | [log](../backups/serial-2026-11-11/logs/B_COM18_flash_id.log) (0) | [log](../backups/serial-2026-11-11/logs/B_COM18_read_flash.log) (0) | [log](../backups/serial-2026-11-11/logs/B_COM18_verify_flash.log) (0) |

Lỗi nhỏ khi thử câu lệnh pyserial lần đầu: PowerShell làm mất dấu nháy trong đoạn `python -c`, khiến lệnh in trạng thái báo `NameError` sau khi cổng đã mở; chạy lại với cú pháp đúng, cả hai cổng mở/đóng thành công. Không ảnh hưởng file backup. Lệnh `arduino-cli core list` thử kiểm kê package trả `Access denied` khi CLI khởi tạo tiện ích ở vùng ngoài workspace; không cài đặt gì. Đã đọc trực tiếp thư mục board package và `library.properties` để lấy thông tin ở mục sau.

## Source, board package và bảo vệ dữ liệu

Không tìm thấy sketch `*.ino`/`*.pde` của firmware Serial A/B trong workspace, nên không thể sao chép source của hai firmware đang chạy. [Bản kiểm kê](../backups/serial-2026-11-11/source_inventory.md) ghi rõ giới hạn này. Máy hiện có Arduino ESP32 core 3.3.12 và ESP32Servo 3.2.1; chưa xác nhận đây là phiên bản dùng để biên dịch firmware Serial hiện tại. Không sửa hay dùng `firmware/sensor-h2` cho checkpoint này.

Root `.gitignore` đã thêm `backups/`; `git check-ignore -v` xác nhận cả hai `.bin` bị loại khỏi Git. Các backup có thể chứa dữ liệu thiết bị; không commit/push công khai. Các thay đổi có sẵn trong workspace ở calibration, firmware test, tài liệu và build output được giữ nguyên.

## Hướng dẫn khôi phục sau này — chưa thực hiện

Chỉ cân nhắc khôi phục khi thực sự cần và đã xác nhận lại đúng **board vật lý qua MAC/PnP**, ngắt tải riêng, đối chiếu SHA-256 file backup, rồi sao chép file ra nơi lưu an toàn. `write_flash` ghi toàn bộ flash và sẽ thay thế firmware/dữ liệu hiện có; **không chạy trong CP5.0.2**. Cú pháp esptool v4.13.0 tương ứng, dùng đúng file của từng board:

```powershell
$py = 'D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe'
& $py -m esptool --chip esp32h2 --port COM19 write_flash --flash_freq keep --flash_mode keep --flash_size keep 0x0 'D:\IOT Hien Dai\Project\Project-NT532.R12\backups\serial-2026-11-11\A_COM19_full_flash.bin'
& $py -m esptool --chip esp32h2 --port COM18 write_flash --flash_freq keep --flash_mode keep --flash_size keep 0x0 'D:\IOT Hien Dai\Project\Project-NT532.R12\backups\serial-2026-11-11\B_COM18_full_flash.bin'
```

Các lệnh trên chỉ là hướng dẫn phục hồi, chưa thực thi. Full flash backup không chứa eFuse hay source sketch. Dừng tại **CP5.0.2**; không build/flash firmware Thread hoặc CoAP.
