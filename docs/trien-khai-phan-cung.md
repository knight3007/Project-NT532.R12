# Runbook dựng phần cứng (tuần 2 trở đi)

Thứ tự bám kế hoạch mục 5 (cổng tuần 1, 2, 3, 4) và [thay-doi-hai-voi.md](thay-doi-hai-voi.md) (hai cụm vòi trên hai node H2). Mỗi bước có lệnh hoặc file cụ thể và điều kiện qua; chưa qua thì không sang bước sau. Tick `[ ]` khi xong, ghi số đo vào chỗ chỉ định.

Người: **Hậu** mạng và firmware, **Hiếu** phần cứng và chấp hành, **Hùng** thị giác. Lệnh Python chạy trong `~/nt532/pi`; dùng `uv run --no-sync` nếu đã cài `--yolo` và `ncnn` (xem [trien-khai-pi.md](trien-khai-pi.md)).

Mục lục: 0 an toàn · 1 Pi · 2 RCP và OTBR · 3 node đầu tiên · 4 CoAP tay và node giả · 5 cảm biến · 6 servo, laser, vòi · 7 camera điện thoại · 8 trạm chạy trọn · 9 các bài đo · 10 sự cố · 11 danh sách giá trị cần đo.

---

## 0. Kiểm kê và an toàn điện (Hiếu, Hậu) - trước khi cắm bất cứ thứ gì

Kiểm kê cho **hai** cụm (thay-doi-hai-voi.md):

- [ ] 2 H2 Super Mini + 1 H2 DevKit (RCP). Không còn board dự phòng.
- [ ] 2 bộ pan–tilt (4 servo MG90S), 2 bơm 12 V, 2 laser dưới 1 mW, 2 MQ-2, 2 SHT31, 2 còi/LED.
- [ ] 2 nguồn 5 V 2–3 A cho servo, 2 nguồn 12 V cho bơm (hoặc một nguồn đủ dòng cho cả hai), 2 bộ MOSFET + diode.
- [ ] Điện thoại + giá cố định, Pi 5 + nguồn 5 V 5 A, quạt tản nhiệt, thẻ nhớ.
- [ ] Đã in tag (36h11) cỡ 8 cm: 4 tham chiếu + tag 21, 22 cho hai node; bảng ChArUco A4 (`docs/print/`, hoặc `uv run --no-sync python scripts/make_print_sheets.py`); thẻ bia. In 100%, đo lại cạnh bằng thước, ghi vào `tags.reference.size` và `tags.nodes.size` nếu không đúng 0,08 m.

Điện (mỗi node), làm xong và kiểm bằng đồng hồ vạn năng **trước** khi cắm H2:

- [ ] Servo và bơm có nguồn riêng; H2 cấp từ USB/sạc. **GND chung** giữa H2, nguồn servo, nguồn bơm.
- [ ] Điện trở kéo xuống ở cổng MOSFET bơm (GPIO12) và laser (GPIO14). Cấp nguồn mà chưa nạp firmware: bơm và laser không được chạy.
- [ ] Diode flyback song song bơm. Tụ lọc (470–1000 uF) sát chân nguồn servo; tụ nhỏ gần H2.
- [ ] Cầu phân áp MQ-2: điện áp ở GPIO1 không vượt 3,1 V khi MQ-2 ra tối đa (đo lúc có khói).
- [ ] Dây servo, bơm đi tách khỏi dây MQ-2 (xem 10: MQ-2 nhiễu).
- [ ] Che nước: hộp che H2 và MQ-2/SHT31; khay hứng dọc chân bảng; ổ cắm và Pi nằm sau vòi. Thử mạch bằng nước sạch trước khi đặt cảm biến.
- [ ] Laser Class 2, không chiếu vào mắt hay gương/kính; có công tắc ngắt nguồn bơm ở tầm tay.
- [ ] Đối chiếu bản đồ chân với sơ đồ chân thực của board người bán (thay-doi-hai-voi.md: GPIO8, 9 strapping, GPIO13/14 chia sẻ xtal 32 kHz).

Qua khi: bật nguồn từng khối một, không có gì nóng, bơm/laser/servo đứng yên.

---

## 1. Cài Pi (Hùng, Hậu)

```bash
git clone <repo> ~/nt532 && cd ~/nt532
bash pi/deploy/setup_pi.sh                 # thêm --yolo nếu dùng YOLO trên Pi; --install-services khi cần chạy nền
```

- [ ] Script chạy hết, in "Xong" và các bước tiếp.
- [ ] `cd ~/nt532/pi && uv run pytest -q` qua (hoặc `uv run --no-sync pytest -q` sau `--yolo`).
- [ ] `coap-client --help` chạy được (tên gói `coap-client` do script dò, `CHƯA KIỂM` trên Bookworm).
- [ ] `~/mediamtx/mediamtx --version` chạy.
- [ ] Chép model từ laptop: `scp -r models/fire-n.pt models/fire-n_ncnn_model pi@<ip-pi>:~/nt532/models/`; đo tốc độ `bench_detect.py` theo trien-khai-pi.md mục 4 (mục tiêu dưới 100 ms), chốt `vision.weights` và `vision.imgsz` trong `config/site.yaml`.
- [ ] Chạy thử không phần cứng: `uv run --no-sync python scripts/run_station.py --source sim` rồi mở `http://<ip-pi>:8080/` thấy dashboard.

Qua khi: dashboard sa bàn ảo chạy trên Pi.

---

## 2. RCP và OTBR (Hậu) - chi tiết ở [firmware/rcp/README.md](../firmware/rcp/README.md)

- [ ] Nạp `examples/openthread/ot_rcp` (target esp32h2) vào H2 DevKit.
- [ ] Cắm vào Pi, tìm cổng (`dmesg`, `/dev/serial/by-id/`).
- [ ] `INFRA_IF_NAME=<wlan0|eth0> ./script/setup` trong `ot-br-posix`; sửa radio URL trong `/etc/default/otbr-agent`; `sudo systemctl restart otbr-agent`.
- [ ] `sudo ot-ctl dataset init new` ... `dataset commit active`, `ifconfig up`, `thread start`.
- [ ] **Qua:** `sudo ot-ctl state` ra `leader`. Sau `sudo reboot` vẫn về `leader`.
- [ ] Lưu `sudo ot-ctl dataset active -x` vào nhóm chat. Ghi địa chỉ mesh-local EID của Pi (`sudo ot-ctl ipaddr`) cho `CONFIG_NT532_PI_ADDR`.
- Kẹt quá 2 ngày: đường lui Docker (kế hoạch mục 9, README của rcp).

---

## 3. Node H2 đầu tiên vào Thread (Hậu, Hiếu cho phần dây)

Chưa nối servo/bơm ở bước này. Chỉ H2 + MQ-2/SHT31 hoặc H2 trần. Firmware chưa từng build trên máy nhóm, nên bước này sẽ có lỗi vặt (kế hoạch: tuần 1 chạy dữ liệu giả, giờ là lần đầu có toolchain).

```bash
cd firmware/sensor-h2
idf.py set-target esp32h2
idf.py menuconfig     # NT532 node: Mã node, Địa chỉ Pi, Thread (khớp dataset ở bước 2)
idf.py build flash monitor
```

- [ ] ESP-IDF 5.4 trở lên cài xong, `idf.py build` không lỗi. Gặp lỗi thì đi theo **checklist `CHƯA KIỂM` mục 1–3 và 14** trong [sensor-h2/README.md](../firmware/sensor-h2/README.md).
- [ ] Điền theo bảng ở rcp/README.md mục 4: `NT532_THREAD_*` và `NT532_PI_ADDR`. Nạp ảnh NVS cho `node_id` (`s1`, `s2`) theo sensor-h2/README.md ("Cấu hình trong NVS") hoặc đổi `NT532_NODE_ID` rồi build riêng cho từng board.
- [ ] Monitor thấy `vai trò Thread: ... child` hoặc `router` (checklist mục 4). Không gắn được: dataset sai (kênh, key, ext PAN ID, prefix có `/64`), hoặc `otDatasetSetActive` đòi thêm trường.
- [ ] `sudo ot-ctl child table` (hoặc `router table`) trên Pi thấy node.
- [ ] Chép địa chỉ mesh-local EID từ dòng `[mesh-local EID  <- dùng cho site.yaml]` vào **`config/site.yaml` → `network.nodes.s1` / `s2`** (đang `null`).
- [ ] **Qua:** `ping -c3 <địa chỉ node>` từ Pi đáp. Lặp cho node thứ hai (nạp NVS `node_id=s2`).
- [ ] Hậu: kiểm `/alarm` multicast giữa hai node (checklist mục 5, 6): kích node 1 (khói hoặc `NT532_GAS_ALARM` hạ tạm), node 2 nháy còi.

---

## 4. Thử CoAP tay và node giả (Hậu) - tách lỗi phía Pi và phía firmware

Cổng tuần 1: *cảnh báo giả từ sensor tới Pi, Pi gửi lệnh ngắm giả tới node, node trả trạng thái, tất cả có trong log của Pi.*

### 4a. Phía Pi trước, bằng node giả

`pi/scripts/fake_node.py` là "node giả": chạy lõi C của firmware (`firmware/sensor-h2/main/core/`) qua CoAP nên Pi thử được mà không cần H2. Xem `uv run --no-sync python scripts/fake_node.py --help` để biết tham số (địa chỉ nghe, tên node, cổng).

- [ ] Chạy node giả trên cổng khác 5683 (cổng đó là của trạm). Ví dụ cùng một Pi, `uv run --no-sync python scripts/fake_node.py --node s1 --bind 127.0.0.1 --port 5701 --pi coap://127.0.0.1:5683 --temp 60` (đổi `--temp`/`--gas` bằng cách gõ `t <độ C>`, `g <gas>`, `off`/`on` để cắt/nối mạng; xem `--help`). Lần đầu cần `gcc` để biên dịch lõi C.
- [ ] Đặt tạm `network.nodes.s1: "127.0.0.1:5701"` (dạng `host:port`, mapping `/status` theo địa chỉ nguồn có đúng với dạng này hay không `CHƯA KIỂM`), chạy trạm (mục 8) và thử `coap-client` ở 4b vào node giả.
- [ ] **Qua:** Pi gửi được `/aim`, `/fire`, `/stop`, `/hb`, nhận `/status` (và `/t`, `/a` nếu node giả phát) trong `runs/station/*.jsonl` và dashboard. Hết bước này mà hỏng thì lỗi ở Pi/mạng, không phải firmware thật.
- Khôi phục `network.nodes` về địa chỉ H2 thật sau khi xong.

### 4b. Với node H2 thật, bằng `coap-client`

Lệnh từ [sensor-h2/README.md](../firmware/sensor-h2/README.md) ("Thử tay từ Pi"). id tăng dần qua các lệnh; chưa nối bơm thì lệnh `/fire` chỉ đổi chân GPIO.

```bash
ADDR="fd00:db8:a0:0:...."       # địa chỉ node
coap-client -m post -N -e '' "coap://[$ADDR]/hb"                                   # phải thấy 2.04 trả về
coap-client -m post -e '{"id":1,"pan":10,"tilt":0,"ttl":1500}' "coap://[$ADDR]/aim"
coap-client -m post -e '{"id":1,"dev":"laser","ms":1000}' "coap://[$ADDR]/fire"   # chỉ nhận sau khi aim cùng id "reached"
coap-client -m post -e '{"id":2}' "coap://[$ADDR]/stop"
# giữ /hb liên tục lúc thử /fire (node tắt bơm/laser khi mất /hb 1,5 s):
while true; do coap-client -m post -N -e '' "coap://[$ADDR]/hb"; sleep 0.5; done
```

- [ ] `/hb` luôn trả `2.04`. Pi tính "còn sống" từ câu trả lời này.
- [ ] `/aim` hợp lệ: `accepted` rồi `reached`; góc ngoài giới hạn: `rejected`.
- [ ] `/stop` rồi `/aim` với id nhỏ hơn: `rejected`, `err: stopped`.
- [ ] Phía Pi nhận `/t`, `/a`, `/status`: chạy trạm thật (mục 8) hoặc server in bản tin trong sensor-h2/README.md; xem dashboard cổng 8080 hoặc `ss -ulnp | grep 5683`.
- [ ] Kiểm an toàn **trước khi nối bơm** (checklist mục 15): rút Thread/ngắt Pi lúc `/fire`, laser tắt trong khoảng 1,5 s.
- [ ] **Qua:** cả hai node làm đủ các việc trên; lỗi ở đâu thì đối chiếu 4a: node giả chạy đúng mà H2 thật sai là lỗi firmware/mạng.

---

## 5. Cảm biến: MQ-2 và SHT31 (Hậu, Hiếu) - tuần 2

- [ ] **Làm nóng MQ-2:** firmware bỏ gas trong `NT532_GAS_WARMUP_S` = 180 s đầu sau cấp nguồn. MQ-2 mới nên được cấp nguồn liên tục lâu hơn (hàng giờ lần đầu, `CHƯA KIỂM` khuyến nghị theo datasheet) trước khi tin số đo; tối thiểu vài chục phút.
- [ ] SHT31: nhiệt/độ ẩm hợp lý (so với nhiệt kế); I2C không treo (checklist mục 9, 14).
- [ ] **Đo nền** 10 phút ở chỗ đặt thật, tắt servo/bơm: ghi trung bình và độ rộng dao động của `g` (ADC thô 0..4095, không phải ppm) và `t` cho từng node. Xem `/t` ở dashboard ("Cảm biến") hoặc server in bản tin.
- [ ] Thử khói (hương/giấy cháy) và nguồn nhiệt (đèn, máy sấy): ghi `g` và `t` tối đa.
- [ ] **Đặt ngưỡng**: nền + biên đủ để không báo nhầm, thấp hơn mức khói thử. Ghi **cùng một số** ở hai nơi:
  - `config/site.yaml` → `sensors.temp_alarm_c` (đang 45), `sensors.gas_alarm` (đang 600).
  - Node: NVS key `temp_c` (độ C, chuỗi), `gas` (ADC thô, chuỗi) trong ảnh NVS của từng node; hoặc `NT532_TEMP_ALARM_DECI_C` (0,1 độ, 450 = 45,0) và `NT532_GAS_ALARM` trong menuconfig.
- [ ] Kiểm logic: vượt ngưỡng 3 mẫu liên tiếp thì còi + `/a` (`alert`); dưới ngưỡng 5 mẫu thì `/a` (`clear`).
- [ ] Hai node có thể có nền khác nhau: nếu vậy `gas` NVS riêng từng node, `sensors.gas_alarm` lấy giá trị dùng chung hợp lý nhất và ghi chú.
- [ ] **Qua:** 10 phút không báo nhầm; khói thử báo động trong vài giây.

---

## 6. Servo, laser, vòi (Hiếu; Hậu cho công thức) - cổng tuần 2

Nối servo, laser, bơm theo bước 0, vẫn **chưa có nước** tới khi laser đã đúng.

- [ ] Servo di chuyển đúng chiều. Quy ước: pan dương quay sang trái (nhìn từ sau vòi), tilt dương ngẩng lên (kế hoạch mục 3). Sai chiều thì NVS `pan_inv=1` / `tilt_inv=1`.
- [ ] **Bảng quy đổi góc → xung và điểm 0:** lệnh `/aim` các góc -40, -20, 0, 20, 40 (pan) và -30, 0, 30 (tilt), đo góc thật bằng thước đo góc hoặc bằng điểm laser. Điền cho từng node:
  - `pmin_us`, `pmax_us` (xung ứng với ∓90°; mặc định 500/2500),
  - `pan_off`, `tilt_off` (độ lệch cơ khí; góc thật = dấu × góc lệnh + lệch),
  - `pan_inv`, `tilt_inv`.
  - Ghi ra `calibration/servo.yaml` kèm ngày đo (tài liệu hiệu chuẩn, hiện chưa có code đọc file này; giá trị có hiệu lực nằm ở NVS).
- [ ] **Giới hạn góc** cho từng node (tia không ra khỏi bảng): NVS `pan_lo`, `pan_hi`, `tilt_lo`, `tilt_hi` **và** `config/site.yaml` → `actuator.nodes.s1/s2.pan_limits` / `tilt_limits` (đang `null`, Pi dùng tạm -50..50 và -35..45). Hai nơi phải khớp: Pi chặn ở `aiming.py`, node chặn ở `actuator.c` và lúc xuất xung.
- [ ] **Offset tâm quay `actuator.pivot_offset`** (đang `[0.0, 0.0, 0.08]`, Hiếu đo): từ tâm tag đế tới tâm quay pan–tilt, trong hệ tag (X phải, Y về mép trên tag, Z lên), đơn vị mét. Cũng đo offset tới đầu laser (ghi `calibration/offsets.yaml`). Hùng kiểm lại bằng ảnh.
- [ ] **Laser:** `/fire dev=laser` bật/tắt đúng; mặc định tắt khi cấp nguồn, khi mất `/hb`, khi `/stop`.
- [ ] **Thử tầm phun và chốt đầu vòi** (có nước, hướng khay hứng): tia tới bảng ở khoảng cách thật. Yếu hoặc tỏa rộng: đầu vòi nhỏ hơn, đưa trụ gần bảng, bơm mạnh hơn (kế hoạch mục 9).
- [ ] Phun thử không làm H2 reset, MQ-2 không nhảy bất thường (xem 10).
- [ ] **`actuator.water_tilt_deg`** (đang `0.0`, "đo ở tuần 3"): ở khoảng cách làm việc, so điểm laser và điểm nước chạm; tilt cần cộng = góc ứng với độ rơi. Bảng bù theo khoảng cách ghi `calibration/water.yaml`; trạm hiện chỉ đọc một số `water_tilt_deg`. Làm riêng cho từng vòi nếu khác nhau (hiện chỉ có một giá trị chung).
- [ ] **Cổng tuần 2 (5 cm):** cần camera và commissioning xong (mục 7) để Pi biết pose node và đo được vết. Gõ tọa độ một điểm trên bảng, `aim_point.py` tính góc từ tâm quay (pose tag + `pivot_offset`), kiểm giới hạn, gửi `/aim`, bắn laser và đo vết bằng camera:

  ```bash
  cd ~/nt532/pi
  uv run --no-sync python scripts/aim_point.py --node s1 --x 0.30 --z 0.20     # một điểm (m, hệ sa bàn)
  uv run --no-sync python scripts/aim_point.py --node s1 --grid                 # lưới 3x3 trong bảng
  uv run --no-sync python scripts/aim_point.py --node s2 --points diem.csv --repeat 3   # CSV cột x,z
  ```

  Mỗi lượt in và ghi `runs/measure/aim_<thời điểm>.csv` (node, đích, pan/tilt, vết, `miss_cm`, `pass`). Điểm ngoài giới hạn góc bị báo và bỏ qua, không gửi `/aim`. Chưa có camera thì thêm `--no-laser` rồi đo vết bằng thước; thử không phần cứng: `--source sim`. Script không chạy orchestrator và luôn gửi `/stop` khi thoát. **Qua:** `miss_cm` ≤ 5 (đổi bằng `--gate-cm`) cho 5 điểm khác nhau, từng vòi. Không đạt: kế hoạch mục 9 (dựa vào CORRECT, hoặc hiệu chỉnh trực tiếp pixel→góc bằng 9 điểm).
- [ ] Ghi sai số (cm) 5 điểm × 2 vòi vào nhóm chat.

---

## 7. Camera điện thoại và commissioning (Hùng) - chi tiết [camera-dien-thoai.md](camera-dien-thoai.md)

- [ ] mediamtx chạy: `sudo systemctl status mediamtx` (hoặc `~/mediamtx/mediamtx ~/nt532/pi/deploy/mediamtx.yml`).
- [ ] Điện thoại (Larix Broadcaster): `rtmp://<IP Pi>:1935/cam`, 1280x720, 30 fps, 4–6 Mbps, keyframe 1 s, khóa ngang, **tắt chống rung**, khóa AF/AE, không zoom, giữ sáng màn hình, cắm sạc. Gắn cứng lên giá.
- [ ] `config/site.yaml` → `camera.stream.url: "rtsp://127.0.0.1:8554/cam"` (đang `null`). Nếu dùng webcam thay thế: `camera.index`, và `camera.focus` / `camera.exposure` (đang `null`; dò bằng `v4l2-ctl -d /dev/video0 -l`), điện thoại thì không dùng hai khóa này.
- [ ] Xem được: `ffplay rtsp://<IP Pi>:8554/cam` từ laptop. Camera thấy cả bảng, 4 tag tham chiếu và hai tag node.
- [ ] **Đo tâm 4 tag tham chiếu bằng thước**, điền `tags.reference.positions` 0..3 (đang `null`) theo hệ sa bàn (gốc góc trước trái, tag nằm ngửa, mép trên hướng bảng, cạnh song song mép bàn). Cũng đo lại `table.*`, `board.*` nếu số thật khác. Dán tag 21 và 22 lên đế cố định của node s1 và s2 (khớp `tags.nodes.ids`).
- [ ] **Hiệu chuẩn nội tại trên chính stream** (20–30 ảnh ChArUco, nghiêng nhiều hướng; không đổi độ phân giải hay ống kính sau đó):

  ```bash
  uv run --no-sync python scripts/calibrate_camera.py --capture    # SPACE lưu ảnh, Q thoát; đọc camera.stream.url trong site.yaml
  uv run --no-sync python scripts/calibrate_camera.py              # tính ra calibration/camera.yaml
  ```

  **Qua:** RMS dưới khoảng 0,5 px (in ra cuối lệnh; ngưỡng là gợi ý). Lưu ý: `camera-dien-thoai.md` mục 5 ghi `--source` nhưng script này không có tham số đó (xem mục "Chênh lệch" cuối runbook).
- [ ] **Commissioning**:

  ```bash
  uv run --no-sync python scripts/commission.py          # lưu calibration/commissioning.yaml khi đạt
  uv run --no-sync python scripts/commission.py --check  # sau khi ai chạm vào sa bàn
  ```

  **Qua:** mã thoát 0, thấy đủ 4 tag tham chiếu và cả hai node. Mã 1 (thiếu node hoặc sai số quá `vision.camera_shift_px`), mã 2 (không thấy đủ tag tham chiếu). Có màn hình thì xem bằng `uv run --no-sync python scripts/live_tags.py --source rtsp://127.0.0.1:8554/cam` (phím C commissioning, K kiểm tra).
- [ ] **Độ trễ stream** (cần node s1 đã chạy, laser chiếu vào vùng camera thấy):

  ```bash
  uv run --no-sync python scripts/stream_latency.py --node s1 --trials 10
  ```

  Chép giá trị gợi ý vào `camera.stream.latency_s` (đang 0,8). **Qua:** các lượt đo ổn định; lượt nào không thấy laser thì tăng `--laser-ms` hoặc đổi `--pan/--tilt` cho laser vào khung. Đặt hơi lớn hơn giá trị thật còn hơn nhỏ (nhỏ thì CORRECT không bao giờ thấy vết, xem 10). Đo lại sau mỗi thay đổi bitrate, độ phân giải.
- [ ] Hùng + Hiếu kiểm lại offset tag → đế vòi bằng ảnh.
- [ ] Kiểm 5 điểm thước (cổng tuần 2 phía thị giác): `uv run --no-sync python scripts/measure.py click --label p1 --truth 0.30,0.20` (và 4 điểm khác); ghi `runs/measure/click.csv`.

---

## 8. Chạy trạm trọn vòng (Hậu, Hùng, Hiếu) - cổng tuần 3 và 4

Điều kiện: `network.nodes` đủ, `calibration/camera.yaml` và `commissioning.yaml` có, model ở `models/`.

```bash
cd ~/nt532/pi
uv run --no-sync python scripts/run_station.py --decider rules     # camera theo camera.stream.url; mở http://<ip-pi>:8080/
```

Jev trên laptop (Pi không chạy nổi mô hình): trên laptop `uv run python scripts/serve_decider.py --decider hybrid --device cuda --host 0.0.0.0` (cùng LAN, không xác thực), trên Pi thêm `--decider remote --decider-url http://<ip laptop>:8090`; laptop tắt hoặc chậm thì Pi tự dùng luật. Chi tiết ở [orchestrator.md](orchestrator.md).

Chạy nền: `bash pi/deploy/setup_pi.sh --install-services` rồi `sudo systemctl start nt532-station`, xem log `journalctl -u nt532-station -f`.

- [ ] Dashboard: "Vòi" cho s1, s2 báo heartbeat và pose không bị chặn (không có lý do chặn).
- [ ] Không còn chặn: chưa commissioning, node bị dời, pose quá 60 s, mất heartbeat quá 1,5 s, mục tiêu ngoài bảng, góc ngoài giới hạn.
- [ ] **Cổng tuần 1** (nếu chưa qua): cảnh báo từ sensor thật lên Pi, `/aim` xuống, `/status` về, đủ trong `runs/station/*.jsonl`.
- [ ] **Cổng tuần 3:** kích cảm biến thật; YOLO thấy bia; laser chỉ vào bia; tia nước chạm bảng (chưa cần vòng kín). Chốt `targeting.tolerance_m` (đang 0,03, "chốt cuối tuần 3").
- [ ] Vòng CORRECT: `correct_max_iters` 3, `correct_done_m` 0,01 (site.yaml) hoạt động; laser tìm thấy. Không thấy: xem 10.
- [ ] **Cổng tuần 4 (MVP):** 10 lượt liên tiếp từ cảm biến tới phun nước, ít nhất 8 lượt tia nước trúng thẻ bia. Mọi lỗi tiêm vào đều kết thúc với bơm và laser tắt.
- [ ] Tiêm lỗi (Hiếu): mất Wi-Fi/Thread, mất heartbeat, lệnh trễ, lệnh trùng, góc ngoài giới hạn, `/stop` giữa chừng. Mỗi lỗi 3 lần, bơm và laser tắt (ghi thời gian tắt).
- [ ] Nút dừng khẩn cấp trên dashboard hoạt động.
- [ ] Cổng tuần 6: demo chạy trọn hai lần liên tiếp mà không sửa tay.

---

## 9. Các bài đo của kế hoạch mục 7

Ghi số thật, kể cả khi không đạt. Thêm `--headless` khi chạy trên Pi không màn hình; `--source` đặt **trước** tên bài (`measure.py --source ... target ...`).

- [ ] Hùng, pose tag, 9 điểm × 3: `uv run --no-sync python scripts/measure.py tag --node s1 --truth 0.40,0.58 --repeat 3 --label p1` (và s2).
- [ ] Hùng, định vị mục tiêu, 9 vị trí × 3: `uv run --no-sync python scripts/measure.py target --label p1 --truth 0.30,0.20 --repeat 3 --headless`.
- [ ] Hiếu, vết laser, ngắm một lần: `uv run --no-sync python scripts/measure.py spot --label p1 --target 0.30,0.20` cho từng vòi.
- [ ] Hùng, thống kê: `uv run --no-sync python scripts/report_errors.py --csv runs/measure/target.csv` (và `tag.csv`, `spot.csv`, `click.csv`); hình PNG nằm cạnh CSV.
- [ ] Hùng, tải của Pi trong lúc chạy end-to-end 30 lượt: `uv run --no-sync python scripts/log_pi_load.py --interval 1 --out runs/pi_load.csv --duration 3600` (song song với trạm). Kiểm cột `throttled` (thiếu điện, quá nhiệt).
- [ ] Hậu, Thread một hop và hai hop, mất Pi, tự phát hiện (SRP, DNS-SD, tuần 4): theo kế hoạch mục 7 (thời gian khứ hồi `/hb` đo ở Pi; không lấy hiệu giữa đồng hồ ESP và Pi). Hai hop cho cả hai vòi: ép bằng `macfilter` hoặc giảm công suất phát, đo lại `ttl` cho lệnh ngắm.
- [ ] End-to-end 20 lượt có bia + 10 không bia: thời gian từ `/a` tới `reached` và FIRE, tỷ lệ trúng, báo nhầm, dừng an toàn (log trong `runs/station/`).
- [ ] Nhận diện bia, từ 50 ảnh: `eval_detect.py` (precision, recall); thời gian suy luận `bench_detect.py` trên Pi.

---

## 10. Sự cố thường gặp: triệu chứng → nguyên nhân → kiểm tra

| Triệu chứng | Nguyên nhân hay gặp | Kiểm tra |
| --- | --- | --- |
| Vòi luôn bị chặn "mất heartbeat" | Node không trả `2.04` cho `/hb`; địa chỉ trong `network.nodes` sai hoặc đã đổi (mesh-local EID đổi sau khi xóa dữ liệu Thread); Pi không ping được node | `coap-client -m post -N -e '' "coap://[$ADDR]/hb"` ra `2.04` không; `ping -c3 $ADDR`; so địa chỉ trong monitor node với `site.yaml`; node bật thật và `ot-ctl child table` có node |
| Node gắn mạng nhưng Pi không nhận `/t`, `/a` | `NT532_PI_ADDR` sai/để trống/dùng RLOC hoặc link-local; trạm chưa chạy; Pi chưa nghe 5683 | Monitor có `bỏ bản tin` (NT532_PI_ADDR=""); `sudo ot-ctl ipaddr` lấy lại EID của Pi; `ss -ulnp \| grep 5683` |
| Node không vào mạng (`detached`) | Dataset lệch một trường (kênh, ext PAN ID, key, prefix thiếu `/64`) | `sudo ot-ctl dataset active` so từng trường với menuconfig; checklist 4 của sensor-h2/README.md |
| CORRECT không bao giờ thấy vết laser | `camera.stream.latency_s` nhỏ hơn độ trễ thật (khung cũ bị coi là mới); laser ngoài khung; phơi sáng cháy; `laser_min_rise` quá cao | Chạy lại `stream_latency.py`, đặt `latency_s` lớn hơn một chút; xem `live_tags.py`/dashboard; khóa AE; hạ `vision.laser_min_rise` (đang 40) |
| "pose stale" / vòi bị chặn vì pose | Pose quá 60 s; tag node bị che (nước, tay, vòi che chính tag); node bị dời hoặc camera xê dịch | `commission.py --check`; chạy lại `commission.py`; tag nằm trên đế cố định, không dán lên phần quay |
| Cảnh báo "node bị dời / camera xê dịch" liên tục | Điện thoại nhích khỏi giá; rung bàn; `camera_shift_px` hay `node_moved_m` quá chặt | Siết giá điện thoại, commissioning lại; xem ngưỡng trong `site.yaml` |
| Servo rung, giật, hoặc H2 reset khi servo/bơm chạy | Servo/bơm cấp chung nguồn H2 hoặc nguồn yếu; thiếu tụ; GND không chung; dây dài | Nguồn servo/bơm riêng, GND chung; tụ 470–1000 uF sát servo; đo sụt áp; quan sát `log_pi_load` không liên quan (nguồn Pi thì xem `throttled`) |
| MQ-2 nhảy lung tung khi bơm/servo chạy | Nhiễu lên chân ADC từ nguồn chung hoặc dây chạy sát; không có cầu lọc | Tách nguồn, thêm tụ lọc ở chân ADC (0,1 uF), dây MQ-2 tách khỏi dây bơm/servo; đo lại `g` lúc bơm chạy |
| MQ-2 báo nhầm ngay sau cấp nguồn | Chưa làm nóng | Chờ hết `NT532_GAS_WARMUP_S` (180 s) và nên lâu hơn lúc mới mua |
| Laser lệch đều một hướng | `pan_off`/`tilt_off`, chiều đảo sai, `pivot_offset` sai, tag đế bị xoay | Làm lại bảng servo (bước 6); kiểm `pivot_offset`; xem yaw của node trong `live_tags.py` |
| Tia nước lệch khỏi điểm laser | `water_tilt_deg` chưa đo, mực nước trong bình đổi, vòi lỏng | Đo lại bước 6, cố định vòi, giữ mực nước |
| `/fire` bị `rejected` | Chưa có `/hb`, hoặc `/aim` cùng id chưa `reached`, hoặc id nhỏ hơn `/stop` đã nhận | Gửi `/hb` liên tục; id tăng dần; xem `err` trong `/status` |
| Bơm hoặc laser không tắt sau khi rút Pi | Điện trở kéo xuống thiếu; MOSFET không đủ mức logic; lỗi firmware | **Ngắt nguồn bơm ngay.** Đo cổng MOSFET khi H2 chưa chạy; checklist an toàn mục 15 |
| Stream rớt, "lỗi camera" trên dashboard | Điện thoại ngủ, mất Wi-Fi, bitrate quá cao | `camera-dien-thoai.md` mục 8; `ffplay rtsp://<IP Pi>:8554/cam` |
| `uv run` làm mất `ncnn`/torch | `uv run` trơn đồng bộ lại môi trường | Luôn `uv run --no-sync`; cài lại `uv pip install ncnn` |
| `otbr-agent` lỗi `InvalidArguments` | RCP đổi `ttyACM0` thành `ttyACM1` hoặc sai baud | Dùng `/dev/serial/by-id/...`; baud 460800 cho UART của `ot_rcp` |
| Pi giảm xung, YOLO chậm | Quá nhiệt hoặc thiếu điện | `vcgencmd get_throttled`, cột `throttled` của `log_pi_load.py`; quạt, nguồn 5 V 5 A chính hãng |

---

## 11. Giá trị còn là placeholder trong `config/site.yaml`

| Khóa | Hiện tại | Đo ở bước | Người |
| --- | --- | --- | --- |
| `table.width`, `table.depth`, `board.*` | đề xuất 1,20 × 0,80; bảng 1,20 × 0,60 | 7 (đo lại theo bàn thật) | Hiếu, Hùng |
| `tags.reference.size` | 0,08 | 0 (đo cạnh tag in) | Hùng |
| `tags.reference.positions` 0..3 | `null` | 7 | Hùng, Hiếu |
| `tags.nodes.size` | 0,08 | 0 | Hùng |
| `tags.nodes.ids` (s1: 21, s2: 22) | đề xuất chưa chốt | 0 (chốt khi in) | Hùng |
| `camera.index` | 0 | 7 (webcam) | Hùng |
| `camera.focus`, `camera.exposure` | `null` | 7 (webcam; điện thoại khóa trên ứng dụng) | Hùng |
| `camera.stream.url` | `null` | 7 | Hùng |
| `camera.stream.latency_s` | 0,8 (giả định) | 7 (`stream_latency.py`) | Hùng |
| `vision.weights`, `vision.imgsz` | `models/fire-n.pt`, 640 | 1 (`bench_detect.py` trên Pi) | Hùng |
| `targeting.tolerance_m` | 0,03 (đề xuất) | 8 (chốt cuối tuần 3) | Cả nhóm |
| `actuator.nodes.s1/s2.pan_limits`, `tilt_limits` | `null` | 6 | Hiếu |
| `actuator.pivot_offset` | `[0.0, 0.0, 0.08]` (Hiếu đo) | 6 | Hiếu |
| `actuator.water_tilt_deg` | 0,0 (đo ở tuần 3) | 6 | Hiếu |
| `sensors.temp_alarm_c`, `sensors.gas_alarm` | 45, 600 (đo thực ở tuần 2) | 5 | Hậu, Hiếu |
| `network.nodes.s1`, `s2` | `null` | 3 | Hậu |
| `network.transports` | `null` (mặc định udp6) | chỉ đổi nếu Pi không có IPv6 | Hậu |

Cũng cần khớp trong **NVS/Kconfig của node** (không nằm trong site.yaml): `node_id`, `temp_c`, `gas`, `pan_off`, `tilt_off`, `pan_inv`, `tilt_inv`, `pmin_us`, `pmax_us`, `pan_lo/hi`, `tilt_lo/hi`, `NT532_PI_ADDR`, `NT532_THREAD_*`.

## Chênh lệch giữa tài liệu, cấu hình và code (cần sửa sau)

- `firmware/sensor-h2/README.md` ("Thử tay từ Pi") ghi gói `libcoap-bin`; tên gói thực tế trên Bookworm chưa xác minh, `setup_pi.sh` thử `libcoap3-bin`, `libcoap2-bin`, `libcoap-bin`.
- Giới hạn góc khai hai nơi (site.yaml `actuator.nodes` và NVS/Kconfig của node), phải đồng bộ tay. Mặc định hai bên hiện trùng (pan -50..50, tilt -35..45).
- `firmware/README.md` mô tả RCP "USB-UART"; ví dụ `ot_rcp` mặc định dùng UART0 460800 baud, còn đường USB-Serial-JTAG là một nhánh cấu hình khác (rcp/README.md).
