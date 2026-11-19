# Tài liệu NT532

Mục lục của đồ án **HỆ THỐNG PHÁT HIỆN VÀ ĐỊNH VỊ SỰ CỐ CHÁY SỬ DỤNG THREAD VÀ EDGE AI**. Camera Android truyền qua MediaMTX tới Raspberry Pi 5; YOLO, ChArUco, AprilTag và Dynamic Mapping phục vụ phát hiện/Localization trước khi Pi tính góc pan–tilt và gửi lệnh qua OTBR/Thread/CoAP. Trong kịch bản trình bày, Node A là primary, Node B là backup; chưa có bằng chứng nghiệm thu tự động chuyển dự phòng trên phần cứng.

[Runbook E2E](nt532-e2e-runbook.md) phân biệt software E2E với node giả đã đạt và physical hardware E2E còn chờ xác minh. Hình lửa trong tư liệu là nội dung hiển thị trên màn hình laptop, không phải lửa thật. Jev2 thuộc Future Work, không phải nguồn của số liệu hiệu năng dưới đây.

## Project Overview & Architecture

- [README chính](../README.md) — kiến trúc, bảng GPIO Node A/B và kết quả trình diễn.
- [Sơ đồ kiến trúc hệ thống](picture%20and%20video/nt532_system_architecture.png) — luồng camera, Pi và hai node A/B.
- [Runbook E2E](nt532-e2e-runbook.md) — trạng thái xác minh và hướng dẫn vận hành an toàn.
- [Orchestrator](orchestrator.md) — luồng xử lý và các đường chạy hiện có.

## Hardware, Firmware & Thread

- [Tổng quan firmware](../firmware/README.md), [node A tích hợp](../firmware/node-a-integrated/README.md), [node B tích hợp](../firmware/node-b-integrated/README.md), [RCP](../firmware/rcp/README.md).
- [Bảng GPIO và hình wiring A/B](../README.md#hardware-design--gpio-mapping) nằm trong README chính.
- [Triển khai phần cứng](trien-khai-phan-cung.md) và [triển khai Pi](trien-khai-pi.md) — đối chiếu source hiện tại trước khi áp dụng hướng dẫn cũ.
- [Bàn giao node A/B và aim hold, 2026-11-14](nt532-node-ab-final-and-aim-hold-2026-11-14.md) — mốc lịch sử, không xác nhận binary đang nạp hôm nay.

## Raspberry Pi, Vision & Localization

- [Vision API](vision-api.md), [camera điện thoại](camera-dien-thoai.md) và [Dynamic Reference Mapping](dynamic-reference-mapping.md).
- [Đánh giá geometry origin22](../reports/nt532_dynamic_mapping_v2_geometry_audit.md) — nêu rõ trường hợp ID 22 còn bị chặn.

## Demonstration & Dashboard

- [Thư mục tư liệu hình ảnh](picture%20and%20video/) chứa sơ đồ kiến trúc, hình A/B và hai biểu đồ được chọn công bố; video và ảnh khác vẫn ở local để rà soát.
- [Homelab control](homelab-control.md) và [triển khai Homelab](homelab-deploy.md) — giao diện giám sát và đường triển khai.
- [Demo sa bàn ảo](demo.md) và [ảnh dashboard mô phỏng cũ](img/dashboard.png) là tư liệu mô phỏng, không đại diện cho demo A/B hiện tại.

## Experimental Results & Media

**11 lượt được ghi nhận; 9 lượt đủ mốc dùng để tính trung bình.** Hai lượt thiếu mốc do định dạng log cũ không được đưa vào. Trung bình toàn lượt là **74,15 giây**; số liệu lấy từ hai biểu đồ thực nghiệm, không kết hợp với ước lượng thời gian trong báo cáo PDF.

| Giai đoạn | Khoảng đo | Trung bình | Tỷ trọng |
| --- | --- | ---: | ---: |
| LOCALIZE | Báo động → DECIDE | 7,83 s | 10,6% |
| AIM | DECIDE → trúng mục tiêu, gồm quét và chỉnh hướng | 26,17 s | 35,3% |
| FIRE | Trúng mục tiêu → log `đã dập` | 39,77 s | 53,6% |
| VERIFY | Log `đã dập` → sự kiện kết thúc | 0,37 s | 0,5% |
| **Tổng** | Báo động → kết thúc | **74,15 s** | **100%** |

![Thời gian trung bình theo giai đoạn trên 9 lượt đủ mốc](picture%20and%20video/nt532-average-phase-durations.png)

[Biểu đồ timeline của 6 lượt đại diện](picture%20and%20video/nt532-representative-trial-timelines.png) minh họa độ dài từng pha. FIRE, `đã dập` và VERIFY là nhãn log của kịch bản với mục tiêu lửa trên màn hình laptop; chúng không chứng minh hệ thống đã dập cháy thật.

Video và ảnh hiệu chỉnh/cụm A/B còn cần rà soát quyền công bố, thông tin trên màn hình và EXIF trước khi đưa vào Git. [Các báo cáo kỹ thuật](../reports/) cung cấp bối cảnh; không coi biểu đồ là nghiệm thu physical E2E.

## Historical Plans & Handoffs

- [Kế hoạch gốc](ke-hoach-trien-khai-NT532.md), [thay đổi sang hai cụm vòi](thay-doi-hai-voi.md), [kế hoạch NT532](NT532_ke_hoach.md) và [checklist](NT532_checklist.md) là tài liệu lịch sử; hợp đồng CoAP và thiết kế vòi trong đó có thể khác source A/B tích hợp.
- [Bàn giao 2026-11-12](ban-giao-2026-11-12.md), [bàn giao A/B 2026-11-13](ban-giao-nt532-node-a-b-2026-11-13.md) và [bàn giao 2026-11-14](ban-giao-2026-11-14.md) phản ánh từng thời điểm; ưu tiên runbook và source hiện tại khi có khác biệt.
