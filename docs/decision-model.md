# Mô hình quyết định: bộ kịch bản tổng hợp và baseline luật

## Mục đích

Orchestrator hiện quyết định bằng luật cố định (ngưỡng conf, bán kính ghép sensor, chọn vòi). Luật này sai khi quan sát nhiễu: vật gây nhiễu giống lửa, sensor spike, hơi nước, vòi gần hỏng, camera bỏ lỡ vài khung. Bước này dựng dữ liệu để huấn luyện một mô hình học P(sự thật | quan sát) thay vì chép lại luật, và đo luật trên cùng dữ liệu để có mốc so sánh.

Các chặn an toàn cứng (mất heartbeat, pose cũ, mục tiêu ngoài bảng, góc ngoài giới hạn) vẫn là luật, không giao cho mô hình.

## Giao diện kiểu Jev

Đầu vào: một đoạn STATE dạng văn bản và các câu hỏi có kiểu. Đầu ra: xác suất cho từng ứng viên, không sinh văn bản.

| Câu hỏi | Kiểu | Ứng viên | Khi nào hỏi |
| --- | --- | --- | --- |
| `real_fire` | boolean | đúng/sai | giai đoạn `decide` |
| `action` | choice | `spray`, `alarm only`, `ignore` | `decide` |
| `target` | choice | các bia đang thấy, dạng `T1 (x 0.31 z 0.22)`, cộng `none of these` | `decide`, khi có ít nhất một bia thấy |
| `nozzle` | choice | `s1`, `s2`, thêm `both (s1 + s2)` khi quan sát cho thấy cả hai vòi khỏe (cùng ngưỡng heartbeat/pose với luật) và có một bia thấy mà cả hai với tới | `decide`, khi lửa thật, bia thật đang thấy và có vòi khỏe với tới được |
| `after_verify` | choice | `done (fire out)`, `re-aim (spray missed)`, `spray more (hit, still burning)`, `call human (give up or fault)` | giai đoạn `verify`, sau mỗi lần phun |

Ứng viên của `target` và `nozzle` thay đổi theo từng bản ghi (bia nào camera thấy; `both` chỉ khi quan sát cho phép). Trạm thực thi đúng đáp án: `both` là AIM + CORRECT lần lượt từng vòi rồi bật hai bơm cùng lúc, lần verify coi như một lần phun; vòi bị chặn thì bỏ vòi đó và phun bằng vòi còn lại (xem [thay-doi-hai-voi.md](thay-doi-hai-voi.md)). Kịch bản chỉ sinh bản ghi `verify` khi nhãn `action` là `spray`.

## Cách sinh dữ liệu

Mỗi kịch bản bốc một sự thật ẩn:

- Loại: lửa thật (45%), vật gây nhiễu kèm báo động sai (25%: đèn nóng, hơi nước hoặc xung, kèm bia giả), hơi nước nấu ăn (15%), xung cảm biến (15%). Bộ `test_shift` đổi tỉ lệ này.
- Cỡ đám cháy của lửa thật: `small` hoặc `large` (large khoảng 30%, `test_shift` 45%). Cỡ chỉ nằm trong truth (`truth.size`), không có trong state.
- Vị trí lửa thật và các bia khác trên bảng; vị trí đo được có nhiễu 1,5 cm.
- Sức khỏe từng vòi (heartbeat, pose) và khả năng với tới bia theo hình học thật của sa bàn (vị trí node lấy từ `data/sim/commissioning.yaml`).
- Sau khi phun: lệch điểm trúng thật, lửa đã tắt chưa, lần phun thứ mấy, vòi có lỗi không.

Quan sát trong state đều đi qua nhiễu:

- Phát hiện YOLO lấy từ cache thật (`scripts/cache_yolo_obs.py`): bia lửa thật dùng ảnh có lửa của đúng split, bia gây nhiễu dùng ảnh âm tính, ưu tiên ảnh mà detector báo sai. 3 đến 5 khung, mỗi khung cộng nhiễu conf và có thể rơi.
- Cảm biến: nhiệt, khí, độ ẩm 5 mẫu cuối trước lúc báo động (3 mẫu liên tiếp vượt ngưỡng).
- Tuổi heartbeat và pose của từng vòi; vòi hỏng đôi khi trông vẫn bình thường (trường hợp sát ngưỡng).
- Cỡ đám cháy thể hiện qua hai quan sát (cùng đường nhiễu như các quan sát khác): hộp YOLO của bia lửa thật nhân hệ số `BOX_SCALE` (small 0,7 đến 1,0; large 1,5 đến 2,2; tối đa 0,95), nên `box WxH` trong state lớn hơn; và lửa lớn làm tín hiệu cảm biến mạnh hơn (đỉnh nhân `large_gain`) và suy giảm theo khoảng cách chậm hơn (`decay_m` nhân `large_decay`), nên cả hai node đều tăng rõ. Chọn cách này vì state đã có `box` nên không thêm trường hay token.
- Khả năng với tới do Pi tính từ vị trí đo, không phải vị trí thật.
- Giai đoạn verify: conf sau phun, nhiệt sau phun, độ lệch vết nước đo được (có thể không tìm thấy), trạng thái vòi (đôi khi lỗi mà không báo).

Nhãn lấy từ truth. Truth nằm ở trường `truth`, chỉ để phân tích; state không chứa nó. Mỗi bản ghi còn có trường `obs` (quan sát có cấu trúc, cùng nội dung với state) để baseline luật khỏi phải phân tích văn bản.

Quy tắc nhãn: `action` = spray nếu lửa thật, bia thật đang thấy và có vòi khỏe với tới được; `alarm only` nếu lửa thật mà không làm gì được; `ignore` nếu không có lửa. `nozzle` = `both (s1 + s2)` nếu lửa thật, cỡ `large`, và cả hai vòi thật sự khỏe và với tới bia thật (và quan sát có ứng viên `both`); ngược lại là vòi khỏe, với tới được và gần bia nhất theo khoảng cách 3D từ trục quay của node (vị trí trong `commissioning.yaml`) tới bia thật. Obs có thêm `targets[].dist3` (khoảng cách 3D tới bia đo được, không in vào state vì hai node cùng độ cao và cùng cách bảng nên thứ tự gần xa giống `dist_s1`/`dist_s2` theo X; live thiếu `pivot` thì `dist3` = `dist`). `after_verify`: tắt rồi thì `done`; vòi lỗi hoặc đã đủ 3 lần thì `call human`; lệch quá 3 cm thì `re-aim`; còn lại (trúng mà vẫn cháy) thì `spray more`. Xác suất đã tắt sau một lần trúng là 65%, giảm còn 40% khi đám cháy `large` mà chỉ một vòi phun (để `spray more` xuất hiện); khi phun `both`, nhiệt sau phun lấy trung bình hai node.

Không rò rỉ giữa các split: kịch bản của split X chỉ dùng ảnh split X. `test_shift` dùng ảnh split test nhưng detector khác (`mix26-neg-20261003.pt`), cảm biến nhiễu gấp 1,8, nhiều vật gây nhiễu hơn, khung rơi 25%, vòi hỏng 20%.

## Phần giả định (placeholder)

Số đọc cảm biến chưa đo thực. Toàn bộ phân phối nằm trong `SensorModel` ở `pi/src/nt532/decider/scenario.py` (nền, nhiễu, ngưỡng 45 °C và 600, độ suy giảm theo khoảng cách, hình dạng xung, độ ẩm khi có hơi nước). Cũng là giả định: `large_gain` (1,5) và `large_decay` (2,0) của lửa lớn, hệ số hộp `BOX_SCALE`, tỉ lệ lửa lớn, xác suất sau phun của lửa lớn (`fit_sensor_model.py` đọc/ghi hai trường mới như mọi trường float khác, nhưng chưa ước lượng chúng), giới hạn pan ±70° (nâng từ ±50°: node ở X 0,30 và 0,90 cách bảng 0,24 m, ±50° không có vùng nào cả hai vòi cùng với tới nên không thể có nhãn `both`) và tilt -35 đến 45° (site.yaml còn để null), xác suất vòi hỏng và lỗi, phân phối độ lệch điểm trúng, ngưỡng pose cũ 60 s. Khi có số đo ở tuần 2 chỉ cần sửa các hằng này rồi sinh lại.

## Đưa số đo thật vào pipeline

Trạm ghi mỗi mẫu `/t` nhận được vào nhật ký `runs/station/*.jsonl` (dòng `"kind": "tel"`, tối đa 1 mẫu/giây/node, không hiện trên dashboard) và mỗi quyết định kèm `obs` và `questions`.

- `scripts/fit_sensor_model.py --logs ... --csv ... [--write]` ước lượng `SensorModel` bằng thống kê bền (trung vị, MAD, phân vị) và in bảng "giả định hiện tại / đo được (n)". `--write` ghi `runs/decider/sensor_fit.yaml`, chỉ chứa đại lượng đã ước lượng được; sinh lại kịch bản bằng `make_scenarios.py --sensor-model ../runs/decider/sensor_fit.yaml` (không truyền thì dùng mặc định như cũ).
- Từ log trạm chỉ lấy được phần NỀN (mẫu dưới ngưỡng, cách mọi cảnh báo >= 60 s): `*_amb` (p5-p95) và `*_noise` (MAD của sai phân). Các đại lượng còn lại cần CSV nhóm ghi tay lúc đo: `t_s,node,temp,gas,hum,label,dist_m` với label thuộc `baseline|fire|steam|spike|lamp`, `hum` và `dist_m` có thể trống. Mỗi đợt có nhãn nên mở đầu bằng 3 mẫu còn ở mức nền, vì mức nền được lấy riêng cho từng đợt.
- Ước lượng được từ CSV: `fire_gain`, `quiet_gain`, `steam_gain`, `spike_gain` (đỉnh / (ngưỡng - nền), cần >= 3 đợt), `steam_hum_rise`, `fire_hum_shift`, `onset`, `tau` (từ đợt `fire`), `decay_m` (cần `dist_m` ở >= 2 node trong >= 2 lần cháy). Không ước lượng: `temp_thr`, `gas_thr` (nhóm đặt, ở site.yaml) và nhãn `lamp` (kịch bản đèn dùng chung `fire_gain`). Chỗ nào thiếu thì in "không đủ dữ liệu" và giữ mặc định. Các hằng ngoài `SensorModel` (xác suất vòi hỏng, giới hạn pan/tilt, hình xung 3 đến 5 mẫu) vẫn là giả định.
- `scripts/export_episodes.py --logs runs/station/*.jsonl [--annotate nhan.csv]` xuất mỗi lượt đã hoàn tất thành bản ghi cùng định dạng kịch bản (thêm `source: "real"`). Nhãn chỉ có khi biết: từ chú thích `run_id,real_fire,fire_out,target,nozzle,action` (xem docstring script), còn lại vắng mặt. `eval_rules.py --data real.jsonl` chấm luật trên đó (nhãn vắng thì bỏ qua câu hỏi tương ứng). Log ghi trước khi có `obs` trong sự kiện `decision` thì không xuất được.

## Sinh lại

```
cd pi
uv run python scripts/cache_yolo_obs.py --max-train 1200 --out ../runs/decider/yolo_obs_fire-n_cap.jsonl
uv run python scripts/cache_yolo_obs.py --weights ../models/mix26-neg-20261003.pt --splits test \
    --out ../runs/decider/yolo_obs_mix26-neg_test.jsonl
uv run python scripts/make_scenarios.py                    # runs/decider/scenarios/*.jsonl
uv run python scripts/eval_rules.py                        # runs/decider/rules_eval.json
# nozzle có thêm ứng viên `both`, nên phải sinh lại bộ kịch bản và train lại Jev (xem kaggle/README.md)
```

Thêm `--device cpu` cho cache_yolo_obs.py khi GPU đang bận.

## Baseline luật

Cài đặt trong `pi/src/nt532/decider/rules.py`: nhận bia nếu conf khung gần nhất ≥ `detect_conf` (0,35) và cách sensor báo động không quá 0,35 m theo X, lấy conf cao nhất; vòi là vòi khỏe (heartbeat ≤ 1,5 s và pose ≤ 60 s), với tới bia và gần bia nhất theo `dist3` (không còn ưu tiên node báo động), không bao giờ trả `both`; `real_fire` = có bia khớp. `eval_rules.py` và `eval_jev.py` in thêm `chọn một vòi khi cần hai`, `chọn hai vòi khi một là đủ` (trong các ca phun đúng bia) và độ chính xác `nozzle` tách theo cỡ lửa.

Sau phun: vòi lỗi thì gọi người, lệch ≤ 3 cm thì `done` (kế hoạch không có bước phun thêm), lệch lớn thì ngắm lại tối đa 3 lần rồi gọi người.

Bảng dưới là số đo CŨ (vòi theo node báo động, chỉ `s1`/`s2`, chưa có cỡ lửa); chạy lại `eval_rules.py` sau khi sinh lại bộ kịch bản.

Kết quả trên 2000 kịch bản mỗi tập (`runs/decider/rules_eval.json`):

| | test | test_shift |
| --- | --- | --- |
| acc `real_fire` | 88,5% | 82,8% |
| acc `action` | 86,0% | 79,3% |
| acc `target` | 86,4% | 79,0% |
| acc `nozzle` | 97,9% | 95,5% |
| acc `after_verify` | 62,8% | 60,8% |
| Phun khi không có lửa | 142/1096 | 203/1284 |
| Lửa thật cần phun mà không phun | 58/771 | 58/510 |
| Phun nhầm bia | 4/771 | 18/510 |
| Phun đúng bia, nhầm vòi | 0/771 | 1/510 |

`after_verify` thấp chủ yếu vì luật không có bước `spray more` và coi trúng là xong dù lửa còn cháy. Phần lớn lỗi nghiêm trọng là phun khi không có lửa (khoảng 13 đến 16% ca không lửa), do vật gây nhiễu có conf trên 0,35 nằm gần sensor báo sai.

## Ghi chú về cache YOLO

Cache train/val/test của `fire-n.pt` chạy trên CPU với `--max-train 1200`: mỗi nguồn lấy cách đều tối đa 1200 ảnh ở train và 400 ở val/test (5002 ảnh; negatives lấy hết). Cache của `mix26-neg-20261003.pt` chỉ có split test (2264 ảnh) vì chỉ test_shift dùng. File: `yolo_obs_fire-n_cap.jsonl` và `yolo_obs_mix26-neg_test.jsonl`. Chạy GPU lúc GPU đang bận bị chậm tới mức không dùng được. Lưu ý `fire-n.pt` có thể đã thấy ảnh train lúc huấn luyện, nên conf trên train sạch hơn trên val/test.

## Mô hình Jev (backbone văn bản EmbeddingGemma 2 + LoRA)

Cài đặt trong `pi/src/nt532/decider/jev.py`, theo thiết kế NanoJev. Với mỗi ứng viên, ghép một chuỗi `"<state>\nquestion: <câu hỏi>\nanswer: <ứng viên>"` (câu hỏi boolean không có dòng answer) và thêm tiền tố tác vụ `task: classification | query: ` theo model card. Một backbone dùng chung mã hóa mọi chuỗi: phần văn bản của `google/embeddinggemma-2` (khoảng 271M tham số, bỏ bộ mã hóa ảnh và âm thanh), tinh chỉnh bằng LoRA trên các phép chiếu attention và MLP (r=16). Vector của mỗi chuỗi là trung bình các token rồi chuẩn hóa L2, đúng như lớp Pooling của model. Đầu Choice là một lớp self-attention trên tập k ứng viên (không mã hóa vị trí nên đổi thứ tự không đổi kết quả) rồi linear ra một logit mỗi ứng viên, softmax. Đầu Boolean là MLP rồi sigmoid. Mỗi câu hỏi có một nhiệt độ T, khớp trên val sau khi train. Mô hình chỉ đọc `state` và `questions`, không đọc `truth` hay `obs`.

Chuỗi dài trung vị khoảng 300 token, tối đa khoảng 480, nên độ dài tối đa mặc định là 512; nếu vượt thì cắt bên trái phần state và đếm lại số lần cắt. Một lô gộp mọi chuỗi của các bản ghi rồi chạy backbone một lần; gradient checkpointing luôn bật (tắt bằng `--no-checkpointing`), trên GPU dùng bf16 autocast.

Huấn luyện (loss là CE cho choice cộng BCE cho boolean, cộng theo bản ghi), đánh giá và so với luật:

```
cd pi
uv run python scripts/train_jev.py --device cuda --name jev1 --epochs 1 --batch 4 --accum 4
uv run python scripts/train_jev.py --device cuda --name heads_only --freeze-backbone   # đối chứng không LoRA
uv run python scripts/eval_jev.py --name jev1 --device cuda                            # runs/decider/jev/jev1/eval.json
```

`train_jev.py` đánh giá val mỗi `--val-every` bước tối ưu (mỗi bước gồm `--batch` x `--accum` bản ghi), giữ bản có val loss thấp nhất, rồi khớp T từng câu hỏi trên toàn bộ val. `eval_jev.py` chấm trên test và test_shift bằng chính logic của `eval_rules.py`: độ chính xác từng câu hỏi, ECE trước và sau nhiệt độ, các lỗi mức hệ thống, chính sách lai (dùng đáp án mô hình khi độ tin cậy ≥ τ với τ = 0,6 / 0,8 / 0,9, ngược lại dùng luật, kèm tỉ lệ phủ) và độ trễ `decide()` mỗi bản ghi. Cả hai script có `--max-records` để chạy thử nhanh.
