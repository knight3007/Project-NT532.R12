# API thị giác cho orchestrator

Phần thị giác nằm trong `pi/src/nt532/vision/`. Orchestrator chỉ cần dùng lớp `Vision` và module `camera`; các hàm rời (`localize`, `solve_camera`…) là phần bên trong.

## Khởi tạo

```python
from nt532.vision import Vision, open_camera, read_fresh, read_frames
from nt532.config import load_site

site = load_site()
cap = open_camera(site["camera"])
vision = Vision.from_site(site)   # nạp calibration/camera.yaml và calibration/commissioning.yaml nếu có
```

`from_site` báo lỗi nếu chưa có `calibration/camera.yaml` (chưa hiệu chuẩn webcam). Thiếu file commissioning thì không lỗi: `vision.blockers()` sẽ trả `["chưa commissioning"]`.

YOLO chỉ được nạp ở lần gọi `detect` hoặc `targets` đầu tiên, mất vài giây. Nên gọi thử một lần lúc khởi động.

## Đọc ảnh

| Hàm | Dùng khi |
| --- | --- |
| `read_fresh(cap)` | Cần đúng khung chụp sau thời điểm gọi, ví dụ ngay sau khi bật laser. Hàm bỏ các khung cũ còn trong bộ đệm của webcam |
| `read_frames(cap, n)` | Commissioning hoặc kiểm tra, cần nhiều khung của cảnh đứng yên |

## Bốn hàm bàn giao

| Hàm | Trả về | Ghi chú |
| --- | --- | --- |
| `vision.detect(frame)` | `list[Detection]` (`u, v, w, h, conf`, pixel ảnh gốc), sắp theo `conf` giảm dần | Chỉ lớp lửa, ngưỡng `vision.detect_conf`. Đã commissioning thì chỉ xét vùng bảng bia |
| `vision.localize((u, v))` | `np.ndarray` (x, y, z) mét, hoặc `None` | `None` khi điểm rơi ra ngoài bảng (nới `board_margin_m`) |
| `vision.tag_poses(frame)` | `dict[str, TagPose]` theo tên node (`"s1"`, `"s2"`) | Pose tức thời trong ảnh; truyền list khung để lấy trung vị |
| `vision.find_spot(on, off)` | `np.ndarray` (x, y, z) của vết laser, hoặc `None` | Chỉ tìm trong vùng bảng bia |

Hàm ghép sẵn cho bước LOCALIZE:

```python
targets = vision.targets(frame, sensor="s1")   # list[Target], mỗi Target có .detection và .position
```

Có `sensor` thì chỉ giữ bia cách sensor đó không quá `targeting.sensor_match_radius_x` theo X. Báo `ValueError` nếu chưa có pose của sensor đó.

## Commissioning và chặn ngắm

```python
state = vision.commission(read_frames(cap, site["vision"]["commission_frames"]))
vision.save()                         # ghi calibration/commissioning.yaml
```

`commission` lấy trung vị trên nhiều khung, giải pose camera từ tag tham chiếu và lấy pose đế từng node. Không thấy đủ 2 tag tham chiếu thì báo `ValueError`. Node nào không hiện đủ khung thì nằm trong `state.missing`. Trên Pi có thể chạy bằng script `pi/scripts/commission.py`.

Trong trạng thái IDLE, gọi định kỳ (ví dụ mỗi 5–10 s):

```python
health = vision.check(read_frames(cap, 5))
```

`check` so cảnh hiện tại với lúc commissioning:

- Tag tham chiếu lệch quá `camera_shift_px` thì đánh dấu `"camera"` hết hạn.
- Tag node dời quá `node_moved_m` thì đánh dấu node đó hết hạn.
- Tag bị che thì không kết luận gì.
- Đánh dấu chỉ xóa khi commissioning lại.

Trước khi ngắm:

```python
reasons = vision.blockers("s1")       # list[str], rỗng là được ngắm
if reasons:
    log(reasons); go_idle()
```

Hàm trả lý do khi chưa commissioning, khi camera bị xê dịch, khi không có pose của node, hoặc khi node bị dời.

## Pose đế vòi

`vision.nodes["s1"]` là `TagPose` của tag trên đế node, lấy lúc commissioning:

- `position` là tâm tag, tính bằng mét.
- `rotation` là ma trận xoay từ hệ tag sang hệ sa bàn.

Hệ tag lấy X sang phải và Y về phía mép trên của tag in ra; Z vuông góc với mặt tag, hướng lên.

Để ra tâm quay pan–tilt, lấy offset do Hiếu đo (tính từ tâm tag, trong hệ tag) rồi gọi:

```python
pivot = vision.nodes["s1"].to_world(offset_xyz)
```

## Bước CORRECT

```python
send_laser(on=False); off = read_fresh(cap)
send_laser(on=True);  on = read_fresh(cap)
spot = vision.find_spot(on, off)      # None nếu không thấy vết
if spot is not None:
    dx, dz = target[0] - spot[0], target[2] - spot[2]
```

Nếu không bao giờ tìm thấy vết laser, nhiều khả năng mặt bảng quá sáng làm kênh đỏ đã chạm trần. Giảm `camera.exposure` trong `config/site.yaml` trước khi hạ `vision.laser_min_rise`.

## Ghép thử bằng sa bàn ảo

Chưa có phần cứng thì dùng `nt532.sim`: `open_camera(cfg, "sim")` trả camera ảo đọc được y như webcam, và `load_site(source="sim")` trả site đã điền vị trí tag tham chiếu mặc định.

```python
from nt532.sim import Scene, SimCamera, sim_site, aim_angles

scene = Scene()                       # bàn, bảng bia, 4 tag tham chiếu, hai node s1 và s2
cap = SimCamera(scene)
```

Trên `scene` có thể thêm hoặc bỏ thẻ bia, bật hoặc tắt laser, đặt góc pan/tilt cho từng node, dời node và xê dịch camera; khung đọc tiếp theo sẽ phản ánh thay đổi. Vị trí thật của mọi thứ đều đọc được để so sai số. `aim_angles` chỉ là công thức tham chiếu cho mô phỏng; công thức chính thức vẫn do orchestrator viết. Xem ví dụ đầy đủ ở `pi/scripts/sim_demo.py`.

## Tham số

Mọi ngưỡng nằm ở mục `vision:` và `targeting:` trong `config/site.yaml`; code không gõ cứng số nào.
