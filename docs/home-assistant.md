# Home Assistant qua MQTT

Trạm đẩy trạng thái lên Home Assistant (HA) bằng MQTT Discovery: HA tự tạo thực thể, lưu lịch sử và thống kê, gửi thông báo khi báo động. Mã ở `pi/src/nt532/integrations/mqtt.py`, tệp mẫu ở `pi/deploy/homeassistant/`.

```
Pi 5 (trạm + mosquitto)  --MQTT-->  máy chạy Home Assistant (LAN)
```

Pi 5 đã bận YOLO và OTBR, nên chạy HA trên máy khác (mini PC, laptop, Pi khác). Broker mosquitto thì nhẹ, đặt trên Pi cũng được; hoặc dùng add-on Mosquitto của HA OS và để trạm nối tới đó.

## 1. Broker

Cách A, mosquitto trên Pi:

```bash
bash pi/deploy/setup_pi.sh --mosquitto             # cài mosquitto và gói Python paho-mqtt (extra mqtt)
sudo mosquitto_passwd -c /etc/mosquitto/passwd nt532
sudo mosquitto_passwd /etc/mosquitto/passwd homeassistant
sudo cp pi/deploy/homeassistant/mosquitto.conf /etc/mosquitto/conf.d/nt532.conf
sudo systemctl restart mosquitto
```

Cách B, add-on Mosquitto của HA: tạo user `nt532` trong HA, trạm nối tới địa chỉ máy HA, cổng 1883.

## 2. Bật cầu nối ở trạm

```bash
cd pi && uv sync --extra mqtt
export NT532_MQTT_PASSWORD='...'
uv run --no-sync python scripts/run_station.py --source sim --mqtt --mqtt-host 192.168.1.10 --mqtt-user nt532
```

Mặc định đọc mục `mqtt:` cuối `config/site.yaml` (`host`, `port`, `username`, `station_id`, `discovery_prefix`); cờ `--mqtt-*` ghi đè. Mật khẩu chỉ lấy từ biến môi trường `password_env`, không để trong tệp cấu hình. Thiếu gói `paho-mqtt` thì `--mqtt` báo lỗi rõ ràng, không dùng `--mqtt` thì không cần gói. Tự nối lại khi mất broker; khi trạm tắt hoặc mất kết nối bất ngờ (LWT), `nt532/<station_id>/status` thành `offline` và mọi thực thể thành `unavailable`.

## 3. Thêm vào HA

Cài đặt > Thiết bị và dịch vụ > Thêm tích hợp > MQTT, nhập broker và user `homeassistant`. Thiết bị "Trạm NT532" xuất hiện cùng các thực thể bên dưới. Cần HA 2025.10 trở lên (discovery dùng `default_entity_id` để entity id ổn định dạng `sensor.nt532_s1_temp`).

| Thực thể | Loại | Ghi chú |
|---|---|---|
| `sensor.nt532_{s1,s2}_temp` | sensor | °C, `measurement` |
| `sensor.nt532_{s1,s2}_gas` | sensor | giá trị thô, `measurement` |
| `sensor.nt532_{s1,s2}_hum` | sensor | %, nếu node có cảm biến |
| `binary_sensor.nt532_{s1,s2}_alarm` | smoke | bật khi node báo động, tắt khi "hết báo động" |
| `binary_sensor.nt532_{s1,s2}_hb_ok` | connectivity | heartbeat còn trong 3 chu kỳ |
| `binary_sensor.nt532_{s1,s2}_pump` | running | bật khi pha FIRE và vòi đó nằm trong danh sách vòi đang phun (`overlay.active`); lượt hai vòi thì cả hai cùng bật |
| `binary_sensor.nt532_{s1,s2}_laser` | binary | bật khi pha CORRECT và vòi đó đang được ngắm (`overlay.active`); lượt hai vòi thì lần lượt từng vòi, không bao giờ cả hai |
| `sensor.nt532_phase`, `_target`, `_decider` | sensor | pha (enum), bia đang xử lý, bộ quyết định |
| `sensor.nt532_runs`, `_extinguished`, `_alarm_only`, `_ignored`, `_human`, `_faults`, `_sprays` | sensor | `total_increasing`; đếm từ lúc trạm khởi động, HA xử lý việc về 0 |
| `sensor.nt532_latency` | sensor | ms, độ trễ của quyết định gần nhất |
| `sensor.nt532_outcome` | sensor (enum) | kết quả lượt gần nhất |
| `sensor.nt532_last_alert` | sensor | dòng cảnh báo gần nhất |
| `button.nt532_emergency_stop` | button | dừng khẩn cấp |
| `select.nt532_decider_select` | select | `rules`, `hybrid`, `jev` (và `remote` nếu có `--decider-url`) |

Bơm và laser của node thật không đọc từ phần cứng mà suy ra từ pha orchestrator cùng danh sách vòi đang làm việc `overlay.active` (một vòi bị bỏ giữa lượt hai vòi thì tắt theo); khi mất heartbeat node đó, hai thực thể ở trạng thái "không rõ". Số đo gửi tối đa 1 lần/giây mỗi node, trạng thái đổi cờ (báo động, kết nối, bơm, laser) gửi ngay; trạng thái trạm được retain.

Đổi `station_id` thì tiền tố entity id đổi theo (`sensor.<station_id>_s1_temp`) và `dashboard.yaml` phải sửa cho khớp.

## 4. Dashboard và thông báo

- `pi/deploy/homeassistant/dashboard.yaml`: tạo dashboard mới, vào trình chỉnh sửa cấu hình thô và dán nội dung. Có thẻ cho hai node, biểu đồ lịch sử nhiệt độ và gas 24 giờ, thống kê số lượt và kết quả theo ngày, pha, bộ quyết định, kết quả gần nhất và nút dừng khẩn cấp có hộp xác nhận.
- `pi/deploy/homeassistant/automations.yaml`: ví dụ báo động, kết quả `human`/`fault`/`alarm_only` và mất kết nối trạm. Đổi `notify.notify` thành dịch vụ thông báo của bạn.

## 5. An toàn

- Chỉ hai thứ ghi được từ HA: nút dừng khẩn cấp (gọi `orch.emergency_stop("Home Assistant")`, tắt bơm và laser) và chọn bộ quyết định. Cầu nối KHÔNG có và không được thêm lệnh ngắm, bắn, bơm hay laser: mọi lệnh tác động phần cứng chỉ đi qua orchestrator và các kiểm tra an toàn của nó.
- Payload lạ trên các chủ đề lệnh bị bỏ qua; lệnh retained còn trên broker không được thi hành lại khi kết nối.
- Đặt mật khẩu (`allow_anonymous false`), giữ broker trong LAN, không mở cổng 1883 ra Internet. Cần truy cập từ xa thì dùng VPN hoặc nhà cung cấp truy cập của HA.
- Ai có quyền ghi vào broker cũng bấm được nút dừng và đổi bộ quyết định; hạn chế user nào được publish `nt532/+/cmd/#` bằng ACL của mosquitto nếu cần.

## 6. Kiểm thử

`pi/tests/test_mqtt.py`: phần đơn vị dùng client giả (không cần broker). Phần tích hợp chạy với broker thật khi đặt `NT532_MQTT_BROKER=host:port` (CI dựng mosquitto trong container):

```bash
NT532_MQTT_BROKER=localhost:1883 uv run --no-sync pytest -q tests/test_mqtt.py
```
