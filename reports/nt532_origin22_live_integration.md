# NT532 CP-ORIGIN22-LIVE — hướng dẫn kiểm thử camera thật

Ngày chuẩn bị: 2026-11-14. Branch: `feature/nt532-origin22-tabletop`.

## Trạng thái và phạm vi

| Hạng mục | Trạng thái hiện tại | Bằng chứng |
|---|---|---|
| Offline regression | **PASS** | 54 passed, 1 skipped; Ruff PASS trên các file thay đổi |
| Localization, camera thật | **BLOCKED — chưa có bộ ảnh thật** | Chưa chạy trên Pi; đây không phải `BLOCKED` do pose ID 22 |
| YOLO, camera thật | **NOT RUN** | Chưa suy luận bằng `fire-n.pt` trên ảnh thật |
| Integration, cùng 15 frame | **NOT RUN** | Chưa có đầu vào camera thật |
| Độ chính xác vật lý | **NOT VERIFIED** | Chưa có số đo bằng thước; tọa độ thuật toán không phải ground truth |

Không SSH hoặc chạy lệnh trên Pi trong checkpoint này. Không sửa production `~/nt532`, clone `~/nt532-dynamic-test`, firmware ESP32-H2, calibration, weights, laser, bơm hay runtime orchestrator. Không push hoặc merge. Bộ kết quả synthetic của checkpoint trước nằm trong `nt532_origin22_tabletop_implementation.md`; không được tính là camera thật.

## CP0 — audit và thay đổi tối thiểu

`dynamic_map.py` giữ optimizer anchor ID 2. `--world-origin 22` chỉ đổi **hệ tọa độ đầu ra** sang ID 22. Khi ID 2 solver FAIL, kết quả vẫn FAIL; khi solver PASS nhưng ID 22 thiếu hoặc pose không đáng tin, kết quả `BLOCKED` và không xuất tọa độ giả. `--target-anchor center` giao tia đi qua tâm bbox với mặt bàn Z=0. Mặc định ID 2 là `bottom_center`, ID 22 là `center`; quy trình bên dưới đặt `center` rõ ràng cho **cả hai** lần chạy.

Đã bổ sung vào JSON của mục tiêu `bbox_xywh` và `anchor_uv` (giữ `uv` cũ), tên coordinate frame trên ảnh debug, và dấu chữ thập ở pixel anchor. `pi/scripts/origin22_live.py` lưu đúng 15 PNG cùng SHA-256, chạy cùng bộ ảnh qua hai hệ tọa độ, suy luận YOLO thật trên cả 15 frame, xuất ảnh debug YOLO thô và đánh giá ba cổng riêng. Không sử dụng `OracleDetector` hay ảnh lửa giả.

## CP1 — chuyển bundle và tạo worktree mới trên Pi

Bundle `build-output/nt532-origin22-live.bundle` chứa **đầy đủ lịch sử** của branch, nên không cần GitHub hay đọc/ghi clone hiện hữu. Đổi IP nếu Pi đã đổi địa chỉ. Chạy PowerShell trên Windows:

```powershell
$PiIp = "192.168.137.30"
scp -P 22 "D:\IOT Hien Dai\Project\Project-NT532.R12\build-output\nt532-origin22-live.bundle" "hunn@${PiIp}:/home/hunn/nt532-origin22-live.bundle"
```

Chạy trên terminal Pi. Dừng nếu một lệnh trả lỗi; `~/nt532-origin22-seed` và `~/nt532-origin22-test` phải chưa tồn tại:

```bash
test ! -e "$HOME/nt532-origin22-seed"
test ! -e "$HOME/nt532-origin22-test"
git init -b main "$HOME/nt532-origin22-seed"
git -C "$HOME/nt532-origin22-seed" bundle verify "$HOME/nt532-origin22-live.bundle"
git -C "$HOME/nt532-origin22-seed" fetch "$HOME/nt532-origin22-live.bundle" 'refs/heads/feature/nt532-origin22-tabletop:refs/heads/feature/nt532-origin22-live'
git -C "$HOME/nt532-origin22-seed" worktree add "$HOME/nt532-origin22-test" feature/nt532-origin22-live
git -C "$HOME/nt532-origin22-test" cat-file -e '6e0b2ff^{commit}'
git -C "$HOME/nt532-origin22-test" merge-base --is-ancestor 6e0b2ff HEAD
git -C "$HOME/nt532-origin22-test" log -1 --oneline
```

`bundle verify` phải báo *complete history*; `cat-file` và `merge-base` phải exit 0. `log -1` phải là commit live mới, là hậu duệ của `6e0b2ff`. Các lệnh chỉ tạo seed/worktree mới; clone PASS và production không được checkout hoặc sửa.

Kiểm tra dependency và file có sẵn, chỉ **đọc** virtualenv production:

```bash
test -f "$HOME/nt532/calibration/camera.yaml"
test -f "$HOME/nt532/models/fire-n.pt"
test -x "$HOME/nt532/pi/.venv/bin/python"
cd "$HOME/nt532-origin22-test/pi"
PYTHONPATH="$PWD/src" "$HOME/nt532/pi/.venv/bin/python" -c 'import cv2, numpy, yaml, torch, ultralytics; assert hasattr(cv2, "aruco"); import nt532; print(nt532.__file__)'
```

Lệnh cuối phải in đường dẫn `.../nt532-origin22-test/pi/src/nt532/...`, không phải `~/nt532/pi/src`. Nếu thiếu dependency, dừng và gửi lỗi; không `pip install` vào production venv. Dùng `NT532_CONFIG` trỏ tới config branch trong các lệnh tiếp theo để tránh cấu hình shell cũ. Cấu hình, intrinsics và weights được đọc, không bị ghi.

## CP2 — chụp một bộ ảnh thật duy nhất

Đặt ID 0–3, ID 21/22 cùng trên bàn; ID 22 là gốc vận hành, ID 2 vẫn là anchor solver. Đặt hình lửa **nằm phẳng** trên mặt bàn và nhìn thấy rõ. Bảo đảm camera vẫn 1280×720, không che ID 2/22. Chạy trên Pi:

```bash
cd "$HOME/nt532-origin22-test/pi"
EVIDENCE="$HOME/nt532-origin22-evidence/$(date -u +%Y%m%dT%H%M%SZ)"
NT532_CONFIG="$HOME/nt532-origin22-test/config/site.yaml" PYTHONPATH="$PWD/src" "$HOME/nt532/pi/.venv/bin/python" scripts/origin22_live.py capture --source rtsp://127.0.0.1:8554/cam/stream --out "$EVIDENCE"
cat "$EVIDENCE/manifest.json"
```

`capture` exit 0 khi 15 frame 1280×720 được lưu và chất lượng `READY`: cả sáu tag có trong frame cuối, mỗi tag xuất hiện ít nhất 60% frame, cạnh nhỏ nhất ở frame cuối ít nhất 12 px. Exit 1 / `REVIEW`: xem lại frame cuối, chỉnh ánh sáng/góc/che khuất và chụp một timestamp mới. `manifest.json` ghi SHA-256 từng PNG, tỷ lệ tag và cỡ tag; `evaluate` từ chối ảnh bị thay đổi. Điều kiện này không chứng minh hình lửa nằm phẳng — kiểm tra bằng mắt tại sa bàn. Không dùng `/tmp/nt532-fire2.jpg`.

## CP3–4 — Dynamic Mapping và YOLO trên cùng 15 frame

Giữ nguyên `$EVIDENCE` từ phiên shell chụp (hoặc gán lại đúng đường dẫn timestamp), rồi chạy:

```bash
cd "$HOME/nt532-origin22-test/pi"
NT532_CONFIG="$HOME/nt532-origin22-test/config/site.yaml" PYTHONPATH="$PWD/src" "$HOME/nt532/pi/.venv/bin/python" scripts/origin22_live.py evaluate --evidence "$EVIDENCE" --intrinsics "$HOME/nt532/calibration/camera.yaml" --weights "$HOME/nt532/models/fire-n.pt" --device cpu
cat "$EVIDENCE/evaluation/summary.json"
cat "$EVIDENCE/evaluation/yolo_stats.json"
```

`evaluate` exit 0 **chỉ khi Integration PASS**; exit 1 vẫn có thể tạo toàn bộ evidence FAIL/BLOCKED. Script dùng cùng `frames/` và cùng intrinsics, weights, confidence 0.35, `imgsz` 640 cho `--world-origin 2` và `22`; cả hai dùng `--target-anchor center`. `commands.json` ghi nguyên lệnh và exit code. YOLO được chạy trên cả 15 ảnh; kết quả mapping/định vị lửa lấy frame cuối của đúng batch. Nếu YOLO không thấy bbox ở ngưỡng chính, `yolo_stats.json` ghi probe ngưỡng thấp để phân biệt khả năng confidence thấp; probe **không** được dùng để tuyên bố PASS.

| Cổng | PASS khi | FAIL/BLOCKED khi |
|---|---|---|
| Localization | Cả hai mapping PASS; `anchor_id=2`; frame đầu ra lần lượt `tag_2_table`/`tag_22_table`; có ID 0–3, 21/22; ID 22 = `[0,0,0]`; camera, reference và node giữ khoảng cách/chiếu ảnh nhất quán; cả hai debug image có mặt | Solver hoặc so sánh hỏng → FAIL. Solver PASS nhưng ID 22 thiếu/không tin cậy → BLOCKED, không fallback |
| YOLO | Model thật cho ≥1 bbox trên frame cuối; bbox/confidence khớp mục tiêu được định vị trong cả hai frame; anchor là tâm bbox; `surface=floor`, Z≈0; XYZ chiếu ngược về tâm bbox trong 2 px | Không có bbox, inference lỗi, không định vị được bbox, anchor/surface/XYZ sai → FAIL |
| Integration | Localization và YOLO cùng PASS trên bộ 15 ảnh có `capture_quality=READY` | Một cổng FAIL/BLOCKED hoặc capture REVIEW → FAIL |

Các ngưỡng kiểm tra chéo trong script: sai khác khoảng cách tham chiếu/camera ≤2 mm, toàn bộ cặp điểm ≤25 mm, sai khác chiếu reference ≤0.1 px; sai khác khoảng cách target ≤5 mm. Đây là **nhất quán nội bộ giữa hai hệ tọa độ**, không phải sai số vật lý. `quality` trong JSON ghi `metric_error_m`, `joint_reprojection_px`, `stability_m`, `frames_used`; `diagnostics` ghi tag thiếu/bị loại và detection rate. `metric_error_m` không được gọi là sai số vị trí lửa thực.

Nếu `fire_targets=[]`, đọc theo thứ tự: `origin22.json`/`origin2.json` status và `reason`; `yolo_stats.json` count ở frame cuối; `low_confidence_probe`; bbox trong `yolo_latest_debug.png`; `target_anchor=center`; vùng mặt bàn và tag trong `manifest.json`. Bbox thô có nhưng không có `fire_targets` thường liên quan anchor ngoài vùng floor, surface khác hoặc mapping không tin cậy. Không hạ ngưỡng chất lượng chỉ để tạo PASS.

## Evidence và tải về Windows

Trên Pi, mỗi timestamp chứa `manifest.json`, `frames/frame-001.png` đến `frame-015.png`, và `evaluation/` gồm `origin2.json`, `origin22.json`, hai ảnh `origin*_debug.png`, `yolo_stats.json`, `yolo_latest_debug.png`, `summary.json`, `commands.json`, cùng stdout/stderr của mỗi lần mapping. Nếu CLI lỗi sớm, JSON/ảnh tương ứng có thể vắng; stderr và summary ghi lỗi.

Sau khi Pi chạy, dùng PowerShell trên Windows; điền timestamp thật từ `$EVIDENCE`:

```powershell
$PiIp = "192.168.137.30"
$Stamp = "YYYYMMDDTHHMMSSZ"
$Dest = "D:\IOT Hien Dai\Project\Project-NT532.R12\build-output\origin22-worktree\reports\origin22_live_evaluation\$Stamp"
New-Item -ItemType Directory -Path $Dest -Force | Out-Null
scp "hunn@${PiIp}:/home/hunn/nt532-origin22-evidence/${Stamp}/manifest.json" "$Dest\manifest.json"
scp -r "hunn@${PiIp}:/home/hunn/nt532-origin22-evidence/${Stamp}/frames" "$Dest"
scp -r "hunn@${PiIp}:/home/hunn/nt532-origin22-evidence/${Stamp}/evaluation" "$Dest"
```

Hiện tại thư mục `origin22_live_evaluation/` chỉ có README, **không có** JSON/ảnh camera thật trên Windows. Khi tải về, kiểm tra `summary.json`, ảnh gốc và ba ảnh debug trước khi cập nhật trạng thái báo cáo.

## Ground truth còn thiếu và bước tiếp theo

Người dùng đã chọn **chưa đo bằng thước**. Giữ `physical_accuracy=NOT_VERIFIED`. Khi có thể, đo từ **tâm ID 22** đến tâm ID 21, tâm ID 2 và tâm hình lửa theo X/Y trên bàn, ghi đơn vị và chiều trục; hoặc ghi khoảng cách thẳng. Chỉ có khoảng cách thẳng thì báo sai số khoảng cách hướng tâm, không gọi là sai số XYZ. Sau khi chạy Pi, gửi `summary.json`, `manifest.json`, `origin2.json`, `origin22.json`, `yolo_stats.json` và các ảnh debug hoặc tải cả thư mục timestamp về để đánh giá kết quả thật và bổ sung số đo sau.
