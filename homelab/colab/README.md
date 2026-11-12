# Colab worker cho bộ quyết định Jev

Chạy mô hình Jev (GPU) trên Google Colab và phục vụ quyết định cho hub homelab. Colab không nhận kết nối
vào, nên worker **tự nối RA hub** bằng WebSocket (`pi/scripts/colab_worker.py`).

```
Pi --HTTP /decide (Tailscale)--> hub (homelab) <--WebSocket (worker mở ra)-- Colab worker (GPU, Jev)
```

- Pi vẫn dùng `RemoteDecider` như cũ, chỉ trỏ `--decider-url` vào hub.
- Hub chuyển mỗi `/decide` thành message `decide` tới worker; chi tiết ở [protocol.md](protocol.md).
- Colab chết/hết phiên/mất mạng: hub không có worker nên trả lỗi, Pi tự dùng luật (đã có sẵn trong `RemoteDecider`).
  Các chặn an toàn cứng vẫn ở orchestrator trên Pi.

## Biến môi trường

| Biến | Ý nghĩa |
|---|---|
| `NT532_REPO_URL` | URL git của repo (bắt buộc) |
| `NT532_BRANCH` | nhánh, mặc định `main` |
| `NT532_HUB_WS` | `wss://<hub>/api/worker/ws` (bắt buộc) |
| `NT532_WORKER_TOKEN` | token worker (bắt buộc; không ghi log) |
| `NT532_JEV_RUN` | tên run, mặc định `jev2` |
| `NT532_WEIGHTS_URL` | tar.gz trọng số (Drive share dùng gdown, hoặc URL thường) |
| `NT532_WEIGHTS_DIR` | hoặc thư mục trọng số có sẵn (vd Drive đã mount) |
| `NT532_WORKDIR`, `NT532_LOG`, `NT532_FOREGROUND` | tùy chọn, xem đầu `bootstrap.sh` |

Trọng số được đặt vào `pi/runs/decider/jev/<run>`. File tar.gz có thể chứa thư mục `<run>/` hoặc file trải phẳng.

## Cách 1: notebook

Mở `colab_worker.ipynb` trên Colab, chọn runtime GPU, chạy lần lượt: ô cấu hình (nhập token bằng getpass),
ô cài đặt (chạy `bootstrap.sh`, worker chạy nền), ô xem log.

## Cách 2: Colab CLI

```bash
colab new --gpu L4
colab upload homelab/colab/bootstrap.sh /content/bootstrap.sh
colab exec "NT532_REPO_URL=... NT532_BRANCH=feature/homelab-logs NT532_HUB_WS=wss://... \
  NT532_WORKER_TOKEN=... NT532_JEV_RUN=jev2 NT532_WEIGHTS_URL=... bash /content/bootstrap.sh"
colab exec "tail -n 50 /content/worker.log"      # kiểm tra đã nối hub chưa
```

(Cú pháp cụ thể của `colab upload/exec` theo bản CLI đang cài; kích hoạt `colab_env\Scripts\activate` trước.)

## Lưu ý

- Chạy lại `bootstrap.sh` an toàn: bỏ qua clone/trọng số đã có, dừng worker cũ rồi chạy lại.
- Phiên Colab giới hạn thời gian và có thể bị thu hồi; worker không tự khởi động lại sau khi runtime chết, cần dựng lại.
- Không dán token vào notebook lưu công khai; dùng getpass hoặc truyền qua `colab exec`.
- Chạy thử không cần GPU: `uv run python scripts/colab_worker.py --hub-ws ... --decider rules`.
