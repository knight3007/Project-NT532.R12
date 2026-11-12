# Kế hoạch NT532 — Sa bàn động, Edge AI và hai cụm A/B (cập nhật 2026-11-05 sau benchmark)

## Trạng thái và phạm vi

Bản cập nhật theo quy ước của nhóm ngày 2026-11-03, đồng bộ lại với nhánh `main` của repo [Project-NT532.R12](https://github.com/knight3007/Project-NT532.R12) ở Mốc 1B. Mô hình dùng để phát hiện, xác minh và định vị sự cố mô phỏng bằng Thread/Edge AI, Pan–Tilt và laser; không thử với lửa hoặc khí gas nguy hiểm. **Luồng RTMP/RTSP và baseline YOLO/NCNN trên Pi đã được thử; ưu tiên tiếp theo là kiểm chứng một mục tiêu qua nhiều ảnh, xác nhận cấu hình model, rồi nghiên cứu định vị camera–A/B.**

**Quyết định kiến trúc camera:** dùng **LOLS IRL (Android) → RTMP → MediaMTX trên Raspberry Pi 5 → RTSP/TCP → StreamCapture/YOLO**; thay Larix do watermark, nhưng giữ cùng giao thức với repo. URL thực tế trên Pi là `cam/stream` do ứng dụng cần đủ cú pháp `/app/stream`; cấu hình repo gốc dùng `cam` và chưa được đồng bộ. MQTT dành cho Home Assistant tùy chọn, không truyền camera.


### Trạng thái thực nghiệm mới nhất và giới hạn MVP — 05/11/2026

- **Giữ MVP một mục tiêu cháy mô phỏng** trong một lượt; nhiều đám cháy độc lập và chọn nhiều vùng đồng thời là mở rộng. YOLO trả bbox `fire` với `confidence`, `(cx,cy)` (pixel) của **một mục tiêu chính**; những giá trị này là dữ liệu quan trọng cho định vị, nhưng không phải tọa độ không gian 3D hay góc servo.
- **Mốc 1B đã chạy qua repo:** Android LOLS IRL → RTMP → MediaMTX → RTSP/TCP → `open_camera()` → JPEG/YOLO. Ảnh 1280×720 đã lưu; `camera.stream.url` đã được kiểm tra trong phiên Pi. Đo trễ end-to-end vẫn CHƯA có.
- **Mốc 2 baseline Edge AI đạt:** `fire-n.pt` và NCNN 640 đã chép lên Pi; YOLO CPU import/nạp được, ảnh có bbox `fire 0.84` qua RTSP, hai model đã đối chiếu và benchmark. **Mốc 2 hoàn thiện chất lượng chưa đạt** vì chưa test nhiều mẫu dương/âm, chưa test NCNN liên tục qua RTSP và chưa chốt cấu hình cuối.
- **Không mặc định đã chuyển model sang 320.** Người dùng xác nhận chưa chuyển về 320 px. Không có bằng chứng xác nhận cấu hình cuối `vision.weights`/`vision.imgsz` trên Pi; luôn đọc `config/site.yaml` trước khi chỉnh. Cần cân nhắc `.pt` 320/480 với NCNN 640 bằng nhiều mẫu hơn.
- **DepthVision/ToF trên Galaxy S20+ và S20 Ultra** là nguồn đo khoảng cách **tùy chọn** có thể giúp bố trí, kiểm tra khoảng cách camera–A/B và hỗ trợ độ sâu mục tiêu. **Chưa xác minh mẫu máy Android đang dùng, chưa thử ToF và chưa tích hợp depth vào RTMP**; không biến chức năng này thành phụ thuộc mới của MVP.
- **Chưa làm:** Thread/OTBR/CoAP thật, Serial test A/B theo checklist, ChArUco intrinsics, AprilTag commissioning, ước lượng tọa độ 3D mục tiêu, phép chuyển sang pan/tilt và phép thử ngắm; vẫn cấm bật laser tự động.

## Quy ước bắt buộc

1. **Cụm A luôn ở bên phải camera; Cụm B luôn ở bên trái camera.** Cả hai ở phía trước điện thoại và trong vùng quan sát khi kiểm thử. Quy ước trái/phải theo hình ảnh camera đã xác nhận không bị lật gương.
2. **Camera là điện thoại độc lập, cố định trong một lượt thử.** Camera không gắn lên, không quay theo Pan–Tilt; mỗi khi chuyển môi trường (lớp học, quán cà phê, ngoài trời...), có thể đặt lại điện thoại, rồi giữ nguyên tư thế cho lượt thử tiếp theo.
3. **Sa bàn dynamic:** vị trí, khoảng cách, hướng đặt của các cụm và bối cảnh có thể khác nhau qua mỗi lượt/môi trường; không có preset góc servo hoặc tọa độ nền cố định cho A/B dùng chung mọi nơi.
4. **Camera phát RTMP tới MediaMTX trên Raspberry Pi 5; Pi đọc RTSP/TCP để chạy AI.** Luồng camera qua Wi-Fi riêng biệt với cảm biến/lệnh chấp hành qua Thread/CoAP. MQTT chỉ dành cho tích hợp Home Assistant tùy chọn hoặc đường lui phải triển khai riêng.
5. **Hệ tọa độ ảnh chỉ hợp lệ cho cấu hình camera hiện tại.** Khi đổi vị trí/góc điện thoại, cần xác nhận lại thứ tự trái-phải và hiệu chỉnh lại phép ánh xạ điểm ảnh → hướng laser. Việc camera cố định giúp duy trì hiệu chỉnh *trong* một lượt, không biến hiệu chỉnh thành bất biến giữa các địa điểm.
6. **Firmware thử nghiệm làm baseline:** `/mnt/data/cumA.ino` và `/mnt/data/cumB.ino`. Không sửa hoặc gộp các file gốc chỉ để cập nhật thiết kế.
7. Cảnh báo A/B cho biết **cụm nào phát hiện bất thường**, không cung cấp sẵn tọa độ mục tiêu. Khi có nhiều mục tiêu cùng phía cần marker hoặc logic ghép mục tiêu đáng tin cậy; chia trái/phải một mình là chưa đủ.

## Thiết bị mạng Thread còn thiếu: ESP32-H2 DevKitM-1 (RCP)

- **ESP32-H2 DevKitM-1** (board thứ ba, tách khỏi hai Supermini) chạy firmware **ESP-IDF `openthread/ot_rcp`**; kết nối USB-UART với Raspberry Pi 5, làm radio IEEE 802.15.4 cho Thread.
- **Raspberry Pi 5** chạy **OpenThread Border Router (`ot-br-posix` / OTBR)** và ứng dụng thu nhận dữ liệu CoAP. **Pi + DevKitM RCP mới tạo thành Thread Border Router**; bản thân DevKitM không làm cầu Wi-Fi độc lập và ESP32-H2 không có Wi-Fi.
- **ESP32-H2 Supermini của Cụm A/B** chạy ứng dụng Thread Node, phát dữ liệu/cảnh báo lên Pi qua mạng Thread. Tùy nhu cầu có thể nhận lệnh qua CoAP hai chiều; đây là bước phát triển tiếp theo, chưa có trong hai firmware Serial thử nghiệm.
- **Điện thoại** chạy LOLS IRL, đẩy RTMP qua hotspot laptop 5 GHz tới MediaMTX trên Pi (cổng `1935`, path `cam/stream`); Pi nhận RTSP/TCP (cổng `8554`). Luồng này **không qua RCP/Thread, không yêu cầu Mosquitto**.
- Khi xác nhận vật tư, kiểm tra tên chip thực trên các Supermini: **ESP32-H2** mới có radio IEEE 802.15.4/Thread; không đánh đồng với board **ESP32-C3 SuperMini**.
- Theo Espressif: https://docs.espressif.com/projects/esp-faq/en/latest/software-framework/thread.html và https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32h2/esp32-h2-devkitm-1/user_guide.html

### Đường truyền tổng thể

```text
                [Camera điện thoại / LOLS IRL]
                          | Wi-Fi / RTMP :1935/cam/stream
                          v
                [MediaMTX / Raspberry Pi 5]
                          | RTSP/TCP 127.0.0.1:8554/cam/stream
                          v
                   [StreamCapture / YOLO]
                          |
                 [Orchestrator trên Pi]
                          |
                  Thread / CoAP qua OTBR
                          |
                 [ESP32-H2 DevKitM-1]
                    RCP qua USB/UART
                        /       \
             Thread/CoAP         Thread/CoAP
                    /               \
     [Supermini H2 Cụm B]       [Supermini H2 Cụm A]
       bên trái camera             bên phải camera
       DHT11 + Pan/Tilt           Gas + Pan/Tilt
         + Relay                    + Relay
```

**Giới hạn hiện tại:** `cumA.ino` và `cumB.ino` vẫn chỉ test bằng Serial; cần firmware Thread + CoAP riêng để hai cụm thực sự kết nối được với OTBR. Việc flash RCP không tự biến code Serial thành thiết bị Thread.

## Baseline phần cứng và code

| Thuộc tính | Cụm A — phải camera | Cụm B — trái camera |
|---|---|---|
| MCU | ESP32-H2 | ESP32-H2 |
| Pan / Tilt | GPIO 4 / GPIO 5 | GPIO 4 / GPIO 5 |
| Relay | GPIO 10, kích HIGH | GPIO 10, kích HIGH |
| Cảm biến | Gas AO -> GPIO 1; DO -> GPIO 12 | DHT11 DATA -> GPIO 11 |
| Servo HOME | 1500 us / 1500 us | 1500 us / 1500 us |
| Pan MIN–MAX | 500–2500 us | 500–2500 us |
| Tilt MIN–MAX | 600–2500 us | 600–5000 us **theo file tải lên: cần kiểm tra an toàn** |
| Serial | 115200; MOVE, PAN, TILT, HOME, ON, OFF, GAS, STATUS | 115200; MOVE, PAN, TILT, HOME, ON, OFF, PULSE, DHT, STATUS |

**Không coi 5000 us là giới hạn servo đã được xác nhận.** Dữ liệu hiệu chỉnh trước đây của Cụm B là 600–2500 us cho Tilt. Không thử ngưỡng quá giới hạn vật lý của servo. Với module cảm biến gas cấp 5 V, phải hạ mức AO/DO về giới hạn logic/ADC 3.3 V của ESP32-H2, và dùng nguồn servo/relay phù hợp, chung GND.

## Luồng hệ thống mong muốn

```text
ĐIỆN THOẠI (camera cố định trong một lượt)
  -- RTMP qua Wi-Fi --> MediaMTX trên Raspberry Pi 5
                         |
                         v RTSP/TCP
                  StreamCapture / FrameHub
                         |
                         v
                    Edge AI / YOLO
              class, confidence, bbox, cx, cy
                         |
CỤM A (gas) / CỤM B (DHT11)
  -- Thread/CoAP alert --> Orchestrator / xác minh, chọn cụm
                         |
                         v Thread/CoAP /aim, /stop, ...
                Node ESP32-H2 đúng cụm
                         |
                         v
                Pan–Tilt + Relay + laser
                         |
                         v
                 kiểm chứng, ghi log

MQTT/Mosquitto (tùy chọn): tích hợp Home Assistant; không tham gia truyền camera.
```

### Mốc 1B — Camera Android → RTMP/MediaMTX → RTSP → Pi (theo repo)

**Nguồn chuẩn:** [`docs/camera-dien-thoai.md`](https://github.com/knight3007/Project-NT532.R12/blob/main/docs/camera-dien-thoai.md), [`pi/deploy/mediamtx.yml`](https://github.com/knight3007/Project-NT532.R12/blob/main/pi/deploy/mediamtx.yml), [`pi/src/nt532/vision/stream.py`](https://github.com/knight3007/Project-NT532.R12/blob/main/pi/src/nt532/vision/stream.py), [`config/site.yaml`](https://github.com/knight3007/Project-NT532.R12/blob/main/config/site.yaml).

**Trạng thái thử nghiệm ngày 2026-11-04:** Raspberry Pi 5 (`aarch64`, IP tạm `192.168.137.30`) kết nối qua hotspot laptop 5 GHz; ứng dụng Android thực tế là **LOLS IRL** thay Larix do watermark. MediaMTX v1.21.3 đã chạy; RTSP tới Pi mở và giải mã được H.264 **1280×720, 30 FPS theo thông số stream**. OpenCV đã import được (`5.0.0`). Cần xác minh việc **lưu frame bằng script của repo** và cấu hình cố định `camera.stream.url`; chưa coi việc chạy AI là đã thử nghiệm.

**1. Luồng thực tế và cấu hình:**

```text
Android / LOLS IRL -- RTMP --> rtmp://<IP_PI>:1935/cam/stream
MediaMTX / Pi       -- RTSP --> rtsp://127.0.0.1:8554/cam/stream
                           --> StreamCapture --> FrameHub / YOLO
```

Ứng dụng đã từ chối `rtmp://<IP_PI>:1935/cam` vì cần đủ `/app/stream`; do đó trên Pi hiện đã sửa `paths` thành `cam/stream`. **Repo GitHub gốc vẫn ghi `cam` và tài liệu dùng Larix: cần cập nhật lại mã/config trên repo khi nhóm chốt cấu hình**, không ghi nhận như thể repo đã được sửa.

**2. Khi khởi động lại Pi:** mở MediaMTX với cấu hình cục bộ đang dùng, giữ cổng `:1935` và `:8554`. Android kết nối vào hotspot cùng mạng, không mở RTMP ra Internet công cộng. Kiểm tra địa chỉ Pi bằng `hostname -I` vì hotspot có thể cấp IP khác ở lượt thử mới.

**3. Đọc stream từ phần mềm repo:** giữ URL RTSP có hậu tố `/cam/stream` trong `config/site.yaml`:

```yaml
camera:
  stream:
    url: "rtsp://127.0.0.1:8554/cam/stream"
    latency_s: 0.8        # Ước lượng ban đầu, KHÔNG phải độ trễ đã đo
```

Chỉ chỉnh hai khóa này trong block camera đã có. Đọc thử với `capture_frames.py`, chạy trong phiên Desktop (dùng `cv2.imshow`); nếu headless, thử đọc/lưu bằng `open_camera()` của repo không dùng GUI, không coi FFmpeg bên ngoài là đủ để nghiệm thu phần OpenCV.

```bash
cd ~/nt532/pi
uv run --no-sync python scripts/capture_frames.py --source rtsp://127.0.0.1:8554/cam/stream --out data/raw/trial_001
```

**4. Số liệu tạm chấp nhận để tiếp tục:** LOLS IRL từng báo `signal weak`, khoảng **15 FPS thực tế** (ảnh cài đặt ghi bitrate 6 Mbps; việc giảm bitrate chưa được xác nhận), trong khi stream công bố 30 FPS. FFmpeg ở bài kiểm tra 15 giây đạt `speed` **1.11× đến 1.40×**; bài đo tự kết thúc do tham số `-t 15`. Khi quan sát qua **RealVNC**, một bàn tay đưa vào khung hình xuất hiện sau khoảng **3 giây**, cần khoảng **2 giây tiếp theo** để nét. **Các số liệu này chỉ là quan sát gián tiếp, gồm cả độ trễ RealVNC; không thể thay cho phép đo end-to-end từ lúc điện thoại chụp đến lúc Pi nhận frame.** Không kết luận CPU Pi, mạng hay autofocus là nguyên nhân duy nhất.

**5. Đo trễ chuẩn khi tích hợp phần cứng:** sau khi node và laser mô phỏng hoạt động an toàn, chạy:

```bash
cd ~/nt532/pi
uv run --no-sync python scripts/stream_latency.py --node s1
```

Ghi `latency_s` theo số đo thật. Trước khi triển khai điều khiển Pan–Tilt/laser tự động, phải kiểm tra frame mới và hiệu chỉnh phản hồi theo thời gian thực, tránh ngắm dựa trên hình cũ.

**Checkpoint 1B (đường RGB đã đạt):** đường phát/nhận RTMP/RTSP, `open_camera()` của repo và lưu JPEG đã có kết quả thực tế; `site.yaml` đã kiểm tra URL cục bộ `/cam/stream`. **Riêng `latency_s` vẫn chỉ là ước lượng 0.8 s**, chưa đo chuẩn, nên chưa đủ điều kiện điều khiển ngắm tự động dựa trên frame. MQTT/Mosquitto vẫn chỉ là tích hợp Home Assistant tùy chọn.

## Mốc 2 — Nạp model và kiểm thử Edge AI trên Raspberry Pi 5 (đã có baseline thực nghiệm)

**Nguồn repo:** [`models/README.md`](https://github.com/knight3007/Project-NT532.R12/blob/main/models/README.md), [`docs/trien-khai-pi.md`](https://github.com/knight3007/Project-NT532.R12/blob/main/docs/trien-khai-pi.md), `pi/scripts/bench_detect.py`, `pi/src/nt532/vision/camera.py`, `pi/src/nt532/vision/stream.py`.

### 2.1. Model và môi trường trên Pi — ĐÃ CHẠY

- Raspberry Pi 5 Linux `aarch64`, Python environment `~/nt532/pi/.venv`, `cv2 5.0.0`.
- Đã chuyển và nạp `~/nt532/models/fire-n.pt` (`Task: detect`, lớp `0=smoke`, `1=fire`) và `~/nt532/models/fire-n_ncnn_model/` (bản xuất 640 px). Trọng số/dataset không nằm trong Git, giữ sao lưu trên laptop.
- Đã cài `torch==2.9.1+cpu`, `torchvision==0.24.1`, `ultralytics==8.4.202` và thư viện `ncnn` có thể suy luận. `uv sync --extra yolo` ban đầu kéo nhiều gói NVIDIA CUDA, bị timeout; bản CPU được cài vào `.venv` bằng `uv pip install --python .venv/bin/python --index https://download.pytorch.org/whl/cpu 'torch==2.9.1+cpu' 'torchvision==0.24.1' 'ultralytics>=8.3'`.
- **Giữ `uv run --no-sync`.** Không chạy lại `uv sync --extra yolo` một cách tùy ý vì có thể đồng bộ lại lock và thay bản CPU/NCNN vừa cài.

Kiểm tra nhanh (không cài lại):

```bash
cd ~/nt532/pi
ls -lh ../models/fire-n.pt
ls -ld ../models/fire-n_ncnn_model
uv run --no-sync python -c 'import torch, ultralytics, ncnn, cv2; print(torch.__version__, ultralytics.__version__, cv2.__version__)'
```

### 2.2. Camera RGB/RTSP — ĐÃ CHẠY

- Android **LOLS IRL** phát RTMP tới `rtmp://192.168.137.30:1935/cam/stream` (IP là giá trị lịch sử, kiểm tra lại bằng `hostname -I`). Pi đọc `rtsp://127.0.0.1:8554/cam/stream` qua MediaMTX.
- `config/site.yaml` đã được kiểm tra có `camera.stream.url` đúng path trên Pi. `open_camera(load_site()['camera'], url)` trả được frame và OpenCV lưu `runs/model-smoke/images/input.jpg` (1280×720). Ban đầu ảnh không có lửa nên `Detections: 0`, không phải lỗi model.
- Sau đó cho camera nhìn **ảnh lửa mô phỏng trên màn hình laptop**, script 30 frame tìm được `fire 0.84` ở frame 6, ảnh minh chứng `runs/model-smoke/detected.jpg` có bbox đúng vùng. Không thử lửa thật.
- `StreamCapture.release()` trên Pi đã được sửa cục bộ để dừng/join luồng FFmpeg trước khi Python thoát. Ban đầu sửa bị `TabError` do trộn tab/space, sau đó đã sửa lại và benchmark chạy được; bài đọc/đóng RTSP có join thủ công báo `Thread stopped: True`, `EXIT CODE: 0`. **Chưa test đóng mở lặp nhiều vòng; chưa xác nhận bản sửa đã commit lên Git.** Kiểm tra `python -m py_compile pi/src/nt532/vision/stream.py` từ gốc repo trước khi commit.

### 2.3. Nhận diện một mục tiêu và dữ liệu cần ghi — ĐÃ THỬ TRÊN MỘT FRAME

Không coi `Detections: 2` luôn là hai đám cháy độc lập: ở ảnh đã test, 480/640 tạo thêm bbox nhỏ nằm trong cùng hình ảnh ngọn lửa. MVP chỉ lấy **một mục tiêu `fire` được xác minh**. Giữ dữ liệu `trial_id`, `frame_id`, thời gian, `class`, `confidence`, bbox `(x1,y1,x2,y2)`, tâm `(cx,cy)`, kích cỡ frame, phiên bản model/`imgsz`. Nếu nhiều vùng tách biệt và không có quy tắc chọn an toàn thì báo trạng thái không chắc chắn, relay OFF.

Cùng một frame RGB (ảnh trong `runs/model-smoke/compare/original.jpg`):

| Runtime / `imgsz` | Bbox `fire` | Conf vùng chính | Tâm vùng chính (pixel) | Ghi chú |
|---|---:|---:|---|---|
| PyTorch `.pt` 320 | 1 | 0.837 | (648, 347) | Một bbox chính |
| PyTorch `.pt` 480 | 2 | 0.799 | (645, 343) | Bbox phụ conf 0.492, tại (907,492) |
| PyTorch `.pt` 640 | 2 | 0.490 | (645, 337) | Bbox phụ conf 0.420, tại (907,493) |

Ở bài đối chiếu lại `.pt` 640 với NCNN 640 **trên cùng ảnh khác bước trên**, kết quả `.pt`: conf 0.52/0.44; NCNN: conf 0.46/0.41; hai vùng khoanh gần trùng nhau. Lưu tại `runs/model-smoke/compare/verify_pytorch.jpg` và `verify_ncnn.jpg`. Không lấy confidence của một ảnh làm phần trăm accuracy.

Tâm bbox **là tọa độ trên ảnh, không phải tọa độ 3D**; đây là đầu vào bắt buộc cho hình học định vị. Tìm 3D phải biết intrinsics, pose, khoảng cách hoặc mặt phẳng mục tiêu; camera độc lập khỏi Pan–Tilt nên không đổi trực tiếp `cx` ra góc servo.

### 2.4. Benchmark CPU Pi 5 — ĐÃ CHẠY

Lệnh dùng script của repo, đo cả tiền xử lý + inference + hậu xử lý qua `model.predict` (chưa tính RTSP):

```bash
cd ~/nt532/pi
uv run --no-sync python scripts/bench_detect.py \
  --weights ../models/fire-n.pt ../models/fire-n_ncnn_model \
  --source ../runs/model-smoke/bench-input \
  --imgsz 640 --n 30
```

| Model | `imgsz` | Mean (ms) | Median (ms) | P95 (ms) | FPS suy luận |
|---|---:|---:|---:|---:|---:|
| `.pt` CPU | 320 | 64.4 | 64.1 | 66.8 | 15.5 |
| `.pt` CPU | 480 | 125.5 | 125.3 | 127.0 | 8.0 |
| `.pt` CPU | 640 | 219.2 | 219.2 | 219.8 | 4.6 |
| NCNN CPU (xuất 640) | 640 | **63.7** | **63.7** | **64.0** | **15.7** |

Hai lượt `.pt` 640 trước đó trên `images/input.jpg` cho mean 217.64 và 220.45 ms. CSV tại `runs/bench/bench-NT532-R12-TH1-01.csv`. Các phép đo được thực hiện với **1 ảnh, 30 lượt** sau warm-up; khác biệt ảnh làm kết quả có thể thay đổi. NCNN nhanh hơn khoảng 3.44 lần so với `.pt` ở 640; chưa có phép đo độ chính xác nhiều mẫu hay FPS của cả luồng. **Bản NCNN đã xuất 640 phải benchmark ở 640; không tự chạy NCNN 320 nếu chưa xuất mới.**

### 2.5. Những gì CHƯA nghiệm thu và quyết định model

1. **Chưa chốt mặc định:** người dùng nói *chưa chuyển model về 320 px*. Không giả định đã chuyển sang NCNN 640. Đọc `grep -A5 '^vision:' ../config/site.yaml` rồi mới lựa chọn; giữ nguyên config hiện tại trong lúc đối chiếu.
2. Chạy NCNN trên **20–30 frame RTSP headless** và lưu kết quả, xác nhận không `FATAL`/treo lúc đóng stream.
3. Thu **ít nhất 10 ảnh dương tính + 10 ảnh âm tính**, gồm vài khoảng cách/ánh sáng/vùng lửa lớn-nhỏ; chạy cùng mẫu ở `.pt 320`, `.pt 480`, NCNN 640. Đếm phát hiện đúng, bỏ sót, báo nhầm; chưa đánh giá mAP nếu không có nhãn.
4. Sau khi có bảng so sánh chất lượng và tốc độ mới quyết định giữ `.pt` 320, `.pt` 480 hoặc NCNN 640. **Một mục tiêu lửa duy nhất** là mục tiêu MVP, không phát triển đa mục tiêu thời điểm này.

**Checkpoint Mốc 2:** baseline model + camera + suy luận + benchmark **đã đạt**. Chất lượng nhận diện trên nhiều ảnh, NCNN RTSP liên tục và lựa chọn runtime **chưa đạt**, không ghi là toàn bộ Mốc 2 hoàn tất.

### 2.6. Handoff cho cuộc trò chuyện tiếp theo

Bắt đầu bằng kiểm tra read-only trên Pi:

```bash
cd ~/nt532/pi
grep -A5 '^vision:' ../config/site.yaml
grep -A5 '  stream:' ../config/site.yaml
uv run --no-sync python -c 'import ultralytics, torch, ncnn; print("AI OK", torch.__version__)'
git -C ~/nt532 status --short
```

**Trình tự được chốt:** xác nhận config (không đổi ngay) → test NCNN trực tiếp 20 frame → bộ mẫu dương/âm tối thiểu cho **một** đám cháy → so sánh/giữ model chính → tính hình học camera–A/B. Không quay lại bước tải model/cài MediaMTX. ToF tùy chọn chỉ đo hỗ trợ; không chặn Mốc 2.

## Mốc 3 — Kiểm tra độc lập phần cứng A/B (sau AI)

- Giữ các bài test Serial từ `cumA.ino` và `cumB.ino`, nguồn, relay, servo theo phần baseline; kiểm tra `STATUS` trước khi thử `HOME`, `PAN`, `TILT`, `GAS`/`DHT`.
- **Không thử `TILT_MAX=5000 us` của B** khi chưa xử lý sai lệch với hiệu chuẩn 2500 us; không cấp 5 V thẳng vào GPIO gas; không bật laser nếu chưa có vùng thử an toàn.
- Checkpoint: cả hai cụm độc lập hoạt động và có log thực tế, không suy luận từ code.

## Mốc 4 — Hiệu chuẩn camera, định vị và Pan–Tilt

- Dùng model đã chạy ở Mốc 2; hiệu chuẩn intrinsics bằng chính ảnh RTSP (`calibrate_camera.py`) và commissioning AprilTag trên từng bố trí (`commission.py`). Ghi pixel `(cx,cy)` của một bbox chính cùng độ tin cậy. Đo khoảng cách camera→A/B và A↔B bằng thước; ToF có thể đối chiếu thêm, không thay pose.
- Hiệu chỉnh hướng pan/tilt riêng từng cụm vì camera độc lập; kết hợp tag tham chiếu, vị trí tương đối, độ sâu và phép thử sai số ngắm. Không dùng tâm ảnh làm góc servo trực tiếp.
- Kiểm tra một mục tiêu trong ít nhất hai bố trí/môi trường; ghép cảnh báo node A hoặc B với cùng một mục tiêu đang xác minh, bỏ frame cũ. Nhiều mục tiêu độc lập là mở rộng, không tự kích laser khi chưa phân giải được mục tiêu.
- Checkpoint: khi đổi vị trí và thực hiện lại hiệu chuẩn, hệ thống định vị và ngắm được mục tiêu mô phỏng với sai số đã ghi nhận.

## Mốc 5 — Tích hợp Thread/CoAP, Edge AI và đo đạc

- Tích hợp A/B → Thread/CoAP → Pi xác minh bằng model → điều khiển đúng cụm → Pan–Tilt/relay/laser → log.
- Đo độ trễ camera đúng cách (không dùng riêng quan sát RealVNC), đo PDR, tỷ lệ phát hiện, thời gian xử lý, sai số ngắm; giữ an toàn OFF khi thiếu xác minh hoặc mất kết nối.
- Checkpoint: demo ít nhất hai bố trí độc lập, đủ log và có kết quả so sánh với tiêu chí nhóm.

### Hiệu chỉnh camera cố định → Pan–Tilt tách rời

- Camera đứng yên nên bounding box có tọa độ pixel `(cx,cy)` ổn định trong một lượt và **rất quan trọng để định vị**. Nhưng Pan–Tilt đặt ở vị trí khác camera: không dùng trực tiếp quy tắc `error_x = cx - W/2` như camera gắn chung đầu servo. Pixel là tọa độ trên ảnh; cần độ sâu/mặt phẳng và pose để lấy 3D.
- Khi bắt đầu bố trí/môi trường mới, hiệu chỉnh thực nghiệm tương ứng giữa vị trí mục tiêu trên ảnh và lệnh Pan/Tilt của từng cụm; có thể dùng marker hoặc quan sát điểm laser trong khung hình để tinh chỉnh vòng kín **nếu điểm laser đủ rõ**. Ngoài trời có thể khó phát hiện điểm laser.
- Đừng giả định mô hình ánh xạ dùng được giữa các độ sâu/địa điểm nếu chưa kiểm chứng. Giữ relay ở OFF khi không có mục tiêu được xác minh và trong khi hiệu chỉnh an toàn.
- Ghi log mỗi lượt: `trial_id`, môi trường, kích cỡ ảnh, frame_id, timestamp, cụm cảnh báo, `(cx,cy)`, lệnh servo, sai số và trạng thái laser.

### Phương án hỗ trợ đo khoảng cách: Samsung DepthVision / ToF (tùy chọn, CHƯA THỬ)

**Thông tin từ tài liệu hãng:** Samsung xác nhận **Galaxy S20+ và Galaxy S20 Ultra** có camera DepthVision/ToF; Galaxy S20 tiêu chuẩn không có ToF. Samsung Quick Measure có các chức năng đo khoảng cách từ máy đến điểm ngắm, chiều dài/diện tích và đo đoạn giữa hai điểm do người dùng chọn (tùy máy/app). Google liệt kê S20+ và S20 Ultra 5G hỗ trợ ARCore Depth API và phần cứng ToF.

**Cách đưa vào NT532, không làm phát sinh phụ thuộc cho MVP:**

1. Xác minh model/firmware điện thoại của nhóm và tính sẵn có của **Quick Measure**. Trong từng `trial_id`, đánh dấu vị trí camera, đế A và đế B; dùng ứng dụng đo `d_cam_A`, `d_cam_B`, thử đo đoạn `d_A_B` nếu hỗ trợ; **đối chiếu với thước dây** (ít nhất 5 lượt) và lưu sai số. Đây là đo hỗ trợ lúc **bố trí/hiệu chuẩn**, không phải tự động dẫn đường laser.
2. **Không suy ra ngay `d_A_B` từ chỉ hai khoảng cách ToF camera→A và camera→B.** Cần hướng/góc giữa hai tia hoặc hệ tọa độ 3D: `d_AB^2 = d_CA^2 + d_CB^2 - 2*d_CA*d_CB*cos(theta)` nếu `theta` được biết và các điểm đo khớp. Đo thước là baseline bắt buộc.
3. Nếu cần độ sâu mục tiêu `fire`: khảo sát **ARCore Depth API** trên máy thật, kiểm tra liệu có lấy được depth map tại pixel/vùng bbox và độ lệch giữa camera RGB với ToF; phải hiệu chỉnh extrinsics, timestamp và độ tin cậy. ARCore có thể kết hợp ToF với ước lượng từ chuyển động, không phải ảnh depth nguyên bản bắt buộc.
4. Luồng **LOLS IRL → RTMP → MediaMTX → RTSP hiện là RGB H.264**, không tự truyền depth/ToF. Muốn có depth trực tuyến đồng bộ cần chứng minh truy cập API và xây dựng thêm kênh/mã Android riêng; chưa làm. Nếu thất bại, bỏ nhánh depth trực tuyến, dùng ChArUco + AprilTag + mặt phẳng + số đo thước để hoàn thành MVP.
5. Chỉ xem ToF là hữu ích khi sai số so với thước có thể chấp nhận với sa bàn, ổn định ở ánh sáng/khoảng cách dùng thật và không cản trở phát RGB. Ánh sáng mạnh, bề mặt phản xạ/hấp thụ và phạm vi đo có thể ảnh hưởng; cần đo thực tế, không giả định độ chính xác.

Log thử nghiệm gợi ý: `trial_id`, `phone_model`, `method`, `cam_to_A_m`, `cam_to_B_m`, `A_to_B_m`, `tape_truth_m`, `abs_error_m`, `notes`, `timestamp`; nếu có depth: `frame_id`, `depth_timestamp`, `rgb_timestamp`, `depth_m`, `valid_mask`.

**Nguồn tra cứu (không phải bằng chứng đã chạy trên máy nhóm):** [Samsung thông số S20](https://www.samsung.com/vn/support/mobile-devices/check-out-the-new-camera-functions-of-galaxy-s20-plus-s20-ultra/); [Quick Measure chính thức](https://galaxystore.samsung.com/detail/com.samsung.android.ruler?langCd=vi); [ARCore Depth](https://developers.google.com/ar/develop/depth); [Danh sách ARCore hỗ trợ ToF](https://developers.google.com/ar/devices); [Android Camera2 Depth](https://developer.android.com/reference/android/hardware/camera2/CameraMetadata#REQUEST_AVAILABLE_CAPABILITIES_DEPTH_OUTPUT).

## Mốc tích hợp mạng Thread (triển khai song song với sa bàn)

1. Xác minh board DevKitM và chip Supermini; chuẩn bị cáp USB dữ liệu và đường mạng IP cho Raspberry Pi.
2. Flash firmware `openthread/ot_rcp` qua ESP-IDF lên DevKitM; nối USB-UART vào Pi và xác nhận cổng serial tương ứng.
3. Dựng `ot-br-posix` / OTBR trên Pi, kiểm tra trạng thái RCP và tạo/join mạng Thread.
4. Nạp firmware OpenThread Node thử nghiệm cho Supermini A/B, xác nhận cả hai tham gia mạng và gửi/nhận gói CoAP về Pi.
5. Sau đó mới nối cảm biến gas/DHT11 và chức năng Pan–Tilt/Relay của `cumA.ino`/`cumB.ino` vào ứng dụng Thread; không thay đổi bản code test gốc.

**Đạt khi:** Raspberry Pi nhận riêng dữ liệu A/B qua Thread/CoAP, đồng thời đọc được stream camera điện thoại qua RTSP từ MediaMTX. Các kênh chạy độc lập; test điều khiển qua Thread chỉ được xác nhận sau khi có firmware mạng, không suy ra từ các lệnh Serial cục bộ.

## Thứ tự triển khai và tiêu chí nghiệm thu

1. **Chốt setup camera điện thoại cố định:** dựng ít nhất hai bố trí thực tế, camera quan sát đủ cả A bên phải/B bên trái; kiểm tra mirror và ghi `trial_id`. **Đạt khi:** đổi bố trí/môi trường mà không phải sửa quy ước A/B.
2. **Xác minh camera → RTMP → MediaMTX → RTSP → Pi:** Android/LOLS IRL phát video; Pi đọc và lưu được frame bằng `StreamCapture`; ghi lại FPS, sự cố và ảnh mẫu. **Đạt khi:** stream ổn định, không điều khiển bằng frame lỗi thời. Đo `camera.stream.latency_s` chính thức khi node/laser sẵn sàng.
3. **Mốc 2 — BASELINE AI ĐÃ ĐẠT:** Pi đã có `.pt`/NCNN, suy luận được trên RTSP, có bbox/confidence/tâm pixel và benchmark CSV. **Còn:** NCNN RTSP liên tục, test nhiều ảnh dương/âm, chọn model/config cuối. Không cài lại từ đầu.
4. **Mốc 3 — Test độc lập phần cứng từ code gốc:** xác nhận mỗi cụm qua Serial, cảm biến, relay, Pan/Tilt; kiểm tra an toàn servo và điện áp cảm biến. **Đạt khi:** cả A/B điều khiển riêng đúng và có log test.
5. **Mốc 4 — Hiệu chỉnh ảnh → laser riêng cho từng cụm:** hiệu chuẩn camera/AprilTag, khảo sát Pan/Tilt và kiểm chứng sai số ở các vị trí/độ sâu. **Đạt khi:** laser chỉ đúng vùng mô phỏng sau khi đổi bố trí, camera đứng yên trong lượt.
6. **Mốc 5 — Tích hợp và đo đạc:** cảnh báo A/B → AI xác minh → điều khiển đúng cụm → ghi log. **Đạt khi:** không nhầm A/B, báo động giả không tự kích relay, có số liệu độ trễ và sai số.

## Rủi ro cần kiểm tra sớm

- **Camera tách khỏi Pan–Tilt:** không thể coi tâm ảnh là điểm ngắm laser; phải có phép hiệu chỉnh ảnh→servo riêng theo bố trí và độ sâu, hoặc cách đo lệch thực tế bằng hình ảnh.
- **Thay môi trường:** góc đặt camera, ánh sáng, khoảng cách, nền ảnh và độ trễ mạng thay đổi; cần hiệu chỉnh lại và kiểm tra khả năng tổng quát của YOLO mỗi khi đổi bối cảnh.
- **Camera preview bị mirror:** thứ tự trái/phải trên ảnh đảo ngược, dễ điều khiển nhầm cụm; xác minh bằng vật đánh dấu A/B trước mỗi buổi.
- **Video RTMP/RTSP bị trễ:** ảnh qua RealVNC từng xuất hiện muộn khoảng 3 giây và thêm 2 giây mới rõ; chưa đo trễ camera→Pi. Khi chuẩn bị điều khiển tự động, khóa AF/AE nếu có, tắt EIS, thử bitrate hợp lý, đo `camera.stream.latency_s` và chỉ xử lý frame mới. Không dùng độ trễ quan sát gián tiếp làm dữ liệu hiệu chuẩn.
- **Mạng quán cà phê/ngoài trời:** captive portal, client isolation hoặc thay đổi IP Pi có thể làm mất RTMP; dùng hotspot/router riêng, không công khai các cổng `1935`/`8554` trên Internet.
- **Servo Cụm B:** `TILT_MAX = 5000 us` theo firmware đang tải, vượt hiệu chỉnh trước đây 2500 us; chưa thử giá trị này khi chưa xác minh cơ khí.
- **Nguồn và an toàn:** GPIO ESP32-H2 mức 3.3 V; giảm mức AO/DO nếu module gas cấp 5 V, dùng nguồn servo riêng và không chiếu laser vào người/camera.

## Cần chốt khi thực hiện

- **Mốc 2 ưu tiên:** model `.pt` và NCNN đã có trên Pi, môi trường CPU và benchmark đã chạy. Cần xác nhận cấu hình `vision.weights`/`imgsz` hiện tại, test NCNN trực tiếp 20 frame, thu bộ dương/âm nhỏ rồi quyết định. Người dùng **chưa đổi sang 320 px**, không ghi nhận như đã chốt. Phạm vi MVP một đám cháy mô phỏng.

- Địa chỉ IP Pi, ứng dụng LOLS IRL, URL `rtmp://<IP_PI>:1935/cam/stream`, độ phân giải/bitrate hoạt động ổn định ở từng môi trường và giá trị `camera.stream.latency_s` sau đo.
- Cách hiệu chỉnh điểm ảnh `(cx,cy)` → vị trí 3D → Pan/Tilt cho **từng cụm**: marker, mặt phẳng và bảng điểm mẫu; kiểm tra ToF/Quick Measure như nguồn đo khoảng cách hỗ trợ nếu thiết bị phù hợp, không mặc định RTSP mang depth.
- Khi tích hợp, sử dụng hợp đồng **Thread/CoAP `/aim`, `/stop`, `/hb`** của firmware trong repo; kiểm thử độc lập hai file `.ino` Serial vẫn là bước riêng. Xác nhận A/B tương ứng với node ID `s1`/`s2` trước khi chạy lệnh thật.
