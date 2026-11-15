# NT532 Dynamic Mapping V2 — audit hình học ID 22 trên camera thật (pos01)

Ngày: 2026-11-14 (Asia/Saigon). Branch `fix/nt532-origin22-geometry`, tách từ `main` tại `9e44f10`. Evidence: `build-output/origin22-accuracy-evidence/pos01-20261114T053535Z`.

## 1. Tóm tắt

| Hạng mục | Trạng thái | Căn cứ |
|---|---|---|
| REFERENCE_MAPPING | **PASS** | ID 2 solver: 15/15 frame, `metric_error_m=0.00161`, joint reprojection 0,351 px. Replay offline khớp JSON của Pi đến chữ số cuối |
| OPERATIONAL_ORIGIN | **BLOCKED** (giữ nguyên, có chủ đích) | ID 22 lệch khỏi giả định "hình vuông 80 mm nằm phẳng trên mặt Z=0" 5,84 mm theo cạnh và 7,35 mm theo đường chéo (ngưỡng 5 mm và 7 mm), ổn định đến 0,007 mm qua 15 frame. PnP và estimator mặt bàn lệch nhau 30,7 mm theo XY |
| RAW_YOLO_DETECTION | **PASS** (theo evidence của Pi) | 15/15 frame có bbox, confidence 0,828–0,831. Không chạy lại vì workspace không có `fire-n.pt`; code YOLO không bị sửa |
| FIRE_LOCALIZATION | **NOT VERIFIED** | Gốc ID 22 bị chặn. Ngoài ra lửa hiển thị trên màn hình điện thoại dựng đứng, nên phép giao tia với Z=0 không ra vị trí 3D thật |
| INTEGRATION | **FAIL** | Localization BLOCKED (cổng của `origin22_live.py` không đổi) |
| PHYSICAL_ACCURACY | **NOT VERIFIED** | Chưa có số đo thước. Đã chuẩn bị công cụ (mục 13) |
| PARTNER_COMPATIBILITY | **PASS** | JSON hệ ID 2 giống byte-for-byte. Evidence 013713 cũ vẫn PASS với tọa độ y hệt. 141 test liên quan pass (mục 10) |

**Root cause.** Thuật toán không bị regression. Bố trí đã đổi: camera tiến gần hơn khoảng 57 cm, cao hơn 9,6 cm và chúc xuống 53,3° thay vì 30,6°. Diện tích hull tham chiếu vì thế giảm một nửa, còn ID 22 bị đẩy ra **sát góc dưới trái ảnh** (tâm ở r/rmax = 0,83, góc BL chỉ cách mép trái ảnh 22 px), cách hull 30 cm. Ở vùng đó, quan sát ID 22 không còn khớp với một hình vuông phẳng. Sai lệch tập trung ở **góc ngoài cùng của tag**, tức góc gần mép ảnh nhất. ID 21 ở góc dưới phải có cùng chữ ký nhưng đối xứng gương và nhỏ hơn. Các tag tham chiếu chỉ nằm trong r/rmax ≤ 0,55 nên solver tham chiếu **không ràng buộc được** mô hình ống kính ở vùng ngoài này: đổi hệ số méo k3 ±50% làm cạnh BR-BL của ID 22 dao động 73,6–100,8 mm, trong khi reprojection của tham chiếu gần như không đổi (0,35–0,40 px).

Độ chắc chắn:
- **Cao:** đây là mismatch mô hình có hệ thống, gắn với vị trí trong ảnh. Không phải nhiễu, không phải bias của solver tham chiếu, không phải lỗi thứ tự góc, không phải chọn sai nghiệm IPPE.
- **Trung bình:** phần nào do mô hình méo ống kính, phần nào do giấy tag cong ở mép (giấy của cả ID 21 và ID 22 thò ra khỏi bìa ở phía ngoài; xem ảnh crop). Bộ dữ liệu này không tách được hai nguyên nhân (mục 7).

**Quyết định.** Không có estimator nào chứng minh được là chính xác hơn khi chưa có ground truth, vì vậy giữ BLOCKED. Thay đổi code chỉ thêm **chẩn đoán có cấu trúc** và **công cụ đo sai số vật lý**. Không đổi ngưỡng, không đổi phép chuyển hệ, không đổi YOLO, firmware hay cấu hình phần cứng.

## 2. Nguồn evidence

| Mục | Giá trị |
|---|---|
| Thư mục | `build-output/origin22-accuracy-evidence/pos01-20261114T053535Z` (đúng đường dẫn yêu cầu) |
| Chụp | `2026-11-14T05:35:43Z`, `rtsp://127.0.0.1:8554/cam/stream`, 15 PNG 1280×720 |
| Toàn vẹn | SHA-256 của 15/15 PNG khớp `manifest.json`, kiểm lại sau mọi lần chạy; không ghi vào thư mục evidence |
| Lệnh Pi | `commands.json`: `dynamic_map.py --image-dir … --intrinsics ~/nt532/calibration/camera.yaml --yolo --weights ~/nt532/models/fire-n.pt --world-origin {2,22} --target-anchor center --device cpu`; exit 0 / 1 |
| Intrinsics | `build-output/origin22-consistency-evidence/camera_user_supplied.yaml` (1280×720, fx=993,765, fy=992,296, cx=643,160, cy=360,627, k1..k3=0,0202/0,1681/−0,4473, RMS 0,2965 px, 29 ảnh). Đây là nội dung `camera.yaml` người dùng gửi ở audit trước, không phải bản sao byte của file trên Pi. **Khớp chức năng:** replay ID 2 cho `origin2.json` giống hệt bản Pi (camera, nodes, references, quality) |
| Phiên trước | `build-output/origin22-consistency-evidence/20261114-013713` (15 JPEG, PASS) |

Replay dùng thư mục output riêng `build-output/origin22-geometry-v2/replay_{old,new}/`, không chạy `origin22_live.py evaluate` trên evidence gốc.

## 3. Phân tích ảnh thật

Đã xác minh trên 15 frame (overlay: `reports/dynamic_mapping_v2/pos01_geometry_overlay_frame015.jpg`):

- Sáu tag 0, 1, 2, 3, 21, 22 có mặt 15/15. Cạnh ảnh nhỏ nhất: ID 22 104 px, ID 21 99 px, tham chiếu 49–78 px.
- ID 22 ở góc dưới trái (tâm (88,8; 619,5) px), ID 21 ở góc dưới phải (tâm (1153,8; 591,1) px). Cả hai nằm **ngoài** convex hull của ID 0–3: ID 22 cách hull −30,1 cm, ID 21 −23,0 cm.
- Mapping ID 2 PASS, ID 22 BLOCKED. Debug của Pi ghi `nodes.s2.z = +0.04435 m` (đây là output PnP, không phải độ cao vật lý).
- YOLO: bbox (659,2; 83,8; 150,8×66,4) px quanh hình lửa trên **màn hình điện thoại dựng đứng** ở mép trên ảnh.
- Crop phóng to (`pos01_id22_crop_x2.5.jpg`, `pos01_id21_crop_x2.5.jpg`) cho thấy giấy tag dán trên bìa. Ở cả hai tag node, **giấy thò ra khỏi mép bìa ở phía ngoài**: ID 22 ở phía dưới trái, ID 21 ở phía dưới phải. Nhãn in trên tag: "ID 22 / 80 x 80 mm".

| Tag | r/rmax (tâm) | Độ dịch do méo ở góc xa nhất (px) |
|---|---:|---:|
| 2 | 0,319 | 0,73 |
| 0 / 3 / 1 | 0,424 / 0,387 / 0,549 | 1,17 / 1,65 / 3,00 |
| 21 | 0,760 | 5,72 |
| **22** | **0,830** | 5,00 |

## 4. Hình học ID 22 qua 15 frame

Bảng đầy đủ (cả ID 21, từng cạnh, PnP, camera, khoảng cách hull): `reports/dynamic_mapping_v2/new_pos01_node_tag_geometry.csv`. Thứ tự cạnh là TL-TR, TR-BR, BR-BL, BL-TL theo thứ tự góc của OpenCV. Mỗi frame dùng camera của chính nó, frame cuối dùng camera của batch, đúng như `estimate_table_frame()`.

| Frame | Cạnh (px) | Cạnh trên mặt bàn (mm) | Chéo (mm) | Sai cạnh max (mm) | Sai chéo max (mm) | Reproj mặt bàn RMS/max (px) | Reproj PnP RMS/max (px) | PnP Z (mm) | Nghiêng PnP (°) | PnP−bàn 3D (mm) |
|---:|---|---|---|---:|---:|---|---|---:|---:|---:|
| 1 | 104,11 / 104,18 / 112,57 / 106,52 | 80,74 / 81,36 / **85,83** / 82,83 | 113,24 / **120,48** | 5,83 | 7,35 | 4,54 / 7,24 | 1,62 / 1,70 | 44,40 | 4,77 | 54,02 |
| 2 | 104,10 / 104,19 / 112,58 / 106,52 | 80,73 / 81,38 / 85,84 / 82,83 | 113,24 / 120,48 | 5,84 | 7,35 | 4,54 / 7,24 | 1,62 / 1,70 | 44,41 | 4,76 | 54,03 |
| 3 | 104,10 / 104,18 / 112,58 / 106,52 | 80,73 / 81,37 / 85,84 / 82,83 | 113,25 / 120,48 | 5,84 | 7,34 | 4,54 / 7,24 | 1,62 / 1,70 | 44,38 | 4,76 | 53,99 |
| 4 | 104,11 / 104,18 / 112,58 / 106,52 | 80,74 / 81,37 / 85,84 / 82,83 | 113,25 / 120,48 | 5,84 | 7,35 | 4,54 / 7,24 | 1,62 / 1,70 | 44,39 | 4,76 | 54,01 |
| 5 | 104,10 / 104,15 / 112,58 / 106,52 | 80,74 / 81,36 / 85,84 / 82,84 | 113,25 / 120,49 | 5,84 | 7,35 | 4,54 / 7,25 | 1,62 / 1,70 | 44,43 | 4,77 | 54,05 |
| 6 | 104,10 / 104,13 / 112,57 / 106,53 | 80,74 / 81,35 / 85,83 / 82,84 | 113,24 / 120,48 | 5,83 | 7,34 | 4,54 / 7,24 | 1,62 / 1,70 | 44,36 | 4,76 | 53,97 |
| 7 | 104,09 / 104,13 / 112,57 / 106,52 | 80,72 / 81,34 / 85,82 / 82,84 | 113,24 / 120,46 | 5,82 | 7,33 | 4,53 / 7,23 | 1,63 / 1,71 | 44,29 | 4,75 | 53,88 |
| 8 | 104,09 / 104,12 / 112,58 / 106,53 | 80,73 / 81,34 / 85,83 / 82,85 | 113,23 / 120,47 | 5,83 | 7,33 | 4,54 / 7,24 | 1,63 / 1,71 | 44,33 | 4,76 | 53,93 |
| 9 | 104,08 / 104,12 / 112,58 / 106,53 | 80,72 / 81,34 / 85,83 / 82,84 | 113,23 / 120,47 | 5,83 | 7,33 | 4,54 / 7,25 | 1,63 / 1,71 | 44,33 | 4,77 | 53,93 |
| 10 | 104,08 / 104,12 / 112,58 / 106,53 | 80,72 / 81,34 / 85,83 / 82,84 | 113,23 / 120,47 | 5,83 | 7,33 | 4,54 / 7,24 | 1,63 / 1,71 | 44,33 | 4,76 | 53,93 |
| 11 | 104,11 / 104,12 / 112,58 / 106,52 | 80,75 / 81,34 / 85,83 / 82,84 | 113,25 / 120,47 | 5,83 | 7,34 | 4,54 / 7,24 | 1,62 / 1,70 | 44,34 | 4,76 | 53,95 |
| 12 | 104,13 / 104,12 / 112,58 / 106,51 | 80,76 / 81,34 / 85,84 / 82,84 | 113,26 / 120,47 | 5,84 | 7,33 | 4,53 / 7,23 | 1,62 / 1,70 | 44,31 | 4,75 | 53,91 |
| 13 | 104,14 / 104,12 / 112,58 / 106,51 | 80,78 / 81,34 / 85,84 / 82,84 | 113,26 / 120,48 | 5,84 | 7,35 | 4,53 / 7,24 | 1,62 / 1,69 | 44,38 | 4,76 | 53,99 |
| 14 | 104,14 / 104,12 / 112,58 / 106,50 | 80,77 / 81,34 / 85,84 / 82,83 | 113,25 / 120,48 | 5,84 | 7,34 | 4,53 / 7,24 | 1,61 / 1,69 | 44,35 | 4,76 | 53,96 |
| 15 | 104,14 / 104,12 / 112,59 / 106,50 | 80,77 / 81,33 / 85,84 / 82,83 | 113,26 / 120,48 | 5,84 | 7,34 | 4,53 / 7,24 | 1,62 / 1,69 | 44,35 | 4,76 | 53,96 |

- **Frame gây lỗi:** `estimate_table_frame()` ném lỗi ngay ở **frame 1** (vòng lặp duyệt theo thứ tự), và cả 15/15 frame đều trượt cùng cổng.
- **Ngưỡng bị vượt:** cả hai cổng của kiểm tra hình vuông. Cạnh: |sai| max 5,84 mm > 5 mm, cạnh **BR-BL** (85,84 mm, dài hơn 80 mm là +5,84). Chéo: |sai| max 7,35 mm > 7 mm, đường chéo **TR-BL** (120,48 mm so với 113,14 mm). Nếu đi tiếp, cổng reprojection 3 px cũng trượt: max 7,24 px.
- **Ổn định:** độ lệch chuẩn qua 15 frame là 0,006 mm (cạnh), 0,007 mm (chéo), 0,004 px (reprojection). Đây là sai lệch có hệ thống, không phải nhiễu phát hiện góc.
- Đường chéo TL-BR đúng (113,24 so với 113,14 mm). Cạnh TL-TR gần đúng (+0,7 mm). Hai cạnh và đường chéo **đi qua góc BL** sai nhiều nhất. Đây là biến dạng **cục bộ ở một góc**, không phải tỷ lệ đều.
- Vị trí camera ở hệ ID 2 qua 15 frame: (0,16917; −0,52037; 0,73571) m ± < 0,1 mm.
- Tâm ID 22: theo mặt bàn (−0,22830; −0,19672; 0) m, theo PnP (−0,20442; −0,21606; 0,04435) m. Lệch XY 30,7 mm, Z 44,4 mm, 3D 54,0 mm. Hướng +X của hai estimator lệch nhau 1,0°.

## 5. Hiện tượng PnP Z ≈ +44 mm

1. **JSON:** `origin2.json.nodes.s2 = [-0.20442, -0.21606, 0.04435]`. Replay offline ra đúng giá trị này.
2. **Nguồn:** `Vision.commission_corners()` → `tags.tag_poses()` → `solvePnP(IPPE_SQUARE)` trên 4 góc ID 22 của frame cuối, đổi sang hệ ID 2. Nghiệm được chọn: reproj 1,62 px, nghiêng 4,76°. Nghiệm IPPE còn lại nghiêng 74° với reproj 3,43 px, nên lựa chọn nghiệm đúng.
3. **ID 21 so với ID 22:** PnP Z lần lượt +23,2 mm và +44,4 mm. Các tag tham chiếu nằm trong hull có PnP Z chỉ từ −3,7 đến +2,4 mm, nghiêng ≤ 1,03°. Nghĩa là PnP không có bias độ sâu chung; hai tag ngoài hull, ở vùng rìa ảnh, mới bị "nổi lên".
4. **Hai estimator:** PnP cho phép tag cao và nghiêng tự do; estimator mặt bàn ép tag nằm phẳng ở Z=0. Hai giả định khác nhau nên cho kết quả lệch nhau 54 mm.
5. **Không thể là độ cao vật lý đơn thuần.** Nếu chỉ tìm một độ cao h làm 4 cạnh trung bình bằng 80 mm thì h = 24,0 mm, không phải 44 mm. Ngay cả khi đó vẫn còn sai cạnh 3,04 mm, sai chéo 3,58 mm, reprojection 3,5/5,0 px. Không có hình vuông phẳng 80 mm ở bất kỳ độ cao nào khớp được quan sát, kể cả PnP với reproj 1,62 px, gấp 5 lần các tag tham chiếu (0,26–0,55 px). Vì vậy +44 mm là triệu chứng của mismatch mô hình, **không phải chiều cao thực** của tag.
6. **Liên hệ với sai cạnh:** đúng là có. Nếu dồn sai lệch vào một độ cao giả định, tag cao khoảng 24 mm sẽ chiếu xuống Z=0 to hơn khoảng 3,4%. Phần còn lại là biến dạng cục bộ ở góc BL.

## 6. Ngoại suy ngoài hull tham chiếu

- Diện tích hull 0,190 m² (phiên trước 0,397 m²). ID 22 cách hull 30,1 cm, ID 21 cách 23,0 cm, cả hai ở phía camera.
- **Độ nhạy theo pose camera (leave-one-out):** giải lại frame cuối bằng 3 tag (bỏ ID 0, 1 hoặc 3). Camera dịch ≤ 2,3 mm. Tâm ID 22 trên mặt bàn dịch ≤ 0,6 mm. Sai cạnh max 5,63–6,07 mm. Bất định của extrinsics **không giải thích** được 5,8 mm.
- **Độ nhạy theo mô hình ống kính** (bảng dưới): vùng của ID 22 cực nhạy với hệ số méo bậc cao, nhưng tham chiếu không "cảm nhận" được thay đổi đó. Solver không có thông tin nào để kiểm hay sửa ống kính ở vùng này.

| k3 × | Reproj chung tham chiếu (px) | Cạnh ID 22 (mm) TL-TR / TR-BR / BR-BL / BL-TL | Chéo ID 22 (mm) |
|---:|---:|---|---|
| 0 | 0,395 | 73,8 / 79,0 / 73,6 / 76,7 | 110,2 / 104,1 |
| 0,5 | 0,363 | 76,9 / 80,1 / 78,4 / 79,0 | 111,6 / 110,8 |
| **1 (calib)** | 0,349 | 80,8 / 81,3 / **85,8** / 82,8 | 113,3 / **120,5** |
| 1,5 | 0,356 | 85,8 / 82,9 / 100,8 / 91,5 | 115,4 / 138,5 |

Đổi tiêu cự ±4% không đưa ID 22 về hình vuông: BR-BL vẫn 84,1–89,0 mm, chéo 119,2–123,3 mm. Vì vậy sai tiêu cự đều đơn thuần bị loại.

## 7. Root cause và giả thuyết

| | Giả thuyết | Kết luận | Bằng chứng |
|---|---|---|---|
| A | Extrinsics có bias hệ thống | **REJECTED** | Reproj chung 0,351 px, metric 1,6 mm; leave-one-out dịch tâm ID 22 ≤ 0,6 mm |
| B | Mặt Z=0 không khớp mặt thật | **NOT VERIFIED** (ngoài hull) | Trong hull: PnP Z tham chiếu ±4 mm, nghiêng ≤ 1°. Ngoài hull không có điểm kiểm |
| C | ID 22 cao hoặc nghiêng khác mặt tham chiếu | Độ cao đều: **REJECTED** là nguyên nhân duy nhất. Cong cục bộ: **NOT VERIFIED** | Fit độ cao còn dư 3,0 mm / 5,0 px. Giấy thò khỏi bìa ở phía ngoài cả hai tag node; muốn tự gây ra +5,8 mm, góc BL phải vểnh khoảng 8 mm |
| D | Giao tia–mặt phẳng khuếch đại sai số khi ngoại suy | **REJECTED** là nguyên nhân chính | Khuếch đại bất định camera < 1 mm (leave-one-out) |
| E | Méo ống kính ở góc dưới trái | **CONFIRMED là yếu tố khuếch đại và khớp chữ ký sai số**; độ lớn sai số calib thực **NOT VERIFIED** | ID 22 ở r/rmax 0,83, tham chiếu ≤ 0,55; ±50% k3 đổi BR-BL ±15 mm mà tham chiếu không đổi; sai tập trung ở góc ngoài cùng của ID 22 (BL) và đối xứng ở ID 21 (BR); k2=+0,168 và k3=−0,447 gần triệt tiêu nhau ở rìa ảnh, dấu hiệu đa thức nhạy |
| F | Intrinsics, độ phân giải hoặc stream không nhất quán | Độ phân giải: **REJECTED**. Chế độ camera điện thoại (zoom, ổn định hình, crop) so với ngày calib: **NOT VERIFIED** | 1280×720 khớp; replay khớp Pi tuyệt đối; tiêu cự ±4% không giải thích |
| G | Thứ tự hoặc hướng góc AprilTag sai | **REJECTED** | Hướng +X của PnP và mặt bàn lệch 1,0°; cùng code với tham chiếu đúng |
| H | Giả định của `TableFrame` không phù hợp | **CONFIRMED** cho quan sát này | Giả định "hình vuông 80 mm phẳng ở Z=0 với ống kính đúng" bị vi phạm; cổng chặn **đúng** |
| I | IPPE chọn nghiệm sai | **REJECTED** | Nghiệm thứ hai nghiêng 74°, reproj 3,43 px so với 1,62 px |
| J | Kích thước detector khác kích thước đo | **REJECTED** là nguyên nhân chính | Tag tham chiếu cùng quy ước 80 mm cho cạnh 79,7–80,3 mm; nhãn in "80 x 80 mm"; sai số ID 22 cục bộ chứ không đều |
| K | Hai estimator dùng giả định khác nhau | **CONFIRMED** | PnP 3D tự do và mặt phẳng ép Z=0 lệch 30,7 mm XY / 44,4 mm Z |

**Vì sao phiên trước PASS còn phiên này BLOCKED:**

| Chỉ số | Phiên 013713 (PASS) | pos01 (BLOCKED) |
|---|---|---|
| Camera ở hệ ID 2 | (0,197; −1,090; 0,640) m | (0,169; −0,520; 0,736) m |
| Góc chúc camera / hướng | 30,6° / 84,7° | 53,3° / 85,7° |
| Diện tích hull | 0,397 m² | 0,190 m² |
| ID 22 so với hull | −43,8 cm | −30,1 cm |
| ID 22 r/rmax | 0,764 | **0,830** |
| Cạnh ảnh ID 22 nhỏ nhất | 69 px | 104 px |
| Reproj chung / metric | 0,725 px / 8,41 mm | 0,351 px / 1,61 mm |
| Ổn định / holdout tham chiếu | 5,97 / 5,22 mm | 0,06 / 0,08 mm |
| ID 22: sai cạnh / chéo max | 1,26–2,36 / 1,56–2,44 mm | 5,82–5,84 / 7,33–7,35 mm |
| ID 22: reproj mặt bàn max | 1,53–2,64 px | 7,23–7,25 px |
| ID 22: PnP Z / nghiêng | 2,3–14,1 mm / 1,1–1,6° | 44,3–44,4 mm / 4,75–4,77° |
| ID 22: PnP−bàn (frame 15) | 7,7 mm XY / 9,9 mm 3D | 30,7 mm XY / 54,0 mm 3D |
| Cạnh dài nhất của ID 22 | BR-BL (qua góc BL) | BR-BL (qua góc BL) |
| Độ nhạy BR-BL khi k3 từ 1 lên 1,5 | +4,5 mm | +15,0 mm |

Ở phiên trước, ID 22 đã có cùng chữ ký (cạnh qua góc BL dài nhất) nhưng nhỏ hơn và lọt dưới ngưỡng. Phiên mới tốt hơn về mặt tham chiếu (camera gần, tag to, nhiễu thấp) nhưng đẩy ID 22 sâu vào vùng rìa ảnh, nơi độ nhạy theo mô hình ống kính tăng khoảng 3 lần. Đây là **thay đổi bố trí**, không phải regression code. Code hiện tại cho lại PASS y hệt trên bộ ảnh cũ.

**Dữ liệu còn thiếu để tách nguyên nhân E và C:**
1. Một batch 15 frame mà ID 22 (cùng tờ giấy, cùng bìa) được dời vào gần tâm ảnh, trong hoặc sát hull. Nếu sai cạnh biến mất thì nguyên nhân là ống kính; nếu còn thì nguyên nhân ở tag.
2. Kiểm tra độ phẳng tag bằng thước hoặc thước lá ở góc BL; đo độ dày bìa.
3. Nếu là ống kính: cần ảnh ChArUco phủ các góc ảnh (đặc biệt góc dưới), chụp đúng chế độ camera điện thoại của stream, để đánh giá lại calibration. Bản calib hiện tại có 29 ảnh nhưng không có dữ liệu về độ phủ góc ảnh.

## 8. Thay đổi thuật toán

Không có estimator nào được thay hay nới lỏng.

- `pi/src/nt532/vision/table_frame.py`
  - Thêm `TableFrameError(ValueError)` có `code` và `diagnostics`. Thông điệp lỗi giữ nguyên chữ, nên mọi chỗ đang `except ValueError` hay match chuỗi vẫn chạy.
  - Thêm `analyze_table_frame()`: chạy **cùng cổng, cùng thứ tự, cùng ngưỡng** như trước nhưng đo tất cả frame và trả `(TableFrame | None, diagnostics)`. Diagnostics gồm reason code, frame đầu tiên gây lỗi, danh sách frame lỗi, ngưỡng, đơn vị, sai từng cạnh và chéo, reprojection mặt bàn, pose mặt bàn và pose PnP (hệ `tag_2_table`, có tên estimator), độ lệch giữa hai pose, r/rmax, khoảng cách hull, phân loại `SYSTEMATIC_GEOMETRY_INCONSISTENCY` / `INTERMITTENT`, và cảnh báo không chặn (`ID22_OUTSIDE_REFERENCE_HULL`, `ID22_IMAGE_PERIPHERY_BEYOND_REFERENCES`).
  - `estimate_table_frame()` là wrapper, nhận thêm kwarg tùy chọn `references` để tính hull.
- `pi/scripts/dynamic_map.py`: với `--world-origin 22`, ghi thêm `diagnostics.operational_origin`, và khi bị chặn thì ghi `reason_code` ở mức đầu. Khi BLOCKED, ảnh debug vẽ góc phát hiện, góc chiếu lại, tâm, hull và chú thích sai số. Output `--world-origin 2` không đổi một byte.
- `pi/scripts/physical_accuracy.py` (mới): tính sai số vật lý từ CSV đo thước (mục 13).
- Test mới: `pi/tests/test_origin22_geometry_v2.py`, `pi/tests/test_physical_accuracy.py`, fixture `pi/tests/data/origin22_pos01_corners.json` (góc AprilTag đã phát hiện từ 15 PNG thật, kèm SHA-256 và intrinsics; 29 KB thay vì 24 MB ảnh).
- Báo cáo và phụ lục: `reports/dynamic_mapping_v2/` (CSV 15 frame cho cả hai phiên, overlay, crop, và hai script phân tích `tools/analyze_geometry.py`, `tools/lens_check.py` để tái hiện).

`nodes.s2` vẫn giữ nghĩa cũ: PnP ở hệ ID 2, `[0,0,0]` ở hệ ID 22. Pose PnP và pose mặt bàn của ID 22 nay được báo riêng trong diagnostics, nên người đọc không còn phải đoán nguồn như audit trước đã nêu.

## 9. So sánh cũ và mới trên cùng 15 frame

| Chỉ số | Cũ | Mới | Delta |
|---|---|---|---|
| ID 2 mapping | PASS | PASS, JSON giống byte-for-byte | 0 |
| ID 22 mapping | BLOCKED, chỉ có chuỗi lý do | BLOCKED, `ID22_SQUARE_GEOMETRY`, đủ diagnostics | Trạng thái không đổi |
| Metric error | 0,00161 m | 0,00161 m | 0 |
| Reprojection RMS (chung) | 0,351 px | 0,351 px | 0 |
| ID 22 sai cạnh | 5,84 mm (không xuất) | 5,84 mm, cạnh BR-BL, 15/15 frame | Chỉ thêm báo cáo |
| ID 22 sai chéo | 7,35 mm (không xuất) | 7,35 mm | Chỉ thêm báo cáo |
| Nhất quán pose | Không xuất | PnP−bàn: 30,7 mm XY, 44,4 mm Z, 54,0 mm 3D | Chỉ thêm báo cáo |
| Ổn định | Tham chiếu 0,06 mm; ID 22 không tới bước này | ID 22: std 0,006 mm, `systematic=true` | Chỉ thêm báo cáo |
| YOLO detection | Thô PASS 15/15 | Không đổi (code không sửa, không chạy lại) | 0 |
| Integration | FAIL | FAIL (`compare_results` cho cùng summary) | 0 |
| Phiên 013713 cũ | PASS | PASS, cùng tọa độ (`s1` = (0,90481; −0,05798; 0,00319)) | 0 |

Thuật toán mới **không** được tuyên bố là định vị tốt hơn: trạng thái và mọi tọa độ giữ nguyên. Cái tốt hơn là khả năng giải thích: lỗi được định vị đến frame, cạnh, ngưỡng, mm và px.

## 10. Kết quả kiểm thử

- Đỏ trước sửa: `test_origin22_geometry_v2.py` lỗi import `TableFrameError`, tức code cũ không có diagnostics.
- Nhóm liên quan: `test_physical_accuracy`, `test_origin22_geometry_v2`, `test_origin22`, `test_origin22_live`, `test_dynamic_mapping`, `test_tabletop_anchor`, `test_laser_geometry`, `test_vision`, `test_system`, `test_aiming_geometry_audit`, `test_orchestrator`: **141 passed, 1 skipped**. Bài skip cần biến `NT532_LOW_ANGLE_DATASET` cho một bộ ảnh riêng không có ở đây.
- Toàn bộ suite như CI (bỏ `test_decider.py` và `test_jev.py` vì cần torch): xem mục 10a.
- `ruff check src tests scripts`: sạch. `git diff --check`: sạch.
- Trên Windows phải chạy pytest với `--basetemp` trỏ ra thư mục khác, vì `%TEMP%\pytest-of-<user>` bị từ chối quyền. Đây là vấn đề môi trường, không phải code.

Test mới chứng minh từng yêu cầu: tham chiếu PASS trên dữ liệu thật; ID 22 phát hiện 15/15; ID 22 BLOCKED với đúng chuỗi lỗi cũ, kèm code, frame, cạnh, mm, ngưỡng, `systematic`, hull, r/rmax và hai pose; cùng camera thật và **cùng ngưỡng** nhưng thay ID 22 bằng hình vuông phẳng đúng thì PASS (tâm sai < 1 mm); mất ID 22 vẫn BLOCKED (`ID22_MISSING`); tag quá nhỏ vẫn BLOCKED; tag nghiêng 25° vẫn BLOCKED; CLI giữ `status`/`reason` và chỉ thêm khóa mới.

### 10a. Toàn bộ suite

Chạy trên Windows sau `scripts/commission.py --source sim`, lệnh `pytest -q --ignore=tests/test_decider.py --ignore=tests/test_jev.py`:

- Lượt 1: **421 passed, 3 skipped, 1 failed, 19 errors**.
- Lượt 2 (bỏ `test_fakenode.py` và `test_info.py`): **415 passed, 3 skipped, 2 failed**.

Không lỗi nào liên quan thay đổi này:

| Test | Nguyên nhân | Đối chứng trên `main` (worktree tạm tại `9e44f10`) |
|---|---|---|
| `test_fakenode.py` (8), `test_info.py` (6), tổng 19 errors cả hai lượt | Cần `make … libcore.so` của lõi C firmware; máy Windows này không có `make` trong PATH (`FileNotFoundError`). CI Linux có | Cùng nguyên nhân môi trường |
| `test_aim_point.py::…[coap]` | Link CoAP thật trên localhost Windows | **Cũng FAIL trên `main`**: lỗi có sẵn từ trước |
| `test_remote_decider.py::test_server_token_rejects_missing_or_wrong_bearer` | Chỉ fail ở lượt 2, socket/thời gian | **PASS trên `main`** và PASS ở lượt 1 của branch: flaky |

Các module bị sửa (`table_frame.py`, `dynamic_map.py`) không nằm trong đường đi của những test trên.

## 11. Tương thích với code partner

Không sửa `dynamic_mapping.py`, `system.py`, `tags.py`, `localize.py`, `calibration.py`, orchestrator, `laser_geometry.py`, `origin22_live.py`, firmware hay config. `TableFrame` (class, `point`, `inverse`, `camera`, `tag_pose`) giữ nguyên, nên `laser_geometry.LaserMount.in_frame()` không bị ảnh hưởng. `estimate_table_frame()` giữ chữ ký cũ, chỉ thêm kwarg tùy chọn, và vẫn ném `ValueError` (lớp con). JSON chỉ thêm khóa. `compare_results()` của `origin22_live.py` cho cùng summary trên cả output cũ và mới.

## 12. Triển khai bản thử lên Raspberry Pi

Không dùng production `~/nt532` để chạy code mới; chỉ **đọc** intrinsics, weights và venv của nó. Không flash, không bật laser hay bơm.

Trên Windows (sau khi commit cục bộ trên branch này):

```powershell
cd "D:\IOT Hien Dai\Project\Project-NT532.R12"
git bundle create build-output\nt532-origin22-geometry-v2.bundle fix/nt532-origin22-geometry
$PiIp = "192.168.137.30"
scp "build-output\nt532-origin22-geometry-v2.bundle" "hunn@${PiIp}:/home/hunn/nt532-origin22-geometry-v2.bundle"
```

Trên Pi (dừng nếu một lệnh lỗi; hai thư mục đích phải chưa tồn tại):

```bash
test ! -e "$HOME/nt532-geom-seed" && test ! -e "$HOME/nt532-geom-test"
git init -b main "$HOME/nt532-geom-seed"
git -C "$HOME/nt532-geom-seed" bundle verify "$HOME/nt532-origin22-geometry-v2.bundle"
git -C "$HOME/nt532-geom-seed" fetch "$HOME/nt532-origin22-geometry-v2.bundle" 'refs/heads/fix/nt532-origin22-geometry:refs/heads/fix/nt532-origin22-geometry'
git -C "$HOME/nt532-geom-seed" worktree add "$HOME/nt532-geom-test" fix/nt532-origin22-geometry
cd "$HOME/nt532-geom-test/pi"
PYTHONPATH="$PWD/src" "$HOME/nt532/pi/.venv/bin/python" -c 'import nt532; print(nt532.__file__)'
```

Replay lại đúng bộ pos01 vào một thư mục output mới (không ghi vào evidence):

```bash
cd "$HOME/nt532-geom-test/pi"
EV="$HOME/nt532-origin22-accuracy/pos01-20261114T053535Z"
OUT="$HOME/nt532-geom-replay/$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$OUT"
for O in 2 22; do
  NT532_CONFIG="$HOME/nt532-geom-test/config/site.yaml" PYTHONPATH="$PWD/src" "$HOME/nt532/pi/.venv/bin/python" scripts/dynamic_map.py \
    --image-dir "$EV/frames" --intrinsics "$HOME/nt532/calibration/camera.yaml" \
    --yolo --weights "$HOME/nt532/models/fire-n.pt" --device cpu \
    --world-origin $O --target-anchor center --json "$OUT/origin$O.json" --debug "$OUT/origin${O}_debug.png"; echo "exit $O: $?"
done
python3 -c "import json,sys;d=json.load(open('$OUT/origin22.json'));o=d['diagnostics']['operational_origin'];print(d['status'],d.get('reason_code'),o['summary'],o.get('pose_disagreement_mm'),o['warnings'])"
```

Kỳ vọng: exit 2 = 0; exit 22 = 1; `BLOCKED ID22_SQUARE_GEOMETRY`, sai cạnh khoảng 5,84 mm, `systematic: True`. `origin2.json` phải giống bản Pi cũ trừ các trường YOLO, vốn phụ thuộc vào lần chạy model.

Batch kế tiếp để tách nguyên nhân E và C (chỉ chụp ảnh, không cần sửa code):

```bash
cd "$HOME/nt532-geom-test/pi"
EVIDENCE="$HOME/nt532-origin22-accuracy/pos02-$(date -u +%Y%m%dT%H%M%SZ)"
NT532_CONFIG="$HOME/nt532-geom-test/config/site.yaml" PYTHONPATH="$PWD/src" "$HOME/nt532/pi/.venv/bin/python" scripts/origin22_live.py capture --source rtsp://127.0.0.1:8554/cam/stream --out "$EVIDENCE"
NT532_CONFIG="$HOME/nt532-geom-test/config/site.yaml" PYTHONPATH="$PWD/src" "$HOME/nt532/pi/.venv/bin/python" scripts/origin22_live.py evaluate --evidence "$EVIDENCE" --intrinsics "$HOME/nt532/calibration/camera.yaml" --weights "$HOME/nt532/models/fire-n.pt" --device cpu
```

Trước khi chụp pos02:
- Dời ID 22 (cùng tờ giấy, cùng bìa) vào gần tâm ảnh, sao cho cả 6 tag vẫn trong khung; ID 22 lý tưởng nằm trong hoặc sát hull của ID 0–3.
- Ép phẳng giấy, không để thò khỏi bìa; dán bằng keo cả bốn mép.
- Không đổi zoom hay chế độ camera điện thoại so với pos01.
- Đặt hình lửa in **nằm phẳng** trên bàn.

## 13. Hướng dẫn đo sai số bằng thước

Chỉ đo khi mapping ID 22 **PASS** trên batch đó. Hệ trục `tag_22_table`: gốc ở tâm ID 22; +X dọc mép dưới của tag (theo chữ in), hướng sang phải; +Y hướng về mép trên của tag; đơn vị cm.

1. Đánh dấu tâm từng tag (giao hai đường chéo của ô đen). Đánh dấu tâm hình lửa in phẳng.
2. Kẻ trục X bằng thước thẳng nối dài mép dưới của ID 22; trục Y vuông góc (dùng ê-ke) qua tâm ID 22.
3. Đo x, y của tâm ID 21, ID 2 và từng hình lửa; mỗi giá trị đo 2 lần, lấy trung bình; ghi độ phân giải thước (1 mm).
4. Ghi vào CSV (ví dụ `truth_pos02.csv`):

```csv
position,target,x_true_cm,y_true_cm,surface,measured_by,notes
pos02,ID21,…,…,table,<người đo>,
pos02,ID2,…,…,table,<người đo>,
pos02,fire1,…,…,flat_on_table,<người đo>,hình in A6
```

5. Tính sai số:

```bash
cd "$HOME/nt532-geom-test/pi"
"$HOME/nt532/pi/.venv/bin/python" scripts/physical_accuracy.py --ground-truth truth_pos02.csv \
  --estimate pos02="$EVIDENCE/evaluation/origin22.json" --json "$EVIDENCE/evaluation/physical_accuracy.json"
```

Công cụ tính sai số X, Y và `error_xy = sqrt((x_est-x_true)^2 + (y_est-y_true)^2)`, rồi MAE (X, Y, XY), median, max, RMSE, theo từng target và từng vị trí. Nó từ chối mapping không PASS hoặc không phải hệ `tag_22_table`. Nó loại ID 22 khỏi thống kê (gốc theo định nghĩa) và loại lửa không `flat_on_table`. Không có `--max-error-cm` thì trạng thái chỉ là `MEASURED`; chỉ khi nhóm chốt ngưỡng và truyền vào mới ra PASS/FAIL. Không bao giờ dùng output mapping làm ground truth, và không dùng `metric_error_m` thay cho sai số vị trí.

Nhiều vị trí: lặp `--estimate posNN=…` cho từng batch và dùng chung một CSV. Mỗi vị trí lửa là một batch 15 frame mới.

## 14. Giới hạn đã biết

- Chưa tách được ống kính (E) và tag cong (C); mục 7 ghi dữ liệu cần thêm.
- Intrinsics dùng ở đây là nội dung người dùng gửi, không phải file byte-for-byte của Pi. Replay khớp hoàn toàn nên chênh lệch, nếu có, không ảnh hưởng kết quả.
- Không chạy lại YOLO vì thiếu weights trong workspace. Kết quả YOLO lấy từ evidence Pi và code YOLO không đổi.
- Cảnh báo vùng rìa ảnh chỉ để thông tin, không chặn. Biến nó thành cổng chặn sẽ chặn cả phiên 013713 vốn đã PASS (r/rmax 0,764), nên cần ground truth trước khi quyết định.
- Bộ ảnh pos01 dùng màn hình điện thoại dựng đứng nên không dùng được để đánh giá vị trí lửa.

## 15. Bước tiếp theo

1. Chụp pos02 theo mục 12: ID 22 gần tâm ảnh, giấy ép phẳng, lửa in phẳng. Đây là phép phân biệt E và C rẻ nhất.
2. Nếu pos02 PASS, đo thước theo mục 13 và chạy `physical_accuracy.py`.
3. Nếu vẫn lệch khi ID 22 ở gần tâm: thay hoặc ép phẳng tag ID 22. Nếu chỉ lệch khi ở rìa: hiệu chuẩn lại camera điện thoại với ảnh ChArUco phủ các góc ảnh, đúng chế độ stream, rồi so sai cạnh của ID 21/22 trước và sau.
4. Bố trí vận hành: giữ ID 22 và ID 21 trong vùng r/rmax ≤ khoảng 0,6 hoặc mở rộng hull tham chiếu để bao các node.
5. Sau khi có ground truth, quyết định có biến `ID22_IMAGE_PERIPHERY_BEYOND_REFERENCES` thành cổng chặn hay không.
