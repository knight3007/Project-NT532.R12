# NT532 CP6 — Chuẩn bị ESP32-H2 Node B Thread/CoAP probe

Ngày 2026-11-11 (Asia/Saigon). **Build PASS, unit test PASS; chưa flash.** Giai đoạn này chỉ tạo firmware trong `firmware/node-b-thread-probe`, không mở COM18/COM19/COM5, không chạm mạng Thread thực tế, không gửi hay lưu dataset/Network Key. Tải relay, laser, bơm và servo phải tiếp tục ngắt nguồn riêng.

## Kết quả nghiệm thu

| Check | Kết quả | Bằng chứng |
| --- | --- | --- |
| ESP-IDF v5.4.4 / target `esp32h2` | **PASS** | `idf.py --version` → `ESP-IDF v5.4.4` (0); `idf.py --no-ccache set-target esp32h2` (0); `project_description.json` ghi `esp32h2`. |
| Build firmware B | **PASS** | `idf.py --no-ccache build` (0), `Project build complete`; app 746.608 byte (`0xB6470`), app partition 1 MiB còn `0x49B90` byte (29%). [Build log](./nt532_node_b_probe_build_2026-11-11.log). |
| Binary | **PASS** | [`nt532_node_b_probe.bin`](../firmware/node-b-thread-probe/build/nt532_node_b_probe.bin), SHA-256 `75E90F5BFB3071997B120B97031B49D654A18847A967B3C82253FC5CA2705F28`; `esptool image_info` nhận `ESP32-H2`, checksum và validation hash hợp lệ. Binary chứa `b-probe-0.1.0` và `nt532_node_b_probe`. |
| Unit test host | **PASS** | GCC 15.2.0 MSYS2 UCRT64, `-std=c11 -Wall -Wextra -Werror`, compile 0, executable 0. [Test log](./nt532_node_b_probe_unit_2026-11-11.log). |
| Định danh và CoAP | **PASS (source/build)** | `/info` và `/status` trả `n: B`; version `b-probe-0.1.0`. Có `GET /info`, `GET /status`, `POST /hb`, `POST /stop`. Các request khác trả `4.05`. Chưa kiểm tra CoAP trên board B. |
| Serial provisioning | **PASS (logic/source)** | Nhận `TLV:<hex>` và `START` với CR, LF hoặc CRLF; unit test kiểm tra parser và TLV 254 byte. Ràng buộc compile-time xác nhận buffer 520 byte đủ cho tối đa 254 byte TLV + prefix/terminator. Không log giá trị đầu vào; bộ đệm/dataset được xóa sau xử lý. Chưa kiểm tra USB Serial/JTAG thực tế trên B. |
| Không điều khiển tải | **PASS (source)** | Không thêm driver GPIO, ADC, LEDC, DHT11, servo hay relay. `/stop` chỉ tăng counter; `stopped` luôn true. Chưa đo tín hiệu vật lý sau flash. |
| Thread identity riêng | **PASS (thiết kế); runtime chờ** | Không hardcode Thread extended address/MAC, không sao chép `sdkconfig`, NVS, flash image hoặc dataset của A. B dùng NVS trên chính board B và MAC phần cứng B khác A. EID/role của B cần kiểm tra sau flash. |

Python được kích hoạt trong process build: `D:\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe` (3.11.15). `IDF_PATH=D:\esp\v5.4.4\esp-idf`, `IDF_TOOLS_PATH=D:\Espressif`. `sdkconfig.defaults` của B đặt target H2, flash 4 MB, USB Serial/JTAG, OpenThread FTD/native radio; không chứa credential mạng thực. `sdkconfig`, `build/` và `dependencies.lock` được `.gitignore` loại khỏi Git.

## Source và thay đổi

Đã đọc đầy đủ `main/app_main.c`, `main/probe_state.c/.h`, `test/test_probe_state.c`, README, CMake và `sdkconfig.defaults` của probe A; kiểm tra các mục target/console/OpenThread của `sdkconfig` sinh ra mà không sao chép file này. Chỉ sao chép tám file source/docs/config mặc định cần thiết sang thư mục mới. Hash SHA-256 của cả tám file gốc A được đối chiếu lại sau thao tác và **8/8 không đổi**.

Thay đổi trong B: project/version/tag/JSON/log đổi sang B; thêm `probe_is_start`, parser chấp nhận CR đơn, vòng Serial chốt trên CR hoặc LF và bỏ qua LF rỗng sau CR; thêm static assertion cho độ dài dòng tối đa; mở rộng unit test cho CR/LF/CRLF và TLV dài 254 byte. Logic join Thread, NVS, radio native, CoAP method/response và bốn resource giữ theo A. Không thêm cấu hình pin B vào chương trình probe.

## Các lỗi công cụ đã xử lý

1. GCC MSYS2 lúc đầu không chạy được `cc1` do DLL khác trên PATH của shell. Đặt `C:\msys64\ucrt64\bin` đầu PATH **chỉ trong process unit test**; test RED sau đó cho thấy thiếu `probe_is_start`, rồi test GREEN compile/run 0. Không đổi PATH toàn hệ thống.
2. Lần `set-target` đầu từ chối `build/` vì file unit test do chính phiên này đặt trong một thư mục chưa phải CMake build. Đã kiểm tra đường dẫn và nội dung, chỉ xóa executable test tự tạo và thư mục rỗng đó; không xóa source/backup. Lần `set-target` sau: exit 0.
3. Git trong sandbox từ chối đọc `D:\esp\v5.4.4\esp-idf` do owner Windows khác (`dubious ownership`). Đã thêm đúng thư mục này vào `safe.directory` **qua biến môi trường Git trong process build**, không sửa Git config global. `set-target` và build sau đó PASS.

Build log chỉ lưu output đã lọc các dòng có thể chứa credential. Không đưa dataset thực vào source, lệnh build, log, báo cáo hay Git. Không commit hoặc push. Các thay đổi có sẵn ở calibration, `sensor-h2`, probe A, tài liệu và `build-output/` được giữ nguyên.

## Kiểm thử thực tế sau khi được phép flash

[README Node B](../firmware/node-b-thread-probe/README.md) có lệnh cụ thể cho flash, provisioning và rollback. Trước flash, người vận hành cần xác nhận lại board vật lý B bằng PnP và MAC `48:31:b7:ff:fe:cd:51:d5` (COM18 là cổng tại thời điểm backup), kiểm tra backup B 4 MB và hash, giữ tải ngắt nguồn. Flash **chỉ B** sau xác nhận riêng; không dùng `erase-flash`.

Sau flash: kiểm tra OTBR hiện tại vẫn `leader`/mạng cũ, cấp dataset đang hoạt động trực tiếp từ Pi tới USB Serial/JTAG của B mà không in hay lưu key; quan sát role/EID của B. Từ Pi gọi lần lượt `GET /info`, `POST /hb`, `GET /status`, `POST /stop`; xác nhận `n=B`, `fw=b-probe-0.1.0`, mã CoAP `2.05/2.04`, heartbeat/stop_count thay đổi phù hợp. Kiểm tra A vẫn online với định danh riêng và RCP tiếp tục hoạt động. Không kết luận runtime PASS trước các bước này.

Nếu cần rollback, xác minh lại B và SHA-256 backup rồi dùng lệnh restore chỉ dẫn trong README **sau một quyết định riêng**. Restore sẽ ghi đè toàn bộ flash/NVS hiện tại; lệnh đó chưa chạy. Dừng tại bước chuẩn bị CP6, trước flash.
