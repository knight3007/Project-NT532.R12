# NT532 — Runbook chạy hệ thống đầu cuối

Cập nhật 2026-11-14. Tài liệu này gom **mọi bước cài đặt và lệnh** để dựng lại và chạy trạm từ đầu: máy Windows dev → Raspberry Pi 5 → OTBR/Thread → node A/B → camera → Dynamic Mapping → hiệu chuẩn → trạm `vision-aim-only`. Mọi lệnh và cờ dưới đây đều có trong code hiện tại; chi tiết sâu hơn nằm ở các tài liệu được dẫn.

## 0. Đọc trước: chế độ nào được phép chạy

| Đường chạy | Dùng khi | Ghi chú an toàn |
|---|---|---|
| `run_station.py --source sim` | Không có phần cứng; demo và test | Hoàn toàn ảo |
| `run_station.py --profile vision-aim-only` | **Phần cứng thật với node A/B integrated** | Chỉ ngắm: không bơm, không quét selfcal, chốt STOP |
| `scripts/aim_point.py` | Ngắm tay từng điểm, đo sai số | Mặc định dry-run; `--execute` mới gửi lệnh |
| `run_station.py` mặc định (`--profile station`) và `nt532-station.service` | **KHÔNG dùng với A/B** | Profile cũ gửi `/aim`, `/fire`, có pha bơm và quét selfcal; firmware A/B không có các endpoint đó |

Nguyên tắc chung:
- Nguồn bơm luôn rút ra. Relay GPIO10 chỉ điều khiển đèn/laser ngắm.
- Không flash tự động, không `erase-flash`, không nạp lại bootloader hay partition table của node.
- Gốc tọa độ vận hành là `tag_2_table` (ID 2). Gốc ID 22 hiện đang BLOCKED khi tag nằm ở rìa ảnh (xem `reports/nt532_dynamic_mapping_v2_geometry_audit.md`).
- Thiếu hiệu chuẩn đo thật thì trạm dừng ở `CALIBRATION_REQUIRED` và không gửi lệnh nào tới node. Đây là hành vi đúng.

Trạng thái hiện tại: **STATION SOFTWARE E2E PASS** (node giả), **PHYSICAL HARDWARE E2E PENDING**.

## 1. Thành phần

```
Điện thoại (RTMP) ──► mediamtx trên Pi (:1935) ──► RTSP rtsp://127.0.0.1:8554/cam/stream
                                                          │
Pi 5: run_station.py ── Vision (YOLO + AprilTag + Dynamic Mapping tag_2_table)
   │        └── dashboard :8080 (GET /api/health, POST /api/stop)
   │
   └── CoAP qua OTBR (wpan0) ── H2 DevKit (RCP, USB)
                                   ├── Node A  s1  a-integrated-0.1.2  (MQ-2, pan/tilt, relay)
                                   └── Node B  s2  b-integrated-0.2.5  (DHT11, pan/tilt, relay)
```

Firmware phát hành (build trong worktree `build-output/node-ab-aim-hold-worktree`):

| Node | File | SHA-256 |
|---|---|---|
| A | `build-output/node-a-integrated-0.1.2/nt532_node_a_integrated.bin` | `87231B61725C952F11CAD814C0B251A980F21BAFA480FD00FA1CB184D5D2E8F6` |
| B | `build-output/node-b-integrated-0.2.5/nt532_node_b_integrated.bin` | `5E9C607FFD9EF4C7229CC15516E9A16A76230E4CE7628843FCA04EC57F861230` |

Cả hai: HOME Pan 1500 µs / Tilt 1700 µs; lệnh hợp lệ Pan 500–2500, Tilt 600–2500; ramp tối đa 20 µs mỗi 20 ms; watchdog heartbeat 1500 ms.

## 2. Máy Windows (dev): kiểm tra code trước khi mang lên Pi

```powershell
cd "D:\IOT Hien Dai\Project\Project-NT532.R12\pi"
uv sync
uv run --no-sync ruff check src tests scripts
uv run --no-sync python scripts/commission.py --source sim
uv run --no-sync pytest -q --ignore=tests/test_decider.py --ignore=tests/test_jev.py --basetemp "$env:TEMP\nt532-pytest"
```

- `--basetemp` cần trên Windows vì thư mục `pytest-of-<user>` mặc định có thể bị từ chối quyền.
- `test_fakenode.py`/`test_info.py` cần `make` và thư viện Linux; trên Windows chúng skip hoặc lỗi môi trường, CI Linux chạy đủ. `test_aim_point.py::…[coap]` đang fail sẵn trên Windows (cả trên `main`).

Host test firmware A/B (MSYS2 gcc tại `C:\msys64\ucrt64\bin\gcc.exe`):

```powershell
powershell -ExecutionPolicy Bypass -File firmware/node-a-integrated/test/run_host_tests.ps1
```

Kỳ vọng: 11 dòng `PASS`.

## 3. Raspberry Pi 5: cài lại từ đầu

Dùng khi Pi phải cài lại, ví dụ sau lỗi bộ nhớ hay thẻ nhớ.

### 3.1 Kiểm tra bộ nhớ trước

```bash
df -h /
free -h
du -sh ~/nt532/runs ~/nt532-* 2>/dev/null
sudo journalctl --disk-usage
```

- `runs/` (ảnh 15 khung, log trạm), các worktree/bundle thử nghiệm và journal là các chỗ hay đầy. Chép evidence cần giữ về máy dev trước khi xóa.
- Thu gọn journal: `sudo journalctl --vacuum-size=200M`.
- Nếu `dmesg` báo lỗi I/O trên `mmcblk0`, thẻ nhớ đang hỏng: ghi lại Raspberry Pi OS Bookworm 64-bit lên thẻ mới rồi làm tiếp 3.2.

### 3.2 Hệ điều hành và mã nguồn

Raspberry Pi OS Bookworm 64-bit, có quạt tản nhiệt.

```bash
git clone https://github.com/knight3007/Project-NT532.R12 ~/nt532
cd ~/nt532
bash pi/deploy/setup_pi.sh --yolo
```

`setup_pi.sh` cài apt (ffmpeg, v4l-utils, coap-client…), uv, `uv sync --extra yolo`, `ncnn` và mediamtx. Chạy bằng user thường, không sudo. **Không dùng `--install-services`**: cờ này bật `nt532-station.service`, vốn chạy profile cũ, không hợp với A/B. Muốn có mediamtx dạng service thì cài riêng theo mục 5.

Từ đây mọi lệnh Python trên Pi chạy bằng `uv run --no-sync` từ `~/nt532/pi`, vì `ncnn` cài ngoài `pyproject.toml`.

### 3.3 Chép file không có trong git

Từ máy dev (PowerShell), thay IP của Pi:

```powershell
$PiIp = "192.168.137.30"
scp "D:\IOT Hien Dai\Project\Project-NT532.R12\models\fire-n.pt" "hunn@${PiIp}:/home/hunn/nt532/models/"
scp "D:\IOT Hien Dai\Project\Project-NT532.R12\calibration\camera.yaml" "hunn@${PiIp}:/home/hunn/nt532/calibration/"
```

- `models/fire-n.pt`: trọng số YOLO.
- `calibration/camera.yaml`: intrinsics **của đúng stream 1280×720 đang dùng**. Nếu chưa có hoặc đã đổi điện thoại/zoom, làm lại theo mục 5.3.
- `calibration/manual_aim_measured.yaml`: tạo ở mục 7.

Kiểm tra:

```bash
cd ~/nt532/pi
uv run --no-sync pytest -q --ignore=tests/test_decider.py --ignore=tests/test_jev.py
```

## 4. Thread: RCP và OTBR

Chi tiết: `firmware/rcp/README.md`.

1. Nạp `ot_rcp` vào H2 DevKit, cắm USB vào Pi, tìm cổng:
   ```bash
   ls -l /dev/serial/by-id/
   ```
2. Cài ot-br-posix:
   ```bash
   git clone --recursive --depth=1 https://github.com/openthread/ot-br-posix
   cd ot-br-posix && INFRA_IF_NAME=wlan0 ./script/setup
   sudoedit /etc/default/otbr-agent     # sửa spinel+hdlc+uart:///dev/serial/by-id/...?uart-baudrate=460800
   sudo systemctl restart otbr-agent
   sudo ot-ctl state
   ```
3. Mạng Thread:
   - Pi cài lại nhưng node A/B vẫn giữ dataset cũ trong NVS: khôi phục **đúng** dataset cũ trên Pi bằng `sudo ot-ctl dataset set active <TLV hex đã lưu>`, rồi `sudo ot-ctl ifconfig up` và `sudo ot-ctl thread start`. Node sẽ tự gắn vào mà không phải nạp lại.
   - Tạo mạng mới bằng `dataset init new` thì phải nạp dataset mới vào từng node qua USB serial (`TLV:<hex>` rồi `START`, xem README của node).
   - Không ghi Network Key/TLV vào tài liệu, log hay lệnh dán chung.
4. Kiểm tra: `sudo ot-ctl state` phải là `leader`, và `sudo ot-ctl neighbor table` phải thấy hai node.

## 5. Camera điện thoại

Chi tiết: `docs/camera-dien-thoai.md`.

1. mediamtx (nếu `setup_pi.sh` chưa chạy nó):
   ```bash
   ~/mediamtx/mediamtx ~/nt532/pi/deploy/mediamtx.yml
   ```
   Chạy nền: dùng `pi/deploy/mediamtx.service` (sửa `User`/đường dẫn), rồi `sudo systemctl enable --now mediamtx`.
2. Ứng dụng RTMP trên điện thoại đẩy lên `rtmp://<IP Pi>:1935/cam/stream`, 1280×720. Khóa zoom, tắt chống rung, khóa lấy nét nếu được.
3. Intrinsics trên chính stream (bảng `docs/print/charuco-a4.pdf` dán bìa phẳng, 20–30 ảnh, có ảnh **sát các góc khung**):
   ```bash
   cd ~/nt532/pi
   uv run --no-sync python scripts/calibrate_camera.py --capture
   uv run --no-sync python scripts/calibrate_camera.py
   ```
   Ảnh phủ góc khung là bắt buộc: audit pos01 cho thấy tag ở rìa ảnh rất nhạy với mô hình méo.
4. Kiểm tra stream: `ffplay rtsp://<IP Pi>:8554/cam/stream` từ laptop, hoặc `uv run --no-sync python scripts/live_tags.py --source rtsp://127.0.0.1:8554/cam/stream` nếu Pi có màn hình.
5. Đặt `camera.stream.url: "rtsp://127.0.0.1:8554/cam/stream"` trong `config/site.yaml` (hoặc truyền `--source` cho từng lệnh).

## 6. Node A/B

### 6.1 Flash firmware (người vận hành, chỉ phân vùng app)

Trên Windows, trong ESP-IDF v5.4.4 shell, từ worktree có thư mục build:

```powershell
Get-FileHash -Algorithm SHA256 build-output\node-b-integrated-0.2.5\nt532_node_b_integrated.bin
cd firmware/node-a-integrated
idf.py -B ../../build-output/node-a-integrated-0.1.2 -p COM_A app-flash
cd ../node-b-integrated
idf.py -B ../../build-output/node-b-integrated-0.2.5 -p COM_B app-flash
```

- Xác định COM từng board trong Device Manager. A vào A, B vào B; mỗi lần một board.
- `app-flash` giữ NVS và dataset Thread. Không dùng `flash` hay `erase-flash`.

### 6.2 Địa chỉ node

Firmware integrated **không đăng ký SRP**, nên phải khai địa chỉ tĩnh. Lấy mesh-local EID của từng node từ log USB serial lúc boot (PuTTY/`idf.py monitor`), rồi kiểm tra:

```bash
ping -6 -c 3 <EID_A>
ping -6 -c 3 <EID_B>
```

Ghi vào bản sao cục bộ của `config/site.yaml`, mục `network.nodes`: `s1: "<EID_A>"`, `s2: "<EID_B>"`. Muốn tách khỏi git thì chép ra `~/nt532-site.yaml` và `export NT532_CONFIG=~/nt532-site.yaml`.

### 6.3 Smoke test (chỉ đọc)

```bash
for N in <EID_A> <EID_B>; do
  coap-client-notls -m get "coap://[$N]/info"
  coap-client-notls -m get "coap://[$N]/status"
  coap-client-notls -m get "coap://[$N]/pantilt"
  coap-client-notls -m get "coap://[$N]/laser"
done
coap-client-notls -m get "coap://[<EID_A>]/mq2"
```

Kỳ vọng: A báo `a-integrated-0.1.2`, B báo `b-integrated-0.2.5`; PWM và relay OFF. Thử ARM/HOME/±40 µs có heartbeat theo `docs/nt532-node-ab-final-and-aim-hold-2026-11-14.md`, mục *Post-flash smoke checks*.

## 7. Dynamic Mapping và hiệu chuẩn ngắm

### 7.1 Bố trí

- 4 tag tham chiếu ID 0–3 nằm phẳng trên mặt bàn (ID 2 là anchor). Tag node: 21 (A) và 22 (B) dán phẳng, giấy không thò khỏi bìa.
- Tốt nhất mọi tag nằm trong vùng giữa ảnh (r/rmax ≲ 0,6) và các node ở trong hoặc sát hull của ID 0–3.
- Hình lửa đặt **nằm phẳng** trên mặt bàn, không dùng màn hình điện thoại dựng đứng.

### 7.2 Kiểm tra mapping (15 khung, không chạm node)

```bash
cd ~/nt532/pi
uv run --no-sync python scripts/dynamic_map.py --source rtsp://127.0.0.1:8554/cam/stream --frames 15 \
  --world-origin 2 --yolo --device cpu --json ../runs/map-check.json --debug ../runs/map-check.png
```

Kỳ vọng: `"status": "PASS"`, đủ references 0–3 và nodes s1/s2, `metric_error_m` < 0,012. Muốn lưu một bộ evidence bất biến thì dùng `scripts/origin22_live.py capture`/`evaluate` (xem `reports/nt532_origin22_live_integration.md`).

### 7.3 Hiệu chuẩn đo riêng cho từng node

```bash
cp ../calibration/manual_aim_measured.example.yaml ../calibration/manual_aim_measured.yaml
nano ../calibration/manual_aim_measured.yaml
```

Điền **bằng số đo thật** cho cả s1 và s2: tịnh tiến tag→tâm quay, offset đầu phát, hướng HOME trong `tag_2_table`, dấu/gain, giới hạn góc, µs/độ quanh 1500/1700. Không dùng `actuator.pivot_offset` chung hay hệ số tạm. Còn ô `null` thì mọi công cụ báo `CALIBRATION_REQUIRED`.

### 7.4 Ngắm tay thử từng node

Dry-run (không gửi lệnh tới node):

```bash
uv run --no-sync python scripts/aim_point.py --source rtsp://127.0.0.1:8554/cam/stream \
  --node s1 --surface floor --frame tag_2_table --x 0.30 --y 0.20 --hold-seconds 30 \
  --calibration ../calibration/manual_aim_measured.yaml
```

Khi plan hợp lý, dây relay đã kiểm, đường quay trống: thêm `--execute`. Tool hỏi `OK` ngay trước khi bật relay; Ctrl+C luôn dẫn tới OFF/STOP. Lặp với `--node s2`. Đo nhiều điểm bằng `--points points.csv --execute`, mỗi node ít nhất 5 điểm.

## 8. Chạy trạm `vision-aim-only`

```bash
cd ~/nt532/pi
uv run --no-sync python scripts/run_station.py --profile vision-aim-only \
  --source rtsp://127.0.0.1:8554/cam/stream \
  --aim-calibration ../calibration/manual_aim_measured.yaml \
  --aim-nodes s1,s2 --ok-timeout 120 --verify-light-s 3 --port 8080
```

- `--source` phải là camera thật (số webcam hoặc URL stream). Profile này từ chối `sim`, file ảnh, `--decider`, `--mqtt`, `--link`, `--detector`, `--control-url` và các tùy chọn sa bàn ảo.
- Chỉ dùng một node: `--aim-nodes s1`.

Trình tự khởi động: `START → CHECK_CAMERA` (preflight hiệu chuẩn, một lô 15 khung Dynamic Mapping) `→ CONNECT_NODES` (`GET /info`: s1=A, s2=B) `→ HEARTBEAT` (400 ms) `→ ARM → HOME 1500/1700 → CALIBRATION/WAIT_OK` lần lượt từng node `→ READY`.

Lệnh gõ tại console của tiến trình:

| Lệnh | Khi nào | Tác dụng |
|---|---|---|
| `OK s1`, rồi `OK s2` | CALIBRATION, **sau khi** thấy đèn của node đó sáng và đã chỉnh cụm vòi thẳng tag | Tắt đèn, kiểm tra OFF, sang node kế; `OK` trần hay OK gõ trước khi đèn sáng bị bỏ qua |
| `VERIFY` | READY, sau một lần ngắm | Bật đèn node vừa ngắm tối đa `--verify-light-s` giây, chụp OFF/ON, tắt; đo sai số nếu thấy vết, không thì `NOT MEASURABLE` |
| `STOP` | Bất kỳ lúc nào | `/laser OFF` + `/stop` mọi node, chốt; bỏ mọi mục tiêu |
| `RECOVER` | Sau STOP | Chạy lại toàn bộ khởi động, gồm mapping, danh tính và WAIT_OK |

HTTP (dashboard cổng 8080):

```bash
curl -s http://127.0.0.1:8080/api/health
curl -s -X POST http://127.0.0.1:8080/api/stop
```

`/api/stop` là lệnh HTTP duy nhất; OK và RECOVER chỉ nhận ở console. Log ngắm ghi vào `runs/station/aim-only-<thời điểm>.jsonl`.

Ở READY: mục tiêu YOLO phải đứng yên trong 2 cm qua 3 khung liên tiếp; node gần nhất có plan hợp lệ nhận một `POST /pantilt`; mục tiêu đứng yên không bị ngắm lại. Mapping chạy lại mỗi 20 s; pose cũ hơn 60 s bị từ chối.

Trạm tự chốt STOP khi: mất heartbeat hoặc Thread, thiếu địa chỉ node, sai danh tính, mất camera, mapping hỏng, pose cũ, servo quá giờ, hết `--ok-timeout`, Ctrl+C. Nếu log báo `OFF/STOP NOT VERIFIED; cut physical laser power`, phải ngắt nguồn đèn bằng tay.

## 9. Chạy không phần cứng (sa bàn ảo)

```bash
cd pi
uv run --no-sync python scripts/run_station.py --source sim          # http://localhost:8080/
uv run --no-sync python scripts/demo_station.py --step               # tám cảnh có chấm điểm
```

Homelab control (tùy chọn, chỉ với profile thường hoặc sim): xem `docs/homelab-deploy.md`, ví dụ `NT532_PI_TOKEN=<token> uv run --no-sync python scripts/run_station.py --source sim --control-url https://<domain>`.

## 10. Đo sai số vật lý bằng thước

Áp dụng khi mapping PASS. Lưu ý `physical_accuracy.py` hiện nhận output hệ `tag_22_table`; với `tag_2_table`, dùng phần đo `VERIFY`/`aim_point.py` (sai số tính từ vết laser trên camera hoặc tọa độ thước nhập sau khi đèn tắt). Quy trình đo và mẫu CSV: `reports/nt532_dynamic_mapping_v2_geometry_audit.md`, mục 13.

## 11. Tắt hệ thống

1. Gõ `STOP` (hoặc `POST /api/stop`), rồi Ctrl+C tiến trình trạm; chờ log OFF/STOP đã kiểm tra.
2. Rút nguồn servo/đèn của node.
3. `sudo systemctl stop mediamtx` nếu không dùng tiếp.
4. Chép `runs/` cần giữ về máy dev; dọn bớt để thẻ nhớ không đầy (mục 3.1).

## 12. Sự cố thường gặp

| Triệu chứng | Kiểm tra |
|---|---|
| `CALIBRATION_REQUIRED` | `calibration/manual_aim_measured.yaml` còn `null` hoặc thiếu `camera.yaml` |
| Mapping FAIL / thiếu tag | Ánh sáng, che khuất, tag quá nhỏ (<12 px), camera bị xê dịch giữa các khung |
| `ID22_SQUARE_GEOMETRY` | Tag ID 22 ở rìa ảnh hoặc giấy cong; dời vào gần tâm, ép phẳng; vận hành bằng `tag_2_table` |
| `CONNECT_NODES` lỗi danh tính | `network.nodes` đảo s1/s2, hoặc node chưa flash đúng phiên bản |
| Node không trả lời | `sudo ot-ctl state`, `ping -6` EID, node còn dataset trong NVS |
| Không có stream | Điện thoại còn đẩy RTMP; `systemctl status mediamtx`; `ffplay` từ laptop |
| Pi chậm hoặc đầy | `df -h`, `free -h`, dọn `runs/`, journal; nhiệt độ (`vcgencmd measure_temp`) |

## 13. Việc còn phải làm trên phần cứng

- [ ] Flash app-only A 0.1.2 và B 0.2.5, smoke test `/info` đúng phiên bản.
- [ ] `camera.yaml` mới cho stream điện thoại, có ảnh phủ góc khung.
- [ ] Một lô 15 khung Dynamic Mapping PASS với tag 21/22 thấy rõ.
- [ ] Điền `manual_aim_measured.yaml` cho A và B bằng số đo.
- [ ] WAIT_OK với đèn thật; đèn tắt sau OK.
- [ ] STOP, mất heartbeat, Thread detach: PWM và relay tắt trên node thật.
- [ ] Ít nhất 5 điểm `VERIFY` có camera cho mỗi node.
