# Khảo sát dataset

Cập nhật 2026-10-02. Chưa tải bộ nào về.

## Phát hiện lửa (bounding box, định dạng YOLO)

| Dataset | Quy mô | Lớp | Dung lượng | Giấy phép | Nguồn |
| --- | --- | --- | --- | --- | --- |
| D-Fire (bản đã chia train/val/test) | hơn 21.000 ảnh; 14.692 hộp lửa, 11.865 hộp khói; 9.838 ảnh âm tính | 0 = smoke, 1 = fire | 3,1 GB | CC0 | https://www.kaggle.com/datasets/sayedgamal99/smoke-fire-detection-yolo |
| Fire-dataset-for-yolo11 | 32.603 ảnh | 0 = fire, 1 = smoke | 2,1 GB | MIT | https://www.kaggle.com/datasets/mehmoodulhaq570/fire-dataset |

### Lửa trong nhà, hộ gia đình (khảo sát 2026-10-03)

| Dataset | Quy mô | Lớp | Dung lượng | Giấy phép | Tải |
| --- | --- | --- | --- | --- | --- |
| Home-fire (Peng và Kim, IEEE Access 2025) | 6.500 ảnh, chia sẵn 3.900/1.300/1.300 | flame, smoke | 1,82 GB (3 file zip) | CC BY-NC 4.0 | GitHub release, không cần tài khoản: https://github.com/PengBo0/Home-fire-dataset/releases/tag/v1.0.0 |
| Indoor Fire Smoke (Binus University, 2025) | 5.000 ảnh, chia sẵn 3.500/750/750 | fire, smoke | 200 MB | CC BY 4.0 | Zenodo, không cần tài khoản: https://zenodo.org/records/15826133 |
| Domestic Fire and Smoke (DataCluster Labs) | khoảng 5.000 ảnh | fire, smoke | | thương mại | Repo chỉ có ảnh mẫu, muốn bản đầy đủ phải liên hệ bán hàng |

Home-fire tập trung vào ngọn lửa nhỏ và khói giai đoạn đầu, góc nhìn camera giám sát trong nhà, cắt từ khoảng 400 video, có cả ảnh hồng ngoại đen trắng ban đêm.

Đã tải về `data/raw/home-fire/` và `data/raw/indoor-fire-smoke/` ngày 2026-10-03. **Cả hai bộ dùng 0 = fire, 1 = smoke, ngược với D-Fire** (xác nhận bằng cách xem ảnh cắt từng lớp); `data.yaml` của mỗi bộ đã ghi đúng tên lớp. Gộp với D-Fire thì phải đổi chỉ số lớp.

| Bộ | Phần | Ảnh | Hộp lửa | Hộp khói | Ảnh không nhãn |
| --- | --- | --- | --- | --- | --- |
| Home-fire | train | 3.900 | 2.978 | 1.834 | 87 |
| | val | 1.300 | 963 | 617 | 33 |
| | test | 1.300 | 897 | 689 | 15 |
| Indoor Fire Smoke | train | 3.500 | 2.504 | 2.342 | 0 |
| | valid | 750 | 547 | 496 | 0 |
| | test | 750 | 541 | 505 | 0 |

Indoor Fire Smoke gần như không có ảnh âm tính, nên nếu train chỉ trên bộ này model dễ báo nhầm.

D-Fire còn có link OneDrive chính thức, không cần tài khoản: https://github.com/gaiasd/DFireDataset

## Phân loại theo chất liệu cháy (nhãn cấp ảnh, không có bounding box)

| Dataset | Quy mô | Lớp | Dung lượng | Giấy phép | Trạng thái |
| --- | --- | --- | --- | --- | --- |
| ClassesOfFire (Alkhammash, Fire 2025) | 4.481 ảnh: A 2.446, B 186, C 462, D 169, F/K 164, không cháy 1.054 | A chất rắn, B chất lỏng và khí, C điện, D kim loại, F/K dầu ăn | 775 MB | CC BY-NC-SA 4.0 | Tải được: https://www.kaggle.com/datasets/imankhammash/classesoffire |
| Fire Type Classes (Refaee và cộng sự, Applied Sciences 2024) | 1.353 ảnh: không cháy 541, gỗ và chất rắn 308, khí và hóa chất lỏng 163, điện 187, dầu 154 | 5 lớp | chưa rõ | chưa rõ | Link Zenodo trong bài báo (records/13119922) trả về 404 |
| Fire_classes_dataset (A/B/C/K) | chưa rõ | 4 lớp | chưa rõ | chưa có | Repo https://github.com/imtidotcom/Fire_classes_dataset chỉ có README, chưa có ảnh |
| MAFD (lửa trong nhà kèm nhãn vật liệu) | 3.899 ảnh | phân đoạn vật liệu | chưa rõ | chưa rõ | Không tìm thấy kho tải |

Ghi chú về ClassesOfFire:

- Lệch lớp nặng: lớp A chiếm hơn một nửa, các lớp B, D, F/K chỉ 160–190 ảnh.
- Ảnh gộp từ năm bộ công khai và ảnh tìm trên mạng, nhãn do tác giả gán và có chuyên gia chữa cháy kiểm tra.
- Giấy phép không cho dùng thương mại; dùng cho đồ án môn học thì được, cần ghi nguồn.

## Liên quan nhưng không phải phân loại chất liệu

- Fire Recognition Image Dataset (Mendeley, 2025): 1.112 ảnh gốc, bốn lớp Real Fire, Smoke, Safe Fire, Artificial Fire. Lớp "Artificial Fire" gần với thẻ bia in hình lửa. https://data.mendeley.com/datasets/7jk6xh7h6w/4
- CerealJosh/Fire-Classification-Project: ảnh chia theo màu ngọn lửa (xanh, lục, cam, trắng), khoảng 150 MB, MIT. https://github.com/CerealJosh/Fire-Classification-Project

## Chưa tra được

- Roboflow Universe: trang chặn truy cập tự động. Có nhắc tới bộ `fypfirerooster/fire-detection-classification`, cần mở bằng trình duyệt để xem.
- Semantic Scholar và arXiv API: bị giới hạn tần suất trong lúc tra.

## Sau khi lọc ClassesOfFire (2026-10-03)

`pi/scripts/prepare_classesoffire.py` bỏ 67 ảnh trùng nội dung, không có ảnh hỏng, còn 4.414 ảnh, chia 70/15/15 theo từng lớp vào `data/dataset/classesoffire/`.

| Lớp | train | val | test |
| --- | --- | --- | --- |
| A | 1.705 | 366 | 366 |
| B | 128 | 27 | 27 |
| C | 287 | 61 | 61 |
| D | 119 | 25 | 25 |
| F | 115 | 24 | 24 |
| none | 738 | 158 | 158 |

Cảnh báo: định dạng file lệch theo lớp. Lớp A gần như toàn JPG (2.445/2.446), còn B, D, F chủ yếu là PNG. Model có thể học phân biệt lớp qua vết nén ảnh thay vì nội dung, nên độ chính xác trên tập test của bộ này có thể cao giả. Cần nén lại toàn bộ về cùng một định dạng trước khi train, và kiểm tra bằng ảnh tự chụp.
