# NT532 — Ngữ cảnh triển khai cho Codex: Phase A–Z

> Mục tiêu gần nhất: đưa kết quả test pan–tilt vào firmware chính, điều khiển được hai node ESP32-H2 qua Thread/CoAP, rồi ghép camera và định vị bằng laser. Không viết lại hệ thống từ đầu.

- **Ngày lập:** 25/10/2026, múi giờ Asia/Ho_Chi_Minh.
- **Repository:** `knight3007/Project-NT532.R12`.
- **Snapshot đã đối chiếu:** nhánh `main`, commit `07eddcd66d3043c81af9c511c9c12c5c1ee8a762`.[R0]
- **Loại tài liệu:** kế hoạch công việc và ngữ cảnh giao việc; không phải báo cáo đã triển khai.
- **Đường dẫn tiến độ hiện hành:** `docs/NT532_CODEX_PHASE_A_Z.md`.
- **Trạng thái ban đầu:** người dùng đã báo hoàn tất test phạm vi xung pan–tilt; các phase dưới đây chưa được đánh dấu hoàn thành. Việc có mã nguồn hoặc CI không chứng minh phần cứng đã chạy đạt.
- **Phạm vi xuất bản tài liệu:** không sửa, commit hoặc push mã nguồn lên GitHub; chưa chạy build/test hay kiểm thử thiết bị trong lần lập kế hoạch này.

## 1. Cách Codex sử dụng tài liệu

Đọc mục 1–5, bảng phase, sau đó chỉ nạp phase đang được giao, các file liên quan và bằng chứng của phase phụ thuộc. Đây là một tài liệu chung; không cần triển khai 26 phase trong một phiên.

1. Đầu phiên, đọc `AGENTS.md` và quy tắc dự án nếu tồn tại, kiểm tra `git status`, nhánh và commit thực tế. Không reset, clean, checkout đè hoặc xóa thay đổi chưa commit của người dùng.
2. Đối chiếu snapshot này với working tree. File mới hoặc thay đổi mới của người dùng có thể chưa nằm trên `main`; không suy luận rằng dữ liệu đó không tồn tại chỉ vì không thấy trên GitHub.
3. Chỉ triển khai phase được giao. Một phase có thể chia thành nhiều lát cắt nhỏ; mỗi lát cắt thường sửa 1–5 file và có kiểm chứng riêng. Không refactor module khác để tiện tay.
4. Trước khi sửa logic, xác định test tái hiện yêu cầu/lỗi, thực hiện thay đổi nhỏ nhất, chạy test liên quan rồi kiểm hồi quy. Không bỏ test, nới giới hạn an toàn hoặc giảm tiêu chí để lấy kết quả xanh.
5. Phân biệt **SOFTWARE VERIFIED**, **HARDWARE VERIFIED**, **BLOCKED** và **NOT RUN**. Không đổi “biên dịch được”, “mô phỏng đúng” thành “thiết bị chạy đúng”.
6. Khi thiếu số đo hoặc thiết bị, hoàn thành phần có thể kiểm chứng, ghi rõ dữ liệu còn thiếu và dừng thao tác phụ thuộc. Không tự điền góc đo, tọa độ, địa chỉ IPv6, GPIO thực tế hoặc kết quả thực nghiệm.
7. Flash, ghi NVS, cấp nguồn servo/laser/bơm và gửi lệnh làm thiết bị chuyển động cần người vận hành xác nhận đúng board và điều kiện an toàn. Tài liệu này không tự cấp quyền thực hiện các thao tác đó.
8. Không tự commit/push, tạo PR, thay kiến trúc, huấn luyện lại model hoặc bật chức năng bơm nếu chưa được giao.
9. Kết thúc phiên bằng mẫu bàn giao ở cuối tài liệu: file thay đổi, lệnh đã chạy, kết quả, phần chưa kiểm, phase tiếp theo.

**Một nguồn theo dõi tiến độ:** dùng bảng phase và nhật ký cuối tài liệu này. Nếu repo đã có `tasks/plan.md`, `tasks/todo.md` hoặc issue tracker đang dùng, liên kết và đồng bộ với chúng; không tạo một bộ trạng thái cạnh tranh hoặc ghi đè kế hoạch chưa xong.

## 2. Phạm vi và kiến trúc cần giữ

### 2.1. Kiến trúc hiện hành

```text
Cảm biến + pan–tilt + laser trên H2 s1 ─┐
                                      ├─ Thread/CoAP ─ H2 RCP + OTBR trên Raspberry Pi 5
Cảm biến + pan–tilt + laser trên H2 s2 ─┘                         │
                                                       Camera cố định
                                                               │
                                             Vision + orchestrator + log
```

Firmware chính là `firmware/sensor-h2/`. Mỗi H2 vừa đọc cảm biến vừa điều khiển một cụm chấp hành. Pi tính toán từ camera cố định và gửi lệnh CoAP tới node. `firmware/actuator-s3/` là đường lui, không phải đích triển khai mặc định. Không quay lại kiến trúc một MCU nối UART hoặc camera gắn trên pan–tilt của đề cương cũ.[R1], [R7]

<a id="mvp-scope"></a>

### 2.2. Mục tiêu bắt buộc của lộ trình này

Luồng cần chứng minh: **cảm biến cảnh báo → Pi xác định khu vực/mục tiêu → camera xác minh và định vị → chọn node đủ điều kiện → pan–tilt ngắm và căn laser → ghi kết quả, sai số và độ trễ → tắt đầu ra an toàn**.

Ưu tiên MVP định vị–laser theo bản tóm tắt đề tài người dùng đã cung cấp. Repository vẫn có chức năng bơm, FIRE và VERIFY; giữ tương thích, không xóa chúng chỉ vì MVP hiện tại chưa dùng. **Phase W–X là nhánh tùy chọn có xác nhận**, không phải điều kiện bắt buộc để hoàn thành MVP laser.[U2], [R13]

Không đưa vào phạm vi bắt buộc: đổi board, chuyển sang Wi-Fi/UART, PID mới, robot di chuyển, huấn luyện lại Jev, viết dashboard mới, Home Assistant hoặc thử nghiệm đám cháy thật. Chỉ sửa phần sẵn có khi một yêu cầu của phase chứng minh cần sửa.

### 2.3. Phân công thực thi

- **Codex:** đọc và sửa code/cấu hình được giao; viết test; chuẩn bị công cụ, biểu mẫu và lệnh; kiểm chứng phần mềm; phân tích log thật.
- **Người vận hành:** xác nhận board và nguồn; đo góc, điện áp, vị trí; lắp cơ khí; flash và thử thiết bị theo điều kiện đã duyệt; cung cấp ảnh/log/số đo.
- **Cả hai:** xác nhận đơn vị, ánh xạ cụm A/B với s1/s2, kết quả nghiệm thu và quyết định có triển khai bơm hay không.

## 3. Dữ kiện đã có và những điều chưa được chứng minh

<a id="calibration-record"></a>

### 3.1. Kết quả test pan–tilt được cung cấp

Đây là **độ rộng xung PWM, đơn vị micro giây (`us`, µs)**, không phải góc độ. Nội dung bản ghi `pantilt_calibration.json` được cung cấp trong ngữ cảnh dự án:[U1]

```json
{
  "recorded_on": "2026-10-25",
  "source": "user_provided",
  "signal": "servo_pwm_pulse_width",
  "pan_tilt_A": {
    "pan":  {"min_us": 500, "home_us": 1500, "max_us": 2500},
    "tilt": {"min_us": 600, "home_us": 1500, "max_us": 2500}
  },
  "pan_tilt_B": {
    "pan":  {"min_us": 500, "home_us": 1500, "max_us": 2500},
    "tilt": {"min_us": 600, "home_us": 1500, "max_us": 2500}
  }
}
```

| Cụm | Trục | MIN (µs) | HOME (µs) | MAX (µs) |
|---|---|---:|---:|---:|
| A | Pan | 500 | 1500 | 2500 |
| A | Tilt | 600 | 1500 | 2500 |
| B | Pan | 500 | 1500 | 2500 |
| B | Tilt | 600 | 1500 | 2500 |

Không suy ra `500–2500 µs = 0–180°`, `±90°` hay `0–360°`. Không coi hai cụm có cùng giới hạn xung là có cùng chiều quay, độ lệch, độ rơ hoặc góc vật lý. HOME 1500 µs cũng chưa tự chứng minh đầu laser đang nằm ngang.

**Ánh xạ cần xác nhận trên firmware:** người vận hành nay quy định A → s1/tag 21 và B → s2/tag 22, nhưng Node ID đang chạy chưa được kiểm qua `/info`. Xem nhật ký Phase C mới nhất trước mọi thao tác phụ thuộc danh tính.

Trong snapshot `main` đã kiểm tra, `calibration/` chỉ chứa `README.md`, chưa thấy JSON này. Người dùng có thể đã lưu nó ở working tree hoặc nhánh khác. Phase B phải tìm cả `pantilt_calibration.json` và tên đã được gõ trong hội thoại là `pahntilt_calibration.json`, rồi chọn một đường dẫn chuẩn; không tạo hai bản khác nhau.[R0], [R2]

### 3.2. Hiện trạng mã nguồn liên quan

| Thành phần | Đã có trong snapshot | Ý nghĩa đối với công việc tiếp theo |
|---|---|---|
| `main/hal_esp.c` | PWM servo 50 Hz; một cặp `pulse_min_us/pulse_max_us` dùng chung hai trục | Cần hiệu chỉnh độc lập từng trục; không chỉ đổi một hằng số chung.[R3] |
| `main/node_cfg.c/.h`, `Kconfig.projbuild` | Mặc định Kconfig, ghi đè từ NVS; offset và đảo chiều | Dữ liệu JSON không tự trở thành cấu hình runtime.[R4] |
| `main/core/actuator.*` | AIM/FIRE/STOP/HB, giới hạn, TTL, trạng thái | Tái sử dụng; không viết thêm state machine chấp hành khác.[R5] |
| `config/site.yaml` | Nhiều thông số sa bàn/giới hạn/địa chỉ còn `null` hoặc là số gợi ý | Điền từ đo đạc, không từ hình dung sa bàn.[R6] |
| `pi/scripts/aim_point.py` | Ngắm điểm/lưới và ghi CSV | Dùng làm công cụ nghiệm thu thay vì viết script trùng chức năng.[R10] |
| `pi/src/nt532/station.py` | Ghép camera, Vision, CoAP, heartbeat, orchestrator | `build_real` cần camera, calibration, model và địa chỉ node.[R9] |
| `.github/workflows/ci.yml` | Test Pi, test lõi C, build ESP-IDF 5.4 | Giữ các kiểm tra; không kết luận đã chạy đạt trên bàn thật.[R11] |

**Lỗi tích hợp cần ngăn:** công thức hiện tại nội suy hai đầu xung theo `[-90°, +90°]`. Nếu đổi cặp chung thành 600/2500 thì điểm giữa ra 1550 µs, không đúng HOME 1500 µs; nếu giữ 500/2500 thì chưa thể hiện riêng giới hạn Tilt 600 µs. Các giới hạn góc hiện tại có thể khiến firmware chưa chạm đến đầu xung, nhưng không giải quyết thiếu mô hình hiệu chuẩn từng trục.[R3]

### 3.3. Các phân biệt bắt buộc

- `reached` trong firmware hiện tại là **ước tính theo thời gian quay và thời gian ổn định**, không có phản hồi encoder. `/status.pan` và `/status.tilt` là góc đã đặt, không phải số đo góc thực.[R5]
- `/stop` tắt bơm và laser; không được mô tả nó là lệnh phanh servo, ngắt nguồn servo hoặc tự HOME nếu code chưa thực hiện như vậy.[R5]
- CoAP trả lời nhận yêu cầu không đồng nghĩa đã hoàn thành chuyển động. Cần đối chiếu `/status` theo `id`.
- `--no-laser` của `aim_point.py` chỉ bỏ bật laser; **không bỏ yêu cầu camera/commissioning**. Trong snapshot, `build_real` còn kiểm tra model được cấu hình. Chưa có các điều kiện đó thì test bằng CoAP trực tiếp, không dùng script này làm bài test servo độc lập.[R9], [R10]
- Không thay HOME bằng trung bình MIN/MAX. Muốn lệnh hình học `0°` tương ứng HOME 1500 µs, phải căn cơ khí và đo xác nhận; nếu HOME khác hướng hình học 0°, ghi rõ điểm 0 riêng, không làm sai dữ liệu thô.
- JSON hiệu chuẩn là hồ sơ đầu vào; NVS/Kconfig mới là cấu hình firmware đang đọc. Phải có đường chuyển đổi được kiểm thử và log chứng minh cấu hình nào đã được nạp.[R2], [R4]

## 4. Ràng buộc kỹ thuật và an toàn

### 4.1. Đơn vị và tọa độ

Giữ hợp đồng CoAP: `pan`, `tilt` tính bằng **độ**; pulse nội bộ bằng **µs**; `ttl`, `ms`, heartbeat bằng **ms**; tọa độ sa bàn bằng **m**. Không gửi 1500 vào trường `pan` để biểu diễn HOME.[R5], [R6]

Hệ sa bàn: X sang phải, Y về bảng bia, Z lên trên. Pan dương quay sang trái khi nhìn từ sau node; tilt dương ngẩng lên. Pan 0 theo hướng tham chiếu của node/yaw, không mặc định mọi node đều nhìn thẳng +Y. Chốt lại dấu bằng phép đo, không sửa dấu ở cả Pi và firmware để bù qua lại.[R1], [R6]

### 4.2. Quy tắc hiệu chuẩn

Tách ba lớp: **phạm vi xung đã thử**, **quan hệ góc vật lý–xung**, **vùng vận hành an toàn của sa bàn**. Một lớp không thay thế lớp còn lại.

Mô hình khởi đầu nên là bảng điểm đơn điệu hoặc nội suy tuyến tính từng đoạn đi qua điểm 0 đã đo, độc lập cho mỗi trục. Ưu tiên phương án nhỏ nhất đáp ứng số đo; chưa có bằng chứng phi tuyến thì không xây framework hiệu chuẩn tổng quát.

Mọi cấu hình phải kiểm tra số hữu hạn, thứ tự giới hạn, điểm 0 nằm trong miền hợp lệ và tính đơn điệu. Tính giới hạn góc có hiệu lực sau offset/đảo chiều. Từ chối lệnh không thực hiện được trước khi báo `accepted/reached`; không âm thầm bão hòa xung rồi báo đã tới góc yêu cầu.

Đối với phần cứng thật, cấu hình thiếu/hỏng phải chặn chuyển động không an toàn và giữ bơm/laser tắt. Không tự phục hồi về dải 500–2500 cho cả hai trục khi điều đó làm mất giới hạn Tilt đã đo. Chính sách tương thích NVS cũ phải được ghi rõ và có test.

### 4.3. Thao tác thiết bị

Giữ bơm ngắt nguồn vật lý trong toàn bộ Phase A–V. Chỉ nối/cấp laser khi đến phép thử laser và đã kiểm an toàn. Servo dùng nguồn phù hợp, chung GND với H2, dây không vướng cơ cấu; xác nhận chân thực trước khi cấp tín hiệu. GPIO4/Pan và GPIO5/Tilt là mặc định repo, không thay cho kiểm tra board.[R7], [R8]

Không thử ở giới hạn cơ khí với tốc độ quét lớn. Dừng nếu có kẹt, rung bất thường, tiếng gằn hoặc reset; không ép servo để chứng minh phạm vi. Hướng laser vào bề mặt khuếch tán có chặn tia, không vào người hay mặt phản xạ. Các bài thử không dùng đám cháy thật.

Không ghi network key, mật khẩu hoặc bản dump chứa bí mật vào Git. Không tự mở port dashboard/CoAP ra Internet. Không tắt watchdog để xử lý lỗi timing.

## 5. Bản đồ phase và điều kiện phụ thuộc

Các chữ A–Z là nhãn công việc, không phải 26 lần viết lại phần mềm. Có phase chỉ đo, cấu hình hoặc chạy kiểm chứng. Kết quả đã có và còn đúng có thể được dùng lại sau khi đối chiếu bằng chứng.

| Phase | Kết quả chính | Phụ thuộc chính | Loại |
|---|---|---|---|
| A | Baseline repo, test và phạm vi | Không | Phần mềm |
| B | Hồ sơ PWM và ánh xạ cụm/node | A | Dữ liệu |
| C | Điểm 0, chiều quay, bảng góc–xung thực | B | Phần cứng |
| D | Hàm chuyển góc–xung có test | Hợp đồng C | Phần mềm |
| E | Cấu hình từng trục và đường nạp NVS | B, C, D | Phần mềm |
| F | HAL dùng đúng hiệu chuẩn, an toàn khởi động | D, E | Phần mềm |
| G | Build và nạp firmware có kiểm soát | F | Kết hợp |
| H | Nghiệm thu khởi động/HOME hai cụm | C, G | Phần cứng |
| I | Môi trường Pi và mô phỏng baseline | A | Phần mềm |
| J | RCP và OTBR hoạt động | I | Kết hợp |
| K | Hai node vào Thread, đúng danh tính | G, J | Kết hợp |
| L | Vòng lệnh CoAP và trạng thái thực | H, K | Kết hợp |
| M | Các chốt chặn chấp hành được kiểm | L | Kết hợp |
| N | Telemetry và cảnh báo cảm biến thực | K, L | Kết hợp |
| O | Hình học sa bàn và vùng góc an toàn | C, H | Phần cứng |
| P | Camera và hiệu chuẩn nội tại | I, O | Kết hợp |
| Q | Commissioning camera và hai node | O, P | Kết hợp |
| R | Ngắm tọa độ một điểm, chưa bật laser | L, M, Q; U.1 preflight | Kết hợp |
| S | Điều khiển laser, tìm vết và timing | R | Kết hợp |
| T | Căn laser vòng kín, đo sai số | S | Kết hợp |
| U | Vision/model và bộ quyết định nền | I, Q; có thể song song R–T | Phần mềm/đo |
| V | MVP cảm biến → định vị–laser | M, N, T, U | Tích hợp |
| W | Bơm đơn, nguồn và chốt chặn — tùy chọn | V + xác nhận mở phạm vi | Phần cứng |
| X | Bù điểm phun và hai vòi — tùy chọn | W | Kết hợp |
| Y | Thử lỗi, đo lặp và báo cáo số liệu | V; thêm X nếu bật bơm | Đánh giá |
| Z | Nghiệm thu và bàn giao tái lập | Y | Bàn giao |

**Nhánh chính:** A → B → C → D → E → F → G → H; I → J → K có thể chuẩn bị song song. Hai nhánh gặp nhau tại L. O → P → Q là nhánh thị giác/hình học. Tất cả hội tụ tại V, sau đó Y → Z; W–X chỉ được mở theo phạm vi đã xác nhận.

**Trạng thái dùng trong nhật ký:** `TODO`, `IN_PROGRESS`, `SOFTWARE_VERIFIED`, `WAITING_HARDWARE`, `HARDWARE_VERIFIED`, `BLOCKED`, `BLOCKED_OPTIONAL`, `DONE`, `SKIPPED_OPTIONAL`. Một phase có phần cứng chỉ được `DONE` khi có bằng chứng phần cứng tương ứng.

---

## Phase A — Chốt baseline và xác định phần việc thực sự còn thiếu

**Phụ thuộc:** không. **Thực hiện:** Codex. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `README.md`, `docs/thay-doi-hai-voi.md`, `docs/trien-khai-phan-cung.md`, `.github/workflows/ci.yml`, quy tắc agent nếu có.

### Công việc

- [x] A.1. Ghi nhánh, commit, working tree và các thay đổi có sẵn. So với snapshot của tài liệu; kiểm tra các đường dẫn trước khi dùng. Không tự kéo/ghi đè nhánh để ép giống snapshot.
- [x] A.2. Liệt kê chức năng đã có, đã được kiểm chứng và còn thiếu. Đặc biệt tìm đường góc → cấu hình → PWM, đường `/aim` → `/status` và đường FIRE có thể bật bơm.
- [ ] A.3. Chạy baseline lõi C và test Pi phù hợp môi trường theo Phụ lục A. Ghi riêng lỗi có sẵn, thiếu dependency, test bị skip và lỗi mới; không sửa mọi lỗi ngoài phạm vi.
- [x] A.4. Chốt MVP laser và trạng thái nhánh bơm trong nhật ký. Thu thập phiên bản ESP-IDF, Python, uv và hệ điều hành đang dùng; giữ toolchain tương thích CI trước khi cân nhắc nâng cấp.

**Đầu ra/file:** cập nhật nhật ký của tài liệu này hoặc tracker đang có; chưa cần sửa firmware.

**Kiểm chứng:** `git status --short`, `git rev-parse HEAD`; nhóm lệnh C1/P1 trong Phụ lục A nếu môi trường cho phép.

**Điều kiện hoàn thành:** baseline có lệnh và kết quả thực; thay đổi của người dùng được bảo toàn; phạm vi và phần thiếu được ghi rõ.

## Phase B — Chuẩn hóa hồ sơ PWM và danh tính hai cụm

**Phụ thuộc:** A. **Thực hiện:** Codex + người vận hành. **Trạng thái ban đầu:** TODO.

**Đọc trước:** file JSON thực tế, `calibration/README.md`, `node_cfg.*`, `config/site.yaml`.

### Công việc

- [x] B.1. Tìm JSON trong working tree/nhánh đang dùng, kể cả biến thể tên `pahntilt_calibration.json`. So với bản dữ liệu mục 3; khi có khác biệt, giữ cả nguồn và ngày đo, không tự chọn số thuận tiện.
- [x] B.2. Chọn một đường dẫn chuẩn, ưu tiên `calibration/pantilt_calibration.json`. Giữ nguyên số đo gốc; chỉ di chuyển/đổi tên sau khi đã kiểm tra mọi tham chiếu.
- [x] B.3. Kiểm tra cấu trúc JSON và `min_us < home_us < max_us` cho từng trục. Ghi rõ đơn vị, nguồn dữ liệu và tình trạng chưa có góc vật lý.
- [ ] B.4. Dán/đối chiếu nhãn A, B trên thiết bị; xác nhận bảng cụm → node ID → cổng nạp → tag ID. Chưa xác nhận thì ghi `UNCONFIRMED`, không xuất cấu hình nạp có vẻ hoàn chỉnh.
- [x] B.5. Cập nhật `calibration/README.md`: JSON là số đo gốc, file/profile nào chứa góc đã đo, công cụ nào chuyển sang cấu hình runtime. Không tuyên bố firmware tự đọc JSON.

**Đầu ra/file:** JSON chuẩn, `calibration/README.md`, bảng nhận diện trong nhật ký. Tên file profile bổ sung chỉ chốt ở C/E; tránh nhiều nguồn giá trị trùng nhau.

**Kiểm chứng:** parse JSON; so tự động bốn bộ MIN/HOME/MAX; kiểm danh tính bằng nhãn thiết bị và log, không chỉ dựa vào thứ tự cắm USB.

**Điều kiện hoàn thành:** một nguồn số đo không mâu thuẫn; không mất HOME Tilt 1500 µs; ánh xạ phần cứng đã xác nhận hoặc được đánh dấu chặn thao tác nạp.

## Phase C — Đo điểm 0, chiều quay và quan hệ góc–xung

**Phụ thuộc:** B. **Thực hiện:** người vận hành đo; Codex chuẩn bị biểu mẫu và phân tích. **Trạng thái ban đầu:** TODO.

**Đọc trước:** quy ước tọa độ mục 4, `docs/trien-khai-phan-cung.md` mục servo, `pi/src/nt532/orchestrator/aiming.py`.

### Công việc

- [ ] C.1. Dùng chương trình test servo đã chạy được của người dùng, không giả định firmware chính đã nhận lệnh Serial. Bơm và laser tắt. Đưa từng trục về 1500 µs; xác nhận nguồn, cơ khí và hướng HOME.
- [ ] C.2. Xác định hướng pan tham chiếu và mặt phẳng tilt 0°. Ưu tiên căn lắp cơ khí để HOME là điểm 0; nếu không thể, lưu riêng HOME và điểm 0 hình học đã đo. Không sửa số đo HOME thành 1550 để khớp phép nội suy cũ.
- [ ] C.3. Thử các bước nhỏ quanh HOME, ghi tăng xung làm cơ cấu quay chiều nào. Đo ít nhất điểm 0 và các điểm hai phía trong vùng sử dụng; đề xuất 5 điểm/trục, đi và về để thấy độ rơ. Không buộc phải chạm giới hạn cơ khí.
- [x] C.4. Ghi dữ liệu với cột `assembly,node,axis,pulse_us,measured_deg,direction,repeat,notes`; phần chưa đo để trống, không điền bằng góc lệnh. Tạo thêm ảnh/sơ đồ chỉ hướng đo khi cần.
- [ ] C.5. Chốt mô hình chuyển đổi tối giản, ý nghĩa offset/đảo chiều và phạm vi góc đã kiểm. Chỉ áp dụng phép đổi dấu ở một nơi; nêu rõ cách tương thích với NVS cũ.

**Đầu ra/file:** bảng đo, ví dụ `calibration/servo_measurements.csv` **mới nếu cần**; hồ sơ góc/điểm 0 của từng trục. Giữ JSON gốc làm giới hạn xung đã thử.

**Kiểm chứng:** đối chiếu số đo với thước/góc cơ khí; kiểm tính đơn điệu và độ lặp. Hai cụm được kiểm riêng dù xung MIN/MAX giống nhau.

**Điều kiện hoàn thành:** biết hướng và điểm 0 của bốn trục; có quan hệ góc–xung dựa trên số đo; biết phạm vi nào chưa đủ dữ liệu. Phần chưa đo không được dùng để cho phép tự động ngắm.

> **Checkpoint A–C:** đủ dữ kiện để thiết kế phép quy đổi. Nếu chỉ có MIN/HOME/MAX, Codex có thể viết test bằng fixture giả định nhưng phải ghi đó là dữ liệu test, chưa được phép coi mô hình góc là đã hiệu chuẩn.

## Phase D — Tạo phép chuyển góc sang xung có thể kiểm thử

**Phụ thuộc:** hợp đồng dữ liệu của C; nghiệm thu phần cứng cần số đo C. **Thực hiện:** Codex. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `hal_esp.c`, `node_cfg.h`, `main/core/`, `test/Makefile`, `test/test_main.c`.

### Công việc

- [x] D.1. Tách phép tính không phụ thuộc ESP-IDF thành hàm C thuần nhỏ. Chỉ thêm `main/core/servo_map.c/.h` nếu không có module phù hợp; không tạo lớp driver hoặc framework plugin mới.
- [x] D.2. Hỗ trợ cấu hình Pan/Tilt riêng, nội suy qua các điểm đã chốt, trả lỗi khi cấu hình hoặc góc không hợp lệ. Quy định rõ thứ tự offset/đảo chiều; tránh bù dấu hai lần.
- [x] D.3. Viết test trước cho HOME, hai phía HOME, các điểm đo, biên, ngoài biên, chiều đảo, NaN/Inf, điểm trùng/không đơn điệu và sai thứ tự giới hạn. Test độc lập cho Tilt 600/1500/2500.
- [x] D.4. Bổ sung test bất biến: mọi kết quả hợp lệ nằm trong giới hạn xung của đúng trục; góc yêu cầu ngoài miền có hiệu lực bị từ chối, không được âm thầm ép rồi báo thành công.

**Đầu ra/file:** module C thuần và `test/test_servo_map.c` **đề xuất mới**; đăng ký trong `test_main.c` và `Makefile`. Tách thành hai lát cắt nếu cần để giữ diff nhỏ.

**Kiểm chứng:** nhóm C1; sanitizer như CI. Đối với fixture có điểm hình học 0 tại HOME, kết quả trước lượng tử hóa PWM phải đúng 1500 µs.

**Điều kiện hoàn thành:** test bao phủ từng trục và cấu hình lỗi; không thay số đo thô; phép quy đổi không phụ thuộc phần cứng và không cấp quyền cho góc chưa đo.

## Phase E — Tích hợp cấu hình từng trục vào Kconfig/NVS

**Phụ thuộc:** B, C, D. **Thực hiện:** Codex; người vận hành xác nhận profile. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `node_cfg.c/.h`, `Kconfig.projbuild`, `partitions.csv`, phần NVS trong README firmware.

### Công việc

- [ ] E.1. Thay biểu diễn một cặp pulse chung bằng cấu hình riêng mỗi trục, chứa đủ mốc/điểm 0 đã được C chốt. Tài liệu hóa trường nào là xung, góc vật lý, giới hạn vận hành.
- [ ] E.2. Chốt quy tắc ưu tiên: NVS hợp lệ của từng trục → cấu hình mặc định đã xác nhận. Cấu hình thiếu/hỏng không được tự mở rộng góc hoặc trở về phạm vi xung không an toàn. Ghi rõ trạng thái chưa hiệu chuẩn.
- [ ] E.3. Xử lý các key cũ `pmin_us/pmax_us`, `pan_off/tilt_off`, `pan_inv/tilt_inv`: chuyển đổi rõ ràng khi đủ thông tin, hoặc báo cần hiệu chuẩn lại. Không giữ key cũ mà âm thầm thay ý nghĩa.
- [ ] E.4. Khi cần tránh nhập tay lặp lại, thêm một script nhỏ xuất CSV NVS từ hồ sơ đã xác nhận, kèm test và chế độ kiểm tra không ghi. Script phải yêu cầu ánh xạ A/B → s1/s2; không suy ra góc từ ba xung.
- [ ] E.5. Kiểm key NVS phù hợp giới hạn 15 ký tự ASCII; dùng kiểu dữ liệu tương thích ESP-IDF đang khóa. Tạo ảnh NVS bằng công cụ có sẵn của ESP-IDF, không tự viết bộ đóng gói NVS.[E1]

**Đầu ra/file:** `node_cfg.c/.h`, `Kconfig.projbuild`; script và test **chỉ thêm nếu cần**; hướng dẫn chuyển cấu hình. Đường dẫn/CLI của script mới phải ghi là mới, không giả định đã tồn tại.

**Kiểm chứng:** test dữ liệu hợp lệ, thiếu trường, sai kiểu, NaN/Inf, key cũ và profile sai node; build F1. So nội dung CSV tạo ra với hồ sơ nguồn.

**Điều kiện hoàn thành:** cấu hình hai trục độc lập; biết chính xác nguồn cấu hình có hiệu lực; có đường nạp tái lập, không làm mất giới hạn Tilt.

## Phase F — Nối cấu hình mới với HAL và hành vi khởi động

**Phụ thuộc:** D, E. **Thực hiện:** Codex. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `hal_esp.c/.h`, `app_main.c`, `act_task.c`, `core/actuator.*`.

### Công việc

- [ ] F.1. Thay nội suy cũ trong `angle_to_duty()` bằng kết quả chuyển đổi đã test. Duy trì PWM 50 Hz; kiểm tần số, độ phân giải, clock và lượng tử hóa theo ESP-IDF/board đang dùng, không mặc định cấu hình LEDC nào cũng hợp lệ.
- [ ] F.2. Dùng cấu hình đúng trục cho từng kênh. Giới hạn góc nhận lệnh phải nằm trong phần giao của vùng cơ khí, miền ánh xạ và vùng vận hành; kiểm lỗi trước khi lệnh được xem là thực hiện thành công.
- [ ] F.3. Bơm/laser về trạng thái tắt trước khi khởi tạo mạng. Khi calibration hợp lệ, khởi động tại vị trí nghỉ đã xác nhận; khi calibration lỗi, không phát chuyển động tùy tiện. Không thêm quét tự động lúc boot.
- [ ] F.4. Ghi log ngắn gọn: node ID, nguồn profile, giới hạn xung riêng, điểm 0 và trạng thái calibration. Xử lý lỗi LEDC theo đường lỗi hiện có; không bỏ qua mã trả về hoặc để trạng thái báo đạt trong khi xuất PWM thất bại.

**Đầu ra/file:** HAL và điểm nối tối thiểu vào cấu hình/chấp hành; test hồi quy nếu thay đường trả lỗi.

**Kiểm chứng:** C1 và F1; test khởi động/cấu hình lỗi trên phần mềm. Đo xung thật ở H, không ghi đã đo trong phase này.

**Điều kiện hoàn thành:** không còn dùng cặp pulse chung cho hai trục; mọi đầu ra có mặc định an toàn; build/test liên quan đạt, phần phần cứng còn chờ được ghi riêng.

> **Checkpoint D–F:** phép quy đổi và cấu hình đã được kiểm chứng bằng phần mềm. Chưa gọi đây là “servo ngắm chính xác”.

## Phase G — Build, chọn đúng board và nạp firmware chính

**Phụ thuộc:** F; ánh xạ B đã xác nhận trước khi flash. **Thực hiện:** Codex chuẩn bị; người vận hành thao tác. **Trạng thái ban đầu:** TODO.

### Công việc

- [ ] G.1. Đối chiếu target `esp32h2`, ESP-IDF tương thích CI, component và partition table. Build trước khi nối tải; lưu lệnh, phiên bản và kết quả thật.
- [ ] G.2. Xác nhận cổng USB của đúng board, không nhầm node với H2 làm RCP. Đóng chương trình khác đang giữ cổng. Ghi cấu hình hiện có trước khi chạy `set-target`, vì lệnh này có thể làm thay đổi cấu hình build.
- [ ] G.3. Kiểm NVS đã tồn tại có ghi đè Kconfig không. Không tự `erase-flash`; nếu cần ghi ảnh NVS, xác nhận offset/kích thước từ partition table thực, sao lưu cần thiết và lưu ý dữ liệu Thread cùng phân vùng có thể bị thay thế.
- [ ] G.4. Nạp s1 trước, bơm/laser ngắt nguồn, kiểm boot không panic/reset lặp. Đối chiếu log profile với dữ liệu B–F; chỉ sau đó làm s2.

**Đầu ra/file:** artifact build, log boot và nhật ký board/profile; không yêu cầu commit binary hoặc `sdkconfig` cá nhân.

**Kiểm chứng:** F1; lệnh flash F2 chỉ khi được phép. Log phải chỉ rõ node ID đúng và profile đúng; chưa có mạng thì không gọi lỗi Thread là lỗi servo.

**Điều kiện hoàn thành:** cả hai firmware nạp được đúng board; danh tính/profile được xác nhận; chưa cấp quyền tự động cho bơm hoặc laser.

## Phase H — Nghiệm thu khởi động và HOME của hai cụm

**Phụ thuộc:** C, G. **Thực hiện:** người vận hành + Codex phân tích. **Trạng thái ban đầu:** TODO.

### Công việc

- [ ] H.1. Nối servo với nguồn riêng phù hợp và GND chung; xác nhận GPIO thực. Kiểm từng cụm, từng trục trước khi nối đồng thời.
- [ ] H.2. Đo hoặc quan sát vị trí lúc khởi động, reset và bật/tắt nguồn. Với cấu hình đã căn HOME = 0°, xác nhận firmware trả đúng HOME; kiểm xung 1500 µs bằng thiết bị đo khi có. Không ghi độ chính xác µs chỉ từ quan sát bằng mắt.
- [ ] H.3. So góc nghỉ với số đo C; ghi rung, độ rơ, tiếng gằn hoặc sụt nguồn. Đề xuất lặp khởi động 5 lần mỗi cụm như một bài nghiệm thu, không phải kết quả đã đạt.
- [ ] H.4. Ghi giới hạn thử ban đầu quanh HOME, dựa trên C. **Firmware chính chưa có Serial parser mặc định**: bài thử nhiều lệnh góc qua firmware chính thực hiện ở L sau khi Thread sẵn sàng, không bịa lệnh Serial để vượt phụ thuộc.

**Đầu ra/file:** log/ảnh/video khởi động và bảng kiểm hai cụm; cập nhật hồ sơ nếu phát hiện thay đổi cơ khí.

**Kiểm chứng:** vị trí nghỉ lặp lại, không kẹt cơ khí hoặc reset bất thường, bơm/laser không tự bật. Phân biệt đo xung bằng thiết bị với quan sát vị trí.

**Điều kiện hoàn thành:** hai cụm có khởi động/HOME an toàn; các góc lệnh khác chưa kiểm được giữ trạng thái chờ L; không đánh dấu đã hoàn tất độ chính xác ngắm.

> **Checkpoint G–H:** firmware đã chạy thật và HOME đã kiểm. Tiếp tục ghép mạng; chưa bật laser/bơm.

## Phase I — Chuẩn bị Raspberry Pi và kiểm baseline mô phỏng

**Phụ thuộc:** A; có thể song song B–H. **Thực hiện:** Codex + người vận hành. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `pi/pyproject.toml`, `pi/uv.lock`, `pi/deploy/setup_pi.sh`, `docs/trien-khai-pi.md`, CI.

### Công việc

- [ ] I.1. Kiểm Pi, hệ điều hành, nguồn, dung lượng và môi trường Python. Đọc script setup trước khi chạy; không cài service tự khởi động trong giai đoạn kiểm thử ban đầu.
- [ ] I.2. Cài dependency từ dự án bằng uv; giữ lockfile. Chỉ thêm extra YOLO/decider/MQTT khi bài thử cần. Sau khi cài extras, dùng `uv run --no-sync` cho các lệnh giữ nguyên môi trường đã chuẩn bị.
- [ ] I.3. Chạy test lõi C, test Pi và lint theo baseline. Khi test cần commissioning mô phỏng, dùng chế độ `--source sim`; lưu ý lệnh commissioning có thể ghi đè dữ liệu bố cục mô phỏng đang có.
- [ ] I.4. Chạy trạm `--source sim --decider rules`, sau đó thử `--link coap` để kiểm đường CoAP tới FakeNode. Dừng trạm khi kết thúc; không để nó chiếm cổng của bài thử sau.
- [ ] I.5. Ghi rõ mô phỏng/FakeNode kiểm logic và giao thức, **không kiểm HAL, xung PWM, nguồn hoặc cơ khí của H2**. Chuẩn bị đường dẫn trọng số hiện có theo `models/README.md`; không tự huấn luyện lại.

**Đầu ra/file:** môi trường tái lập, log baseline và log mô phỏng; chỉ sửa script setup khi có lỗi được tái hiện.

**Kiểm chứng:** nhóm P1/S1 trong Phụ lục A; dashboard/log phản ánh hai node giả và khi thoát đầu ra mô phỏng tắt.

**Điều kiện hoàn thành:** Pi chạy được baseline và mô phỏng; dependency/model thiếu được ghi rõ; không tạo kết luận phần cứng từ dữ liệu giả.

## Phase J — Dựng H2 RCP và OpenThread Border Router

**Phụ thuộc:** I. **Thực hiện:** Codex hướng dẫn/cấu hình; người vận hành flash và nối thiết bị. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `firmware/rcp/README.md`, `docs/trien-khai-phan-cung.md` phần RCP/OTBR.

### Công việc

- [ ] J.1. Chọn đúng H2 dành riêng làm RCP; không nạp firmware RCP đè node mang servo. Chọn transport/cổng theo board và runbook thực tế.
- [ ] J.2. Build/nạp RCP bằng ESP-IDF tương thích, cấu hình radio URL và OTBR. Kiểm đường kết nối của RCP với Pi trước khi tạo/chọn dataset Thread.
- [ ] J.3. Khi đã có mạng Thread của nhóm, dùng lại dataset; không chạy tạo mạng mới chỉ vì đó là ví dụ trong tài liệu. Lưu cấu hình nhạy cảm ngoài Git, chỉ ghi tên mạng/kênh và trạng thái cần thiết trong báo cáo.
- [ ] J.4. Kiểm `ot-ctl state`, dataset, interface và route; khởi động lại theo lịch đã cho phép để kiểm OTBR trở lại hoạt động.
- [ ] J.5. Xác định địa chỉ IPv6 **của host Pi nơi CoAP server thực sự lắng nghe**. Kiểm địa chỉ thuộc interface/route nào và khả năng tới từ Thread; không chép tùy tiện một địa chỉ RLOC/link-local hoặc địa chỉ đầu tiên trong danh sách.

**Đầu ra/file:** cấu hình OTBR của máy, bản ghi cổng RCP/dataset/IPv6 đã che thông tin nhạy cảm; cập nhật runbook nếu khác thực tế.

**Kiểm chứng:** nhóm N1; trạng thái Thread hợp lệ, RCP không mất kết nối và OTBR hồi phục sau restart được kiểm.

**Điều kiện hoàn thành:** border router chạy ổn định; biết đúng điểm nhận CoAP trên Pi; không làm mất mạng đang hoạt động hoặc lộ network key.

## Phase K — Cho s1 và s2 tham gia Thread và tìm đúng node

**Phụ thuộc:** G, J. **Thực hiện:** Codex + người vận hành. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `main/net_ot.c`, `Kconfig.projbuild`, `pi/src/nt532/net/discover.py`, `pi/scripts/find_nodes.py`.

### Công việc

- [ ] K.1. Cấu hình từng node với danh tính đã chốt, dataset khớp OTBR và địa chỉ Pi đã kiểm. NVS có quyền ghi đè node ID nên phải kiểm log lúc boot.
- [ ] K.2. Thử một node trước: log có vai trò child/router và địa chỉ hợp lệ. Kiểm ping/routing, nhưng không coi ping thành công là đã chứng minh server CoAP và đường `/status` hoạt động.
- [ ] K.3. Thêm node thứ hai, tránh trùng node ID và tên SRP. Chạy `find_nodes.py`; dùng `network.discover: static` và địa chỉ tường minh khi SRP chưa được kiểm, không biến dự phòng thành lý do bỏ sửa lỗi mạng.
- [ ] K.4. Điền địa chỉ vào cấu hình phù hợp và kiểm sau reboot. Phân biệt cổng serial khi flash với địa chỉ IPv6 khi vận hành.

**Đầu ra/file:** `config/site.yaml` hoặc cấu hình máy triển khai, log nhận diện hai node và bảng ánh xạ B hoàn chỉnh.

**Kiểm chứng:** N1; `find_nodes.py`; GET `/info` từng địa chỉ trong L. Các phép thử chưa gắn servo phải vẫn phân biệt rõ với bài thử tải ở H/L.

**Điều kiện hoàn thành:** Pi tìm/định tuyến được tới cả hai node; danh tính không trùng; địa chỉ trả về có thể dùng cho liên lạc hai chiều.

## Phase L — Kiểm vòng CoAP: lệnh xuống và trạng thái về

**Phụ thuộc:** H, K. **Thực hiện:** Codex chuẩn bị test; người vận hành quan sát chuyển động. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `core/proto.*`, `core/actuator.*`, `coap_node.c`, `pi/src/nt532/net/{protocol,coap,link}.py`.

### Công việc

- [ ] L.1. Chạy đúng một CoAP receiver cho `/t`, `/a`, `/status` trên Pi; chưa chạy orchestrator tự xử lý cảnh báo. Có thể dùng receiver tối giản đã ghi trong README firmware. Tránh hai tiến trình cùng giữ UDP 5683.
- [ ] L.2. GET `/info` và gửi `/hb`; đối chiếu danh tính, giới hạn, timeout và đường phản hồi. Giữ heartbeat bằng cơ chế sẵn có hoặc terminal riêng theo runbook.
- [ ] L.3. Gửi `/aim` tới hướng 0 đã xác nhận, rồi các góc nhỏ hai phía nằm trong miền đo. Đối chiếu đúng trục, chiều, số đo vật lý và `accepted → reached` cùng `id`. Đây mới là bài kiểm nhiều góc qua firmware chính.
- [ ] L.4. Kiểm `id` tăng dần trong phiên; `/fire` dùng cùng `id` với `/aim` đã reached, `/stop` dùng ID mới. Không coi ACK CoAP là trạng thái reached. Ghi rõ đơn vị độ trong payload.
- [ ] L.5. Kiểm độc lập s1/s2 và đối chiếu nhãn: gửi s1 không làm s2 quay. Bơm/laser vẫn ngắt nguồn, chưa cần gọi `/fire` để chứng minh ngắm.

**Đầu ra/file:** log CoAP hai chiều, log monitor H2, bảng góc lệnh/góc đo và tình trạng từng node. Chỉ sửa protocol/link khi có lỗi tái hiện, giữ hợp đồng hiện hành.

**Kiểm chứng:** N2; C1; test Pi `tests/test_net.py`, `tests/test_info.py`, `tests/test_discover.py` nếu sửa phần tương ứng.

**Điều kiện hoàn thành:** cả hai node thực hiện đúng các góc nhỏ đã đo; trạng thái về đúng receiver; không nhầm µs với độ, không nhầm node và không dựa chỉ vào ACK.

## Phase M — Kiểm giới hạn, STOP, TTL và mất heartbeat

**Phụ thuộc:** L; unit test có thể chuẩn bị từ D–F. **Thực hiện:** Codex + người vận hành. **Trạng thái ban đầu:** TODO.

### Công việc

- [ ] M.1. Kiểm góc ngoài miền, payload lỗi, NaN/Inf và cấu hình sai bằng unit test/FakeNode trước. Trên thiết bị, chỉ gửi trường hợp đã chứng minh bị từ chối; quan sát không có chuyển động vượt vùng an toàn.
- [ ] M.2. Kiểm lệnh trùng không thực hiện lại; ID cũ sau `/stop` bị chặn; TTL thiếu không được báo reached sai. Bổ sung bài thử Pi/node khởi động lại để phát hiện vấn đề tái sử dụng ID hoặc trạng thái cũ.
- [ ] M.3. Với bơm/laser vẫn tách tải, kiểm chân điều khiển bằng dụng cụ hoặc tải chỉ thị phù hợp. Thử `/fire` thiếu heartbeat, chưa aim, sai aim ID và quá thời gian cho phép; so với hợp đồng hiện có.
- [ ] M.4. Ngắt heartbeat/mạng và gửi STOP giữa thao tác để đo thời điểm đầu ra tắt. Lấy timeout từ `/info`/cấu hình có hiệu lực; không mặc định mọi build dùng cùng số. Phép đo vật lý đầu ra khác với chỉ thấy log “tắt”.
- [ ] M.5. Kiểm giới hạn khai ở Pi và node: dùng phần giao hợp lệ; nếu giao rỗng hoặc cấu hình không hợp lệ thì chặn node, không tạo bộ giới hạn đảo chiều để tiếp tục ngắm. Giữ kiểm an toàn độc lập ở node.

**Đầu ra/file:** ma trận thử lỗi và log; sửa tối thiểu `core/actuator.*`, `proto.*`, lớp link/giới hạn và test tương ứng khi có lỗi thật.

**Kiểm chứng:** C1; các test `test_fakenode.py`, `test_info.py`, `test_net.py`; đo đầu ra trên từng node. Không ghi `/stop` có khả năng phanh cơ khí servo.

**Điều kiện hoàn thành:** lỗi không làm bơm/laser bật ngoài điều kiện cho phép; timeout/STOP được chứng minh; lỗi thực tế chưa sửa phải chặn phase dùng laser.

> **Checkpoint I–M:** Pi ↔ Thread ↔ H2 chạy hai chiều; các góc lệnh đã kiểm; chốt chặn được thử trước khi lắp tải laser/bơm.

## Phase N — Kiểm cảm biến, telemetry và cảnh báo

**Phụ thuộc:** K, L. **Thực hiện:** người vận hành đo; Codex cấu hình/phân tích. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `main/sensors.c`, `hal_esp.c`, `core/alarm.*`, `pi/src/nt532/orchestrator/fusion.py`.

### Công việc

- [ ] N.1. Xác nhận cảm biến thực tế có khớp SHT31/MQ-2 trong firmware không. Nếu khác, ghi yêu cầu thay driver riêng; không đổi tên biến để giả vờ phần cứng tương thích.
- [ ] N.2. Kiểm nguồn và mức điện áp ở chân ADC theo board/datasheet; ghi dữ liệu nền từng node sau thời gian ổn định phù hợp. Không coi thời gian warm-up trong code là chứng minh cảm biến đã ổn định.
- [ ] N.3. Đo nhiễu khi servo đứng/quay, thử kích thích an toàn; chọn ngưỡng từ số đo. Giá trị gas hiện là ADC thô, không tự đổi nhãn thành ppm.[R7]
- [ ] N.4. Đồng bộ ngưỡng có hiệu lực giữa node và Pi; ghi khác biệt riêng từng node nếu có. Kiểm seq, node ID, nhịp `/t`, chuỗi `/a alert/clear` và khả năng nhận `/alarm` giữa node nếu phần này nằm trong mục tiêu demo.
- [ ] N.5. Kiểm mất cảm biến: giá trị cũ/lỗi phải được nhận biết, không được mô tả dữ liệu giữ lại như số đo mới. Chỉ bổ sung báo lỗi/độ mới cần thiết khi logic đang che lỗi.

**Đầu ra/file:** bảng đo nền/ngưỡng, log telemetry/cảnh báo và cấu hình tương ứng.

**Kiểm chứng:** C1; `tests/test_net.py`, `tests/test_orchestrator.py` khi sửa liên quan. Bài đo đề xuất: 10 phút nền không kích thích và các lượt kích thích có kiểm soát, ghi cả báo nhầm.

**Điều kiện hoàn thành:** dữ liệu mang đúng node/đơn vị; ngưỡng có căn cứ; có đường cảnh báo thật tới Pi, tách biệt cảnh báo giả trong simulator.

## Phase O — Đo sa bàn, tâm quay và vùng ngắm hợp lệ

**Phụ thuộc:** C, H; chuẩn bị song song N. **Thực hiện:** người vận hành + Codex. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `config/site.yaml`, `orchestrator/aiming.py`, runbook phần sa bàn và tag.

### Công việc

- [ ] O.1. Chốt bố cục thực và hệ tọa độ. Đo kích thước bàn/bảng, vị trí tâm tag tham chiếu, độ cao và vị trí node; không giữ số gợi ý chỉ vì đã có trong YAML.
- [ ] O.2. Gắn tag lên đế cố định, không lên phần quay. Kiểm kích thước in thực và ID; bảng cấu hình hiện dùng tag 21 cho s1, 22 cho s2 nhưng phải khớp bản in/lắp thực.[R6]
- [ ] O.3. Đo offset tâm tag → tâm quay và hướng pan 0/yaw; thống nhất với C. Nếu hai cụm có offset khác nhau, không lấy trung bình âm thầm: chỉ mở rộng cấu hình offset theo node khi số đo chứng minh cần, kèm test tương thích.
- [ ] O.4. Chốt giới hạn góc vận hành nằm trong vùng đã hiệu chuẩn, cập nhật đồng bộ node và Pi. Giới hạn pan/tilt hình chữ nhật chỉ là chặn sơ bộ; từng mục tiêu vẫn cần kiểm nằm trên vùng bàn/bảng và đường ngắm an toàn.

**Đầu ra/file:** `config/site.yaml`, sơ đồ/số đo sa bàn, hồ sơ cơ khí; NVS giới hạn vận hành khi được phép cập nhật.

**Kiểm chứng:** kiểm đơn vị mét, chiều trục và tính toán tay vài hướng đơn giản; test `tests/test_yaw.py`, `tests/test_floor.py`, `tests/test_3d_targets.py` nếu thay logic hình học.

**Điều kiện hoàn thành:** tọa độ/hướng có số đo; cấu hình không dùng placeholder cho trường bắt buộc; chưa giả định laser cùng gốc quang học với tâm quay nếu chưa đo.

## Phase P — Thiết lập camera và hiệu chuẩn nội tại

**Phụ thuộc:** I, O. **Thực hiện:** người vận hành chụp; Codex chạy/phân tích. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `pi/scripts/calibrate_camera.py`, `vision/calibration.py`, `vision/camera.py`; thêm `docs/camera-dien-thoai.md` nếu dùng điện thoại.

### Công việc

- [ ] P.1. Chọn một nguồn ảnh thực tế: webcam hoặc stream điện thoại. Cố định camera và độ phân giải; khóa các tham số cần thiết theo thiết bị, ghi lại lens/zoom/focus. Không triển khai cả hai pipeline khi chỉ dùng một.
- [ ] P.2. Đối chiếu bảng ChArUco in với thông số thực trong `site.yaml`; không chỉ kiểm hình in trông giống nhau.
- [ ] P.3. Chụp ảnh đa góc bằng công cụ có sẵn, đề xuất 20–30 khung đạt chất lượng; tính `calibration/camera.yaml`. Dùng tham số thực được `--help` hỗ trợ, không tự thêm `--source` cho script chỉ đọc cấu hình camera.
- [ ] P.4. Kiểm lỗi chiếu lại và phân bố ảnh; RMS khoảng dưới 0,5 px là ngưỡng gợi ý trong runbook, không phải kết quả đã đạt. Nếu chưa đạt, kiểm bảng, blur và góc chụp trước khi đổi thuật toán.[R8]

**Đầu ra/file:** intrinsics, ảnh hiệu chuẩn và thông tin nguồn ảnh. Không ghi đè bộ hiệu chuẩn thật bằng dữ liệu simulator.

**Kiểm chứng:** nhóm V1; mở camera ở đúng cấu hình vừa hiệu chuẩn và kiểm ảnh/ma trận; ghi nguyên nhân nếu không đạt.

**Điều kiện hoàn thành:** có file intrinsics hợp lệ, nguồn ảnh tái lập và số đo lỗi; biết thay đổi nào làm phải hiệu chuẩn lại.

## Phase Q — Commissioning camera và pose hai node

**Phụ thuộc:** O, P. **Thực hiện:** Codex + người vận hành. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `pi/scripts/commission.py`, `live_tags.py`, `vision/system.py`, `vision/tags.py`.

### Công việc

- [ ] Q.1. Cho camera thấy đủ tag tham chiếu và tag đế node. Kiểm ID, kích thước và chiều tag với dữ liệu O; không đổi tọa độ khai báo để che một tag đặt sai.
- [ ] Q.2. Chạy commissioning, lưu `calibration/commissioning.yaml` khi đạt. Kiểm đủ s1/s2, pose và hướng yaw phù hợp số đo. Không dùng `--force` để chuyển một kết quả không đạt thành đạt.
- [ ] Q.3. Chạy `--check`; đo vài điểm tham chiếu bằng thước và so với tọa độ suy ra. Sai số camera/pose phải được xử lý trước khi đổ lỗi cho servo.
- [ ] Q.4. Thử che tag hoặc dời nhẹ camera/node có kiểm soát. Kiểm chính sách `auto_repose_camera` hiện có: tự cập nhật chỉ khi đủ dữ liệu hợp lệ; thiếu điều kiện thì chặn, không tiếp tục dùng pose cũ như pose mới.

**Đầu ra/file:** commissioning đã nghiệm thu, log check, số đo đối chiếu; cập nhật `site.yaml` chỉ khi có căn cứ.

**Kiểm chứng:** nhóm V1; test `tests/test_system.py`, `tests/test_vision.py`, `tests/test_yaw.py` khi thay code. `commission.py --source sim` lưu dữ liệu mô phỏng, không phải nghiệm thu sa bàn thật.[R12]

**Điều kiện hoàn thành:** camera và hai node được định vị trong cùng hệ tọa độ; check đạt; dịch chuyển/thiếu tag có hành vi phát hiện và chặn/cập nhật đúng chính sách.

> **Checkpoint N–Q:** có cảnh báo cảm biến và hình học/camera đã đo; sẵn sàng kiểm ngắm điểm trước khi ghép AI vào luồng tự động.

## Phase R — Ngắm một tọa độ bằng hình học, chưa bật laser

**Phụ thuộc:** L, M, Q; preflight model/đường dẫn của U.1. **Thực hiện:** Codex + người vận hành. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `pi/scripts/aim_point.py`, `orchestrator/aiming.py`, `station.py`, `tests/test_aim_point.py`.

### Công việc

- [ ] R.1. Chọn một điểm đo thật nằm trong bảng và miền góc an toàn. Kiểm tính góc từ pose, yaw và pivot offset; so một trường hợp đơn giản với tính toán tay.
- [ ] R.2. Chuẩn bị đúng các điều kiện `build_real`: camera, intrinsics, commissioning, model được cấu hình và địa chỉ node. `--no-laser` không loại bỏ các phụ thuộc này; thiếu chúng thì quay lại CoAP trực tiếp ở L.
- [ ] R.3. Chạy `aim_point.py --no-laser` trên s1 rồi s2. Quan sát hướng và đo góc thực; không ghi sai số vết laser khi chưa bật laser. Thử một điểm trước, chưa chạy cả lưới.
- [ ] R.4. Kiểm điểm ngoài miền bị bỏ qua trước khi gửi aim. Đối chiếu log timeout/trạng thái; không tăng giới hạn để làm lệnh qua nếu hướng vật lý chưa an toàn.

**Đầu ra/file:** log góc tính/góc đo, dữ liệu output hiện có của `aim_point.py`; sửa hình học chỉ khi lỗi đã được tách khỏi hiệu chuẩn servo.

**Kiểm chứng:** nhóm V2 với `--no-laser`; `tests/test_aim_point.py`, `tests/test_yaw.py`, test hình học liên quan. Ngừng trạm khác đang dùng camera/cổng CoAP trước khi chạy script.

**Điều kiện hoàn thành:** từ tọa độ thật tới đúng node và hướng cơ khí hợp lý; không vượt giới hạn; chưa tuyên bố đã đạt sai số điểm ngắm bằng camera.

## Phase S — Bật/tắt laser, tìm vết và đo độ trễ ảnh

**Phụ thuộc:** R và các chốt chặn M đạt. **Thực hiện:** người vận hành + Codex. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `vision/spot.py`, `vision/stream.py`, `orchestrator/frames.py`, `scripts/stream_latency.py`, `scripts/aim_point.py`.

### Công việc

- [ ] S.1. Lắp laser đúng mạch điều khiển và điều kiện an toàn; bơm vẫn ngắt nguồn. Kiểm mặc định tắt, bật theo `/fire dev=laser` với aim ID hợp lệ, tự tắt, STOP và mất heartbeat trên tải thật.
- [ ] S.2. Mỗi lúc chỉ bật laser một node. Thu khung tắt/bật từ đúng thời điểm, kiểm `find_spot` và điểm sáng giả do phản xạ/phơi sáng. Không bật cả hai tia khi thuật toán chưa phân biệt được nguồn.
- [ ] S.3. Nếu dùng stream, chạy công cụ đo độ trễ có sẵn, cập nhật `camera.stream.latency_s` từ kết quả. Đối chiếu cửa sổ laser bật với thời gian lấy ảnh; không kéo dài laser vượt giới hạn node hoặc tắt watchdog để lấy được khung.
- [ ] S.4. Chạy ngắm laser một điểm rồi ít nhất 5 điểm an toàn mỗi node. Ghi điểm đích, điểm đo, sai số cm và trường hợp không thấy vết. Mục tiêu kiểm ban đầu theo runbook là sai số ≤5 cm tại 5 điểm mỗi node; đó là tiêu chí, không phải số liệu đã có.[R8]

**Đầu ra/file:** cấu hình timing/exposure đã đo; CSV `aim_*`/đo vết theo script; log kiểm an toàn laser thật.

**Kiểm chứng:** nhóm V2 và CLI `stream_latency.py --help` trước khi đo; `tests/test_stream.py`, `tests/test_stream_latency.py`, test vision tương ứng nếu sửa.

**Điều kiện hoàn thành:** laser tắt đúng các tình huống lỗi; vết được tìm từ khung đúng thời điểm; có sai số định lượng từng node, không bỏ các lượt không thấy vết ra khỏi báo cáo.

## Phase T — Kiểm vòng căn laser và sai số sau hiệu chỉnh

**Phụ thuộc:** S. **Thực hiện:** Codex + người vận hành. **Trạng thái ban đầu:** TODO.

**Đọc trước:** vòng CORRECT trong `orchestrator/machine.py`, `aiming.py`, `vision/system.py`, test orchestrator.

### Công việc

- [ ] T.1. Dùng vòng CORRECT hiện có. Nếu chưa có entrypoint đo độc lập, thêm harness nhỏ gọi logic sẵn có và luôn STOP khi thoát; không chép một thuật toán căn mới hoặc chạy cả nhánh bơm để thử laser.
- [ ] T.2. Đo một điểm: ngắm ban đầu → tìm vết → tính sai số → hiệu chỉnh → đo lại. Kiểm dấu hiệu chỉnh bằng dữ liệu; sai số tăng hoặc tới biên thì dừng để tìm nguyên nhân.
- [ ] T.3. Thử lưới 3×3 và lặp 3 lượt mỗi điểm khi tất cả điểm đều an toàn. Ghi trước/sau, số lần chỉnh, thời gian, lý do thất bại và node. Điểm ngoài miền được ghi riêng, không coi là đạt cũng không âm thầm xóa.
- [ ] T.4. Giữ giới hạn số vòng, bước chỉnh và kiểm pose/heartbeat. Cấu hình hiện có `correct_max_iters=3`, `correct_done_m=0.01`, `tolerance_m=0.03`; xác nhận tiêu chí thực nghiệm trước khi dùng làm ngưỡng nghiệm thu, không tự nới khi kết quả xấu.[R6]

**Đầu ra/file:** harness/test mới chỉ khi cần; log và CSV so sánh ngắm ban đầu với sau căn; cập nhật tham số có lý do đo đạc.

**Kiểm chứng:** C1 nếu ảnh hưởng firmware; `tests/test_orchestrator.py`, `tests/test_aim_point.py`, bài đo phần cứng. Kiểm mất vết, hết vòng, tới biên và lỗi link đều kết thúc an toàn.

**Điều kiện hoàn thành:** căn làm giảm sai số trong miền hoạt động đã chốt; có số liệu lặp; không có vòng lặp vô hạn hoặc trường hợp lỗi vẫn được ghi như khóa mục tiêu thành công.

## Phase U — Kiểm Vision/model và bộ quyết định nền

**Phụ thuộc:** I, Q; có thể song song R–T. **Thực hiện:** Codex + người phụ trách Vision. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `models/README.md`, `vision/detect.py`, `vision/localize.py`, `decider/rules.py`, `scripts/live_detect.py`, `scripts/bench_detect.py`.

### Công việc

- [ ] U.1. Preflight trọng số, tên lớp, định dạng và đường dẫn theo `site.yaml`. Tận dụng model đã có; thiếu model thì ghi thiếu artifact, không tạo file giả hoặc tự chuyển sang detector mô phỏng mà không báo.
- [ ] U.2. Kiểm nhận diện trên chính camera/sa bàn: có bia, không bia, nhiều bia, ánh sáng khác và vật gây nhầm. Ghi confidence, vị trí và thời gian xử lý; phân biệt hiệu quả model trên ảnh mẫu với trên camera thật.
- [ ] U.3. Kiểm định vị dùng đúng bề mặt. Ưu tiên nghiệm thu bảng đứng trước; chỉ mở bài thử sàn khi có dữ liệu/góc tương ứng. Không chiếu mọi bounding box vào mặt bảng nếu bia thực ở sàn.
- [ ] U.4. Dùng baseline `rules` trước khi ghép Jev/remote. Kiểm lựa chọn vòi theo khả năng với tới và sức khỏe node, không mặc định node báo động luôn là node phải ngắm. Không cho bộ quyết định vượt các chốt chặn cứng.
- [ ] U.5. Đo inference và tổng pipeline riêng trên Pi; ghi độ phân giải, định dạng model và môi trường. Chỉ tối ưu phần gây nghẽn đã đo, không huấn luyện lại/model-switch tùy tiện.

**Đầu ra/file:** model manifest hoặc bản ghi phiên bản, kết quả test camera, số liệu inference, cấu hình hợp lệ.

**Kiểm chứng:** CLI `--help` của script detect/bench/eval trước khi gọi; test vision/decider hiện có theo dependency thực. Các test cần extra/model phải ghi rõ có chạy hay không.

**Điều kiện hoàn thành:** model thật dùng đúng lớp/nguồn ảnh; định vị đúng mặt phẳng; baseline quyết định chọn được node hợp lệ và chấp nhận từ chối khi thiếu bằng chứng.

> **Checkpoint R–U:** ngắm tọa độ, laser, căn sai số và Vision đều đã được kiểm độc lập. Chưa bật toàn bộ chu trình tự động cho tới khi chế độ laser-only ở V được xác nhận.

## Phase V — Ghép MVP: cảm biến → xác minh → định vị–laser

**Phụ thuộc:** M, N, T, U. **Thực hiện:** Codex + người vận hành. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `station.py`, `orchestrator/machine.py`, `orchestrator/events.py`, `dashboard/server.py`, `scripts/run_station.py` và test liên quan.

### Công việc

- [ ] V.1. Rà đường FIRE của code hiện tại: `run_station.py --decider rules` vẫn có thể đi tới nhánh bơm. Không coi việc chọn rules là chế độ “chỉ laser”.[R13]
- [ ] V.2. Triển khai hoặc xác minh một chế độ vận hành tường minh cho **laser-only** nếu chưa có. Trong chế độ này, chu trình kết thúc bằng kết quả định vị/căn ngắm; không gửi `dev=pump`, không báo “đã dập tắt” dựa trên laser. Tên cấu hình/CLI phải được ghi đúng sau triển khai; tài liệu này không giả định đã có `--laser-only`.
- [ ] V.3. Thêm/kiểm chặn bật bơm tại node cho profile triển khai chưa được cho phép phun. Chặn này độc lập với Pi; giữ bơm ngắt nguồn vật lý trong nghiệm thu laser. Giữ tương thích đường phun sẵn có qua cấu hình được bật rõ ràng, không xóa chức năng.
- [ ] V.4. Chia thay đổi thành lát cắt: cấu hình + test chặn ở node; chế độ orchestrator + test; log/dashboard/báo cáo tối thiểu cho kết quả laser. Mỗi lát cắt nhỏ, không gom thành refactor toàn dự án.
- [ ] V.5. Chạy cảnh báo thật: nhận `/a` → Vision xác minh → chọn mục tiêu/node → AIM → CORRECT → ghi kết quả → tắt laser. Thử không có mục tiêu, node lỗi và mục tiêu ngoài tầm. Với nhiều node, phép đo vết phải tuần tự từng laser.
- [ ] V.6. Dùng dashboard sẵn có để quan sát và STOP; không xây giao diện mới. Kiểm log giữ được run ID, node cảnh báo, node được chọn, mục tiêu, góc, sai số, trạng thái và lý do kết thúc.

**Đầu ra/file:** chế độ laser-only/chặn bơm có test, nếu cần; log end-to-end và hướng dẫn chạy đúng chế độ. Các tên trạng thái mới phải được cập nhật nơi tiêu thụ, không lẫn với `extinguished`.

**Kiểm chứng:** test `test_orchestrator.py`, `test_dashboard.py`, `test_report.py` theo thay đổi; C1 cho chặn node; chạy thật từng node rồi toàn hệ thống. Khẳng định bằng test/log rằng không phát lệnh bơm trong chế độ laser-only.

**Điều kiện hoàn thành:** luồng tự động định vị–laser chạy có bằng chứng; không có đường vô ý bật bơm; lỗi/không có mục tiêu kết thúc đúng và mọi đầu ra tắt khi dừng.

> **Checkpoint V — MVP laser:** có thể nghiệm thu mà không triển khai bơm. Sau V, đi thẳng Y nếu phạm vi chỉ là định vị–laser; không mặc định chạy W–X.

## Phase W — Nhánh tùy chọn: bơm đơn và an toàn nguồn/tải

**Phụ thuộc:** V + người dùng xác nhận tiếp tục chức năng phun. **Thực hiện:** người vận hành; Codex hỗ trợ kiểm logic. **Trạng thái ban đầu:** CHƯA ĐƯỢC MỞ PHẠM VI.

### Công việc

- [ ] W.1. Xác nhận bơm, điện áp, dòng, mạch đóng cắt, diode/bảo vệ, nguồn và dây thực tế; không cấp bơm từ GPIO. Chỉ tiến hành khi nguồn/tải đã được người phụ trách phần cứng kiểm.
- [ ] W.2. Lắp đường nước, khay hứng và che mạch. Chuẩn bị bơm theo yêu cầu của loại bơm thực tế; không tự chạy khô vì một ví dụ thử firmware ghi “chưa có nước”.
- [ ] W.3. Mở quyền pump rõ ràng trên một node, thử trong thời gian ngắn không vượt `/info.fire`, với aim đã reached và heartbeat liên tục. Nút/ngắt nguồn vật lý phải có tác dụng độc lập với Pi.
- [ ] W.4. Kiểm STOP, mất heartbeat, mất mạng, reset và giới hạn thời gian trên tải thật. Đo thời điểm tắt/dòng/điện áp khi có dụng cụ; không suy từ log rằng nước đã ngừng ngay lập tức.
- [ ] W.5. Lặp với node còn lại; kiểm bơm không làm H2 reset hoặc làm sai số cảm biến tăng bất thường. Chưa bật cả hai bơm cùng lúc.

**Đầu ra/file:** sơ đồ đấu nối đã kiểm, cấu hình cho phép bơm, log và bảng nghiệm thu từng bơm; không cần code mới nếu giao thức sẵn có đáp ứng.

**Kiểm chứng:** ma trận lỗi M chạy lại với tải thật, có bảo vệ vật lý. Bơm tắt khi không đủ điều kiện, không chỉ khi Pi còn hoạt động bình thường.

**Điều kiện hoàn thành:** hai bơm đơn hoạt động và dừng an toàn; không gây lỗi nguồn/cảm biến; có xác nhận riêng cho phép thử phun tiếp. Không có xác nhận phạm vi thì ghi `SKIPPED_OPTIONAL`.

## Phase X — Nhánh tùy chọn: bù điểm phun và phối hợp hai vòi

**Phụ thuộc:** W, O, T. **Thực hiện:** người vận hành đo; Codex phân tích/cấu hình. **Trạng thái ban đầu:** CHƯA ĐƯỢC MỞ PHẠM VI.

**Đọc trước:** `aiming.water_tilt`, `config/site.yaml`, runbook phần bù nước và hai vòi.

### Công việc

- [ ] X.1. Đo chênh lệch điểm laser/điểm nước theo khoảng cách của từng vòi, cùng điều kiện nguồn và thời gian phun. Giữ lại dữ liệu gốc; không suy bù điểm phun từ MIN/MAX servo.
- [ ] X.2. Dùng `water_tilt_deg` riêng từng node nếu đủ; chỉ dùng `water_tilt_table` khi số đo chứng minh phụ thuộc khoảng cách. Sau khi cộng bù, kiểm lại giới hạn góc và vùng ngắm.
- [ ] X.3. Chạy nhánh phun đơn đầu cuối, kiểm số lần thử, VERIFY và STOP. Không gán thành công chữa cháy thật cho một thử nghiệm mô phỏng trên sa bàn.
- [ ] X.4. Chỉ sau khi phun đơn ổn định, thử `both`: hai nguồn/tải đủ khả năng, AIM/CORRECT từng laser, phun hai node theo cơ chế có sẵn. Kiểm một node lỗi, cả hai lỗi và STOP toàn hệ thống.

**Đầu ra/file:** bảng đo bù nước, cấu hình từng node và log phun đơn/đôi. Tái sử dụng nội suy/điều phối sẵn có trước khi thêm code.

**Kiểm chứng:** test hình học, `tests/test_orchestrator.py`, bài thử tải kép và lỗi của từng node. Ghi sai số nước riêng, không dùng sai số laser thay thế.

**Điều kiện hoàn thành:** bù được kiểm bằng điểm nước thật; sau bù không vượt giới hạn; hai vòi/đường suy giảm khi một node lỗi được kiểm và dừng an toàn. Phạm vi không dùng bơm thì bỏ qua có lý do.

## Phase Y — Thử độ bền, đo lặp và tổng hợp số liệu

**Phụ thuộc:** V; thêm X khi nghiệm thu phun. **Thực hiện:** Codex thiết kế/phân tích; người vận hành chạy. **Trạng thái ban đầu:** TODO.

**Đọc trước:** `scripts/measure.py`, `report_errors.py`, `make_report.py`, `log_pi_load.py`, `orchestrator/events.py`.

### Công việc

- [ ] Y.1. Chốt trước bộ kịch bản và tiêu chí trong Phụ lục B: có bia/không bia, nhiều vị trí, từng node, mất liên lạc, mất camera, pose thay đổi, lệnh lỗi và STOP. Không chọn bỏ lượt thất bại sau khi thấy kết quả.
- [ ] Y.2. Thu log cấu trúc bằng công cụ sẵn có. Với PDR, ghi seq nhận được ở mức đủ chi tiết trước bộ hạn chế tần suất log/hiển thị; dữ liệu telemetry đã giảm mẫu không đủ để quy mọi khoảng trống thành mất gói. Tách reboot/wrap seq và gói lặp.
- [ ] Y.3. Tính sai số trước/sau căn, tỷ lệ đạt, thời gian từ Pi nhận alert tới các mốc, RTT heartbeat và tải Pi. Không lấy timestamp uptime H2 trừ timestamp Pi để tuyên bố độ trễ một chiều nếu chưa có đồng bộ/ước lượng sai lệch đồng hồ.
- [ ] Y.4. Chạy bộ đo lặp đã chốt, giữ cấu hình cố định trong một nhóm so sánh. Ghi node, firmware, model, calibration và điều kiện ánh sáng/nguồn để truy vết kết quả.
- [ ] Y.5. Dùng `report_errors.py`/`make_report.py` khi định dạng tương thích; chỉ mở rộng phần thiếu. Báo cả trường hợp không thấy vết, timeout, ngoài miền và lỗi cảm biến; phân biệt dữ liệu thật với simulator.

**Đầu ra/file:** log/CSV, bảng tổng hợp, hình/báo cáo tạo bằng công cụ có sẵn và danh sách hạn chế. File lớn đặt ngoài Git theo quy ước dự án, giữ đường dẫn/manifest cần tái lập.

**Kiểm chứng:** đối chiếu một số dòng dữ liệu và phép tính bằng tay; `tests/test_measure.py`, `tests/test_report.py` nếu sửa xử lý số liệu; chạy lại bài lỗi an toàn ít nhất theo bộ kiểm đã chốt.

**Điều kiện hoàn thành:** mọi kết luận có dữ liệu truy vết; mẫu số/tình huống lỗi rõ ràng; không gộp kiểm thử mô phỏng với kiểm thử phần cứng để làm đẹp tỷ lệ.

## Phase Z — Nghiệm thu cuối và bàn giao tái lập

**Phụ thuộc:** Y. **Thực hiện:** Codex tổng hợp; người vận hành xác nhận demo. **Trạng thái ban đầu:** TODO.

### Công việc

- [ ] Z.1. Rà toàn bộ phase và bằng chứng: phần bắt buộc đạt hoặc còn blocker được nêu thẳng; W–X chỉ được bỏ qua khi nằm ngoài phạm vi. Không đánh dấu DONE chỉ vì có file code.
- [ ] Z.2. Cập nhật README/runbook với đúng đường dẫn, lệnh, GPIO đã kiểm, profile servo, phiên bản và điều kiện camera. Loại bỏ hoặc ghi rõ ghi chú cũ mâu thuẫn với kết quả đã được xác minh.
- [ ] Z.3. Chuẩn bị quy trình đưa hệ thống lên từ trạng thái tắt: kiểm nguồn → OTBR → node → nhận diện/profile → camera/commissioning check → chế độ được cho phép → phép thử. Có quy trình dừng và khôi phục, không tự kích đầu ra khi service khởi động lại.
- [ ] Z.4. Chạy smoke test hai lượt liên tiếp theo cùng cấu hình; ghi video/log và mọi thao tác thủ công đã cần. Chạy bộ hồi quy phần mềm liên quan; các nhóm chưa chạy phải ghi nguyên nhân.
- [ ] Z.5. Bàn giao mã nguồn, hồ sơ hiệu chuẩn, cấu hình mẫu không chứa bí mật, dữ liệu đo và hướng dẫn demo. Chỉ commit/push/tag khi người dùng giao; không coi việc tạo tài liệu là quyền xuất bản repo.

**Đầu ra/file:** runbook hoàn chỉnh, bảng trạng thái phase, hồ sơ phiên bản/cấu hình, log test và danh sách việc còn mở.

**Kiểm chứng:** một người khác có thể làm theo tài liệu; xác nhận khả năng dừng ở mọi bước; đối chiếu nguồn số đo và build với thiết bị được bàn giao.

**Điều kiện hoàn thành:** tái lập được phạm vi đã chốt; không có bí mật trong artifact công khai; mọi giới hạn, test chưa chạy và nhánh tùy chọn đều được ghi trung thực.

---

## Phụ lục A — Lệnh tham chiếu và phạm vi kiểm chứng

Các lệnh dưới đây được đối chiếu với script/CI của snapshot. Trước khi dùng trên nhánh mới, đọc `--help` hoặc file tương ứng. Chúng là hướng dẫn cho lần triển khai, **không phải lệnh đã được chạy khi lập tài liệu**.

Shell ví dụ là Bash, trừ lệnh ESP-IDF có thể chạy trong terminal ESP-IDF trên Windows. Thay `<repo>`/`PORT`/IPv6 bằng thông tin thực; không copy nguyên placeholder. Trên Windows không có GNU Make/GCC, dùng môi trường phù hợp cho host test, không bỏ host test để coi flash thành công là đủ.

### C1. Test lõi C — chạy từ gốc repo

```bash
make -C firmware/sensor-h2/test CC=gcc

# Khi môi trường hỗ trợ ASan, tương ứng cách CI kiểm lõi C:
make -C firmware/sensor-h2/test CC=gcc \
  SAN='-fsanitize=address,undefined -fno-sanitize-recover=all'

# Chỉ khi cần FakeNode dùng lõi C qua thư viện dùng chung:
make -C firmware/sensor-h2/test CC=gcc libcore.so
```

Khi đổi cờ sanitizer mà binary đã tồn tại, kiểm Makefile có rebuild không; chỉ dọn artifact test được tạo ra theo `make clean` của đúng thư mục nếu cần. Không dùng `git clean` để xử lý. ASan không chạy được trong một số môi trường là giới hạn môi trường phải ghi lại, không phải lý do bỏ test chức năng.[R11]

### P1. Môi trường Python và test Pi

```bash
cd <repo>/pi

# Dành cho môi trường baseline mới; không dùng để vô ý gỡ extra đang cần.
uv sync

# Chuẩn bị dữ liệu mô phỏng theo CI; lệnh này ghi data/sim/commissioning.yaml.
uv run --no-sync python scripts/commission.py --source sim

uv run --no-sync ruff check src tests scripts
uv run --no-sync pytest -q --ignore=tests/test_decider.py --ignore=tests/test_jev.py
```

Hai nhóm decider/Jev không chạy trong lệnh baseline trên vì CI có job/dependency riêng, **không có nghĩa chúng bị loại khỏi chất lượng toàn dự án**. Khi sửa các module đó phải chuẩn bị dependency và chạy đúng test. Với YOLO, repo có `uv sync --extra yolo`; sau đó giữ môi trường bằng `uv run --no-sync`. Khi cần nhiều extras, khai đúng các extras cần giữ theo cấu hình dự án.[R1], [R11]

Ví dụ kiểm tập trung cho phần tích hợp:

```bash
uv run --no-sync pytest -q tests/test_net.py tests/test_info.py tests/test_discover.py
uv run --no-sync pytest -q tests/test_aim_point.py tests/test_yaw.py
uv run --no-sync pytest -q tests/test_orchestrator.py tests/test_dashboard.py
```

Các file test **mới được đề xuất** trong phase D/E/V chỉ được gọi sau khi Codex đã tạo và đăng ký chúng, không coi đó là tên test sẵn có.

### F1. Build firmware — chưa flash

```bash
cd <repo>/firmware/sensor-h2

# Chỉ lần đầu hoặc khi cần đổi target; kiểm/sao lưu cấu hình đang có trước đó.
idf.py set-target esp32h2
idf.py menuconfig
idf.py build
```

Đọc build log và kích thước partition. Giữ `sdkconfig`, binary và `managed_components` theo quy ước ignore của dự án. Không tự nâng ESP-IDF chỉ để tránh tìm nguyên nhân lỗi.

### F2. Flash — chỉ với board/cổng đã xác nhận

```bash
# Chạy trong firmware/sensor-h2 và terminal ESP-IDF đã chuẩn bị.
# PORT là cổng thực, ví dụ COM7 hoặc /dev/ttyACM0; không dùng ví dụ nếu chưa kiểm.
idf.py -p PORT flash monitor
```

Không tự thêm `erase-flash`. Nạp firmware ứng dụng không chứng minh NVS cũ đã bị thay; phải đọc log cấu hình. Với ảnh NVS riêng, lấy offset/kích thước từ partition table thực và dùng công cụ ESP-IDF, không dùng một địa chỉ flash cố định chỉ vì có trong bản README cũ.

### S1. Mô phỏng — không nối phần cứng

```bash
cd <repo>/pi
uv run --no-sync python scripts/run_station.py --source sim --decider rules

# Chạy sau khi dừng lệnh trước; CoAP ở đây tới FakeNode, không phải H2 thật.
uv run --no-sync python scripts/run_station.py --source sim --link coap --decider rules
```

Thoát từng bài đúng cách và kiểm log STOP. Dữ liệu simulator không được chép sang profile của sa bàn thật.

### N1. Kiểm mạng trên Pi

```bash
sudo ot-ctl state
sudo ot-ctl ipaddr
sudo ot-ctl child table
sudo ot-ctl router table
sudo ot-ctl srp server state
ip -6 addr show
ip -6 route show

# Từ thư mục pi:
uv run --no-sync python scripts/find_nodes.py
```

Không nhất thiết node đều nằm trong `child table`: vai trò router và cấu trúc mạng quyết định bảng cần xem. Dataset/network key dùng để cấu hình phải giữ riêng; không đưa output nhạy cảm vào báo cáo công khai.

### N2. Kiểm CoAP thủ công — trước tiên không bật tải

Đảm bảo CoAP receiver trên Pi đang nhận `/status`; chưa chạy orchestrator tự động. Tên binary `coap-client` phải được kiểm trên bản hệ điều hành đang dùng. Chọn `ID` lớn hơn ID gần nhất trong phiên và hợp lệ theo parser; ví dụ 101 dưới đây không phải ID bắt buộc.

```bash
ADDR="IPv6_THUC_CUA_NODE"
ID=101

coap-client -m get "coap://[$ADDR]/info"
coap-client -m post -N -e '' "coap://[$ADDR]/hb"

# Chỉ sau khi đã xác nhận góc hình học 0/0 an toàn:
coap-client -m post \
  -e "{\"id\":$ID,\"pan\":0,\"tilt\":0,\"ttl\":1500}" \
  "coap://[$ADDR]/aim"

# Theo dõi accepted/reached trong receiver/monitor, không chỉ terminal coap-client.
STOP_ID=$((ID + 1))
coap-client -m post -e "{\"id\":$STOP_ID}" "coap://[$ADDR]/stop"
```

Một lần `/hb` không đủ duy trì một phép thử laser dài: dùng heartbeat lặp theo cấu hình và kiểm phản hồi. Không có lệnh `/fire pump` trong bộ lệnh bring-up mặc định này. STOP trên node hiện tại tắt bơm/laser, không phải bảo đảm servo ngừng chuyển động cơ học.[R5], [R7]

### V1. Hiệu chuẩn và commissioning — dùng camera thật đã cấu hình

```bash
cd <repo>/pi
uv run --no-sync python scripts/calibrate_camera.py --help
uv run --no-sync python scripts/calibrate_camera.py --capture
uv run --no-sync python scripts/calibrate_camera.py

uv run --no-sync python scripts/commission.py
uv run --no-sync python scripts/commission.py --check
```

Các lệnh tạo file calibration thực; sao lưu dữ liệu đang dùng trước khi đo lại. Không dùng `--force` để bỏ qua nghiệm thu. Khi kiểm bằng simulator phải chỉ rõ `--source sim` ở script hỗ trợ nó.

### V2. Ngắm tọa độ — chỉ sau khi đủ điều kiện phase tương ứng

Tọa độ dưới đây là **ví dụ cú pháp**, không khẳng định nằm an toàn trong sa bàn người dùng. Thay bằng tọa độ thật đã được O/R duyệt.

```bash
cd <repo>/pi
uv run --no-sync python scripts/aim_point.py --help

# Phase R: vẫn cần camera/commissioning/model; không bật laser.
uv run --no-sync python scripts/aim_point.py --node s1 --x 0.30 --z 0.20 --no-laser

# Phase S: có thể bật laser thật. Chỉ chạy sau khi kiểm an toàn và có người vận hành.
uv run --no-sync python scripts/aim_point.py --node s1 --x 0.30 --z 0.20

# Chỉ khi toàn bộ lưới dự kiến đã được kiểm vùng an toàn:
uv run --no-sync python scripts/aim_point.py --node s1 --grid --repeat 3
```

`aim_point.py` đo ngắm điểm; không tự coi lệnh `--grid` là đã chạy vòng CORRECT. Kiểm vòng căn theo T bằng logic hiện có/harness được bổ sung có chủ đích. Không chạy đồng thời script này và trạm thật nếu tranh camera/cổng CoAP.

### Không dùng lệnh dự kiến như thể đã tồn tại

Các tên như `--laser-only`, `--calibration`, `--set-home`, `MOVE`, `HOME` qua Serial, hoặc script xuất NVS mới **không được coi là API hiện có**. Sau khi một phase thực sự bổ sung chức năng, cập nhật tài liệu bằng CLI/schema đúng và test kèm theo. Giao diện chính của node trong repo là CoAP, không phải giao thức Serial của chương trình test Arduino.

## Phụ lục B — Bộ nghiệm thu và cách ghi chỉ số

Các số dưới đây là **đề xuất cho kế hoạch**, trừ giá trị được dẫn từ cấu hình/runbook. Nhóm phải chốt trước đợt đo; không âm thầm thay ngưỡng sau khi thấy kết quả. Không ô nào dưới đây là kết quả đã đo.

| Bài thử | Mẫu/điều kiện đề xuất | Tiêu chí hoặc dữ liệu phải có |
|---|---|---|
| Khởi động servo | 5 lần mỗi cụm | HOME lặp lại, không kẹt/reset, bơm/laser không tự bật |
| Góc–xung | Ít nhất 5 điểm/trục, có lượt đi/về | Số đo góc thật, chiều, độ rơ; không đồng nhất với góc lệnh |
| Ngắm laser ban đầu | Ít nhất 5 điểm khác nhau mỗi node | Mục tiêu runbook ≤5 cm; lưu cả lượt không đạt.[R8] |
| Căn vòng kín | 9 vị trí × 3 lượt × 2 node, chỉ trong miền an toàn | Đề xuất ≥90% lượt có sai số cuối ≤3 cm; chốt trước khi đo. 1 cm là ngưỡng dừng vòng căn đang cấu hình, không phải tự động là tiêu chí mọi lượt.[R6] |
| Luồng laser tự động | 20 lượt có bia, chia đều hai node; 10 lượt không bia | Đúng mục tiêu/node hoặc từ chối hợp lệ; không bật bơm; không ghi báo động giả là thành công |
| Lỗi an toàn | Ít nhất 3 lần mỗi loại lỗi liên quan | Kết cục và thời gian tắt đo được; không tự nới HB/TTL để bài qua |
| Telemetry/PDR | Đề xuất 30 phút, hai node, cấu hình cố định | Seq duy nhất, gói lặp, reboot; tính trên dữ liệu nhận đủ chi tiết. Ngưỡng PDR phải lấy từ đặc tả được nhóm chốt |
| Độ trễ | Dùng log monotonic ở cùng máy cho các mốc có thể so | Median/p95/max và số mẫu; RTT không gọi là độ trễ một chiều |
| Phun nước | Chỉ khi mở W–X | Sai số nước và tắt tải thật; không dùng sai số laser thay cho sai số phun |

**Cách tính tối thiểu:**

- Sai số ngắm: khoảng cách giữa điểm đích và điểm quan sát trong cùng hệ tọa độ, đổi mét → cm đúng một lần. Khi dùng mặt phẳng khác nhau, lưu rõ wall/floor.
- Tỷ lệ đạt: số lượt đạt / tổng số lượt đủ điều kiện đã định nghĩa trước. Báo riêng ngoài tầm, mất vết và lỗi link; không lặng lẽ loại chúng khỏi mẫu số để tăng tỷ lệ.
- PDR: cần biết số gói dự kiến và số seq duy nhất nhận được trong cùng phiên node; tách reset/wrap và thời gian node được chủ động tắt. Log đã giảm mẫu không thay được log nhận gói để tính PDR.
- Độ trễ hệ thống: tách `Pi nhận alert → định vị`, `định vị → ngắm/căn xong` và `Pi nhận alert → kết thúc`. Muốn đo `node phát → Pi nhận` phải có phương pháp đồng bộ/đánh giá sai lệch đồng hồ riêng.
- Trạng thái an toàn: ghi lệnh/đầu ra vật lý và thời điểm, không chỉ chuỗi log. Độ phân giải thiết bị đo phải được nêu khi so ngưỡng thời gian.

## Phụ lục C — Sổ trạng thái và mẫu bàn giao cho từng phiên

### Bảng trạng thái hiện hành

Giữ checkbox chưa đánh dấu cho tới khi có bằng chứng tương ứng. Khi phase bị chặn, ghi điều kiện còn thiếu thay vì tự suy ra đã hoàn thành từ file tồn tại.

| Phase | Trạng thái | Bằng chứng / commit / việc còn thiếu |
|---|---|---|
| A | BLOCKED | Khảo sát baseline đã thực hiện; bộ C chuẩn, sanitizer, lint, pytest và build ESP-IDF còn blocker. Xem nhật ký Phase A bên dưới. |
| B | WAITING_HARDWARE | Người vận hành đã quy định A → s1/tag 21, B → s2/tag 22 và báo COM hiện tại; B.4 còn chờ dấu nhận diện board ổn định và xác minh Node ID firmware qua `/info`. |
| C | WAITING_HARDWARE | Có 11 góc cơ khí ước lượng bằng mắt và mô hình sơ bộ; thiếu mốc ngắm hình học, đo góc chính xác, dữ liệu đi/về và hướng laser. Chưa nghiệm thu hiệu chuẩn phần cứng. |
| D | SOFTWARE_VERIFIED | Module C thuần và 173 kiểm tra tập trung đạt; chưa tích hợp firmware, bộ C chuẩn còn bị lỗi `proto.c:116` và sanitizer thiếu `libubsan`. Fixture chỉ dùng góc cơ khí sơ bộ. |
| E | TODO | Chưa triển khai theo kế hoạch này |
| F | TODO | Chưa triển khai theo kế hoạch này |
| G | TODO | Chưa có log build/nạp cho cấu hình mới |
| H | TODO | Cần xác nhận HOME bằng firmware chính |
| I | TODO | Chưa có baseline môi trường Pi của lượt triển khai này |
| J | TODO | Cần kiểm trạng thái OTBR thực tế |
| K | TODO | Cần kiểm hai node và ánh xạ địa chỉ thực tế |
| L | TODO | Cần log góc lệnh và trạng thái hai chiều |
| M | TODO | Cần bằng chứng chốt chặn trên cấu hình mới |
| N | TODO | Cần số đo cảm biến và log cảnh báo |
| O | TODO | Cần số đo sa bàn/offset/giới hạn vận hành |
| P | TODO | Cần kiểm bộ intrinsics đúng camera đang dùng |
| Q | TODO | Cần commissioning/check trên sa bàn thật |
| R | TODO | Cần test tọa độ tới góc trên thiết bị |
| S | TODO | Cần phép đo laser/timing/sai số ban đầu |
| T | TODO | Cần phép đo trước/sau căn |
| U | TODO | Cần kiểm model và quyết định trên nguồn ảnh thật |
| V | TODO | Cần laser-only/chặn bơm và test end-to-end |
| W | BLOCKED_OPTIONAL | Chưa có xác nhận mở chức năng phun |
| X | BLOCKED_OPTIONAL | Phụ thuộc W; không bắt buộc cho MVP laser |
| Y | TODO | Chưa có bộ số liệu nghiệm thu của lộ trình này |
| Z | TODO | Chưa nghiệm thu/bàn giao |

`BLOCKED_OPTIONAL` ở W/X chỉ có nghĩa chưa được mở phạm vi, không phải một lỗi của MVP. Khi chốt không làm, đổi thành `SKIPPED_OPTIONAL` và ghi quyết định.

### Mẫu cập nhật sau mỗi phase/lát cắt

```markdown
### Nhật ký: Phase <chữ> / task <mã>
- Ngày, người thực hiện:
- Nhánh / commit bắt đầu:
- Mục tiêu được giao:
- File đã đọc liên quan:
- File đã sửa / file mới:
- Thay đổi của người dùng được giữ nguyên:
- Lệnh kiểm chứng đã chạy:
- Kết quả thực tế, số test pass/fail/skip và log:
- Kiểm phần cứng đã làm: board / nguồn / profile / phương pháp đo:
- Phần chưa chạy và nguyên nhân:
- Rủi ro hoặc lỗi còn mở:
- Trạng thái: TODO / IN_PROGRESS / SOFTWARE_VERIFIED / WAITING_HARDWARE /
  HARDWARE_VERIFIED / BLOCKED / BLOCKED_OPTIONAL / DONE / SKIPPED_OPTIONAL
- Phase/task tiếp theo có thể thực hiện:
- Quyền thao tác còn cần xác nhận: flash / NVS / bật tải / xuất bản repo:
```

### Metadata nên đi cùng dữ liệu thực nghiệm

```text
run_id, recorded_at, source(real|sim), repo_commit, firmware_version,
node_id, assembly_id, calibration_profile, camera_profile, model_version,
target_surface, target_x_m, target_y_m, target_z_m,
pan_cmd_deg, tilt_cmd_deg, observed_x_m, observed_y_m, observed_z_m,
miss_cm, correction_iterations, outcome, failure_reason, log_path
```

Đây là danh sách trường cần truy vết, **không bắt buộc tạo schema log mới** nếu log hiện có đã chứa hoặc có thể ghép ra chúng. Không thêm cột “góc đo” bằng cách sao chép góc lệnh.

### Nhật ký: Phase A / khảo sát baseline (25/10/2026)

- **Repo lúc khảo sát:** `main`, HEAD `07eddcd66d3043c81af9c511c9c12c5c1ee8a762`, working tree có hai file untracked: `calibration/pantilt_calibration.json` và tài liệu này. Nhánh `phase-A` từng được tạo tại cùng HEAD, sau đó đã xóa và quay về `main` theo yêu cầu người dùng; không có commit riêng. Không tìm thấy `AGENTS.md` trong repo.
- **Phạm vi đã khảo sát:** đường góc → Kconfig/NVS → PWM hiện dùng chung một cặp min/max cho pan và tilt; `/aim` đi qua lõi actuator rồi gửi `/status`, `reached` ước tính theo thời gian; FIRE của orchestrator còn gọi `dev=pump`. Đây là đối chiếu mã nguồn, chưa phải kiểm chứng trên thiết bị.
- **Lệnh thực tế (PowerShell):** `git branch --show-current`, `git rev-parse HEAD`, `git status --porcelain=v1 -uall`; từ gốc repo chạy `& 'C:\msys64\usr\bin\make.exe' -C firmware/sensor-h2/test CC=gcc`; từ `pi/` chạy `& 'C:\Users\dotha\anaconda3\python.exe' -m ruff check src tests scripts`, rồi đặt `$env:PYTHONPATH='src'` và chạy `& 'C:\Users\dotha\anaconda3\python.exe' -m pytest -q --ignore=tests/test_decider.py --ignore=tests/test_jev.py`; tiếp theo `uv sync --offline --python 3.13`; kiểm `idf.py --version`.
- **Chẩn đoán lõi C riêng:** từ `firmware/sensor-h2/test/`, biên dịch trực tiếp cùng danh sách nguồn/flags của Makefile, thêm `-Wno-error=float-conversion`. Lần giữ `-fsanitize=undefined -fno-sanitize-recover=undefined` không liên kết được vì thiếu `libubsan`. Lần bỏ sanitizer chạy binary tạm với `-v` và đạt **6.773 checks**; binary tạm đã xóa. Đây là lần chạy chẩn đoán đã nới một cảnh báo, **không thay thế bộ kiểm thử C chuẩn hoặc kết quả sanitizer**.
- **Kết quả chuẩn và blocker:** `make` dừng ở `firmware/sensor-h2/main/core/proto.c:116` do GCC 15.2 báo `float-conversion` dưới `-Werror`; sanitizer chưa chạy đạt. Ruff báo 7 lỗi E731 trong mã hiện có. Pytest chưa thu thập được test vì `conftest.py` cần `cv2`; không có số pass/fail/skip của pytest. `uv sync --offline` thiếu wheel `opencv-python==5.0.0.93` trong cache; `.venv` tạm đã xóa. Build ESP-IDF **NOT RUN** vì `idf.py` chưa có. Không sửa lỗi C/lint hay cài toolchain trong Phase B.
- **Môi trường:** Windows 10 build 19045.7807; GCC 15.2.0; Python Conda 3.13.9, Python hệ thống 3.14.3; uv 0.13.5; phiên bản ESP-IDF chưa lấy được vì thiếu `idf.py`.
- **Phạm vi MVP:** ưu tiên định vị–laser; chế độ laser-only trong code chưa được xác nhận. W–X (bơm) chưa được cho phép triển khai. Không chạy orchestrator trên thiết bị thật, không flash/ghi NVS hoặc gửi lệnh phần cứng trong phiên khảo sát/Phase B.
- **Trạng thái:** khảo sát baseline **đã thực hiện**; Phase A giữ `BLOCKED` cho bộ kiểm thử chuẩn và build chưa đạt/chưa chạy. File chưa commit của người dùng được giữ nguyên trong phiên khảo sát.

### Nhật ký: Phase B / B.1–B.5 (25/10/2026)

- **Repo lúc bắt đầu:** `main`, HEAD `07eddcd66d3043c81af9c511c9c12c5c1ee8a762`; hai file untracked như nhật ký Phase A. Không tạo hoặc chuyển nhánh.
- **Lệnh kiểm tra:** `rg --files --hidden --no-ignore -g 'pantilt_calibration.json' -g 'pahntilt_calibration.json' -g '!.git/**'`; `rg` tìm tham chiếu trong `calibration/`, `docs/`, `firmware/`, `config/`, `pi/`; `python -` đọc JSON bằng `json.loads`, kiểm đúng khóa, kiểu số (loại `bool`), `math.isfinite`, `min_us < home_us < max_us`, và đối chiếu từng bộ với mục 3.1. Chỉ tìm thấy `calibration/pantilt_calibration.json`; không có file sai chính tả hoặc bản số đo thứ hai.
- **Nguồn số đo:** `recorded_on=2026-10-25`, `source=user_provided`, `signal=servo_pwm_pulse_width`; JSON gốc giữ nguyên và chọn làm đường dẫn chuẩn. Đơn vị là µs; chưa có góc vật lý, chiều quay hay vùng vận hành an toàn.

| Cụm | Trục | MIN / HOME / MAX (µs) | Đối chiếu dữ liệu gốc |
|---|---|---|---|
| A | Pan | 500 / 1500 / 2500 | Khớp; hữu hạn; đúng thứ tự |
| A | Tilt | 600 / 1500 / 2500 | Khớp; hữu hạn; đúng thứ tự |
| B | Pan | 500 / 1500 / 2500 | Khớp; hữu hạn; đúng thứ tự |
| B | Tilt | 600 / 1500 / 2500 | Khớp; hữu hạn; đúng thứ tự |

`config/site.yaml` dự kiến node `s1` dùng tag 21, `s2` dùng tag 22; địa chỉ mạng của cả hai còn `null`. Kconfig mặc định `node_id=s1` và NVS có thể ghi đè, nên mặc định đó không chứng minh danh tính board. Chưa có nhãn, log hoặc quan sát cổng nạp của thiết bị:

| Cụm vật lý | Node ID | Dấu nhận diện board | Cổng nạp quan sát | Tag ID dự kiến | Trạng thái xác nhận |
|---|---|---|---|---|---|
| A | Chưa xác nhận (`s1`/`s2`) | Chưa có bằng chứng | Chưa quan sát | Chưa gán cho A; cấu hình `s1=21`, `s2=22` | **UNCONFIRMED** |
| B | Chưa xác nhận (`s1`/`s2`) | Chưa có bằng chứng | Chưa quan sát | Chưa gán cho B; cấu hình `s1=21`, `s2=22` | **UNCONFIRMED** |

| Task roadmap | Trạng thái | Bằng chứng / việc còn thiếu |
|---|---|---|
| B.1 | DONE | Đã tìm cả hai cách viết tên file, không thấy bản trùng/khác. |
| B.2 | DONE | Đường dẫn chuẩn là `calibration/pantilt_calibration.json`; không đổi JSON hoặc tạo bản sao. |
| B.3 | SOFTWARE_VERIFIED | Parse, cấu trúc, số hữu hạn, thứ tự và bốn bộ số đều đạt; góc vật lý vẫn chưa đo. |
| B.4 | WAITING_HARDWARE | Chưa xác nhận nhãn A/B, node ID, board, cổng nạp, tag trên thiết bị; chặn thao tác nạp và lệnh phụ thuộc ánh xạ. |
| B.5 | DONE | `calibration/README.md` mô tả JSON, Kconfig/NVS và phần profile/runtime chưa triển khai. |

- **File thay đổi trong phiên này:** chỉ tài liệu này và `calibration/README.md`; JSON gốc giữ nguyên. Không kiểm phần cứng, không mở serial, flash/ghi NVS, gửi CoAP, điều khiển tải, commit hoặc push.
- **Trạng thái Phase B:** hồ sơ và tài liệu đã hoàn tất; toàn Phase B là `WAITING_HARDWARE` cho B.4, chưa nghiệm thu danh tính vật lý. Phase A còn các blocker kiểm thử đã ghi trên. Chỉ tiếp tục phần phụ thuộc khi người vận hành cung cấp bằng chứng ánh xạ.

### Nhật ký: Phase C / chuẩn bị đo, chưa thao tác thiết bị (25/10/2026)

- **Repo lúc bắt đầu:** `main`, HEAD `07eddcd66d3043c81af9c511c9c12c5c1ee8a762`; giữ các thay đổi chưa commit của Phase B và JSON gốc. Không tạo/chuyển nhánh.
- **Nguồn đã đọc:** Phase B/C, quy ước tọa độ và quy tắc hiệu chuẩn trong tài liệu này; `calibration/README.md`, `calibration/pantilt_calibration.json`, mục servo của `docs/trien-khai-phan-cung.md`, `pi/src/nt532/orchestrator/aiming.py`.
- **Lệnh kiểm tra chỉ đọc:** `git status --porcelain=v1 -uall`, `git branch --show-current`, `git rev-parse HEAD`; `Test-Path calibration/servo_measurements.csv`; `rg --files --hidden --no-ignore -g '!.git/**'` lọc tên servo/pan–tilt/Arduino/`.ino`, và `rg` tìm dấu hiệu chương trình điều khiển xung trong file nguồn. Không tìm thấy sketch/chương trình test servo độc lập đã dùng; chỉ thấy test firmware/Pi và HAL hiện hành. Chưa mở serial hoặc chạy bất kỳ chương trình thiết bị nào.
- **File cập nhật:** thêm hướng dẫn đo và checklist vào `calibration/README.md`; tạo `calibration/servo_measurements.csv` **chỉ có header** `assembly,node,axis,pulse_us,measured_deg,direction,repeat,notes`. Cập nhật bảng trạng thái và nhật ký ở đây; không sửa JSON gốc hoặc logic.
- **Kiểm chứng tài liệu:** `git diff --check` trả mã 0; CSV có đúng một dòng header và không có hàng dữ liệu; SHA-256 của JSON gốc vẫn là `4FE64DF980BAAC95BED58A51D058A5154FDC5690C11090006FB77F7F6849E5A8`. Không chạy test firmware/Pi vì phiên này chỉ chuẩn bị biểu mẫu.
- **Phân biệt bắt buộc:** HOME 1500 µs chưa chứng minh góc hình học 0; góc thực phải đo theo hướng tham chiếu riêng của cụm, không chép góc nội suy của chương trình. Chiều tiếp cận `increasing_pulse`/`decreasing_pulse` khác dấu góc. Phạm vi xung đã thử không thay thế phạm vi góc đo được hay vùng vận hành an toàn.

| Task Phase C | Trạng thái | Bằng chứng còn cần |
|---|---|---|
| C.1 | WAITING_HARDWARE | Chương trình/cách dùng test servo đã chạy được, board và dây đúng, quan sát HOME với bơm/laser tắt. |
| C.2 | WAITING_HARDWARE | Mốc pan/yaw và tilt 0 của từng cụm, góc vật lý tại HOME và điểm 0 nếu khác. |
| C.3 | WAITING_HARDWARE | Chiều quay khi tăng xung và số đo hai chiều cho từng trục, khoảng 5 điểm/trục trong vùng dùng. |
| C.4 | WAITING_HARDWARE | CSV chỉ có header; chưa có hàng số đo thực hoặc bằng chứng nhận diện board/cụm. |
| C.5 | WAITING_HARDWARE | Chờ bảng góc–xung và B.4; chưa chốt hệ số, mô hình, offset, chiều đảo hay profile runtime. |

- **Thông tin người vận hành cần cung cấp:** nhãn/ảnh nhận diện hai cụm và board dùng từng lượt; xác nhận A/B ↔ node ID ↔ tag ID và cổng nạp quan sát nếu có; chương trình test servo đã dùng cùng cú pháp đặt xung thật; sơ đồ dây Pan/Tilt và nguồn; cách đánh dấu mốc pan/yaw, tilt 0; bảng đo góc thực đi/về cho bốn trục, ghi cả góc tại 1500 µs. Nếu một board test dùng chung, ghi thứ tự và thời điểm đổi kết nối. Không tự điền ô `node` trước xác nhận.
- **Trạng thái tại thời điểm chuẩn bị:** chỉ hoàn thành phần chuẩn bị biểu mẫu. Phase C `WAITING_HARDWARE`, không có nghiệm thu C.1–C.4; C.5 chờ dữ liệu. B.4 vẫn `WAITING_HARDWARE`; blocker Phase A (C chuẩn, sanitizer, lint, pytest, ESP-IDF build) vẫn mở. Thông tin người vận hành gửi sau đó được ghi trong nhật ký tiếp theo. Không flash, ghi NVS, gửi lệnh, chạy orchestrator trên thiết bị, commit hoặc push; chưa triển khai Phase D.

### Nhật ký: Phase C / quan sát thị giác và mô hình sơ bộ (25/10/2026)

- **Repo lúc bắt đầu:** `main`, HEAD `07eddcd66d3043c81af9c511c9c12c5c1ee8a762`; `calibration/README.md` đã sửa, JSON/CSV và roadmap chưa được Git theo dõi. Giữ nguyên phần có sẵn, không tạo/chuyển nhánh hoặc commit/push.
- **Nguồn:** người vận hành cung cấp danh tính theo quy định, chương trình test đã dùng, chiều quay và góc cơ khí **ước lượng bằng mắt** qua hội thoại; không có log/ảnh đo hoặc sketch trong working tree. Đã đọc Phase B/C, quy ước tọa độ, JSON gốc, CSV, README, mục servo của `docs/trien-khai-phan-cung.md` và `pi/src/nt532/orchestrator/aiming.py`.
- **Board/test:** hai board được báo là ESP32-H2 Super Mini; Arduino IDE + ESP32Servo 3.2.1, Pan GPIO4, Tilt GPIO5, PWM 50 Hz. `HOME` đưa cả hai trục về 1500 µs; `US PAN_US TILT_US` đặt xung trực tiếp. Đầu vào `180 180` thuộc thang lệnh giả lập 0–360, **không phải số đo góc**. Cú pháp là thông tin người vận hành cung cấp, không được chạy trong phiên này; sketch chưa nằm trong repo. Chưa có bằng chứng kiểm nguồn, GND và tình trạng bơm/laser trong từng lượt đo.

| Cụm vật lý | Node ID quy định | Board/dấu nhận diện | Cổng COM lúc báo cáo | AprilTag đã gắn | Mức xác nhận |
|---|---|---|---|---|---|
| A | s1 | ESP32-H2 Super Mini; chưa có dấu board ổn định | COM19 (có thể đổi) | 21 | Người vận hành xác nhận ánh xạ; Node ID firmware qua `/info` **UNCONFIRMED** |
| B | s2 | ESP32-H2 Super Mini; chưa có dấu board ổn định | COM18 (có thể đổi) | 22 | Người vận hành xác nhận ánh xạ; Node ID firmware qua `/info` **UNCONFIRMED** |

Không suy vị trí trái/phải từ tên cụm. B.4 vẫn `WAITING_HARDWARE` cho dấu nhận diện board ổn định và danh tính firmware runtime; chưa xuất profile NVS hoặc phát lệnh phụ thuộc ánh xạ.

- **HOME/chiều quay người vận hành báo:** Pan và Tilt ở A/B đều HOME 1500 µs, khớp cả bốn `home_us` trong JSON. Tilt HOME dựng đứng khoảng 90° so với đế Pan. **Hướng laser mong muốn tại HOME là hướng lên trên**; hướng thực tế sau khi lắp đặt chưa được xác nhận. Trên cả hai cụm, giảm PWM: Pan quay phải, Tilt quay lên; tăng PWM: Pan quay trái, Tilt quay xuống. Góc ngắm dự án Pan dương trái, Tilt dương lên; `pan_inv=0`, `tilt_inv=1` mới là dự kiến, chưa ghi NVS hoặc kiểm đường firmware. Pan HOME 90° trong bảng tính **không phải quan sát trực tiếp**. Đính chính hướng laser không thay đổi công thức PWM, giới hạn xung hoặc cấu hình đảo chiều.
- **Quan sát thị giác (µs → góc cơ khí xấp xỉ, không phải góc ngắm laser):** A.pan 1000→135°, 2000→45°; A.tilt 1000→45°, 1500→90° HOME, 2000→135°; B.pan 500→180°, 1000→135°, 2000→45°, 2500→0°; B.tilt 1500→90° HOME, 2000→135°. CSV chứa đúng 11 hàng `visual_estimate`; `direction` và `repeat` để trống vì chưa biết chiều tiếp cận và số lần lặp. COM trong notes chỉ là cổng lúc báo cáo.
- **Mô hình sơ bộ:** `pan_mechanical_deg = 90 - 0.09 * (pulse_us - 1500)`; `tilt_mechanical_deg = 90 + 0.09 * (pulse_us - 1500)`. Pan tính toán giảm đơn điệu và Tilt tăng đơn điệu theo xung; tại cả 11 điểm, kết quả toán học bằng số ước lượng đã báo. Điều này chỉ cho thấy **mô hình khớp dữ liệu ước lượng**, không cho phép báo sai số đo bằng 0. Bảng tính đầy đủ ở `calibration/README.md`; Pan A chỉ có điểm 1000–2000 µs, Tilt A 1000–2000 µs, Pan B 500–2500 µs, Tilt B 1500–2000 µs. Các đoạn giữa điểm là nội suy, ngoài khoảng quan sát từng trục là ngoại suy. JSON gốc giữ Pan 500–2500 µs, Tilt 600–2500 µs; giá trị Tilt ở 500 µs trong bảng tính nằm ngoài phạm vi JSON. Không dùng các giới hạn xung làm bằng chứng vùng góc hoặc hành trình an toàn.
- **Mốc hình học còn thiếu:** góc cơ khí Tilt 90° khi HOME không đồng nghĩa với Tilt ngắm 0° (phương ngang trong `aiming.py`). Chưa đo hướng pan tham chiếu, mặt phẳng Tilt 0, hướng laser tại HOME, offset laser so với cơ cấu hoặc độ lặp/độ rơ. Không áp mô hình vào tự động ngắm hoặc đổi cấu hình runtime.
- **Lệnh kiểm chứng đã chạy:** `git branch --show-current`, `git rev-parse HEAD`, `git status --porcelain=v1 -uall`; đọc file và tìm sketch bằng `Get-Content`, `rg`; chạy Python Conda 3.13.9 với `csv.DictReader`, `json.loads`, `hashlib.sha256`, `math.isfinite` để kiểm schema, 11 hàng đúng tập quan sát, bốn bộ MIN/HOME/MAX, mô hình tại 11 điểm và tính đơn điệu ở 500/600/1000/1500/2000/2500 µs; `git diff --check` và `git status --porcelain=v1 -uall`. Kết quả: 11/11 hàng khớp mô hình theo số ước lượng, Pan tính `[180,171,135,90,45,0]°`, Tilt tính `[0,9,45,90,135,180]°`; bốn bộ JSON đúng giá trị gốc; SHA-256 JSON `4FE64DF980BAAC95BED58A51D058A5154FDC5690C11090006FB77F7F6849E5A8`, bằng giá trị phiên trước; `git diff --check` mã 0. Kiểm cả khoảng trắng cuối dòng hai file mới chưa được Git theo dõi bằng Python, đạt. Không chạy test firmware/Pi vì phiên này chỉ ghi dữ liệu/tài liệu; không mở cổng Serial, flash, NVS hoặc điều khiển tải.

| Task | Trạng thái hiện tại | Bằng chứng và phần còn thiếu |
|---|---|---|
| C.1 | IN_PROGRESS | HOME bốn trục và Tilt dựng đứng được người vận hành báo; chưa có xác minh nguồn, cơ khí, bơm/laser tắt trong phiên đo và sketch/log tái lập. |
| C.2 | WAITING_HARDWARE | Chiều quy ước đã rõ; chưa đánh dấu/đo hướng Pan tham chiếu, mặt phẳng Tilt ngắm 0 và hướng laser thực. |
| C.3 | IN_PROGRESS | Đã biết chiều quay A/B và một số điểm thị giác; thiếu bước nhỏ có log, khoảng 5 điểm/trục đo bằng dụng cụ, đi/về và lặp. |
| C.4 | DONE | Đã ghi đủ 11 quan sát được cung cấp theo schema gốc, đánh dấu nguồn ước lượng; mọi `direction`/`repeat` chưa biết để trống. DONE cho việc ghi dữ liệu hiện có, không phải nghiệm thu số đo Phase C. |
| C.5 | IN_PROGRESS | Mô hình tuyến tính/chiều biến thiên kiểm toán học sơ bộ; chưa chốt hệ số, offset, đảo chiều NVS, độ rơ hoặc phạm vi góc có hiệu lực. |

- **Người vận hành cần cung cấp tiếp:** sketch nguyên bản và phiên bản đã nạp cho từng board; ảnh/nhãn nhận diện board bền vững và xác minh `node_id` runtime qua `/info` trong một phiên được cho phép; nhật ký nguồn/GND, tình trạng laser/bơm và dây Pan/Tilt từng lượt; mốc Pan theo hướng tham chiếu riêng mỗi cụm, mặt phẳng Tilt ngắm 0 và hướng tia laser thực tại HOME; số đo bằng thước góc khoảng 5 điểm/trục trên cả bốn trục với chiều tiếp cận tăng/giảm và lần lặp, đặc biệt Pan ở HOME. Không cần chạy sát giới hạn cơ khí.
- **Kết luận:** Phase C giữ `WAITING_HARDWARE`. Có đủ cơ sở để **thiết kế và kiểm thử toán học** phép đổi góc–xung của Phase D bằng dữ liệu sơ bộ/fixture có nhãn; chưa đủ cơ sở chốt phép đổi góc ngắm, phạm vi an toàn hoặc triển khai trên thiết bị. Phase D chưa thực hiện. Phase A vẫn `BLOCKED`: bộ C chuẩn lỗi `float-conversion` tại `proto.c:116`; 6.773 checks chỉ là lần chẩn đoán nới kiểm tra; sanitizer thiếu `libubsan`; lint có 7 E731; pytest thiếu `cv2`; ESP-IDF build chưa chạy vì thiếu `idf.py`.

### Nhật ký: Phase D / phép đổi góc cơ khí–xung C thuần (25/10/2026)

- **Repo lúc bắt đầu:** `main`, HEAD `07eddcd66d3043c81af9c511c9c12c5c1ee8a762`. Working tree đã có `calibration/README.md` sửa và ba file chưa theo dõi: JSON, CSV, roadmap này. Không tạo/chuyển nhánh, reset, clean, commit hoặc push.
- **Đã đọc:** Phase C/D và nhật ký ở đây; JSON, CSV, `calibration/README.md`; `hal_esp.c`, `node_cfg.h`, `main/core/`, `test/Makefile`, `test/test_main.c`, `test/check.h`. Core chưa có phép đổi góc–xung phù hợp; HAL hiện dùng một cặp xung chung và nội suy `[-90,+90]`, nên module mới **chưa được nối vào HAL hoặc firmware**.
- **File tạo:** `main/core/servo_map.c/.h`, `test/test_servo_map.c`. **File sửa:** `test/Makefile`, `test/check.h`, `test/test_main.c` và nhật ký/checkbox Phase D ở đây. JSON và CSV giữ nguyên; không sửa HAL, node_cfg, Kconfig/NVS, CoAP hoặc `/aim`.
- **API:** `servo_map_to_pulse()` và `servo_map_to_relative()` nhận profile điểm **góc cơ khí tuyệt đối ↔ µs cho từng trục**, có MIN/HOME/MAX xung riêng. Đầu vào góc là **độ lệch cơ khí tương đối với HOME**, không phải góc ngắm hình học. Tính góc cơ khí đích bằng `HOME_angle + (invert ? -relative_deg : relative_deg) + offset_deg`; phép đổi ngược khử đúng một lần cùng offset/dấu. `invert`/`offset_deg` của API này chưa phải diễn giải trực tiếp các key NVS cũ. Kết quả xung là `float` µs trước lượng tử hóa PWM; lỗi trả `false` và giữ output cũ. Cấu hình cần ít nhất hai điểm xung tăng, góc đơn điệu nghiêm ngặt, có điểm HOME, các điểm nằm trong giới hạn xung. Chỉ nội suy trong miền điểm đã cấp; ngoài miền, NaN/Inf, điểm trùng hoặc cấu hình sai bị từ chối, không clamp/ngoại suy.
- **Fixture test:** Pan 1000→135°, 1500→90°, 2000→45°; Tilt 1000→45°, 1500→90°, 2000→135°. Pan tại HOME 90° là **suy từ mô hình**, chưa có quan sát độc lập. Fixture biên Pan 500/1500/2500 và Tilt 600/1500/2500 dùng giá trị **giả định cho kiểm thử toán học**, không xác nhận hành trình an toàn hay miền góc ngắm. Module không hardcode hệ số 0,09°/µs, giới hạn A/B hoặc góc laser; caller phải cung cấp profile đã kiểm khi tích hợp sau này.
- **TDD và lệnh kiểm chứng:** biên dịch test tập trung trước khi có implementation bằng `gcc -std=c11 -Wall -Wextra -Werror -Wshadow -Wconversion -O1 -g -Ifirmware/sensor-h2/main/core -DSERVO_MAP_STANDALONE firmware/sensor-h2/test/test_servo_map.c -o firmware/sensor-h2/test/test_servo_map_run.exe -lm` thất bại như mong đợi vì thiếu định nghĩa `servo_map_to_pulse`/`servo_map_to_relative` (RED). Thêm `firmware/sensor-h2/main/core/servo_map.c` vào lệnh trên; sau khi sửa hai cảnh báo chuyển kiểu trong module mới, test đầu tiên đạt 8 checks; mở rộng test đạt **173 checks, 0 fails** với cùng cờ nghiêm ngặt, không sanitizer. File exe tập trung tạm đã xóa sau kiểm tra.
- **Bộ C chuẩn:** `C:\msys64\usr\bin\make.exe -C firmware/sensor-h2/test CC=gcc` vẫn dừng tại `main/core/proto.c:116` vì `-Werror=float-conversion`, trước khi chạy test; không có số pass/fail của full suite. Biên dịch riêng module mới với `-fsanitize=undefined -fno-sanitize-recover=undefined` không liên kết được vì thiếu `-lubsan`; sanitizer **NOT RUN**. Không nới cảnh báo toàn dự án hoặc sửa lỗi ngoài phạm vi. Kiểm tra cuối `git diff --check` và `git status --porcelain=v1 -uall` được ghi khi bàn giao.

| Task Phase D | Trạng thái phần mềm | Bằng chứng / giới hạn |
|---|---|---|
| D.1 | SOFTWARE_VERIFIED | Hàm C thuần không phụ thuộc ESP-IDF; test tập trung biên dịch với cờ nghiêm ngặt. |
| D.2 | SOFTWARE_VERIFIED | Profile từng trục, nội suy thuận/ngược, offset/đảo chiều một lần và lỗi đầu vào; điểm hiệu chuẩn thực chưa chốt. |
| D.3 | SOFTWARE_VERIFIED | Test HOME, 1000/1500/2000, biên Pan/Tilt riêng, đầu vào/cấu hình lỗi và vòng đổi thuận/ngược; fixture không phải góc ngắm đã đo. |
| D.4 | SOFTWARE_VERIFIED | Test bất biến giới hạn xung và từ chối ngoài miền điểm cung cấp, không tự mở rộng theo JSON. |

- **Trạng thái:** Phase D `SOFTWARE_VERIFIED` cho module độc lập qua 173 kiểm tra tập trung; **không** phải nghiệm thu servo/laser hoặc tích hợp firmware. Chưa đủ điều kiện dùng module trên thiết bị: Phase C cần góc cơ khí đo bằng dụng cụ cho bốn trục, mốc Pan/Tilt ngắm, hướng/offset laser thực, miền an toàn và độ lặp; B.4 cần xác nhận Node ID runtime; Phase E cần thiết kế cấu hình riêng trục, đối chiếu NVS cũ và kết nối HAL bằng phép kiểm thử mới. Phase A vẫn `BLOCKED` bởi bộ C chuẩn `proto.c:116`, sanitizer thiếu `libubsan`, 7 lỗi lint E731, pytest thiếu `cv2`, ESP-IDF build thiếu `idf.py`. Không triển khai Phase E/F hay thao tác thiết bị.

## Phụ lục D — Prompt giao việc cho Codex

### Bắt đầu từ Phase A

```text
Đọc AGENTS.md nếu có và docs/NT532_CODEX_PHASE_A_Z.md.
Chỉ thực hiện Phase A. Đối chiếu working tree và commit thực tế trước khi dùng
snapshot trong tài liệu. Giữ nguyên thay đổi chưa commit của tôi.
Chạy những kiểm tra baseline phù hợp với môi trường và báo rõ test chưa chạy.
Chưa sửa logic nghiệp vụ, chưa flash/ghi NVS, chưa gửi lệnh tới thiết bị và chưa
commit/push. Kết thúc bằng bảng hiện trạng, blocker và đề xuất task kế tiếp.
```

### Giao một phase triển khai sau khi đủ điều kiện

```text
Đọc docs/NT532_CODEX_PHASE_A_Z.md và chỉ thực hiện Phase <chữ>.
Kiểm bằng chứng các phase phụ thuộc; chỉ đọc thêm file cần thiết cho phase này.
Chia việc thành các thay đổi nhỏ có test. Không viết lại chức năng repo đã có.
Không tự đổi đơn vị, phạm vi xung hoặc giới hạn an toàn. Nếu thiếu số đo, ghi
BLOCKED/WAITING_HARDWARE cho phần đó và hoàn thành phần phần mềm kiểm chứng được.
Không flash/ghi NVS/bật tải/commit/push nếu chưa có yêu cầu riêng.
Kết thúc bằng mẫu nhật ký, lệnh kiểm chứng và kết quả thực tế.
```

**Điểm khởi đầu đề nghị:** Phase A → B. Song song, chuẩn bị Phase I và bảng đo Phase C; chưa cho Codex tự gán góc ±90° từ file PWM để chạy thẳng Phase D–F trên phần cứng.

## Nguồn đối chiếu

Các liên kết repo được ghim về commit đã đọc để tách dữ kiện kiểm chứng khỏi mã có thể thay đổi sau này. Codex phải đọc lại phiên bản trong working tree trước khi sửa. Các đường dẫn ghi “đề xuất mới” trong phase không phải file đã tồn tại ở snapshot.

[U1]: #calibration-record
[U2]: #mvp-scope

- **[U1]** Bản `pantilt_calibration.json` người dùng cung cấp trong ngữ cảnh Project, `recorded_on=2026-10-25`; nội dung được giữ đầy đủ ở mục 3.1. Bản này không phải bằng chứng file đã được commit lên `main`.
- **[U2]** Bản tóm tắt đề tài `sum (2).md` trong ngữ cảnh Project: mạng Thread, camera/Edge AI, pan–tilt hướng laser và đánh giá độ tin cậy/độ trễ/sai số. Nhánh phun được giữ tùy chọn để không âm thầm thay đổi phạm vi.
- **[R0]** Snapshot repository và nhánh `main` đã đọc.
- **[R1–R15]** Mã nguồn, cấu hình và runbook hiện có; các tham số của chúng không đồng nghĩa với số đo đã nghiệm thu.
- **[E1]** Tài liệu chính thức ESP-IDF 5.4 về NVS, dùng để đối chiếu định dạng và giới hạn key; khi đổi toolchain phải kiểm tài liệu đúng phiên bản.

[R0]: https://github.com/knight3007/Project-NT532.R12/tree/07eddcd66d3043c81af9c511c9c12c5c1ee8a762
[R1]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/README.md
[R2]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/calibration/README.md
[R3]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/firmware/sensor-h2/main/hal_esp.c
[R4]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/firmware/sensor-h2/main/node_cfg.c
[R5]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/firmware/sensor-h2/main/core/actuator.h
[R6]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/config/site.yaml
[R7]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/firmware/sensor-h2/README.md
[R8]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/docs/trien-khai-phan-cung.md
[R9]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/pi/src/nt532/station.py
[R10]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/pi/scripts/aim_point.py
[R11]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/.github/workflows/ci.yml
[R12]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/pi/scripts/commission.py
[R13]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/pi/src/nt532/orchestrator/machine.py
[R14]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/firmware/rcp/README.md
[R15]: https://github.com/knight3007/Project-NT532.R12/blob/07eddcd66d3043c81af9c511c9c12c5c1ee8a762/docs/trien-khai-pi.md
[E1]: https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/storage/nvs_flash.html
