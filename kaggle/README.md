# Train mô hình Jev trên Kaggle

1. Đảm bảo code `pi/src/nt532/decider/` và `pi/scripts/train_jev.py` đã được commit và push lên GitHub (notebook clone repo công khai, bản clone phải có các file này).
2. Đăng nhập kaggle.com và xác minh số điện thoại (cần để bật GPU và Internet).
3. Tạo Dataset: Create, New Dataset, tải lên `kaggle/nt532-scenarios.zip` (Kaggle tự giải nén), đặt tên slug `nt532-scenarios`, để Private.
4. Tạo Notebook: Create, New Notebook, File, Import Notebook, chọn `kaggle/train_jev.ipynb`.
5. Cài đặt notebook: Accelerator `GPU T4 x2`, Internet `On`, Add Data và chọn dataset `nt532-scenarios`. Nếu Hugging Face đòi quyền cho `google/embeddinggemma-2`, thêm HF_TOKEN vào Add-ons, Secrets.
6. Bấm Save Version, chọn Save & Run All (Commit). Hai lượt train chạy song song. Lần chạy 08/10 với 20.000 bản ghi: `jev1` mất 8 giờ 37 phút (0,67 bản ghi/s trên T4, fp32), `heads_only` 2 giờ 37 phút, rất sát giới hạn phiên. Val gần bão hòa từ khoảng bước 900, nên lần sau có thể giảm `MAX_RECORDS` xuống khoảng 14.000.
7. Khi xong, vào Output của phiên bản, tải `jev_results.zip`, giải nén vào `runs/decider/jev/` (ra hai thư mục `jev1` và `heads_only`, mỗi thư mục có `eval.json`, `eval.log`, `train.log`).
8. Chấm lại cục bộ nếu cần: `uv run python scripts/eval_jev.py --name jev1 --device cuda` (có thể thêm `--precision fp32`).

## Chấm lại mà không train lại

Nếu train xong nhưng bước chấm lỗi (như lần 08/10, thiếu `data/sim/commissioning.yaml`), không cần train lại:

1. Mở notebook, Add Data, chọn tab Notebook Output và thêm output của phiên bản đã train (có `jev_results.zip`). Vẫn giữ dataset `nt532-scenarios`.
2. Save & Run All. Ô "Nạp kết quả cũ" giải nén mô hình vào `runs/decider/jev/`; lượt nào đã có `config.json` hoàn chỉnh thì bỏ qua train, chỉ chấm. Ô này cũng chạy `eval_rules.py` để bắt lỗi chấm điểm trước khi train.
3. Tải `jev_results.zip` mới, có thêm `eval.json` và `eval.log`.

`train_jev.py` ghi mô hình mỗi khi val tốt hơn (chưa khớp nhiệt độ, `config.json` có `partial_step`), nên phiên bị cắt giữa chừng vẫn còn bản dùng được; notebook coi bản này là chưa xong và train lại.
