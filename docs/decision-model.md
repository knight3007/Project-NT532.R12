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
- Vị trí lửa thật và các bia khác, trên sàn (mặt bàn) và trên bảng đứng, tọa độ 3D; vị trí đo được có nhiễu 1,5 cm.
- Sức khỏe từng vòi (heartbeat, pose) và khả năng với tới bia theo hình học thật của sa bàn (vị trí node lấy từ `data/sim/commissioning.yaml`). Hai node ở hai góc trước bàn, tâm quay pan–tilt tại (0; 0; 0,33) m và (1,2; 0; 0,33) m, pan = 0 hướng về điểm (0,6; 0,8) (xoay −36,87° và +36,87° so với +Y), nên bia ở xa node cùng phía hơn so với đặt sát bảng và vùng cả hai vòi cùng với tới là phần giữa sa bàn.
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

Số đọc cảm biến chưa đo thực. Toàn bộ phân phối nằm trong `SensorModel` ở `pi/src/nt532/decider/scenario.py` (nền, nhiễu, ngưỡng 45 °C và 600, độ suy giảm theo khoảng cách, hình dạng xung, độ ẩm khi có hơi nước). Cũng là giả định: `large_gain` (1,5) và `large_decay` (2,0) của lửa lớn, hệ số hộp `BOX_SCALE`, tỉ lệ lửa lớn, xác suất sau phun của lửa lớn (`fit_sensor_model.py` đọc/ghi hai trường mới như mọi trường float khác, nhưng chưa ước lượng chúng), giới hạn pan ±70° (đủ rộng để còn vùng cả hai vòi cùng với tới, tức còn nhãn `both`; với bố cục trụ sát bảng cũ của `jev1`, node ở X 0,30 và 0,90 cách bảng 0,24 m, ±50° không có vùng nào như thế) và tilt -35 đến 45° (site.yaml còn để null), xác suất vòi hỏng và lỗi, phân phối độ lệch điểm trúng, ngưỡng pose cũ 60 s. Khi có số đo ở tuần 2 chỉ cần sửa các hằng này rồi sinh lại.

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
# bố cục hai vòi ở hai góc trước: thêm --nodes "s1=0,0,0.33@0.6,0.8;s2=1.2,0,0.33@-0.6,0.8" (xem kaggle/README.md)
```

Thêm `--device cpu` cho cache_yolo_obs.py khi GPU đang bận.

## Baseline luật

Cài đặt trong `pi/src/nt532/decider/rules.py`: nhận bia nếu conf khung gần nhất ≥ `detect_conf` (0,35) và cách sensor báo động không quá 0,35 m theo X, lấy conf cao nhất; vòi là vòi khỏe (heartbeat ≤ 1,5 s và pose ≤ 60 s), với tới bia và gần bia nhất theo `dist3` (không còn ưu tiên node báo động), không bao giờ trả `both`; `real_fire` = có bia khớp. `eval_rules.py` và `eval_jev.py` in thêm `chọn một vòi khi cần hai`, `chọn hai vòi khi một là đủ` (trong các ca phun đúng bia) và độ chính xác `nozzle` tách theo cỡ lửa.

Sau phun: vòi lỗi thì gọi người, lệch ≤ 3 cm thì `done` (kế hoạch không có bước phun thêm), lệch lớn thì ngắm lại tối đa 3 lần rồi gọi người.

Kết quả trên bộ kịch bản sinh lại ngày 13/10 (có `both` và cỡ lửa), chấm trên toàn bộ test (2839 bản ghi) và test_shift (2590 bản ghi), trong `runs/decider/rules_eval.json`:

| | test | test_shift |
| --- | --- | --- |
| acc `real_fire` | 86,9% | 82,0% |
| acc `action` | 86,2% | 80,5% |
| acc `target` | 85,0% | 79,1% |
| acc `nozzle` | 79,4% | 69,5% |
| acc `nozzle`, lửa nhỏ | 94,9% (559/589) | 90,3% (288/319) |
| acc `nozzle`, lửa lớn | 42,8% (107/250) | 45,0% (122/271) |
| acc `after_verify` | 65,6% | 60,5% |
| Phun khi không có lửa | 161/1083 | 228/1291 |
| Lửa thật cần phun mà không phun | 62/839 | 74/590 |
| Phun nhầm bia | 10/839 | 21/590 |
| Phun đúng bia, nhầm vòi | 131/839 | 118/590 |
| Chọn một vòi khi cần hai | 110 | 98 |
| Chọn hai vòi khi một là đủ | 0 | 0 |

`nozzle` tụt từ khoảng 98% (số đo trước khi có `both`) xuống 79,4% chủ yếu vì lửa lớn cần hai vòi mà luật không bao giờ trả `both`: trên test có 110 ca "chọn một vòi khi cần hai" (lửa lớn đúng 107/250), còn lửa nhỏ vẫn đạt 94,9%. `after_verify` thấp chủ yếu vì luật không có bước `spray more` và coi trúng là xong dù lửa còn cháy. Lỗi nghiêm trọng nhất vẫn là phun khi không có lửa (khoảng 15 đến 18% ca không lửa), do vật gây nhiễu có conf trên 0,35 nằm gần sensor báo sai.

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

### Kết quả trên bộ kịch bản mới (có `both`)

Số đo của `jev1` và `heads_only` lần train ngày 08/10 thuộc bộ kịch bản cũ (nozzle chỉ có `s1`/`s2`, chưa có cỡ lửa), không còn so sánh được với luật mới, nên không đưa vào đây.

Mọi số đo trong mục này, kể cả `jev1` nhưng trừ `jev2`, là số đo trên bố cục cũ: hai trụ sát bảng (cách bảng khoảng 0,25 m, X 0,30 và 0,90) và bia chỉ nằm trên bảng đứng. Bố cục hiện hành (hai vòi ở hai góc trước bàn, bia trên cả sàn lẫn bảng) cho ra phân phối khoảng cách, vùng với tới và nhãn `nozzle` khác, nên các số này không áp dụng nguyên cho nó. Lượt train trên bố cục hiện hành là `jev2` (xem dưới).

**heads_only** (đóng băng backbone, chỉ train các đầu; Kaggle T4, fp32, batch 4 x accum 4, 20.000 bản ghi, val loss tốt nhất 1,642, không chuỗi nào bị cắt ở max_len 512), so với luật:

| | test: luật | test: heads_only | test_shift: luật | test_shift: heads_only |
| --- | --- | --- | --- | --- |
| acc `real_fire` | 86,9% | 81,4% | 82,0% | 78,1% |
| acc `action` | 86,2% | 68,4% | 80,5% | 65,6% |
| acc `target` | 85,0% | 54,7% | 79,1% | 51,4% |
| acc `nozzle` | 79,4% | 41,6% | 69,5% | 41,5% |
| acc `nozzle`, lửa nhỏ | 94,9% | 50,8% | 90,3% | 49,8% |
| acc `nozzle`, lửa lớn | 42,8% | 20,0% | 45,0% | 31,7% |
| acc `after_verify` | 65,6% | 67,6% | 60,5% | 63,7% |
| Phun khi không có lửa | 161/1083 | 327/1083 | 228/1291 | 493/1291 |
| Lửa thật cần phun mà không phun | 62/839 | 228/839 | 74/590 | 76/590 |
| Phun nhầm bia | 10/839 | 356/839 | 21/590 | 372/590 |
| Phun đúng bia, nhầm vòi | 131/839 | 152/839 | 118/590 | 91/590 |

Chỉ riêng `after_verify` heads_only hơn luật (khoảng 2 đến 3 điểm); mọi câu hỏi khác kém rõ rệt. Số ca phun đúng bia và đúng vòi giảm từ 636 xuống 103 trên test và từ 377 xuống 51 trên test_shift. Hiệu chỉnh nhiệt độ giảm ECE (ví dụ `nozzle` test từ 0,175 xuống 0,101) nhưng độ chính xác vẫn thấp. Với backbone cố định thì các đầu không đủ để phân biệt bia và vòi từ chuỗi văn bản.

Chính sách lai (dùng mô hình khi độ tin cậy ≥ τ, ngược lại dùng luật), tỉ lệ phủ là phần câu hỏi do mô hình trả lời:

| τ | phủ test | phủ test_shift | acc `real_fire` test | acc `target` test | acc `after_verify` test |
| --- | --- | --- | --- | --- | --- |
| 0,6 | 44,7% | 47,4% | 84,3% | 80,6% | 69,4% |
| 0,8 | 26,4% | 26,2% | 88,1% | 85,0% | 69,6% |
| 0,9 | 20,4% | 20,3% | 87,6% | 85,0% | 69,4% |

Ở τ = 0,8 và 0,9 hybrid gần như trùng luật ở `action`, `target`, `nozzle`; `real_fire` hơn luật khoảng 1 đến 1,6 điểm và `after_verify` hơn khoảng 4 điểm (test) và 6 điểm (test_shift). τ = 0,6 làm `target` tụt xuống 80,6% trên test nên không dùng.

Độ trễ `decide()` trên T4 (100 bản ghi): test trung vị 271 ms, p95 598 ms; test_shift trung vị 272 ms, p95 697 ms.

**jev1** (LoRA + các đầu): train 13/10 trên bộ kịch bản sinh ngày 13/10 (có `both` và cỡ lửa) ở bố cục cũ (trụ sát bảng, bia chỉ trên bảng đứng). `config.json` không ghi đường dẫn bộ kịch bản; suy ra từ cột luật trong `eval.log`, trùng từng số với bảng luật ở trên (2839 và 2590 bản ghi). 1250 bước dự kiến, batch 4 x accum 4, 20.000 bản ghi, fp32, val 500 bản ghi, T khớp trên 2857. Train chết vì hết VRAM (OOM) ở cuối vòng sau khoảng 1200 bước; dùng bản tốt nhất đã lưu dở ở bước 600 (val loss 0,755, thấp nhất trong 12 lần đo; lần đo cuối 1,089) rồi khớp lại T bằng `--finish`. Vì vậy không có số liệu thời gian và cắt chuỗi của lượt này. Val loss dao động mạnh giữa các lần đo (0,755 đến 1,359) nên bước 600 chỉ là điểm tốt nhất trên 500 bản ghi val.

| | test: luật | test: jev1 | test_shift: luật | test_shift: jev1 |
| --- | --- | --- | --- | --- |
| acc `real_fire` | 86,9% | 90,7% | 82,0% | 90,8% |
| acc `action` | 86,2% | 88,9% | 80,5% | 87,3% |
| acc `target` | 85,0% | 87,2% | 79,1% | 87,8% |
| acc `nozzle` | 79,4% | 68,5% | 69,5% | 60,7% |
| acc `nozzle`, lửa nhỏ | 94,9% | 72,2% (425/589) | 90,3% | 69,6% (222/319) |
| acc `nozzle`, lửa lớn | 42,8% | 60,0% (150/250) | 45,0% | 50,2% (136/271) |
| acc `after_verify` | 65,6% | 90,8% | 60,5% | 87,8% |
| Phun khi không có lửa | 161/1083 | 36/1083 | 228/1291 | 50/1291 |
| Lửa thật cần phun mà không phun | 62/839 | 107/839 | 74/590 | 85/590 |
| Phun nhầm bia | 10/839 | 39/839 | 21/590 | 54/590 |
| Phun đúng bia, nhầm vòi | 131/839 | 232/839 | 118/590 | 176/590 |
| Chọn một vòi khi cần hai | 110 | 50 | 98 | 44 |
| Chọn hai vòi khi một là đủ | 0 | 101 | 0 | 45 |
| Phun đúng bia đúng vòi | 636/839 | 461/839 | 377/590 | 275/590 |

jev1 hơn luật ở `real_fire`, `action`, `target` và nhất là `after_verify` (khoảng 25 điểm trên test); phun khi không có lửa giảm từ 161 xuống 36. `nozzle` kém luật: lửa nhỏ tụt từ 94,9% xuống 72,2% vì model hay chọn `both` khi một vòi là đủ (101 ca), lửa lớn hơn luật (60,0% so với 42,8%). Phun nhầm bia và bỏ sót đều nhiều hơn luật. Số ca đúng bia đúng vòi thấp hơn luật (461 so với 636), nên model đứng riêng chưa thay được luật ở khâu chọn vòi.

Chính sách lai (mô hình khi độ tin cậy ≥ τ, ngược lại luật):

| split | τ | phủ | phun nhầm (không lửa) | bỏ sót | đúng bia+vòi | acc `real_fire` | acc `action` | acc `target` | acc `nozzle` | acc `after_verify` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| test | 0,6 | 92,1% | 23/1083 | 94/839 | 518/839 | 91,8% | 90,5% | 89,4% | 72,9% | 89,9% |
| test | 0,8 | 80,8% | 19/1083 | 66/839 | 574/839 | 93,2% | 92,3% | 92,0% | 75,2% | 85,2% |
| test | 0,9 | 63,6% | 36/1083 | 63/839 | 611/839 | 93,3% | 92,2% | 91,9% | 78,4% | 78,4% |
| test_shift | 0,6 | 92,3% | 41/1291 | 75/590 | 289/590 | 91,5% | 88,2% | 89,5% | 59,2% | 85,9% |
| test_shift | 0,8 | 80,7% | 44/1291 | 58/590 | 312/590 | 91,6% | 89,1% | 90,4% | 61,4% | 80,7% |
| test_shift | 0,9 | 65,0% | 70/1291 | 73/590 | 355/590 | 91,3% | 88,2% | 90,0% | 67,1% | 74,7% |

τ = 0,8 là điểm cân bằng của jev1: phun nhầm 19 và bỏ sót 66 trên test, `real_fire`, `action`, `target` hơn luật 6 đến 7 điểm. Tăng τ đưa `nozzle` và số ca đúng bia+vòi về gần luật (611 so với 636 ở τ = 0,9) nhưng `after_verify` giảm dần vì phần luật được dùng nhiều hơn.

ECE trước -> sau nhiệt độ (T: `real_fire` 1,42; `action` 1,09; `target` 1,26; `nozzle` 1,11; `after_verify` 1,20): trên test `real_fire` 0,052 -> 0,026, `action` 0,034 -> 0,038, `target` 0,048 -> 0,020, `nozzle` 0,062 -> 0,051, `after_verify` 0,040 -> 0,018. Trên test_shift `nozzle` còn lệch nhiều (0,155 -> 0,143), các câu khác dưới 0,06 sau hiệu chỉnh.

Độ trễ `decide()` trên GPU máy ảo (100 bản ghi, không phải T4): test trung vị 184 ms, p95 367 ms; test_shift trung vị 185 ms, p95 432 ms.

**jev2** (LoRA + các đầu): train trên bộ kịch bản sàn/tường 3D với hai vòi ở hai góc trước bàn (`runs/decider/scenarios_3d_corners`, chép làm `runs/decider/scenarios` trên máy train, xem [kaggle/README.md](../kaggle/README.md)). Đây là bố cục hiện hành. 1250 bước, 16 bản ghi mỗi bước (`config.json` ghi batch 2 x accum 8), 20.000 bản ghi, fp32, val 500 bản ghi, T khớp trên 2892, val loss tốt nhất 0,846 (bước 1200 và 1250 như nhau). Luật chấm trên cùng bộ kịch bản (2837 bản ghi test, 2611 test_shift), nên cột luật khác bảng ở đầu mục (bố cục cũ).

| | test: luật | test: jev2 | test_shift: luật | test_shift: jev2 |
| --- | --- | --- | --- | --- |
| acc `real_fire` | 86,4% | 93,5% | 82,3% | 93,3% |
| acc `action` | 86,0% | 92,5% | 81,8% | 90,6% |
| acc `target` | 83,5% | 92,1% | 78,4% | 90,2% |
| acc `nozzle` | 74,4% | 68,7% | 67,9% | 59,7% |
| acc `nozzle`, lửa nhỏ | 93,5% (561/600) | 77,2% (463/600) | 87,9% (291/331) | 66,8% (221/331) |
| acc `nozzle`, lửa lớn | 26,2% (62/237) | 47,3% (112/237) | 44,3% (124/280) | 51,4% (144/280) |
| acc `after_verify` | 65,9% | 78,3% | 55,0% | 80,2% |
| Phun khi không có lửa | 171/1111 | 35/1111 | 228/1301 | 52/1301 |
| Lửa thật cần phun mà không phun | 71/837 | 64/837 | 74/611 | 48/611 |
| Phun nhầm bia | 12/837 | 27/837 | 34/611 | 55/611 |
| Phun đúng bia, nhầm vòi | 164/837 | 237/837 | 136/611 | 195/611 |
| Chọn một vòi khi cần hai | 144 | 104 | 106 | 74 |
| Chọn hai vòi khi một là đủ | 0 | 61 | 0 | 24 |
| Phun đúng bia đúng vòi | 590/837 | 509/837 | 367/611 | 313/611 |

jev2 hơn luật rõ ở `real_fire`, `action`, `target` và `after_verify`. Phun khi không có lửa giảm từ 171 xuống 35 trên test (228 xuống 52 trên test_shift), bỏ sót cũng giảm (71 xuống 64; 74 xuống 48). `nozzle` kém luật: lửa nhỏ model đúng 77,2% so với 93,5% của luật, có 61 ca chọn hai vòi khi một là đủ; lửa lớn model hơn luật (47,3% so với 26,2%) vì luật không bao giờ trả `both`. Số ca đúng bia đúng vòi vì thế thấp hơn luật (509 so với 590 trên test, 313 so với 367 trên test_shift). Phun nhầm bia cũng nhiều hơn luật (27 so với 12 trên test, 55 so với 34 trên test_shift). So với jev1, jev2 cao hơn ở `real_fire`, `action`, `target` nhưng `after_verify` thấp hơn (78,3% so với 90,8% trên test); hai lượt khác bộ kịch bản nên không so ngang được.

Chính sách lai (mô hình khi độ tin cậy ≥ τ, ngược lại luật):

| split | τ | phủ | phun nhầm (không lửa) | bỏ sót | đúng bia+vòi | acc `real_fire` | acc `action` | acc `target` | acc `nozzle` | acc `after_verify` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| test | 0,6 | 88,0% | 37/1111 | 64/837 | 530/837 | 94,4% | 92,3% | 92,9% | 70,1% | 73,0% |
| test | 0,8 | 76,4% | 47/1111 | 57/837 | 563/837 | 93,8% | 92,4% | 92,3% | 73,1% | 73,8% |
| test | 0,9 | 68,2% | 65/1111 | 63/837 | 581/837 | 93,0% | 91,4% | 91,0% | 74,6% | 73,5% |
| test_shift | 0,6 | 90,2% | 50/1301 | 49/611 | 306/611 | 93,3% | 90,8% | 90,9% | 58,8% | 69,1% |
| test_shift | 0,8 | 78,3% | 68/1301 | 40/611 | 334/611 | 92,8% | 90,3% | 90,6% | 62,8% | 68,1% |
| test_shift | 0,9 | 68,3% | 88/1301 | 41/611 | 368/611 | 91,9% | 89,5% | 88,9% | 67,6% | 68,2% |

Ở jev2, tăng τ làm phun nhầm tăng (35 khi model đứng riêng, 65 ở τ = 0,9 trên test) vì luật được dùng nhiều hơn, còn số ca đúng bia+vòi tiến gần luật (581 so với 590) mà không vượt. `after_verify` của hybrid (khoảng 73% trên test) thấp hơn model đứng riêng (78,3%).

ECE trước -> sau nhiệt độ (T: `real_fire` 1,36; `action` 1,22; `target` 1,30; `nozzle` 1,08; `after_verify` 1,15):

| | test | test_shift |
| --- | --- | --- |
| `real_fire` | 0,029 -> 0,018 | 0,029 -> 0,012 |
| `action` | 0,029 -> 0,014 | 0,046 -> 0,027 |
| `target` | 0,019 -> 0,017 | 0,021 -> 0,014 |
| `nozzle` | 0,054 -> 0,048 | 0,140 -> 0,111 |
| `after_verify` | 0,033 -> 0,030 | 0,062 -> 0,068 |

`nozzle` trên test_shift còn lệch nhiều sau hiệu chỉnh (0,111); `after_verify` trên test_shift tăng nhẹ (0,062 -> 0,068).

Độ trễ `decide()` trên GPU máy ảo (100 bản ghi, không phải T4): test trung vị 185 ms, p95 383 ms; test_shift trung vị 185 ms, p95 367 ms.

Chính sách lai theo câu hỏi: `nozzle` luôn theo luật, các câu khác theo model. Chấm lại trên RTX 4050 (bf16) với `eval_jev.py --name jev2 --data-dir ../runs/decider/scenarios_3d_corners --rule-questions nozzle`, kết quả ở `runs/decider/jev/jev2/eval_perq.json` và `eval_perq.log`. Cột luật khớp tuyệt đối bảng trên. Cột model thuần lệch dưới 1 điểm so với lần chấm trên máy ảo (ví dụ đúng bia đúng vòi 514 so với 509), nên trong bảng này mọi cột lấy từ cùng lần chấm lại. "Theo câu + τ 0,6" nghĩa là các câu không phải `nozzle` chỉ dùng model khi độ tin cậy ≥ 0,6, ngược lại dùng luật.

| | test: luật | test: model | test: lai τ 0,9 | test: theo câu | test: theo câu + τ 0,6 | test_shift: luật | test_shift: model | test_shift: theo câu |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| acc `real_fire` | 86,4% | 93,5% | 93,0% | 93,5% | 94,3% | 82,3% | 93,2% | 93,2% |
| acc `action` | 86,0% | 92,5% | 91,5% | 92,5% | 92,4% | 81,8% | 90,6% | 90,6% |
| acc `target` | 83,5% | 92,3% | 91,1% | 92,3% | 93,0% | 78,4% | 90,1% | 90,1% |
| acc `nozzle` | 74,4% | 69,1% | 74,4% | 74,4% | 74,4% | 67,9% | 59,7% | 67,9% |
| acc `after_verify` | 65,9% | 79,1% | 73,5% | 79,1% | 72,8% | 55,0% | 79,5% | 79,5% |
| Phun khi không có lửa | 171/1111 | 35/1111 | 64/1111 | 35/1111 | 37/1111 | 228/1301 | 52/1301 | 52/1301 |
| Lửa thật cần phun mà không phun | 71/837 | 64/837 | 62/837 | 64/837 | 63/837 | 74/611 | 47/611 | 47/611 |
| Phun nhầm bia | 12 | 25 | 18 | 25 | 19 | 34 | 60 | 60 |
| Phun đúng bia, nhầm vòi | 164 | 234 | 176 | 193 | 188 | 136 | 195 | 161 |
| Phun đúng bia đúng vòi | 590/837 | 514/837 | 581/837 | 555/837 | 567/837 | 367/611 | 309/611 | 343/611 |

Lai theo câu hỏi giữ được lợi thế lớn nhất của model: phun khi không có lửa vẫn là 35/1111 (luật 171) và 52/1301 (luật 228), còn `after_verify` vẫn 79%. `nozzle` trở về đúng mức luật, không còn ca chọn hai vòi khi một là đủ. Số ca đúng bia đúng vòi tăng từ 514 lên 555 trên test, nhưng vẫn thấp hơn luật (590), do phun nhầm bia nhiều hơn (25 so với 12) và nhầm vòi nhiều hơn (193 so với 164) dù `nozzle` đã theo luật. Phần nhầm vòi tăng thêm chưa được phân tích. Thêm τ cho các câu còn lại kéo `after_verify` về mức luật (khoảng 73%), còn τ cao (0,9) đưa phun khi không có lửa lên lại 64/1111 và 90/1301, nên không dùng.

Ghi chú sự cố train của jev2 (nguồn: `runs/decider/jev/jev2_train.log`, `jev2/train.log`):

- Máy ảo bị segfault (mã thoát 139); script bọc ngoài tự chạy lại với `--resume` từ bản lưu dở (mỗi 50 bước). Log còn lại chỉ ghi một lần mã 139 (lúc 19:32 UTC ngày 14/10); các lần trước đó không còn trong log.
- Lần chạy sau đó loss thành NaN ở bước 600 (bước 595 loss 1,075, bước 600 NaN, val loss NaN). Không tái hiện được trên từng bản ghi, dữ liệu sạch; nghi gradient tràn số hoặc lỗi GPU tạm thời lan qua `clip_grad_norm_`.
- Đã dừng tay và chạy lại từ bản lưu bước 400 (val loss 1,120). Đã thêm chốt chặn NaN vào `train_jev.py`: bỏ bước nhỏ có loss không hữu hạn, bỏ `opt.step()` khi grad norm không hữu hạn, thoát trước khi ghi checkpoint nếu trọng số có NaN. Lần chạy lại đi hết 1250 bước không NaN (11.956 s, 1,14 bản ghi/s, VRAM đỉnh 4,4 GB; không chuỗi nào bị cắt trên 129.689 chuỗi, token trung vị 307, p95 441, tối đa 491).
- Moment AdamW bị khởi tạo lại: log ghi hai lần cảnh báo (chạy tiếp từ bản lưu bước 300, rồi bước 400); RNG không khôi phục. Vì vậy jev2 không hoàn toàn so sánh ngang được với một lượt train liền mạch.
