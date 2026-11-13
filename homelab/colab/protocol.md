# Giao thức hub <-> Colab worker

Colab không nhận kết nối vào, nên **worker mở WebSocket RA hub**. Mọi frame là JSON dạng text.
Hub phơi `/decide` HTTP y hệt `scripts/serve_decider.py` cho Pi; mỗi lượt gọi được chuyển thành một
message `decide` tới worker đang nối, kết quả `decision` chính là body trả về của `/decide`
(`Decision.to_json()`, có thể kèm `"error"` nếu bộ lai ở worker phải dùng luật).

## Kết nối

- URL: `NT532_HUB_WS`, vd `wss://control.example/api/worker/ws`.
- Xác thực: header `Authorization: Bearer <NT532_WORKER_TOKEN>`. Hub cũng nên chấp nhận `?token=<...>`
  làm phương án dự phòng (worker hiện chỉ gửi header; token không bao giờ được ghi log, hub cũng không nên log query).
- Thiếu/sai token: hub từ chối bắt tay (HTTP 401/403). Worker sẽ thử lại.
- Worker tự nối lại mãi mãi, lùi mũ 1 s -> 2 -> 4 ... tối đa 30 s; nối thành công thì đặt lại 1 s.
- Kích thước frame tối đa mà worker nhận: 4 MB. Worker không dùng ping của WebSocket (`ping_interval=None`);
  keepalive là `ping`/`pong` mức ứng dụng bên dưới.
- Nên chỉ cho một worker "chủ" tại một thời điểm; nếu có worker mới nối, hub tự quyết (đề xuất: dùng worker mới nhất, đóng cũ).

## Worker -> hub

| type | trường | khi nào |
|---|---|---|
| `hello` | `worker_id`, `decider` (vd `"jev"`), `run` (vd `"jev2"`), `device` (`"cuda"`/`"cpu"`), `gpu` (tên GPU hoặc `null`), `started` (ISO-8601 UTC) | ngay sau khi nối |
| `status` | `ready` (bool), `busy`, `done` | ngay sau `hello`; mô hình đã nạp xong trước khi nối nên `ready: true` |
| `pong` | `busy` (bool), `done` (số yêu cầu đã xử lý thành công) | trả lời `ping` |
| `result` | `id`, `decision` (object `Decision.to_json()`), `latency_ms` (thời gian chạy mô hình, không tính chờ hàng) | xong một `decide` |
| `error` | `id`, `error` (chuỗi) | `decide` lỗi |

Giá trị `error`: `"deadline"` (đã quá hạn lúc lấy ra khỏi hàng), `"yêu cầu sai dạng: ..."` (thiếu/sai `obs`
hoặc `questions`), hoặc `"<Loại lỗi>: <thông điệp>"` khi mô hình ném ngoại lệ.

## Hub -> worker

| type | trường | ý nghĩa |
|---|---|---|
| `decide` | `id` (uuid chuỗi), `obs` (object), `questions` (object), `deadline_ms` (số, tùy chọn) | yêu cầu quyết định |
| `ping` | | hub gửi mỗi ~20 s; không thấy `pong` trong vài chu kỳ thì coi worker chết |
| `shutdown` | | worker thoát với mã 0 (không nối lại) |

- Có thể gửi nhiều `decide` cùng lúc. Worker xử lý **từng cái một** (khóa quanh mô hình, chạy trong thread nên
  `ping` vẫn được trả lời lúc đang bận); thứ tự trả lời có thể khác thứ tự gửi, hãy ghép bằng `id`.
- `deadline_ms` tính từ lúc worker NHẬN message. Nếu lúc đến lượt xử lý đã chờ lâu hơn `deadline_ms`, worker
  trả `error: "deadline"` mà không chạy mô hình. Không có `deadline_ms` thì không kiểm tra.
- Frame không phải JSON hoặc `type` lạ bị bỏ qua (chỉ ghi log).

## Gợi ý phía hub

- Khi chưa có worker, worker mất kết nối giữa chừng hoặc quá hạn: hub trả HTTP lỗi (5xx/timeout) để
  `RemoteDecider` của Pi rơi về luật; hoặc tự chạy `RuleDecider` rồi trả kết quả.
- Dọn các `id` đang chờ khi worker rớt (trả lỗi cho chúng ngay).
- Đặt `deadline_ms` nhỏ hơn timeout phía Pi (mặc định 3 s) một chút.

## Ví dụ

```json
{"type":"hello","worker_id":"colab-ab12cd","decider":"jev","run":"jev2","device":"cuda","gpu":"NVIDIA L4","started":"2026-11-12T08:00:00+00:00"}
{"type":"decide","id":"7c1e...","obs":{"stage":"verify"},"questions":{"real_fire":{"type":"boolean","candidates":[]}},"deadline_ms":2500}
{"type":"result","id":"7c1e...","decision":{"decider":"jev","answers":{"real_fire":{"answer":true,"confidence":0.93,"probs":{},"source":"model"}}},"latency_ms":41.2}
```
