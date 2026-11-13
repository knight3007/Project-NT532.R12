# NT532 CP-ORIGIN22-CONSISTENCY — audit 15 frame camera thật

Ngày audit: 2026-11-14 (Asia/Saigon). Branch `feature/nt532-origin22-tabletop`, đầu vào code tại commit `0f04780`.

## Kết luận

| Hạng mục | Kết luận |
|---|---|
| **ROOT CAUSE** | Hai estimator cho cùng tag ID 22 được trộn trong JSON: `nodes.s2` legacy là pose 3D `solvePnP(IPPE_SQUARE)` trên frame cuối; gốc vận hành ID 22 là tâm bốn góc chiếu lên mặt bàn Z=0 theo camera đã fit. Ở hệ ID 22, code đổi `nodes.s2` từ pose PnP rồi **ghi đè** `[0,0,0]` để biểu diễn gốc mặt bàn. Hai pose đầu vào cách nhau 9,944 mm, nên các khoảng cách chứa `s2` thay đổi. |
| **Coordinate transform** | **PASS** cho mọi điểm giữ cùng pose nguồn. Ma trận quay là một yaw 1,13013°, định thức ≈1, sai số trực giao 1,2×10⁻¹⁵; sai khác lớn nhất của khoảng cách giữa các cặp **không chứa `s2`** chỉ 0,0146 mm do JSON làm tròn. `s2` là ngoại lệ do thay pose nguồn sau chuyển hệ, không phải lỗi quay/dịch. |
| **Tag pose consistency** | **WARN**: PnP và giả định tag nằm đúng mặt bàn lệch 7,737 mm theo XY, 6,245 mm theo Z, tổng 9,944 mm. Cả hai vẫn chiếu ảnh hợp lý; không có số đo vật lý để xác định estimator nào sát vị trí tag thật hơn. |
| **Camera pose ambiguity** | **RESOLVED** theo tiêu chí chọn nghiệm hiện có. `pose_ambiguity_m=2.70478` là khoảng cách giữa **hai nghiệm camera ứng viên ở frame 9**, không phải sai số camera 2,7 m. Chi phí Huber của nghiệm chọn và nghiệm kia là 1,329 và 239,320 (chênh 237,991), vượt xa ngưỡng chặn mơ hồ 2,0. |
| **Fire detection** | **PASS**: bbox thật trên frame cuối, confidence 0,7109; cùng bbox và tâm pixel ở cả hai JSON. |
| **Fire physical localization** | **NOT VERIFIED**: hình lửa hiển thị trên màn hình điện thoại dựng/nghiêng, trong khi mã giao tia qua tâm bbox với **mặt bàn Z=0**. Tọa độ `(0.50195, 0.66585, 0)` là giao điểm tia với mặt bàn theo mô hình, không phải vị trí vật lý đã đo của hình lửa trên màn hình. |

Không có bằng chứng lỗi optimizer. Không sửa optimizer, phép đổi hệ, firmware, laser, YOLO, calibration hoặc production trong audit này.

## Evidence và khả năng tái hiện

Nguồn: `20261114-013713.tar.gz` tại gốc workspace, SHA-256 `773DD514EBA117EB805133116C272EDBBB7B32825BE6CCCBFB2BBDBBCE9F4309`. Archive gốc được giữ nguyên; bản giải nén và các script phân tích chỉ nằm dưới `build-output/origin22-consistency-evidence/` ngoài feature worktree. Archive có `mapping_id2.json`, `mapping_id22.json`, 15 JPEG, hai debug JPEG và log. Intrinsics **không** nằm trong archive; phép chạy lại dùng đúng nội dung `camera.yaml` do người dùng cung cấp trong chat: 1280×720, `K` có `fx=993.76546`, `fy=992.29590`, `cx=643.16035`, `cy=360.62700`, năm distortion coefficients, calibration RMS 0,2965 px trên 29 ảnh. Bản nhập để phân tích lưu riêng tại `build-output/origin22-consistency-evidence/camera_user_supplied.yaml`; đây không phải bản sao byte-for-byte của file trên Pi.

Đã đọc ảnh gốc và debug. Cả 15 frame giải mã ở 1280×720, có SHA-256 khác nhau và thấy đủ tag 0,1,2,3,21,22 bằng AprilTag detector độc lập. Hai JSON đều `status=PASS`, `anchor_id=2`, `target_anchor=center`; `operational_origin_id` lần lượt 2 và 22. Kết quả chạy lại từ JPEG và intrinsics người dùng cung cấp khớp vị trí camera Pi trong 4,57 µm do làm tròn; `metric_error_m=0.00841254` và `ambiguity_m=2.70478275` khớp đến chữ số JSON công bố. Điều này xác nhận đúng code/calibration và cùng batch ảnh ở mức có thể kiểm tra offline.

## Nguồn hai pose ID 22

| Giá trị | Đường tính trong code | ID 2 XYZ (m) |
|---|---|---|
| `mapping_id2.json.nodes.s2` | `Vision.commission_corners()` → `tags.tag_poses()` → `cv2.solvePnP(..., SOLVEPNP_IPPE_SQUARE)` trên **bốn góc ID 22 ở frame cuối**, rồi đổi pose camera sang ID 2 | `(-0.18857685, -0.40015103, +0.00624524)` khi chạy lại; JSON làm tròn `(-0.18858, -0.40015, +0.00625)` |
| Gốc `TableFrame.origin` dùng cho ID 22 | `estimate_table_frame()` → bốn tia qua góc ID 22 giao mặt bàn Z=0 của Dynamic Mapping → lấy trung bình bốn điểm; hướng +X từ hai cạnh, chỉ giữ yaw; kiểm tra ổn định qua batch | `(-0.19232490, -0.39338036, 0)` |
| `mapping_id22.json.nodes.s2` | `dynamic_map.py` ban đầu chuyển pose PnP sang hệ ID 22, sau đó gán tường minh `[0,0,0]` | Gốc mặt bàn theo định nghĩa, **không phải** pose PnP đã chuyển |

PnP trừ gốc mặt bàn trong hệ ID 2 là `(+0.00374805, -0.00677068, +0.00624524)` m; khoảng cách 3D **9,944 mm**. Nếu chỉ chuyển cứng pose PnP legacy, `nodes.s2` trong hệ ID 22 sẽ là khoảng `(+0.00361, -0.00684, +0.00625)` m, không phải zero. Góc nghiêng PnP ước lượng 1,387° so với trục pháp tuyến mặt bàn. Sai số chiếu lại bốn góc ID 22 ở frame cuối: PnP RMS/max **0,686/0,710 px**; pose phẳng RMS/max **1,491/2,095 px** (vẫn dưới ngưỡng tin cậy 3 px của estimator mặt bàn). Việc tag được dán trên bìa/giấy có thể tạo độ cao thật; chưa đo độ dày và không thể quy toàn bộ 6,245 mm cho nhiễu PnP.

Vì vậy ID 22 bằng `[0,0,0]` là **định nghĩa của gốc mặt bàn**, không phải bằng chứng pose PnP của tag đo được bằng zero. Code hiện không xuất hai nguồn pose riêng, khiến người đọc dễ hiểu nhầm rằng `nodes.s2` ở hai JSON là cùng một ước lượng đã đổi hệ.

## Kiểm tra chuyển hệ và khoảng cách

Từ hai camera extrinsics, `R_22→2 = R_2ᵀ R_22` là quay quanh Z `+1.13013°`; `det=1.0000000000000007`. Gốc ID 22 suy ra trong hệ ID 2 là `(-0.19232588, -0.39338055, ≈0)` m, sai khác vài µm so với replay vì `t` trong JSON được làm tròn 5 chữ số. Phương trình `R_camera,22 = R_camera,2 R_22→2` và `t_camera,22 = R_camera,2 O_22 + t_camera,2` khép kín tới độ chính xác số máy; camera position mới khớp chuyển hệ trong 0,0083 mm. Các reference 0–3, node `s1`, camera và fire đều khớp phép biến đổi trong ≤0,0133 mm mỗi điểm.

| Cặp trong JSON | Khoảng cách ID 2 (m) | Khoảng cách ID 22 (m) | Sai khác |
|---|---:|---:|---:|
| reference 0 – `s2` | 0,902132 | 0,895769 | **6,363 mm** = 0,636 cm, lớn nhất nếu chỉ tính reference/node/fire |
| reference 2 – `s2` | 0,442404 | 0,437879 | 4,525 mm |
| `s1` – `s2` | 0,902651 | 0,906671 | 4,020 mm |
| fire – `s2` | 0,837198 | 0,833853 | 3,346 mm |
| camera – `s2` | 1,012858 | 1,022804 | **9,946 mm**, nếu tính cả camera |
| Mọi cặp không chứa `s2` | — | — | **≤0,0146 mm** |

Số 0,636 cm trong yêu cầu đúng với nhóm điểm loại camera. Khi xét cả camera, cực đại là 0,995 cm. Các sai khác cùng quy về việc `s2` bị thay bởi gốc phẳng; không có dấu hiệu trộn frame ở reference, `s1`, camera hoặc fire. `nodes.s1.z=0.00319 m` giữ nguyên khi đổi hệ; chỉ `s2` bị zero hóa.

## `pose_ambiguity_m=2.70478` và chất lượng nghiệm

`estimate_reference_map()` khởi tạo hai nghiệm IPPE của **tag anchor ID 2**, rồi fit chung camera và bốn reference square trên Z=0 bằng Huber loss. Trường `ambiguity_m` là khoảng cách giữa **vị trí camera** của hai nghiệm sau fit; `estimate_reference_maps()` lấy **max qua 15 frame**. Cực đại 2,70478 m đến từ **frame 9**, không phải pose camera xuất từ frame 15. Nếu hai nghiệm cách nhau trên 0,02 m mà chênh chi phí Huber dưới 2,0, code sẽ trả FAIL; cả 15 frame ở đây PASS.

| Frame | Nghiệm chọn: Huber cost / metric error | Nghiệm kia: Huber cost / metric error | Chênh cost | Tách vị trí camera |
|---|---|---|---:|---:|
| 9 (ambiguity lớn nhất) | 1,32905 / 0,00485 m | 239,32019 / 0,11619 m | **237,99114** | **2,70478 m** |
| 15 (camera pose xuất JSON) | 3,13445 / 0,00841 m | 241,54172 / 0,11667 m | **238,40727** | 2,69439 m |

Qua 15 frame, chênh cost nằm trong **235,521–239,830**, luôn lớn hơn ngưỡng 2,0. Đây là tổng Huber của residual góc ảnh, **không phải mét** và cũng không phải RMS pixel. Nghiệm kia có sai lệch hình vuông reference khoảng 10,7–12,0 cm, vượt xa ngưỡng metric 1,2 cm của nghiệm được chọn. Các seed IPPE riêng của ID 2 trong `diagnostics` có reprojection frame 9 khoảng 0,119 và 1,258 px; sau joint fit, đa tag phân biệt hai nhánh rõ hơn. Do đó trường ambiguity lớn biểu thị hai khả năng hình học cách xa nhau, trong khi **lựa chọn nghiệm đã được giải quyết bởi chi phí**; nó không nói camera hiện sai 2,7 m.

Chất lượng nhánh chọn: 15/15 frame PASS, mọi tag phát hiện 15/15; batch `metric_error_m=0.00841` (<0,012), `joint_reprojection_px=0.725`, `anchor_reprojection_px=0.136`, `stability_m=0.00597`, `holdout_m=0.00522`, không tag bị loại. Calibration RMS 0,2965 px không chuyển trực tiếp thành sai số vị trí vật lý. Độ đúng tuyệt đối của camera vẫn chưa có ground truth bằng thước, dù nhánh nghiệm theo model đã rõ.

## YOLO và màn hình dựng/nghiêng

Hai JSON ghi cùng bbox `(u,v,w,h)=(625.09045,261.64063,79.79419,45.33167)` px, `confidence=0.7109`, `anchor_uv=(625.09045,261.64063)`, `surface=floor`. Debug hai hệ vẽ bbox, dấu tâm và tọa độ đúng theo hệ tương ứng. ID 2 fire là `(0.29640,0.28224,0)`, đổi cứng thành ID 22 `(0.50195,0.66585,0)` trong sai số làm tròn. **YOLO nhận diện ảnh lửa PASS theo kết quả Pi và debug.** Archive không chứa weights nên audit này không chạy lại inference YOLO độc lập.

Frame 1, 8, 15 và debug cho thấy hình lửa đang chạy trên **màn hình điện thoại dựng/nghiêng trên giá**, không nằm phẳng trên mặt bàn. Thuật toán đặt tâm bbox trên tia camera rồi giao với Z=0. Giao điểm đó tồn tại và tự nhất quán, nhưng bề mặt thật của pixel lửa ở trên mặt bàn. Chỉ thay anchor từ tâm bbox sang đáy bbox `(v≈284.30646)` làm giao điểm với mặt bàn dời **7,779 cm** trong mô hình; đây là độ nhạy của giả định mặt bàn, **không phải sai số đo được** của lửa. Đáy bbox vẫn có thể nằm trên màn hình. Không suy ra vị trí 3D của hình lửa từ một ảnh nếu chưa biết mặt phẳng màn hình; không dùng XYZ thuật toán làm ground truth.

## Quyết định code và bước tiếp theo

Audit này **chỉ thêm báo cáo**, không sửa source hay test vì phép đổi hệ và optimizer không lỗi theo bằng chứng. Không nên sửa legacy `nodes.s2` hoặc bỏ gốc phẳng ngay: cả hai thay đổi sẽ đổi nghĩa output đã PASS. Trước một thay đổi API sau này, chọn rõ một pose chuẩn cho từng mục đích: với tọa độ mặt bàn, dùng tâm ID 22 suy từ mặt phẳng cho **cả** biểu diễn ID 2 và ID 22; xuất pose PnP 3D như diagnostic riêng cùng độ lệch/nguồn estimator. Nếu muốn giữ `nodes.s2` PnP trong legacy, đừng dùng nó làm đối tượng kiểm tra bất biến với gốc phẳng zero; ghi provenance tường minh. Bất kỳ thay đổi schema/behavior nào cũng cần test tái hiện trước và giữ tương thích với consumer hiện hữu.

Kiểm thử thực tế kế tiếp: đặt **hình lửa in phẳng sát mặt bàn** thay cho màn hình dựng; chụp một batch 15 frame mới, giữ cùng intrinsics nếu focus/zoom/resolution không đổi. Đo bằng thước từ tâm ID 22 tới tâm ID 21, tâm ID 2 và tâm hình lửa theo X/Y; đo thêm độ cao mặt tag ID 22 trên bìa so với mặt bàn. Khi ấy mới so sai số vật lý. Nếu tiếp tục dùng màn hình, phải hiệu chuẩn mặt phẳng màn hình/3D riêng, ngoài phạm vi checkpoint này. Không bật laser hoặc bơm.

## Verification và file thay đổi

Chạy lại ảnh thật với intrinsics do người dùng gửi bằng OpenCV 5.0.0 trên Windows: camera, metric error, ambiguity, pose s2 và gốc mặt bàn khớp JSON Pi đến độ làm tròn. `pi/tests/test_origin22.py`, `test_origin22_live.py`, `test_dynamic_mapping.py`, `test_tabletop_anchor.py`: **54 passed, 1 skipped**. Regression thêm `test_vision.py` và `test_system.py`: **73 passed, 1 skipped**. Bài skip yêu cầu biến `NT532_LOW_ANGLE_DATASET` cho một bộ ảnh riêng chưa được cung cấp. Một cảnh báo pytest cache Windows ở lượt focused không ảnh hưởng test; lượt regression tắt cache và sạch. Chưa chạy full repo suite trong audit này; checkpoint trước đã ghi các blocker environment của full suite.

File thêm trên branch: `reports/nt532_origin22_consistency_audit.md` (báo cáo này). Không sửa source, test, archive gốc, production, firmware hoặc YOLO. Các script/tệp tạm phục vụ tính toán nằm ngoài branch dưới `build-output/origin22-consistency-evidence/` và không được coi là ground truth.
