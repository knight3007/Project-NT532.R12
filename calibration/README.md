# Hiệu chuẩn

Hồ sơ độ rộng xung pan–tilt đã thử nghiệm nằm ở
[`pantilt_calibration.json`](pantilt_calibration.json). File này ghi MIN, HOME, MAX bằng
micro giây (µs) cho pan và tilt của hai cụm A/B, kèm ngày ghi nhận và nguồn dữ liệu.
Đây là số đo xung gốc, chưa phải quan hệ góc vật lý–xung hoặc giới hạn vận hành an toàn.
HOME 1500 µs được giữ riêng cho từng trục; không suy góc 0°, ±90°, 180° hoặc 360° từ
các giá trị xung. Người vận hành quy định A → s1, tag 21 và B → s2, tag 22;
`/info` chưa xác minh Node ID đang chạy trong firmware.

Firmware node hiện lấy mặc định từ Kconfig và đọc giá trị ghi đè từ NVS namespace
`nt532` qua `node_cfg_load()`; firmware **chưa tự đọc JSON này**. Profile góc–xung dựa
trên góc vật lý đo đủ chính xác và đường chuyển hồ sơ sang cấu hình runtime sẽ được xử lý
ở các phase C–E; mô hình sơ bộ Phase C ở dưới chưa được triển khai vào firmware. Xem
[roadmap Phase A–Z](../docs/NT532_CODEX_PHASE_A_Z.md), nhất là Phase B–E và nhật ký.

Code chỉ đọc hai file trong thư mục này (qua `Vision`, đường dẫn khai ở `config/site.yaml`):

- `camera.yaml`: ma trận camera, hệ số méo và sai số chiếu lại (`calibrate_camera.py`, tuần 1). Đổi độ phân giải hoặc lấy nét thì hiệu chuẩn lại.
- `commissioning.yaml`: pose camera và pose tag từng node trên sa bàn (`commission.py`). Làm lại mỗi khi dời camera hoặc node.

Các giá trị hiệu chuẩn khác không nằm ở đây:

- Cấu hình servo đang có hiệu lực (xung, offset, đảo chiều) và giới hạn góc phía node: NVS/Kconfig của firmware node. Giới hạn góc phía Pi ở `config/site.yaml` → `actuator.nodes.*`. Các cấu hình này chưa được sinh từ JSON trên.
- Offset tâm quay `actuator.pivot_offset` và góc bù tia nước `actuator.water_tilt_deg`: `config/site.yaml`.

Các file sau là chỗ ghi chép tay (kèm ngày đo), **chưa dùng**: chưa có code nào đọc chúng.

- `servo.yaml`: tên hồ sơ góc–xung được đề xuất trong tài liệu cũ; hiện chưa có file này. Quan sát góc cơ khí ước lượng nằm trong CSV bên dưới; profile triển khai chỉ chốt khi đo đủ và xác nhận mốc hình học ở Phase C/E.
- `offsets.yaml`: offset từ tag đế vòi tới tâm quay và tới đầu laser (tuần 3).
- `water.yaml`: bảng bù tilt cho tia nước theo khoảng cách (tuần 3).

## Quan sát góc–xung và phần đo còn thiếu của Phase C

Dùng [`servo_measurements.csv`](servo_measurements.csv) để ghi quan sát góc cơ khí của
từng trục A.pan, A.tilt, B.pan, B.tilt. Hiện có 11 góc do người vận hành **ước lượng bằng
mắt**, đánh dấu `visual_estimate`; chưa có số đo bằng thước đo góc. Giữ
[`pantilt_calibration.json`](pantilt_calibration.json) làm hồ sơ **phạm vi xung đã thử**;
không sửa số MIN/HOME/MAX từ phép nội suy góc. Phạm vi góc đo được và vùng vận hành an
toàn của sa bàn là hai kết quả riêng, chỉ xác định sau khi có bằng chứng tương ứng.

Người vận hành cho biết đã dùng chương trình test trong Arduino IDE với ESP32Servo 3.2.1,
Pan GPIO4, Tilt GPIO5 và PWM 50 Hz: `HOME` đặt cả hai trục về 1500 µs; cú pháp
`US PAN_US TILT_US` đặt xung trực tiếp. Chế độ nhập `180 180` chỉ là thang lệnh giả lập
0–360 của chương trình, **không phải góc vật lý**. Sketch được cung cấp qua hội thoại
nhưng chưa có trong working tree; cần lưu bản sketch/cú pháp và phiên bản dùng trong
lần đo tiếp theo để tái lập. Firmware ESP-IDF của repo không được coi là CLI Serial
để đặt xung. Hai board được mô tả là ESP32-H2 Super Mini; A dùng COM19, B dùng COM18
tại thời điểm báo cáo, nhưng cổng COM có thể đổi khi kết nối lại. Người vận hành quy
định A → s1/tag 21, B → s2/tag 22; cột `node` dùng quy định đó, không chứng minh
firmware đang nhận đúng Node ID. Không suy A ở bên trái hoặc B ở bên phải sa bàn.

Mốc góc dùng để đo: hệ sa bàn có X sang phải, Y về bảng bia, Z hướng lên. Pan dương
quay sang trái khi nhìn từ sau cụm, đo từ hướng tham chiếu/yaw **của chính cụm đó**;
không mặc định pan 0 của mọi cụm là +Y. Tilt 0 là phương ngang, tilt dương ngẩng lên.
Đánh dấu và mô tả mốc vật lý này trước phép đo chính xác tiếp theo. HOME của cả bốn
trục là **1500 µs**. Người vận hành quan sát Tilt HOME dựng đứng khoảng 90° so với
đế Pan; Pan HOME khoảng 90° **chỉ là giá trị tính từ mô hình**, chưa quan sát độc lập.
HOME chưa được chứng minh trùng điểm 0 hình học của góc ngắm. Tilt ngắm 0° trong
`aiming.py` là phương ngang, không đồng nhất với góc cơ khí Tilt 90° ở đây. **Hướng laser
mong muốn tại HOME là hướng lên trên**, nhưng hướng thực tế sau khi lắp đặt chưa được
xác nhận; chưa biết offset laser so với khung Tilt. Nếu HOME khác mốc ngắm 0, ghi riêng
hai mốc; không sửa HOME hoặc lấy trung bình MIN/MAX. Góc mà chương trình test hiển
thị/tự nội suy **không phải**
`measured_deg`.

| Cột CSV | Cách ghi |
|---|---|
| `assembly` | `A` hoặc `B` theo nhãn cụm vật lý đang nối. |
| `node` | `s1` cho A, `s2` cho B theo quy định của người vận hành; firmware runtime chưa được xác minh bằng `/info`. Nếu quy định chưa có cho lượt đo khác thì để trống. |
| `axis` | `pan` hoặc `tilt`; kiểm dây của trục trước mỗi lượt. |
| `pulse_us` | Độ rộng xung dùng trong chương trình test, đơn vị µs; không ghi góc vào đây. |
| `measured_deg` | Góc cơ khí quan sát, đơn vị độ. Các hàng hiện có chỉ là ước lượng bằng mắt (`visual_estimate` trong `notes`), chưa phải số đo bằng dụng cụ; không sao chép góc do chương trình nội suy hoặc góc ngắm laser. |
| `direction` | `increasing_pulse` khi đi tới điểm bằng cách tăng xung, `decreasing_pulse` khi giảm xung; đây là chiều tiếp cận điểm đo, không phải chiều quay cơ cấu. Để trống ở 11 hàng hiện có vì chưa ghi chiều tiếp cận. |
| `repeat` | Số thứ tự lần đo của cùng trục/điểm/chiều, bắt đầu từ 1; để trống ở 11 hàng hiện có vì chưa ghi số lần lặp. |
| `notes` | Ghi nguồn `visual_estimate`, `mechanical_angle`, lý do thiếu chiều tiếp cận/lặp và COM tại thời điểm báo cáo; COM không phải dấu nhận diện board ổn định. Đặt trong dấu ngoặc kép nếu chứa dấu phẩy. |

### Chiều quay và mô hình sơ bộ

Người vận hành xác nhận trên **cả A và B**: giảm PWM làm Pan quay phải, Tilt ngẩng lên;
tăng PWM làm Pan quay trái, Tilt hạ xuống. Theo quy ước góc ngắm của dự án (Pan dương
sang trái, Tilt dương lên), `pan_inv=0`, `tilt_inv=1` là **phương án dự kiến**, chưa ghi
NVS và chưa kiểm qua firmware chính. Chỉ áp dụng phép đổi dấu tại một nơi trong đường
góc ngắm → xung sau khi xác nhận mốc hình học; không đổi dấu thêm ở Pi. Góc cơ khí
trong CSV là thang quan sát của người vận hành: số góc Pan giảm khi xung tăng, số góc
Tilt tăng khi xung tăng tại các điểm đã báo. Các góc này **không phải** góc ngắm có
dấu của `aiming.py`.

Mô hình tuyến tính **sơ bộ**, khớp các ước lượng đã báo (500 µs tương ứng khoảng 45°):

```text
pan_mechanical_deg  = 90 - 0.09 * (pulse_us - 1500)
tilt_mechanical_deg = 90 + 0.09 * (pulse_us - 1500)
```

| PWM (µs) | Pan cơ khí tính toán (°) | Tilt cơ khí tính toán (°) |
|---:|---:|---:|
| 500 | 180 | 0 — ngoài phạm vi xung Tilt trong JSON |
| 600 | 171 | 9 |
| 1000 | 135 | 45 |
| 1500 | 90 | 90 |
| 2000 | 45 | 135 |
| 2500 | 0 | 180 |

Bảng này **chỉ là phép tính**, không thêm hàng vào CSV và không xác nhận hành trình
servo ở các mốc chưa quan sát. Các điểm quan sát ước lượng là A.pan 1000/2000 µs,
A.tilt 1000/1500/2000 µs, B.pan 500/1000/2000/2500 µs và B.tilt 1500/2000 µs.
Khoảng giữa các điểm quan sát chỉ là **nội suy**; ngoài khoảng quan sát của từng trục
chỉ là **ngoại suy**, kể cả khi còn nằm trong phạm vi xung của JSON. Pan A chỉ có quan
sát trong 1000–2000 µs, Tilt A 1000–2000 µs, Pan B 500–2500 µs, Tilt B 1500–2000 µs.
JSON gốc vẫn ghi Pan A/B 500–2500 µs và Tilt A/B 600–2500 µs, HOME đều 1500 µs;
đây không phải vùng góc đo chính xác hoặc biên an toàn cơ khí. Hệ số 0,09°/µs và
góc gốc 90° chưa có sai số, độ lặp hoặc độ rơ được định lượng; chưa dùng mô hình
này để tự động ngắm laser.

Checklist cho **người vận hành** khi đã có chương trình và điều kiện thử phù hợp:

1. Đối chiếu nhãn cụm A/B, board test, nguồn servo, GND chung, dây Pan/Tilt và đường đi cơ khí. Giữ bơm ngắt nguồn vật lý và laser tắt.
2. Dùng đúng chương trình test đã hoạt động; xác nhận cách đặt xung từ chương trình thật trước khi cấp tín hiệu. Không dùng lệnh Serial suy đoán.
3. Với từng trục, xác nhận vị trí tại HOME 1500 µs, hướng pan tham chiếu và mặt phẳng tilt 0 bằng quan sát/đo. Ghi riêng nếu HOME lệch góc 0.
4. Thử bước nhỏ quanh HOME để xác định tăng xung làm pan/tilt quay về chiều góc nào. Ghi chiều quay quan sát được, không suy từ dải xung.
5. Chọn khoảng 5 điểm/trục trong vùng sử dụng, có HOME và điểm hai phía; đo khi xung tăng rồi khi giảm, lặp để nhận ra độ rơ. Kiểm riêng A.pan, A.tilt, B.pan, B.tilt; không yêu cầu chạm sát giới hạn cơ khí.
6. Ghi mỗi quan sát thành một hàng CSV với góc đo bằng dụng cụ, mốc tham chiếu và chiều tiếp cận điểm. Nếu một board test dùng chung, ghi rõ cụm đang nối trong từng lượt. Dừng khi cơ cấu kẹt, rung bất thường hoặc nguồn không ổn định. Đo hướng laser ở HOME và mốc ngắm nằm ngang riêng khi có điều kiện an toàn phù hợp.

Mô hình hiện phù hợp với **ước lượng thị giác**, chưa phải hiệu chuẩn góc ngắm. Cần đo
góc cơ khí bằng dụng cụ ở nhiều điểm đi/về, xác nhận mốc Pan, mặt phẳng Tilt 0,
hướng laser và điểm ngắm 0 trước khi chốt hệ số, offset, giới hạn vận hành hoặc profile
nạp NVS.
