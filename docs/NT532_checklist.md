# Checklist NT532 — Camera, Edge AI, định vị A/B (cập nhật 2026-11-05 sau kiểm thử model)


## Quyết định mới và trạng thái bàn giao (đọc trước)

- **MVP một mục tiêu lửa mô phỏng tại một thời điểm.** Chọn một bounding box `fire` đại diện, giữ `confidence`, `(x1,y1,x2,y2)` và tâm ảnh `(cx,cy)` (pixel). Nhiều đám cháy/điều khiển đồng thời chuyển thành mở rộng; nếu gặp nhiều vùng lửa tách biệt mà chưa thể phân giải, không tự bật laser.
- **Không bỏ `(cx,cy)`:** đó là tọa độ mục tiêu trên ảnh 1280×720, làm đầu vào bắt buộc để tính vị trí mục tiêu theo hệ tọa độ sa bàn. Muốn suy ra tọa độ 3D cần thêm intrinsics, pose camera và độ sâu hoặc giả định mặt phẳng; không thể dùng pixel như góc servo trực tiếp khi camera tách khỏi A/B.
- **Chưa chốt model mặc định, chưa xác nhận đã đổi `vision.imgsz` sang 320.** Người dùng xác nhận *chưa chuyển model về 320 px*; không tự ghi cấu hình đã đổi. Hai ứng viên cần giữ: `fire-n.pt` ở 320 hoặc 480; `fire-n_ncnn_model` ở 640 (nhanh). Đọc `config/site.yaml` trên Pi trong buổi mới trước khi chỉnh.
- **Bằng chứng đã có:** Pi 5 `aarch64`; RTMP/RTSP qua MediaMTX; `open_camera()` đọc và lưu JPEG; `fire-n.pt` và NCNN đã tải lên và chạy; phát hiện `fire` trên khung RTSP mô phỏng (có ảnh bbox); đã benchmark CPU và đối chiếu hai định dạng.
- **Chưa làm:** kiểm thử số lượng ảnh dương/âm đủ để đánh giá báo nhầm và bỏ sót; lựa chọn model cuối; kiểm thử NCNN trực tiếp 20 frame; đo trễ camera→Pi chuẩn; hiệu chuẩn intrinsics/AprilTag/pose, kiểm thử Thread và phần cứng.
- **DepthVision / ToF (Galaxy S20+ hoặc S20 Ultra):** nguồn đo khoảng cách hỗ trợ khi bố trí sa bàn, **chưa đo trên thiết bị của nhóm và chưa được tích hợp vào RTMP**. Có thể kiểm tra bằng Samsung Quick Measure/ARCore; phải so với thước và thử khả năng lấy dữ liệu thật, không mặc định LOLS IRL truyền depth.

## Mốc 0: Thiết lập Thread Border Router (song song với bố trí sa bàn)

- [ ] Kiểm tra bo thứ ba đúng là **ESP32-H2 DevKitM-1**, hai Supermini A/B đúng chip **ESP32-H2** (không phải C3).
- [ ] Cài ESP-IDF và flash ví dụ `openthread/ot_rcp` lên DevKitM; nối USB-UART DevKitM với Raspberry Pi 5.
- [ ] Cài/chạy **OTBR (`ot-br-posix`) trên Raspberry Pi 5**, kiểm tra cổng serial, RCP và trạng thái mạng Thread.
- [ ] Nạp firmware OpenThread Node thử nghiệm cho hai Supermini, join cùng một mạng Thread.
- [ ] Truyền thử dữ liệu A và B bằng **CoAP qua Thread** tới Pi; phân biệt node nào phát thông báo.
- [ ] Chốt kênh phản hồi lệnh Pi→A/B; test riêng trước khi đưa servo/relay vào mạng.
- [ ] **Checkpoint 0:** cả hai Supermini giao tiếp được với Pi qua DevKitM RCP + OTBR; camera điện thoại truyền Wi-Fi/RTMP tới MediaMTX trên Pi, Pi đọc RTSP; hai kênh độc lập.

**Chú ý:** hai file `.ino` hiện tại chỉ hoạt động qua Serial; không tự động kết nối Thread nếu chưa thay/nâng cấp firmware. ESP32-H2 không có Wi-Fi tích hợp.

## Mốc 1: Bố trí động và camera điện thoại cố định (ưu tiên)

- [ ] Chọn vị trí đặt điện thoại độc lập với Pan–Tilt, **không chuyển động trong lượt thử**; thử các bố trí tại nhiều môi trường (ví dụ lớp học, quán cà phê, ngoài trời).
- [ ] Đặt A **bên phải camera**, B **bên trái camera**; hai cụm ở trước và trong vùng camera thấy được.
- [ ] Kiểm tra hướng trái/phải trên khung hình thật, loại trừ chế độ mirror.
- [ ] Ghi lại `trial_id`, ảnh bố trí, độ phân giải, vị trí tương đối của điện thoại/A/B ở từng lượt.
- [ ] **Checkpoint 1:** ít nhất hai bố trí khác nhau, camera đứng yên trong mỗi lượt và vẫn thấy đủ cả A/B.

## Mốc 1B: Camera Android → RTMP → MediaMTX → RTSP → Raspberry Pi 5 (theo repo)

**Luồng chính (đã thử):** Android dùng **LOLS IRL** (thay Larix do watermark), phát RTMP tới MediaMTX trên Pi 5 (cổng `1935`, path thực tế `cam/stream`). Pi đọc RTSP/TCP qua `rtsp://127.0.0.1:8554/cam/stream`; chương trình `StreamCapture` trong repo xử lý frame cho AI. **MQTT không dùng cho camera chính.**

- [x] **1B.1 — Mạng:** laptop phát hotspot 5 GHz; Pi có địa chỉ LAN thử nghiệm `192.168.137.30` (cần xác nhận lại khi đổi mạng), Android kết nối tới MediaMTX qua hotspot.
- [x] **1B.2 — MediaMTX:** Pi Linux ARM64 (`aarch64`), MediaMTX v1.21.3 chạy; cấu hình hợp lệ, RTMP `:1935`, RTSP `:8554`. Trên máy Pi đã cấu hình path `cam/stream` để phù hợp cú pháp URL mà ứng dụng Android chấp nhận. **Đây là thay đổi cục bộ so với cấu hình `cam` trên GitHub; đồng bộ vào repo sau khi chốt.**
- [x] **1B.3 — Android:** phát video H.264 bằng **LOLS IRL** theo URL `rtmp://192.168.137.30:1935/cam/stream`; chọn 720p, 30 FPS danh nghĩa, CBR; ảnh cài đặt ghi bitrate 6 Mbps, **chưa xác nhận đã thay đổi bitrate** (đề xuất thử 1.5–2 Mbps nếu còn giật). Đặt camera cố định, tránh thay góc/zoom và khóa focus/exposure nếu ứng dụng hỗ trợ.
- [x] **1B.4 — Xem stream:** FFplay trên Pi mở được RTSP `rtsp://127.0.0.1:8554/cam/stream` với video H.264 1280×720, 30 FPS theo metadata. FFmpeg giải mã luồng và tự kết thúc bài đo `-t 15` như dự kiến.
- [x] **1B.5 — Nguồn trong repo:** `cv2 5.0.0`; xác nhận `config/site.yaml` trỏ tới `rtsp://127.0.0.1:8554/cam/stream`, `nt532.vision.camera.open_camera()` đọc thành công và lưu JPEG tại `runs/model-smoke/images/input.jpg`; sau đó YOLO xử lý thành công ảnh này.
- [x] **1B.6 — Ghi nhận sơ bộ:** hotspot laptop 5 GHz; xem bằng RealVNC. Quan sát thao tác đưa tay: hình trên màn hình xem xuất hiện chậm khoảng **3 s**, thêm **2 s** mới rõ; đây là **độ trễ cảm nhận qua RealVNC**, không phải kết quả đo trễ camera→Pi chính xác. Ứng dụng có lúc báo `signal weak` và khoảng **15 FPS thực tế**; FFmpeg giải mã thử có `speed` từ **1.11× đến 1.40×**. Chưa kết luận nút thắt từ các số liệu này.
- [ ] **1B.7 — Đo trễ định lượng (để sau):** khi node/laser mô phỏng đã hoạt động an toàn và xem trực tiếp không qua RealVNC, chạy `cd ~/nt532/pi && uv run --no-sync python scripts/stream_latency.py --node s1`; ghi kết quả vào `camera.stream.latency_s`. Giá trị `0.8` trong cấu hình chỉ là giá trị khởi đầu, không phải số đo.
- [x] **Checkpoint 1B — Đạt baseline đường video:** Android → RTMP → MediaMTX → RTSP → `open_camera()` → JPEG/YOLO đã hoạt động. **Chưa đo độ trễ định lượng 1B.7**, nên không đánh dấu đường video đủ điều kiện để điều khiển laser tự động.

**Ghi nhớ:** hiện chưa tối ưu độ trễ theo cảm nhận qua RealVNC; trước khi điều khiển Pan–Tilt/laser tự động, phải kiểm tra chất lượng frame mới và đo `latency_s` trực tiếp. Cấu hình hotspot/điện thoại có thể thay đổi khi chuyển môi trường.

**Nguồn chuẩn:** [`docs/camera-dien-thoai.md`](https://github.com/knight3007/Project-NT532.R12/blob/main/docs/camera-dien-thoai.md), [`pi/deploy/mediamtx.yml`](https://github.com/knight3007/Project-NT532.R12/blob/main/pi/deploy/mediamtx.yml), [`pi/src/nt532/vision/stream.py`](https://github.com/knight3007/Project-NT532.R12/blob/main/pi/src/nt532/vision/stream.py). **MQTT** trong repo hiện phục vụ Home Assistant tùy chọn; không phải kênh camera chính.

## Mốc 2: Nạp model YOLO và kiểm thử Edge AI trên Raspberry Pi 5 — BASELINE ĐÃ CHẠY

**Đã hoàn thành các phép thử nền tảng trên Pi thật; checkpoint chất lượng triển khai vẫn mở.** Không quay lại cài MediaMTX/model từ đầu trong cuộc trò chuyện tiếp theo.

- [x] **2.1 — Có trọng số:** `~/nt532/models/fire-n.pt` và `~/nt532/models/fire-n_ncnn_model/` được chuyển từ laptop qua `scp`. `.pt` đã nạp, `Task: detect`, `{0: smoke, 1: fire}`; NCNN 640 px đã nạp và benchmark được. Model và dataset không đưa lên Git.
- [x] **2.2 — Môi trường CPU:** Pi `aarch64`, OpenCV `5.0.0`, `torch 2.9.1+cpu`, `torchvision 0.24.1`, `ultralytics 8.4.202`, `ncnn` chạy được. `uv sync --extra yolo` từng timeout và kéo gói CUDA; đã cài bản CPU từ PyTorch CPU index trực tiếp vào `pi/.venv`. Dùng **`uv run --no-sync`**, tránh chạy `uv sync` làm thay đổi môi trường hiện tại.
- [x] **2.3 — Camera vào repo:** `camera.stream.url = rtsp://127.0.0.1:8554/cam/stream`; `open_camera()` đọc được ảnh `1280×720`, lưu `runs/model-smoke/images/input.jpg`. Ảnh đầu tiên chạy suy luận đúng quy trình nhưng `Detections: 0` vì không có mục tiêu hợp lệ.
- [x] **2.4 — Có phát hiện dương tính qua RTSP:** dùng ảnh lửa mô phỏng hiển thị trên laptop, LOLS IRL phát đến Pi; script vòng lặp tìm được `fire 0.84` ở frame 6/30 và lưu `runs/model-smoke/detected.jpg` với bbox chính. **Không thử lửa/khói thật.**
- [x] **2.5 — So sánh kích thước trên cùng frame:** `.pt` 320 px: 1 bbox `fire`, conf 0.837, tâm (648,347); 480 px: 2 bbox conf 0.799 và 0.492; 640 px: 2 bbox conf 0.490 và 0.420. Ảnh 480/640 có bbox nhỏ ở rìa của **cùng một ảnh ngọn lửa**, không phải bằng chứng hai đám cháy. Ảnh lưu `runs/model-smoke/compare/`.
- [x] **2.6 — Benchmark CPU bằng repo:** `scripts/bench_detect.py --n 30`, dữ liệu thử **chỉ 1 ảnh**, không bao gồm RTSP. Bảng kết quả ở dưới và `runs/bench/bench-NT532-R12-TH1-01.csv`.
- [x] **2.7 — Đối chiếu NCNN/PyTorch:** cùng ảnh, cùng `imgsz=640`, hai bản đều vẽ hai bbox trên vùng lửa; `.pt` conf 0.52 / 0.44, NCNN conf 0.46 / 0.41. Ảnh `verify_pytorch.jpg`, `verify_ncnn.jpg` đã được xem. NCNN nhanh hơn ≈3,44 lần trên benchmark 640 px.
- [x] **2.8 — Kiểm tra lỗi thoát RTSP mức cơ bản:** bản `stream.py` cục bộ trên Pi đã thêm `join()` trong `StreamCapture.release()`; từng gặp `TabError` do trộn tab/space, đã sửa; benchmark chạy lại được. Bài test mở/đọc/đóng luồng dùng `join()` thủ công báo `Thread stopped: True`, `EXIT CODE: 0`. **Chưa chứng minh hết lỗi dưới tải/lặp lâu; rà soát và test lại sửa đổi trước khi commit.**
- [ ] **2.9 — Test NCNN headless liên tục qua RTSP:** thực hiện 20–30 frame, lưu `class`, `confidence`, `(cx,cy)` và ảnh; xác nhận thoát chương trình sạch, không `FATAL`.
- [ ] **2.10 — Đánh giá chất lượng tối thiểu cho một mục tiêu:** thu ít nhất 10 mẫu dương tính và 10 mẫu âm tính ở vài khoảng cách/ánh sáng; thử cùng ảnh với `.pt 320`, `.pt 480`, `NCNN 640`. Báo riêng tỷ lệ phát hiện, bỏ sót, báo nhầm; không lấy confidence một ảnh làm accuracy/mAP.
- [ ] **2.11 — Chọn cấu hình cuối:** kiểm tra `vision.weights`/`vision.imgsz` thực tế trên Pi trước; **chưa chốt 320 hay NCNN**. Chỉ cập nhật `config/site.yaml` sau khi đánh giá thêm; giữ một mục tiêu và an toàn OFF khi không chắc chắn.
- [ ] **Checkpoint Mốc 2 đầy đủ:** hoàn thành kiểm thử NCNN trực tiếp + bộ mẫu dương/âm + chọn config. Không bắt buộc triển khai depth/ToF để chốt baseline AI.

### Số đo benchmark thực tế trên Raspberry Pi 5

| Weights / runtime | `imgsz` | Mean (ms) | Median (ms) | P95 (ms) | FPS suy luận |
|---|---:|---:|---:|---:|---:|
| `fire-n.pt` / PyTorch CPU | 320 | 64.4 | 64.1 | 66.8 | 15.5 |
| `fire-n.pt` / PyTorch CPU | 480 | 125.5 | 125.3 | 127.0 | 8.0 |
| `fire-n.pt` / PyTorch CPU | 640 | 219.2 | 219.2 | 219.8 | 4.6 |
| `fire-n_ncnn_model` / NCNN CPU | 640 | **63.7** | **63.7** | **64.0** | **15.7** |

**Giới hạn:** một ảnh, `n=30`, script đo `model.predict`; các lượt đầu với `.pt` 640 trên ảnh khác đạt 217.64–220.45 ms. FPS suy luận **không phải** FPS camera/độ trễ end-to-end. Chưa có mAP hoặc kết quả bộ test gán nhãn. Model chỉ có hai lớp `fire/smoke`, chưa tinh chỉnh cho toàn bộ vật mô phỏng sa bàn.

### Điểm bắt đầu cuộc trò chuyện mới — KHÔNG lặp lại bước cài đặt

1. Xem cấu hình thực tế, không tự sửa sang 320/NCNN:

   ```bash
   cd ~/nt532/pi
   grep -A5 '^vision:' ../config/site.yaml
   grep -A5 '  stream:' ../config/site.yaml
   uv run --no-sync python -c 'import torch, ultralytics, ncnn; print("AI READY", torch.__version__)'
   ```

2. Tiếp tục **2.9** bằng bài thử **NCNN trên 20 frame RTSP**, tiếp theo là **2.10** (ít nhất 10 dương/10 âm, một mục tiêu chính), trước khi quyết định cấu hình.
3. Sau checkpoint AI, ưu tiên **hình học camera–A/B** (Mốc 4); các bài Serial/Thread vẫn là phần riêng chưa được nghiệm thu. Không bật laser khi chưa hiệu chuẩn và đo trễ.

**Nguồn repo:** [`models/README.md`](https://github.com/knight3007/Project-NT532.R12/blob/main/models/README.md), [`pi/scripts/bench_detect.py`](https://github.com/knight3007/Project-NT532.R12/blob/main/pi/scripts/bench_detect.py), [`pi/src/nt532/vision/stream.py`](https://github.com/knight3007/Project-NT532.R12/blob/main/pi/src/nt532/vision/stream.py).

## Mốc 2C: DepthVision/ToF của Galaxy S20+ / S20 Ultra — THỬ NGHIỆM TÙY CHỌN

- [ ] Xác minh **đúng thiết bị điện thoại thực tế** là Galaxy S20+ hoặc S20 Ultra và Quick Measure/ARCore Depth có hoạt động trên máy đang dùng; chưa tự coi điện thoại đang chạy LOLS IRL là mẫu này.
- [ ] Dùng **Samsung Quick Measure** đo khoảng cách camera→A, camera→B và (nếu ứng dụng cho phép xác định hai điểm cùng cảnh) A↔B; so sánh ít nhất 5 lần với thước đo thật, ghi sai số tuyệt đối và điều kiện đo.
- [ ] Chỉ tính A↔B từ hai khoảng cách camera→A/B **khi có thêm góc/hướng tương đối hoặc vị trí 3D**; ToF không tự biết khoảng cách giữa hai node khi chỉ biết hai giá trị độ sâu.
- [ ] Khảo sát truy cập depth từ **ARCore Depth API hoặc Camera2 `DEPTH_OUTPUT`** trên đúng mẫu máy; nếu không thể lấy depth được, chỉ dùng Quick Measure để đo hỗ trợ **offline khi bố trí sa bàn**. LOLS IRL→RTMP→RTSP đang cung cấp **ảnh màu RGB**, không tự truyền depth map hoặc đồng bộ thời gian với depth.
- [ ] Nếu dùng được depth có tọa độ/timestamp tin cậy, đánh giá với mục tiêu mô phỏng đặt ở ít nhất 3 khoảng cách, so độ sâu với thước, kiểm tra sự khớp ảnh RGB/ToF rồi mới nghiên cứu bổ sung vào định vị `(cx,cy) → (X,Y,Z)`.

**Định vị cơ sở không phụ thuộc ToF:** intrinsics ChArUco + AprilTag/commissioning + kích thước/hình học bảng bia/mặt sàn + đo thủ công. ToF là nguồn đo hỗ trợ, không thay pose camera hay pose A/B.

Nguồn ngoài đã kiểm tra: [Samsung S20 camera](https://www.samsung.com/vn/support/mobile-devices/check-out-the-new-camera-functions-of-galaxy-s20-plus-s20-ultra/), [Samsung Quick Measure](https://galaxystore.samsung.com/detail/com.samsung.android.ruler?langCd=vi), [Google ARCore Depth](https://developers.google.com/ar/develop/depth), [Android Camera2 DEPTH_OUTPUT](https://developer.android.com/reference/android/hardware/camera2/CameraMetadata#REQUEST_AVAILABLE_CAPABILITIES_DEPTH_OUTPUT).

## Mốc 3: Test phần cứng theo file tải lên (sau AI)

- [ ] Cụm A (`cumA.ino`): test `HOME`, `PAN`, `TILT`, `MOVE`, `ON`, `OFF`, `GAS`, `STATUS`.
- [ ] Cụm B (`cumB.ino`): test `HOME`, `PAN`, `TILT`, `MOVE`, `ON`, `OFF`, `PULSE`, `DHT`, `STATUS`.
- [ ] Xác nhận relay A/B GPIO10 kích HIGH; nguồn và dây đấu đúng.
- [ ] Kiểm tra giới hạn Tilt B đang để **5000 us** (khác mức hiệu chỉnh cũ 2500 us); chưa thử vượt phạm vi an toàn.
- [ ] Kiểm tra điện áp AO/DO cảm biến gas trước khi nối GPIO1/12.
- [ ] **Checkpoint 3:** ghi nhận kết quả test riêng của từng cụm; không mặc định đã chạy thật chỉ dựa trên source code.

## Mốc 4: Hiệu chuẩn camera, định vị và Pan–Tilt

- [ ] Dựa trên model đã test ở Mốc 2, dùng ảnh của **chính stream RTSP** để hiệu chuẩn nội tại camera (`calibrate_camera.py`) và commissioning với AprilTag (`commission.py`); đo thước camera→A/B và A↔B, có thể đối chiếu thêm ToF; các giá trị chỉ hợp lệ với bố trí hiện tại.
- [ ] Xác minh model phát hiện mục tiêu trong ít nhất hai bố trí/môi trường; lưu class, confidence và `(cx, cy)` để kiểm tra định vị.
- [ ] Hiệu chỉnh ánh xạ tọa độ ảnh → Pan/Tilt **riêng từng cụm**, kiểm tra sai số ngắm bằng mục tiêu mô phỏng ở nhiều vị trí/độ sâu; chỉ bật laser khi đã kiểm soát an toàn.
- [ ] MVP một mục tiêu: liên kết một cảnh báo node A/B với một vùng `fire` đã xác minh; nếu xuất hiện nhiều mục tiêu không phân biệt được, giữ relay OFF và ghi log. Xử lý nhiều đám cháy là mở rộng; phải loại frame cũ.
- [ ] **Checkpoint 4:** đổi bố trí A/B đúng quy ước và vẫn định vị, ngắm chính xác sau khi hiệu chỉnh lại; không suy ra chỉ từ tọa độ tâm ảnh.

## Mốc 5: Tích hợp và đo đạc

- [ ] Cảm biến A/B báo qua Thread/CoAP -> điện thoại phát RTMP liên tục -> Pi đọc RTSP và chạy YOLO/xác minh -> Pi gửi lệnh CoAP tới node tương ứng -> Pan/Tilt + relay/laser -> ghi log.
- [ ] Test cảnh báo A rồi B ở các bố cục khác nhau; không kích hoạt khi chưa xác minh mục tiêu.
- [ ] Đo tỷ lệ nhận diện, độ trễ camera RTMP/RTSP (`stream_latency.py`), độ trễ tổng và sai số laser theo từng `trial_id` và môi trường; không dùng độ trễ quan sát qua RealVNC thay số đo thực.
- [ ] **Checkpoint 5:** demo ổn định với ít nhất hai bố trí sa bàn, camera độc lập và đứng yên trong từng lượt.

## Quy ước giữ nguyên

- DevKitM-1: board ESP32-H2 thứ ba, chạy RCP qua USB với Pi; Pi chạy OTBR làm Thread Border Router.
- Cụm A: bên phải camera, gas AO GPIO1 + DO GPIO12, Pan4/Tilt5, Relay10 kích HIGH.
- Cụm B: bên trái camera, DHT11 GPIO11, Pan4/Tilt5, Relay10 kích HIGH.
- Hai cụm độc lập, có thể thay đổi vị trí giữa các lần thử nhưng luôn ở phía trước camera.
- Code test nền tảng: `/mnt/data/cumA.ino` và `/mnt/data/cumB.ino`; không sửa bản gốc khi mới lập kế hoạch.

## Ghi chú kiến trúc cố định

- Điện thoại là **camera cố định độc lập với Pan–Tilt**, có thể đặt lại giữa các lượt/môi trường; luồng chính **RTMP → MediaMTX trên Pi → RTSP/TCP → StreamCapture → AI**.
- Cảm biến và lệnh điều khiển A/B sử dụng **Thread/CoAP** theo hợp đồng firmware trong repo; `cumA.ino`/`cumB.ino` là code thử Serial, chưa phải firmware tích hợp mạng. **MQTT** là phần tích hợp Home Assistant tùy chọn; không dùng làm đường video chính.
- Không dùng giả định camera nằm trên đầu Pan–Tilt hay tham số căn chỉnh bất biến khi chuyển môi trường.
