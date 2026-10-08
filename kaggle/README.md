# Train mô hình Jev trên Kaggle

1. Đảm bảo code `pi/src/nt532/decider/` và `pi/scripts/train_jev.py` đã được commit và push lên GitHub (notebook clone repo công khai, bản clone phải có các file này).
2. Đăng nhập kaggle.com và xác minh số điện thoại (cần để bật GPU và Internet).
3. Tạo Dataset: Create, New Dataset, tải lên `kaggle/nt532-scenarios.zip` (Kaggle tự giải nén), đặt tên slug `nt532-scenarios`, để Private.
4. Tạo Notebook: Create, New Notebook, File, Import Notebook, chọn `kaggle/train_jev.ipynb`.
5. Cài đặt notebook: Accelerator `GPU T4 x2`, Internet `On`, Add Data và chọn dataset `nt532-scenarios`. Nếu Hugging Face đòi quyền cho `google/embeddinggemma-2`, thêm HF_TOKEN vào Add-ons, Secrets.
6. Bấm Save Version, chọn Save & Run All (Commit). Cả hai lần train chạy song song, ước tính 5 đến 6 giờ, dưới giới hạn 9 giờ.
7. Khi xong, vào Output của phiên bản, tải `jev_results.zip`, giải nén vào `runs/decider/jev/` (ra hai thư mục `jev1` và `heads_only`, mỗi thư mục có `eval.json`, `eval.log`, `train.log`).
8. Chấm lại cục bộ nếu cần: `uv run python scripts/eval_jev.py --name jev1 --device cuda` (có thể thêm `--precision fp32`).
