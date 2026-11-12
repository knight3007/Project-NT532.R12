# NT532 CP-AIM-GEOM2 — Bàn giao đánh giá định vị 3D bằng AprilTag 23

**Trạng thái:** proof of concept ngoại tuyến, bàn giao tạm thời; **chưa đủ căn cứ tích hợp runtime hoặc kích hoạt phần cứng**.

**Nhánh:** `evaluation/tag-assisted-3d`, xuất phát từ audit `cfbe62ea72fea69ea572970501f746fb8b538f5d` (`evaluation/aiming-2d-vs-3d`).
**Phạm vi thay đổi:** thêm đúng một script, một bộ test tập trung, CSV/đồ thị và báo cáo này. Không sửa Dynamic Mapping, YOLO, firmware, calibration, orchestrator, CoAP/Thread hoặc config production.

## Kết luận để người nhận tiếp tục

1. **Ngắm điểm hiển thị trên màn hình ở nhiều độ cao:** hình học tag 23 + mặt phẳng màn hình giải được XYZ tại Z = 0, 5, 10, 15, 20, 30 cm trong các tư thế có pose chấp nhận được. Ma trận sạch 96 cảnh × 2 node: phương pháp B trúng 71/96 (A), 76/96 (B); oracle C trúng 84/96 và 88/96. B chặn 13/96 cảnh mỗi node do hai nghiệm pose phẳng mơ hồ; một số điểm còn lại vượt giới hạn Pan/Tilt giả định. Đây là **PASS về khả năng hình học có điều kiện**, **FAIL về bằng chứng sẵn sàng triển khai thật**.
2. **Nguồn sai số:** chiếu Z=0 sai theo độ cao (XYZ P95 188,50 cm trên toàn bộ ca sạch); lệch bbox 3/6 px tạo XYZ P95 0,44/0,88 cm; nhiễu map 1 px tạo P95 0,93 cm; offset tag–screen sai 5/10 mm tạo P95 0,83/1,67 cm. Dù XYZ đúng, TILT HOME lệch +5° chỉ trúng 2/24 lượt của node A; đảo dấu PAN trúng 0/24. Kết quả đó tách rõ hình học camera/tag/giá đỡ khỏi cơ khí servo.
3. **AI Depth:** không cần để tìm **điểm đã biết nằm trên mặt phẳng màn hình** khi tag và transform gắn cứng đã được đo. Với **ngọn lửa thật ngoài mặt phẳng**, một pixel đơn và một camera vẫn không cho biết khoảng cách dọc tia; phải có quan sát độ sâu bổ sung (ví dụ hai camera, chuyển động nhiều góc, cảm biến đo sâu hoặc mô hình depth được kiểm định). Mô phỏng này không chứng minh AI Depth là lựa chọn bắt buộc hay đủ chính xác.
4. **Muốn dùng thật:** gắn ID 23 cứng cạnh màn hình, đủ lớn và luôn thấy trọn bốn góc; đo kích thước in thực tế, biến đổi 6 bậc tự do từ tag sang mặt phẳng và diện tích hiển thị, hiệu chuẩn intrinsics đúng độ phân giải/zoom RTSP, kiểm tra pose Dynamic Mapping liên tục. Đo riêng vector tâm tag 21/22 → tâm quay PAN (dự kiến 10 cm mới là độ dài), hướng HOME, dấu, gain, backlash, giới hạn PWM và đường tia/vòi thực. Cần đo sai số với bia 3 cm trên sa bàn.
5. **Trước hạn đồ án:** **chưa nên tích hợp vào NT532 runtime**. Chỉ cân nhắc sau thử nghiệm RTSP thật và đo cơ khí đáp ứng ngưỡng 3 cm. Nhánh này là tài liệu bàn giao để người tiếp theo thực hiện các phép đo đó; không push/merge vào nhánh production.

## Định nghĩa ba loại XYZ

| Ký hiệu | Ý nghĩa | Có thể suy từ pixel + tag 23? |
|---|---|---|
| `XYZ_screen` | Điểm video ngọn lửa **nằm trên kính/mặt phẳng màn hình** | Có, nếu pose màn hình và giao tia đáng tin cậy |
| `XYZ_fire` | Vị trí ngọn lửa **thật trong không gian** | Không, nếu không có phép đo độ sâu độc lập |
| `XYZ_floor` | Giao tia camera với Z=0 | Chỉ là giả định mặt sàn, không thay thế hai loại trên |

Ca `free_flame_off_plane` trả `UNKNOWN/real_flame_depth_unobservable` cho phương pháp B. Đây là kiểm tra hợp đồng phân loại, **chưa mô phỏng một ngọn lửa thật có kích thước/độ sâu cụ thể**.

## Phương pháp và giả định tái lập

Đơn vị nội bộ là mét, góc là độ; XYZ gốc theo anchor tham chiếu ID 2. Camera pose được `estimate_reference_map` tính từ bốn tag tham chiếu 0–3 mô phỏng; **không đưa camera pose ground truth vào B**. Intrinsics K/dist là các giá trị đã nhúng trong regression `test_dynamic_mapping.py` cho ảnh góc thấp 1280×720, **không xác nhận đó là intrinsics của camera RTSP đang vận hành**. Tag 23 dùng AprilTag 36h11 kích thước 4 cm và `SOLVEPNP_IPPE_SQUARE`; hình học theo [OpenCV PnP](https://docs.opencv.org/doc/doxygen/html/d5/d1f/calib3d_solvePnP.html). B nhận bốn góc tag 23, pixel mục tiêu, camera Dynamic Mapping và transform cơ khí giả định. Chỉ evaluator mới giữ `target` ground truth.

Màn hình giả định rộng 7,5 cm, cao 14,5 cm, tag nằm bên phải tâm màn hình 6,5 cm theo trục màn hình. Các tư thế sạch: thẳng đứng; yaw +20°/lean +12°; yaw −25°/lean −10°; và tag/màn hình cùng roll +20°. Các vị trí XY: `(0,18; 0,19)`, `(0,30; 0,23)`, `(0,43; 0,30)`, `(0,30; 0,36)` m. Cảnh Z=0 đặt mục tiêu tại mép dưới màn hình; đó là giới hạn hình học lý tưởng, **chưa kiểm tra viền/bezel và khả năng dựng điện thoại thật tại điểm đó**. Cảnh `marker_roll20` xoay tag cùng màn hình, chưa kiểm tra tag xoay độc lập trên giá đỡ.

Node A/B lần lượt dùng ID 21/22, tâm tag mô phỏng `(0,05; −0,10; 0,20)` và `(0,55; −0,10; 0,20)` m, vector tag→pivot `(0;0;0,10)` m. Hướng PAN HOME mô phỏng −35°/+35°, TILT HOME 0°, 1500 µs, gain danh định 2000/180 µs/°, dấu danh định +1. Giới hạn giả định PAN ±70°, TILT −35°..+45°, bán kính trúng 3 cm. **Chưa thông số cơ khí nào được xác nhận trên thiết bị.** Lệnh Pan/Tilt dùng `nt532.orchestrator.aiming.angles`; tia vật lý được tính trong simulator, không gửi lệnh ra node.

Các chốt chặn B: thiếu tag/offset/map; tag quá nhỏ hoặc bị thu hẹp phối cảnh; hai nghiệm IPPE khó tách; sai số chiếu lại >2 px; mặt màn hình không phù hợp giả định điện thoại dựng (pháp tuyến Y < −0,60, trục trên Z > +0,70); độ bất định góc tag ước từ 5 khung vượt bán kính 3 cm; tia gần song song; giao sau camera; pixel ngoài phần hiển thị. Các ngưỡng này là **giả định PoC**, cần hiệu chuẩn theo RTSP thật. Không có fallback sang Z=0 trong B.

## Kết quả mô phỏng

`cases.csv` chứa **3.312 hàng**: A = chiếu sàn Z=0, B = tag/mặt phẳng màn hình, C = oracle XYZ; mỗi hàng có node, chiều cao, XY, góc màn hình, trạng thái, lý do, XYZ/Pan/Tilt/ray error và hit. `summary.csv` ghi median, P95, worst cho từng phương pháp/node/scenario. Thống kê lỗi chỉ lấy các hàng có ước lượng/góc/tia hữu hạn; **hit rate lấy mẫu số là mọi cảnh, gồm cả BLOCKED và OUT_OF_RANGE**. Giá trị `nan` nghĩa là không tạo ước lượng, không phải sai số bằng 0.

### Ma trận sạch: 96 cảnh cho mỗi phương pháp/node

| Phương pháp/node | XYZ có giá trị | Reachable | Trúng / 96 | XYZ median / P95 / worst (cm) | Pan P95 (°) | Tilt P95 (°) | Ray P95 (cm) |
|---|---:|---:|---:|---:|---:|---:|---:|
| A / A | 96 | 84 | 12 | 35,00 / 188,50 / 192,60 | 17,61 | 8,38 | 14,35 |
| A / B | 96 | 88 | 8 | 35,00 / 188,50 / 192,60 | 41,59 | 8,81 | 28,49 |
| B / A | 83 | 71 | 71 | ~0 / ~0 / ~0 | ~0 | ~0 | ~0 |
| B / B | 83 | 76 | 76 | ~0 / ~0 / ~0 | ~0 | ~0 | ~0 |
| C / A | 96 | 84 | 84 | 0 / 0 / 0 | 0 | 0 | ~0 |
| C / B | 96 | 88 | 88 | 0 / 0 / 0 | 0 | 0 | ~0 |

Số ~0 của B là kết quả ảnh dựng và góc tag **không nhiễu**, không phải độ chính xác thực nghiệm. Với B, 13 cảnh/node trả `tag_pose_ambiguous_or_poor`. Oracle chỉ dùng làm chuẩn so sánh. Đồ thị: [XYZ theo độ cao](tag_assisted_3d/xyz_error_by_height.svg), [khoảng cách tia đến mục tiêu](tag_assisted_3d/ray_miss_by_height.svg).

### Nhiễu, sai số cơ khí và trường hợp chặn (mỗi hàng: 24 cảnh/node A)

| Scenario B | XYZ có giá trị | Trúng / 24 | XYZ P95 / worst (cm) | Ray P95 (cm) | Nhận xét |
|---|---:|---:|---:|---:|---|
| bbox lệch 3 px | 24 | 21 | 0,44 / 0,45 | 0,43 | 3 ca vượt giới hạn |
| bbox lệch 6 px | 24 | 21 | 0,88 / 0,90 | 0,87 | 3 ca vượt giới hạn |
| corner tag nhiễu 0,5 px, median 5 khung | 20 | 17 | 1,47 / 1,50 | 0,55 | 3 ca uncertainty BLOCKED, 1 ngoài màn hình, 3 vượt giới hạn |
| corner tag nhiễu 1,5 px, median 5 khung | 0 | 0 | — | — | 23 uncertainty BLOCKED, 1 pose kém |
| corner reference nhiễu 1 px | 24 | 21 | 0,93 / 1,08 | 0,88 | pose camera là nguồn sai số riêng |
| góc camera thấp + nhiễu ref 0,5 px | 24 | 21 | 0,40 / 0,49 | 0,37 | chỉ là synthetic, chưa chứng nhận RTSP |
| offset tag–screen sai 5 mm | 24 | 21 | 0,83 / 0,87 | 0,30 | sai số hệ thống không thấy qua reprojection |
| offset tag–screen sai 10 mm | 24 | 21 | 1,67 / 1,74 | 0,61 | cần đo bracket chính xác |
| pivot A sai 1 cm | 24 | 21 | ~0 / ~0 | 1,00 | XYZ đúng nhưng tia lệch |
| servo gain +10% | 24 | 19 | ~0 / ~0 | 3,19 | 2 ca miss sau khi reachable |
| TILT HOME +5° | 24 | 2 | ~0 / ~0 | 5,28 | 19 ca miss sau khi reachable |
| đảo dấu PAN | 24 | 0 | ~0 / ~0 | 16,22 | 21 ca miss sau khi reachable |
| tag mất hoặc bị che | 0 | 0 | — | — | `tag_23_missing`, không fallback |
| offset chưa biết | 0 | 0 | — | — | `screen_offset_unknown` |
| pixel ngoài màn hình | 0 | 0 | — | — | `pixel_outside_screen` |
| tia gần song song | 0 | 0 | — | — | 19 `ray_near_parallel`, 5 pose kém |
| giới hạn servo thu hẹp | 24 | 0 | ~0 / ~0 | — | `command_or_pwm_limit` |
| ngọn lửa thật ngoài màn hình | 0 | 0 | — | — | `real_flame_depth_unobservable` |

Các sai số offset và servo được **bơm vào simulator** để phân tích độ nhạy; không phải số đo thiết bị. P95/worst Pan và Tilt cho mọi scenario nằm trong `summary.csv`; không suy ra thành sai số servo thực. `ray_miss_cm` là khoảng cách vuông góc từ tia vật lý tới điểm target, chỉ tính khi lệnh trong giới hạn.

## Khả năng nhìn tag 23 cạnh màn hình trên camera hiện tại

`detector_trials.csv` chạy **detector OpenCV thật trên 16 ảnh raster dựng**, gồm bốn tư thế × 1280×720 hoặc ảnh giảm còn 640×360 × có/không che tag. Tag được phát hiện **8/8** khi rõ, **0/8** khi che. Cạnh ngắn chiếu của tag từ khoảng 35,7–45,1 px ở 1280×720; sau giảm còn 17,8–22,6 px. Đây là bằng chứng pipeline detector có thể nhận ID 23 trên ảnh dựng với bố trí cạnh màn hình. **Chưa có khung RTSP thật, phép đo ánh sáng/phản chiếu màn hình, motion blur, nén video, hoặc tỷ lệ phát hiện trên thiết bị.** Kết luận RTSP thực tế: `UNKNOWN`.

Người tiếp theo cần đặt tag in thật bên cạnh màn hình, thu ít nhất vài chục khung RTSP ở mọi độ cao/góc dùng trong demo; ghi tỷ lệ phát hiện ID23, cạnh pixel ngắn nhất, jitter bốn góc, số nghiệm PnP và kiểm tra map ID0–3 vẫn ổn. Phải kiểm tra tag không bị điện thoại, giá đỡ hoặc tay người che.

## Kiểm thử và lệnh tái lập

Trong worktree/checkout của nhánh này, từ `pi/` (PowerShell):

```powershell
python -m pip install -e '.[dev]'
$env:PYTHONPATH = 'src'
python -m pytest tests/test_tag_assisted_3d_eval.py tests/test_aiming_geometry_audit.py tests/test_dynamic_mapping.py -q --basetemp ../reports/tag_assisted_3d/pytest_tmp
python scripts/tag_assisted_3d_eval.py --out ../reports/tag_assisted_3d
ruff check scripts/tag_assisted_3d_eval.py tests/test_tag_assisted_3d_eval.py
ruff format --check scripts/tag_assisted_3d_eval.py tests/test_tag_assisted_3d_eval.py
```

Nếu đã có venv của dự án, dùng Python/ruff trong venv đó thay lệnh cài đặt; không cần YOLO weights hoặc thiết bị. Các file đầu ra tất định với seed ghi trong script. `pytest_tmp` là thư mục chạy test tạm, không đưa vào commit. Kiểm thử kết hợp sau lần định dạng cuối: **81 PASS, 1 SKIP** trong 3,71 s; ca SKIP là bộ ảnh góc thấp bên ngoài repo, chỉ chạy khi đặt `NT532_LOW_ANGLE_DATASET`. Riêng PoC **37/37 PASS**, audit nền **18/18 PASS**. `ruff check` **PASS** và `ruff format --check` **PASS**.

## Việc còn lại cho người nhận

1. **Đo RTSP thật:** đối chiếu đúng intrinsics và resolution; thu thống kê ID23 ở cạnh màn hình, độ ổn định pose sau 5 khung, đặc biệt tại Z=30 cm và góc không chính diện. Nếu tag chỉ 18–23 px cạnh ở chế độ stream thực, thử bản in lớn hơn hoặc đổi vị trí camera.
2. **Đo transform tag–screen:** vị trí tâm, pháp tuyến, hai trục, kích thước vùng pixel dùng được; kiểm tra tag và màn hình không dịch tương đối khi thay video/điện thoại. Thử tag quay độc lập với bracket; simulation hiện chưa bao phủ.
3. **Đo cơ khí A/B:** vector 3D 21/22→pivot, yaw/pitch HOME tại 1500 µs, dấu và gain PWM→góc, giới hạn, backlash; xác nhận tia laser/vòi so với trục hình học. Không dùng 10 cm hoặc ±35° mặc định làm số đo.
4. **Kiểm chứng target thật:** test với điểm trên màn hình có kích thước 3 cm ở nhiều XY/Z và góc nghiêng; đo ray miss vật lý. Sau đó kiểm riêng ngọn lửa thật ngoài màn hình bằng cách có độ sâu độc lập; không dùng kết quả màn hình để tuyên bố localization cho lửa thật.
5. **Rà soát PoC trước tích hợp:** `free_flame_off_plane` hiện là kiểm tra trạng thái chặn, chưa có mô hình thể tích lửa; `near_parallel` tạo tư thế edge-on trong `observe`, nên metadata góc màn hình trong CSV vẫn phản ánh case gốc. Kiểm thử thêm tag quay **độc lập** với màn hình, mô hình bbox YOLO thực, nén RTSP và các ảnh có glare. Các threshold pose/uncertainty chỉ là giả định cần hiệu chuẩn thực nghiệm.

Không có giao diện điều khiển servo, laser hoặc bơm trong nhánh đánh giá. Không có kết quả phần cứng, không có kiểm chứng deadline hay triển khai production.
