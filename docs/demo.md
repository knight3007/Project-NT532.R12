# Demo trạm trên sa bàn ảo

Kịch bản demo chạy trọn trạm (cảm biến ảo, camera ảo, orchestrator, bộ quyết định, hai vòi ảo) qua tám tình huống cố định, đo từng cảnh và chấm điểm. Dùng cho hai việc: trình diễn ở tuần 6 (kèm dashboard, khán giả xem từng pha) và lấy số liệu cho báo cáo HTML của tuần 5 (kết cục, độ trễ báo động tới bơm, tỷ lệ quyết định đúng, vi phạm an toàn).

Mã nằm ở `pi/src/nt532/sim/demo.py` (các cảnh, chấm điểm), `pi/scripts/demo_station.py` (dòng lệnh), mục "Kịch bản demo trên sa bàn ảo" của `pi/src/nt532/report/build.py` và test ở `pi/tests/test_demo.py`.

## 1. Cách chạy

Trình diễn trực tiếp, mở http://localhost:8080/ để xem; `--step` dừng chờ Enter trước mỗi cảnh để người nói kịp giới thiệu (sa bàn được dọn sạch trước khi bấm Enter):

```bash
cd pi
uv run python scripts/demo_station.py --step
uv run python scripts/demo_station.py                  # chạy liền, nghỉ 3 s giữa các cảnh (--pause)
uv run python scripts/demo_station.py --scenes fire_s1,lamp,estop
```

Lấy số liệu cho báo cáo, không dashboard:

```bash
uv run python scripts/demo_station.py --headless --report
uv run python scripts/demo_station.py --headless --fast --scenes spike,estop --report   # thử nhanh
```

`--report` dựng `runs/report/<thời điểm>/index.html` chỉ từ lần demo này (nhật ký trạm cùng tóm tắt của demo). `--fast` dùng chu kỳ ngắn như các test (khung 0,05 s, chờ VERIFY 1 s) nên chạy nhanh hơn nhưng độ trễ nhỏ hơn thật; số độ trễ đưa vào báo cáo thì chạy không có `--fast`.

Chọn bộ quyết định giống `run_station.py`:

```bash
uv run python scripts/demo_station.py --decider hybrid --model-stages decide,verify   # Jev ở DECIDE và VERIFY, cần runs/decider/jev/jev1
uv run python scripts/demo_station.py --decider remote --decider-url http://laptop:8090
uv run python scripts/demo_station.py --detector yolo                                  # YOLO thật thay oracle
```

Ctrl+C gửi `/stop` tới mọi node rồi thoát, vẫn ghi tóm tắt các cảnh đã xong. Mã thoát là 1 nếu có cảnh vi phạm an toàn, ngược lại 0; quyết định sai không đổi mã thoát.

Kết quả ghi vào `runs/` (hoặc `--runs-dir`):

| File | Nội dung |
| --- | --- |
| `station/<thời điểm>.jsonl` | Nhật ký sự kiện của trạm, như `run_station.py`; `make_report.py` đọc được. Mỗi cảnh có hai sự kiện `demo` đánh dấu đầu và cuối |
| `demo/<thời điểm>/summary.json` | Giờ bắt đầu và kết thúc, bộ quyết định, cài đặt, seed và từng cảnh: tiêu đề, mong đợi, các lượt (rút gọn), vòi đã phun, độ trễ, lửa tắt hay chưa, verdict |
| `report/<thời điểm>/index.html` | Chỉ có khi dùng `--report` |

## 2. Các cảnh

Tọa độ trên mặt bảng (mét), node s1 ở X 0,30 và s2 ở X 0,90.

| Cảnh | Dàn gì | Khán giả thấy | Kết quả mong đợi với luật | Với Jev ở DECIDE |
| --- | --- | --- | --- | --- |
| `fire_s1` | Đám cháy nhỏ tại (0,32; 0,30), sát s1 | s1 báo động, khung hình có bia lửa, laser chấm lên bia, vòi s1 phun | Phun bằng s1, lửa tắt, bơm và laser tắt hết | Như luật; chọn cả hai vòi cho lửa nhỏ bị tính là nhầm vòi |
| `fire_s2` | Đám cháy nhỏ tại (0,90; 0,30), sát s2 | Như trên với s2 | Phun bằng s2, lửa tắt | Như luật |
| `fire_large` | Đám cháy lớn tại (0,60; 0,30) | Cả hai node báo động; thẻ lửa to, cần nhiều nước hơn nên có thể qua vài lượt do trạm tự xử lý lại | Một vòi gần nhất (luật không bao giờ chọn `both`), lửa tắt, có thể sau nhiều lượt | Có thể trả lời `both`: hai bơm chạy cùng lúc (`--decider hybrid --model-stages decide,verify` hoặc `--decider remote`), cột "vòi phun" ghi `s1+s2 (cùng lúc)` |
| `lamp` | Đèn nóng (thẻ đèn) tại (0,32; 0,30): nhiệt cao, gần như không khí | Cảm biến báo động, camera thấy một thẻ giống lửa | Không phun. Luật chỉ nhìn độ tin cậy của camera nên thỉnh thoảng phun nhầm: ghi nhận là sai quyết định, không phải lỗi chương trình | Dựa cả vào chuỗi cảm biến nên kỳ vọng nhận ra đèn; đây là cảnh cho thấy khác biệt giữa luật và Jev |
| `steam_object` | Vật màu cam tại (0,88; 0,30) cộng hơi nước nấu ăn gần s2 | Ẩm và nhiệt tăng, camera thấy vật cam | Không phun | Như luật |
| `spike` | Xung khí ngắn trên s1, bảng trống | Báo động rồi lượt kết thúc `ignored` sau LOCALIZE vì không có bia | Không phun, mọi lượt `ignored` | Như luật |
| `node_offline` | s2 mất liên lạc, đám cháy tại (0,90; 0,30) và một xung nhiễu trên s1 để có cảnh báo | Dashboard báo s2 mất heartbeat; trạm không ngắm hay phun bằng s2 | s2 không bao giờ bơm hay bật laser; lượt kết thúc an toàn (`ignored`, `alarm_only`, `extinguished` hoặc `human`); lửa gần s2 có thể không tắt | Như luật: chặn an toàn là luật cứng, mô hình không vượt được |
| `estop` | Đám cháy nhỏ; khi trạm vào CORRECT hoặc FIRE thì bấm dừng khẩn cấp | Lượt dừng giữa chừng, bơm và laser tắt ngay | Lượt `stopped`; bơm và laser tắt hết trong 1 s sau lệnh dừng | Như luật |

Hai thứ cố ý để mô phỏng "không hoàn hảo": detector oracle cho thẻ đèn và vật cam đôi khi vượt ngưỡng tin cậy, và cảm biến có nhiễu. Cùng `--seed` vẫn cho kết quả hơi khác giữa các lần vì các luồng chạy theo thời gian thật.

## 3. Cách chấm

Mỗi cảnh có hai kết luận độc lập.

**An toàn** (`AN TOÀN` hoặc `VI PHẠM AN TOÀN`). Vi phạm khi một trong các điều sau xảy ra:

- hết cảnh mà còn bơm hoặc laser đang bật (một cảnh hết giờ thì trạm bị dừng khẩn cấp trước khi kiểm);
- node bị cấm (`node_offline`: s2) từng bơm hoặc bật laser, theo mẫu lấy 20 lần mỗi giây từ sa bàn ảo;
- sau nút dừng khẩn cấp (`estop`) bơm hoặc laser còn bật quá 1 s.

Đây là lỗi nghiêm trọng: mã thoát của `demo_station.py` là 1 và phải sửa trước khi demo.

**Quyết định** (`ĐẠT`, `KHÔNG ĐẠT`, hoặc `không chấm` với cảnh không đặt kỳ vọng nào). Trạm phải phun khi cần và không phun khi không cần, phun đúng vòi của cảnh nhỏ, lửa phải tắt khi cảnh đòi, mọi lượt kết thúc trong tập mong đợi (`spike`: `ignored`; `estop`: `stopped`), cảnh có ít nhất một lượt và không hết giờ. Sai quyết định là thông tin để so luật với Jev, ví dụ `lamp` phun nhầm; nó không đổi mã thoát và các ghi chú in kèm sau mỗi cảnh. Tiêu chí lửa tắt đọc từ trạng thái thật của sa bàn ảo, không phải kết luận `extinguished` của trạm (luật coi một lần phun trúng là xong dù lửa chưa tắt hẳn).

Trước mỗi cảnh trạm được đưa về trạng thái sạch: sa bàn trống, mọi node online, orchestrator ở IDLE với hàng đợi trống, không node nào còn báo động, và quên các lượt "xử lý lại" còn treo từ cảnh trước (`Orchestrator.forget_followups`), để lượt tự xử lý lại của cảnh trước không mở thêm lượt trong cảnh sau. Khi viết demo, cảnh `estop` từng lộ lỗi: dấu "đã xong" của một lượt cũ trên cùng node làm trạm tự mở lượt mới khoảng 2 s sau nút dừng và bơm chạy lại. Lỗi đã sửa trong orchestrator: dừng khẩn cấp giờ quên mọi cảnh báo và lượt xử lý lại có trước lúc dừng.

Sau mỗi cảnh in một dòng kết quả, cuối buổi in bảng tổng hợp và dòng "Quyết định đạt x/y, an toàn x/y". Độ trễ là từ cảnh báo đầu tiên của cảnh tới lần bơm đầu tiên bật. Cảnh nào không phun thì không có độ trễ.

## 4. Lặp lại trên sa bàn thật

Cùng tám tình huống này sẽ được dàn lại bằng tay trên sa bàn thật ở tuần 5–6 (thẻ lửa, thẻ đèn và vật cam đặt lên bảng, hơi nước hoặc kích cảm biến, rút nguồn một node, bấm nút dừng khẩn cấp trên dashboard), chạy `run_station.py` với phần cứng và so kết quả với bảng ở trên. Các bước dựng, thứ tự kiểm tra và sự cố thường gặp ở [trien-khai-phan-cung.md](trien-khai-phan-cung.md) (mục 8 chạy trạm trọn vòng, mục 9 các bài đo). Kết quả trên sa bàn ảo là mốc để so, không thay cho số đo thật.
