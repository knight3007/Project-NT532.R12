# Firmware node H2 (cảm biến và vòi)

Mỗi ESP32-H2 Super Mini vừa đọc cảm biến vừa điều khiển một cụm pan–tilt, bơm và laser, nói CoAP qua Thread với Pi (OTBR). Thiết kế: [docs/thay-doi-hai-voi.md](../../docs/thay-doi-hai-voi.md), hợp đồng CoAP: mục 4 của [kế hoạch](../../docs/ke-hoach-trien-khai-NT532.md).

**CHƯA BUILD ĐƯỢC TRÊN MÁY NHÓM:** phần ghép ESP-IDF viết khi chưa có toolchain, mọi chỗ chưa chắc đều đánh dấu `CHƯA KIỂM` trong code (danh sách ở cuối). Lần build đầu sẽ có lỗi vặt; sửa theo checklist.

## Node làm gì

- Mỗi giây đọc MQ-2 (ADC) và SHT31 (I2C). Vượt ngưỡng 3 mẫu liên tiếp thì còi bật, gửi `/a` (`alert`) cho Pi và `/alarm` multicast `ff03::1`; xuống dưới ngưỡng 5 mẫu thì tắt còi, gửi `/a` (`clear`). Mỗi `NT532_TELEMETRY_S` giây (mặc định 1) gửi `/t`: bộ quyết định dùng 5 mẫu cuối mỗi node, giống bộ kịch bản lấy mẫu 1 Hz.
- Nhận `/aim`, `/fire`, `/stop`, `/hb` từ Pi, điều khiển servo, bơm, laser, gửi `/status` về Pi. `/hb` luôn được trả `2.04` (NON) vì Pi đo tuổi heartbeat bằng câu trả lời đó.
- `GET /info` trả JSON tự mô tả (`{"n","fw","pan":[lo,hi],"tilt":[lo,hi],"hb","fire","srp"}`, khoảng 95 byte nên dùng bộ đệm `PROTO_INFO_MAX` 160 riêng). Pi gọi một lần khi khởi động để so giới hạn góc và nhịp heartbeat với site.yaml. `fw` lấy từ `esp_app_get_description()->version` (`esp_app_desc.h`, component `esp_app_format`); đặt `PROJECT_VER` trong CMakeLists gốc hoặc dùng git describe mặc định của IDF.
- Nhận `/alarm` từ node kia thì nháy còi `NT532_REMOTE_ALARM_S` giây, không cần Pi.
- Số đọc gas `g` là **giá trị ADC thô 12 bit (0..4095) trên chân sau cầu phân áp**, không đổi ra ppm. Ngưỡng 600 trong `config/site.yaml` cùng đơn vị này; chỉnh lại sau khi đo thực.

## Cấu trúc

| Tệp | Vai trò |
| --- | --- |
| `main/core/` | C thuần (`actuator`, `alarm`, `proto`), không phụ thuộc ESP-IDF, test trên máy bằng `make -C firmware/sensor-h2/test` |
| `main/app_main.c` | Thứ tự khởi động: GPIO an toàn, NVS, cấu hình, HAL, netif, các task |
| `main/node_cfg.[ch]` | Cấu hình: mặc định Kconfig, ghi đè từ NVS |
| `main/hal_esp.[ch]` | Bơm, laser, còi, servo (LEDC 50 Hz, 14 bit), MQ-2 (ADC oneshot), SHT31 (I2C master mới) |
| `main/act_task.[ch]` | Task chấp hành: sở hữu `act_t`, hàng đợi lệnh, `act_tick` mỗi 10 ms |
| `main/sensors.[ch]` | Task cảm biến, báo động, telemetry; task còi/LED |
| `main/net_ot.[ch]` | OpenThread FTD, dataset từ Kconfig, netif lwIP, log vai trò và địa chỉ |
| `main/coap_node.[ch]` | Task CoAP (libcoap): server, hàng đợi gửi ra, gửi `/status`, `/a`, `/t`, `/alarm` |

Luồng dữ liệu: handler CoAP chỉ parse rồi đẩy lệnh vào hàng đợi của task chấp hành; task đó gọi `act_*` và đẩy `/status` vào hàng đợi ra; chỉ task CoAP được gọi libcoap (libcoap không an toàn đa luồng).

## Build và nạp

Cần ESP-IDF 5.4 trở lên (đã viết theo API 5.x; `idf_component.yml` đòi >= 5.2).

```sh
cd firmware/sensor-h2
idf.py set-target esp32h2
idf.py menuconfig          # mục "NT532 node": mã node, địa chỉ Pi, dataset Thread, ngưỡng, chân, servo
idf.py build flash monitor
```

`sdkconfig.defaults` đã bật OpenThread FTD, IPv6 lwIP, libcoap (không DTLS, không TCP). `sdkconfig` và `build/` không đưa lên git.

## Nối vào mạng Thread của Pi

Dataset trong `menuconfig` > NT532 node > Thread phải khớp OTBR. Đọc trên Pi:

```sh
sudo ot-ctl dataset active        # từng trường: Network Name, Channel, PAN ID, Ext PAN ID, Mesh Local Prefix, Network Key
sudo ot-ctl dataset active -x     # hoặc dạng TLV hex để tra cứu
```

Điền: `Network Name`, `Channel`, `PAN ID` (hex), `Ext PAN ID` (16 số hex), `Network Key` (32 số hex), `Mesh Local Prefix` (dạng `fd00:db8:a0:0::/64`). Node tự đặt dataset mỗi lần khởi động rồi gắn mạng (vai trò child, sau đó có thể lên router). PSKc không cần.

Địa chỉ Pi cho `NT532_PI_ADDR`: địa chỉ mesh-local của Pi (`sudo ot-ctl ipaddr` lấy dòng mesh-local EID, hoặc địa chỉ OMR trên cổng ethernet/wlan của Pi nếu node có route tới đó). Dùng mesh-local EID của Pi là chắc nhất.

## Cấu hình trong NVS (mỗi board một giá trị)

Một firmware dùng cho cả hai board; `node_id`, ngưỡng và hiệu chuẩn servo ghi đè bằng NVS namespace `nt532`, cách đơn giản nhất là sinh file ảnh NVS rồi nạp vào phân vùng `nvs` (offset 0x9000, 0x6000 byte). Tạo `s2.csv`:

```csv
key,type,encoding,value
nt532,namespace,,
node_id,data,string,s2
temp_c,data,string,47.5
gas,data,string,650
pan_off,data,string,-3.5
tilt_off,data,string,2.0
pan_inv,data,i32,1
pmin_us,data,i32,600
pmax_us,data,i32,2400
```

Các key được đọc: `node_id`, `temp_c`, `gas`, `telem_s`, `pmin_us`, `pmax_us`, `pan_off`, `tilt_off`, `pan_inv`, `tilt_inv`, `pan_lo`, `pan_hi`, `tilt_lo`, `tilt_hi`. Số thực viết dạng chuỗi. Key thiếu thì dùng Kconfig.

```sh
python $IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py generate s2.csv s2.bin 0x6000
python -m esptool --chip esp32h2 -p /dev/ttyACM0 write_flash 0x9000 s2.bin
```

Nạp ảnh NVS xóa luôn dữ liệu OpenThread trong cùng phân vùng, không sao vì dataset được đặt lại mỗi lần khởi động. Nạp lại ảnh sau mỗi `idf.py erase-flash`.

## Đăng ký SRP và địa chỉ node

Sau khi gắn mạng, node đăng ký với SRP server của border router (`NT532_SRP`, mặc định bật): host `<id>`, dịch vụ `_nt532._udp` instance `<id>`, cổng `NT532_COAP_PORT`, TXT `n=<id>`. Monitor in `SRP: tìm thấy server ...` rồi `SRP: OK, host ... Registered, dịch vụ Registered`. Trên Pi:

```sh
ot-ctl srp server service        # thấy s1._nt532._udp.default.service.arpa. với addresses
uv run python scripts/find_nodes.py
```

`build_real` tự dùng kết quả này (`network.discover: srp`); node nào không thấy thì rơi về `network.nodes` trong site.yaml. Cách nhập tay dưới đây là dự phòng.

### Nhập tay (dự phòng)

Khi chạy, monitor in các dòng `địa chỉ ... [mesh-local EID  <- dùng cho site.yaml]`. Chép địa chỉ đó vào:

```yaml
network:
  nodes:
    s1: "fd11:22:33:0:aaaa:bbbb:cccc:dddd"
    s2: "fd11:22:33:0:..."
```

Mesh-local EID có thể đổi sau khi xóa dữ liệu Thread; khi đó cập nhật lại (SRP ở trên thay bước này khi chạy được).

## Thử tay từ Pi

Cài `libcoap-bin` trên Pi (`coap-client`). Thay `ADDR` bằng địa chỉ node. Ghi nhớ id tăng dần giữa các lệnh.

```sh
ADDR="fd11:22:33:0:aaaa:bbbb:cccc:dddd"
coap-client -m post -e '{"id":1,"pan":10,"tilt":0,"ttl":1500}' "coap://[$ADDR]/aim"
coap-client -m post -N -e '' "coap://[$ADDR]/hb"                     # NON, phải thấy 2.04 trả về
coap-client -m get "coap://[$ADDR]/info"                              # JSON tự mô tả: n, fw, pan, tilt, hb, fire, srp
coap-client -m post -e '{"id":1,"dev":"laser","ms":1000}' "coap://[$ADDR]/fire"   # chỉ nhận sau khi aim cùng id "reached"
coap-client -m post -e '{"id":2}' "coap://[$ADDR]/stop"
```

`/fire` bị từ chối nếu chưa có `/hb` nào hoặc `/aim` cùng id chưa `reached`; muốn thấy bơm/laser chạy, gửi `/hb` mỗi 500 ms trong lúc thử (ví dụ `while true; do coap-client -m post -N -e '' "coap://[$ADDR]/hb"; sleep 0.5; done`).

Xem `/status`, `/t`, `/a` tới Pi: chạy trạm thật với `NT532_PI_ADDR` đúng (`cd pi && uv run python scripts/run_station.py --decider rules`, cần `network.nodes` đã điền) và xem dashboard cổng 8080, hoặc dựng nhanh một server in bản tin:

```sh
cd pi && uv run python -I - <<'PY'
import asyncio, aiocoap, aiocoap.resource as r
class P(r.Resource):
    async def render_post(self, req):
        print(req.remote.hostinfo, req.opt.uri_path, req.payload.decode()); return aiocoap.Message(code=aiocoap.CHANGED)
async def main():
    s = r.Site()
    for p in ("t","a","status"): s.add_resource([p], P())
    await aiocoap.Context.create_server_context(s, bind=("::", 5683)); await asyncio.get_running_loop().create_future()
asyncio.run(main())
PY
```

## An toàn (hành vi mặc định)

- Khởi động: bơm GPIO12, laser GPIO14 và còi GPIO13 thành output mức thấp ngay đầu `app_main`, trước NVS và mạng (board còn có điện trở kéo xuống). Servo về giữa theo hiệu chuẩn.
- Chưa nhận `/hb` nào thì không bật được bơm hay laser. Mất `/hb` quá `NT532_HB_TIMEOUT_MS` (1500 ms, tức 3 nhịp) thì tắt cả hai và báo `fault`/`hb`. Mất liên kết Thread được log nhưng không có xử lý riêng: watchdog này là chốt chặn.
- `/stop` tắt chân bơm và laser ngay trong handler, rồi mới vào hàng đợi (chèn lên đầu). Node nhớ id `/stop` lớn nhất; `/aim`, `/fire` có id nhỏ hơn bị `rejected`/`stopped`.
- `ttl` của `/aim` tính từ lúc task chấp hành nhận lệnh; lệnh trùng id không xử lý lại. `/fire` bị chặn trên bởi `NT532_MAX_FIRE_MS`; góc bị chặn theo giới hạn pan/tilt cả ở `actuator.c` lẫn lúc xuất xung servo.
- Hàng đợi lệnh đầy thì handler trả `5.03`. Payload sai trả `4.00`.

## Checklist lần build đầu và các mục CHƯA KIỂM

Mức biên dịch (đã xác nhận bằng CI ngày 2026-10-09, firmware build với ESP-IDF `release-v5.4`, ứng dụng 0x101ed0 byte, còn trống 33% trong phân vùng 0x180000):

1. [x] `idf.py build` không lỗi: tên component trong `main/CMakeLists.txt` (`esp_driver_*`, `espressif__coap`), include `coap3/coap.h`.
2. [x] `espressif/coap` kéo về được; `CONFIG_COAP_MBEDTLS_PSK=n` và `CONFIG_COAP_TCP_SUPPORT=n` (DTLS và TCP tắt) biên dịch được.
3. [x] Trường `addr.sin6` của `coap_address_t`, macro `COAP_RESPONSE_CLASS`, `COAP_INVALID_MID` (`coap_node.c`).
13a. [x] Kích thước ứng dụng nằm trong phân vùng 1,5 MB.

Mã SRP thêm sau lần build đó (`net_ot.c`, `CONFIG_OPENTHREAD_SRP_CLIENT`) CHƯA qua CI; xem mục SRP dưới.

Mức chạy thật (vẫn CHƯA KIỂM):

4. Monitor thấy `vai trò Thread: ... -> child/router` và các dòng địa chỉ. Nếu không gắn mạng: sai dataset, sai kênh, hoặc `otDatasetSetActive` đòi thêm trường (channel mask, security policy).
5. `coap_join_mcast_group_intf(ff03::1)` trả 0 và node thật sự nhận `/alarm` từ node kia qua netif OpenThread (lwIP MLD). Hop limit multicast đặt 8 bằng `coap_mcast_set_hops`.
6. Hai node nhận `/alarm` của nhau, và node nhận có trả `2.04` cho multicast hay không (libcoap có thể tự nén phản hồi multicast; vô hại).
7. `adc_oneshot_io_to_channel(1)` ra ADC1 (kênh của GPIO1 trên H2); giá trị `g` hợp lý khi thổi khói vào MQ-2, không vượt 4095 do cầu phân áp.
8. `ADC_ATTEN_DB_12` đo được tới khoảng 3,1 V; kiểm điện áp thực sau cầu phân áp.
9. `i2c_master_bus_reset` có gỡ được bus SHT31 bị treo (`hal_esp.c`); dây lỏng thì chỉ log cảnh báo và giữ số cũ.
10. LEDC 50 Hz với 14 bit xin được bộ chia hợp lệ (nếu `ledc_timer_config` lỗi, hạ xuống 13 bit); đo xung bằng máy hiện sóng hoặc quan sát servo: `NT532_SERVO_PULSE_MIN_US/MAX_US`, offset, chiều.
11. GPIO13 và 14 trên H2 cũng là chân xtal 32 kHz; chỉ có tác dụng nếu bật nguồn xung đó, mặc định không. Đối chiếu sơ đồ chân thực của board.
12. Libcoap gửi lại gói CON bằng tham số mặc định (ACK_TIMEOUT 2 s, 4 lần); xem `/a` và `/status` có tới Pi đủ nhanh trên multi-hop, chỉnh `coap_session_set_ack_timeout` nếu cần.
13. Stack các task (coap 8 KB, ot_main 10 KB, act 4 KB, sensors 4 KB) đủ, xem `uxTaskGetStackHighWaterMark` khi chạy lâu.
14. `crc8` SHT31 (đa thức 0x31, khởi tạo 0xFF) khớp với sample trong datasheet (0xBE 0xEF -> 0x92).
15. Kiểm thử an toàn trước khi nối bơm: rút Thread/ngắt Pi lúc đang `/fire`, bơm phải tắt trong khoảng 1,5 s; `/stop` rồi `/aim` id nhỏ hơn phải bị `rejected`.
16. SRP (CHƯA KIỂM): CI build qua với `net_ot.c` mới; `srp server state` trên OTBR là `running`; node in `SRP: ... Registered`; `ot-ctl srp server service` thấy `<id>._nt532._udp` với đúng địa chỉ; đổi OMR prefix hoặc khởi động lại otbr-agent thì node tự đăng ký lại; ba service/host của hai node không đụng tên.
