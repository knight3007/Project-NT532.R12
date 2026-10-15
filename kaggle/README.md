# Train mô hình Jev trên Kaggle

1. Đảm bảo code `pi/src/nt532/decider/` và `pi/scripts/train_jev.py` đã được commit và push lên GitHub (notebook clone repo công khai, bản clone phải có các file này).
2. Đăng nhập kaggle.com và xác minh số điện thoại (cần để bật GPU và Internet).
3. Tạo Dataset: Create, New Dataset, tải lên `kaggle/nt532-scenarios.zip` (Kaggle tự giải nén), đặt tên slug `nt532-scenarios`, để Private.
4. Tạo Notebook: Create, New Notebook, File, Import Notebook, chọn `kaggle/train_jev.ipynb`.
5. Cài đặt notebook: Accelerator `GPU T4 x2`, Internet `On`, Add Data và chọn dataset `nt532-scenarios`. Nếu Hugging Face đòi quyền cho `google/embeddinggemma-2`, thêm HF_TOKEN vào Add-ons, Secrets.
6. Bấm Save Version, chọn Save & Run All (Commit). Hai lượt train chạy song song. Lần chạy 08/10 với 20.000 bản ghi: `jev1` mất 8 giờ 37 phút (0,67 bản ghi/s trên T4, fp32), `heads_only` 2 giờ 37 phút, rất sát giới hạn phiên. Val gần bão hòa từ khoảng bước 900, nên lần sau có thể giảm `MAX_RECORDS` xuống khoảng 14.000.
7. Khi xong, vào Output của phiên bản, tải `jev_results.zip`, giải nén vào `runs/decider/jev/` (ra hai thư mục `jev1` và `heads_only`, mỗi thư mục có `eval.json`, `eval.log`, `train.log`).
8. Chấm lại cục bộ nếu cần: `uv run python scripts/eval_jev.py --name jev1 --device cuda` (có thể thêm `--precision fp32`).

## Phải sinh lại bộ kịch bản và train lại (câu hỏi `nozzle` có thêm `both`)

Câu hỏi `nozzle` nay có ứng viên `both (s1 + s2)` và nhãn mới theo cỡ lửa, nên `nt532-scenarios.zip` phải sinh lại (`make_scenarios.py`, zip lại) và Jev phải train lại. `jev1` cũ chưa từng thấy `both`. Trước khi train lại, chính sách lai vẫn giữ `nozzle` ở luật vì đó là câu hỏi giai đoạn decide.

## Chấm lại mà không train lại

Nếu train xong nhưng bước chấm lỗi (như lần 08/10, thiếu `data/sim/commissioning.yaml`), không cần train lại:

1. Mở notebook, Add Data, chọn tab Notebook Output và thêm output của phiên bản đã train (có `jev_results.zip`). Vẫn giữ dataset `nt532-scenarios`.
2. Save & Run All. Ô "Nạp kết quả cũ" giải nén mô hình vào `runs/decider/jev/`; lượt nào đã có `config.json` hoàn chỉnh thì bỏ qua train, chỉ chấm. Ô này cũng chạy `eval_rules.py` để bắt lỗi chấm điểm trước khi train.
3. Tải `jev_results.zip` mới, có thêm `eval.json` và `eval.log`.

`train_jev.py` ghi mô hình mỗi khi val tốt hơn (chưa khớp nhiệt độ, `config.json` có `partial_step`), nên phiên bị cắt giữa chừng vẫn còn bản dùng được; notebook coi bản này là chưa xong và train lại.

## Chỉ train đối chứng `heads_only` trên Kaggle

Đặt `RUNS = ['heads_only']` trong notebook để chỉ chạy lượt không LoRA (nhanh hơn nhiều, không cần chờ `jev1`).

## Train `jev1` trên GPU cục bộ thay cho Kaggle

Đã chạy trên máy ảo Linux (Ubuntu) có RTX 2060 SUPER 8 GB. Từ thư mục `pi/`:

1. `uv sync --extra decider`.
2. Chép `runs/decider/scenarios/` (bộ kịch bản đã sinh lại), `data/sim/commissioning.yaml` và `data/sim/camera.yaml` sang máy đó, đúng đường dẫn tương đối trong repo.
   Bộ kịch bản hiện dùng là `scenarios_3d_corners` (bia trên sàn và trên bảng, hai vòi ở hai góc trước bàn), sinh từ thư mục `pi/` bằng:

   ```
   uv run python scripts/make_scenarios.py --out ../runs/decider/scenarios_3d_corners --nodes "s1=0,0,0.33@0.6,0.8;s2=1.2,0,0.33@-0.6,0.8"
   ```

   Chép nó thành `runs/decider/scenarios/` trên máy train trước khi chạy. Lượt train trên bộ này tên `jev2` (thay `--name jev1` bằng `--name jev2` ở lệnh dưới); kết quả ở `runs/decider/jev/jev2/`. `jev1` train trên bố cục cũ (trụ sát bảng, bia chỉ trên bảng đứng).
3. Chạy trong `tmux` để phiên SSH rớt không làm mất lượt train:

```
uv run --no-sync python scripts/train_jev.py --device cuda --name jev1 --precision fp32     --epochs 1 --batch 4 --accum 4 --max-records 20000 --max-rss-gb 6
uv run --no-sync python scripts/eval_jev.py --name jev1 --device cuda --precision fp32
```

Với `scenarios_3d_corners`, bản ghi dài hơn và `--batch 4` hết VRAM 8 GB ở bước backward (lượt `jev2` đầu tiên chết ở bước 62). Dùng `--batch 2 --accum 8` (batch hiệu dụng vẫn 16) và đặt `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` trước lệnh; tốc độ khoảng 1,3 đến 1,5 bản ghi/s.

Trên máy ảo (Python 3.14), lượt `jev2` bị segfault trong luồng autograd của PyTorch cứ khoảng 170 bước (`journalctl -k`: `pt_autograd_0 segfault ... in python3.14`). `train_jev.py` nay tắt GC tự động và dọn ở luồng chính sau mỗi bước, ghi `resume.pt` mỗi `--ckpt-every` bước (mặc định 50), và chạy tiếp bằng `--resume`. Script bọc lệnh train trong vòng lặp: mã thoát khác 0 thì chạy lại với `--resume`.

Tốc độ đo được với `jev1` khoảng 1,2 bản ghi/s (Kaggle T4 là 0,67), 20.000 bản ghi mất khoảng 4,5 giờ. RAM dùng 2,6 đến 3,9 GB. VRAM lên tới khoảng 6 đến 7 GB lúc khớp nhiệt độ và chạy val; log có cảnh báo `CUDACachingAllocator` OOM rồi tự thử lại, vô hại. Kết quả nằm ở `runs/decider/jev/jev1/` (`eval.json`, `eval.log`, `train.log`).

