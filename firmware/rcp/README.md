# RCP và OTBR trên Pi 5

Pi 5 chạy `ot-br-posix` (otbr-agent) làm border router; một ESP32-H2 DevKit chạy firmware RCP (radio co-processor) là "chiếc radio 802.15.4" cắm vào Pi bằng USB. Hai node H2 Super Mini gắn vào mạng Thread do Pi tạo. Chạy lần lượt, mỗi bước có lệnh kiểm tra. Người làm: Hậu.

Nguồn đã đối chiếu: [openthread.io build-native](https://openthread.io/guides/border-router/build-native), [form-network](https://openthread.io/guides/border-router/form-network), [Docker](https://openthread.io/guides/border-router/build), README của ví dụ `ot_rcp` trong ESP-IDF. Chỗ nào chưa xác minh được ghi `CHƯA KIỂM`.

## 1. Nạp firmware RCP vào H2 DevKit

Làm trên máy có ESP-IDF 5.4 trở lên (cùng bản với node, xem [../sensor-h2/README.md](../sensor-h2/README.md)). Có thể làm trên laptop rồi cắm DevKit sang Pi sau.

```sh
cd $IDF_PATH/examples/openthread/ot_rcp
idf.py set-target esp32h2
idf.py -p <CỔNG> build flash
```

`<CỔNG>` là `/dev/ttyUSB0` hay `/dev/ttyACM0` (Linux) hoặc `COMx` (Windows) của DevKit lúc cắm vào máy nạp.

Kênh nối với Pi (chọn đúng một, và phải khớp với radio URL ở mục 3):

- **UART mặc định:** ví dụ `ot_rcp` dùng UART0 của H2, **460800 baud** (đã xác minh trong `main/esp_ot_config.h`, sửa được ở file đó). DevKit có cổng USB ghi UART đi qua chip cầu USB-UART; cắm cổng này vào Pi thì Pi thấy `/dev/ttyUSB0` (hoặc `ttyACM0`, tùy chip cầu). Radio URL: `spinel+hdlc+uart:///dev/ttyUSB0?uart-baudrate=460800`.
- **USB-Serial-JTAG của H2:** ví dụ `ot_rcp` có nhánh `HOST_CONNECTION_MODE_RCP_USB` khi cấu hình không chọn UART hay SPI. Chọn nhánh nào bằng `idf.py menuconfig` (mục OpenThread, giao diện RCP); tên mục chính xác `CHƯA KIỂM`. Dùng cổng USB còn lại của DevKit; Pi thấy `/dev/ttyACM0`.
- Cách chắc nhất: nạp, cắm vào Pi, xem `dmesg` (mục 2), rồi thử radio URL tương ứng. Nếu `otbr-agent` báo không nói chuyện được với RCP thì đổi sang kênh kia hoặc đúng baud.

Kiểm tra: nạp xong không có lỗi `idf.py`. (`ot_rcp` không in log; log mặc định tắt để nhỏ gọn, nên không thấy gì trên monitor là bình thường.)

## 2. Tìm cổng nối tiếp của RCP trên Pi

```sh
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null     # liệt kê trước khi cắm
# cắm DevKit vào Pi (cổng USB 3 màu xanh hoặc cổng nào cũng được)
sudo dmesg | tail -20                        # dòng "cdc_acm ... ttyACM0" hoặc "ch341/cp210x ... ttyUSB0"
ls -l /dev/serial/by-id/                     # tên cố định, không đổi khi cắm lại
```

- Dùng đường dẫn `/dev/serial/by-id/usb-...` trong cấu hình nếu có, để khỏi bị đổi `ttyACM0` thành `ttyACM1` khi cắm lại (openthread.io cảnh báo đúng chuyện này).
- Pi 5 không cấp đủ dòng cho DevKit + vài thiết bị USB khác thì dùng hub có nguồn.
- User chạy `ot-ctl` không cần vào nhóm `dialout`, vì `otbr-agent` chạy bằng root; nhưng muốn dùng `esptool` hay `screen` trên Pi thì `sudo usermod -aG dialout $USER`.

## 3. Cài ot-br-posix (native)

Raspberry Pi OS Bookworm 64-bit. Mạng của Pi nối ra ngoài bằng `wlan0` hay `eth0` thì đặt `INFRA_IF_NAME` tương ứng. Với sa bàn nên có Pi nối LAN/Wi-Fi cùng điện thoại camera; chọn giao diện đang dùng cho mạng đó.

```sh
sudo apt install -y git
git clone --recursive --depth=1 https://github.com/openthread/ot-br-posix
cd ot-br-posix
INFRA_IF_NAME=wlan0 ./script/setup        # hoặc INFRA_IF_NAME=eth0
```

- Trang openthread.io viết `./script/setup` làm luôn phần cài; trong repo còn có `./script/bootstrap` (cài gói phụ thuộc). Bản hiện tại gộp bootstrap vào setup hay không `CHƯA KIỂM`: nếu `setup` báo thiếu gói thì chạy `./script/bootstrap` trước rồi `setup` lại.
- Mất vài chục phút. Pi 5 phải nối mạng suốt lúc cài.
- `setup` đặt IP forwarding và `accept_ra` cho giao diện hạ tầng, và tạo giao diện Thread `wpan0`.

Đặt radio URL:

```sh
sudoedit /etc/default/otbr-agent
```

Sửa tham số radio trong `OTBR_AGENT_OPTS` thành URL của RCP, ví dụ:

```
OTBR_AGENT_OPTS="-I wpan0 -B wlan0 spinel+hdlc+uart:///dev/serial/by-id/usb-XXXX?uart-baudrate=460800 trel://wlan0"
```

(Giữ nguyên các tham số khác trong file; chỉ đổi phần `spinel+hdlc+uart://...`. Dạng chính xác của dòng này `CHƯA KIỂM` trên bản bạn cài. `-B` là `INFRA_IF_NAME`.) Rồi:

```sh
sudo systemctl restart otbr-agent
sudo systemctl status otbr-agent          # active (running)
sudo ot-ctl state                         # "disabled" là bình thường trước khi tạo mạng
```

Pass: `otbr-agent` active và `ot-ctl state` trả lời. Lỗi `InvalidArguments` thì RCP không ở đường dẫn đã khai: xem lại mục 2, rút cắm lại.

**Đường lui (kế hoạch mục 9):** kẹt cài native quá 2 ngày thì dùng Docker theo [openthread.io/guides/border-router/build](https://openthread.io/guides/border-router/build): chạy `setup-host` (đặt `INFRA_IF_NAME` nếu không phải wlan0), `docker pull openthread/border-router:latest`, file `otbr-env.list` gồm `OT_RCP_DEVICE=spinel+hdlc+uart:///dev/ttyACM0?uart-baudrate=...`, `OT_INFRA_IF=wlan0`, `OT_THREAD_IF=wpan0`, rồi `docker run --network=host --cap-add=NET_ADMIN --device=/dev/ttyACM0 --device=/dev/net/tun ... openthread/border-router`. Khi đó mọi lệnh `sudo ot-ctl` đổi thành `docker exec -it otbr ot-ctl`.

## 4. Tạo mạng Thread

Cách A, để OTBR sinh ngẫu nhiên rồi chép sang firmware:

```sh
sudo ot-ctl dataset init new
sudo ot-ctl dataset                       # xem lại, chưa lưu
sudo ot-ctl dataset commit active
sudo ot-ctl ifconfig up
sudo ot-ctl thread start
```

Cách B, đặt theo mặc định của `Kconfig.projbuild` để khỏi sửa firmware (khóa `00112233...` chỉ hợp cho sa bàn kín; đổi nếu đặt ở nơi đông người). Đặt xong mới `commit active`; tên lệnh `dataset ...` `CHƯA KIỂM` trên bản bạn cài (xem `ot-ctl help`):

```sh
sudo ot-ctl dataset init new
sudo ot-ctl dataset networkname NT532
sudo ot-ctl dataset channel 15
sudo ot-ctl dataset panid 0x1234
sudo ot-ctl dataset extpanid 1111111122222222
sudo ot-ctl dataset networkkey 00112233445566778899aabbccddeeff
sudo ot-ctl dataset meshlocalprefix fd00:db8:a0:0::/64
sudo ot-ctl dataset commit active
sudo ot-ctl ifconfig up
sudo ot-ctl thread start
```

Chờ khoảng 10–20 giây:

```sh
sudo ot-ctl state                         # leader (Pi là thiết bị đầu tiên nên thành leader)
```

Pass: `leader`. Kẹt ở `detached`/`disabled` thì xem `journalctl -u otbr-agent -e`.

### Dataset sang menuconfig của node

```sh
sudo ot-ctl dataset active                # từng trường
sudo ot-ctl dataset active -x             # TLV hex, để lưu lại / đối chiếu
```

| Dòng trong `dataset active` | Mục `idf.py menuconfig` (NT532 node > Thread) | Ghi chú |
| --- | --- | --- |
| `Network Name` | `CONFIG_NT532_THREAD_NETWORK_NAME` | giữ đúng chữ hoa thường |
| `Channel` | `CONFIG_NT532_THREAD_CHANNEL` | 11–26 |
| `PAN ID` | `CONFIG_NT532_THREAD_PANID` | kiểu hex, nhập `0x....` |
| `Ext PAN ID` | `CONFIG_NT532_THREAD_EXTPANID` | 16 số hex, không dấu `:` |
| `Network Key` | `CONFIG_NT532_THREAD_NETWORK_KEY` | 32 số hex |
| `Mesh Local Prefix` | `CONFIG_NT532_THREAD_MESH_LOCAL_PREFIX` | dạng `fd..:..:..:0::/64`, có `/64` |
| `PSKc`, `Active Timestamp`, `Channel Mask`, `Security Policy` | không cần | node không làm commissioner |

Sai một trường là node không bao giờ gắn vào mạng. Lưu cả đầu ra `dataset active -x` vào nhóm chat để ai nạp node cũng dùng chung.

## 5. Địa chỉ của Pi trên mạng Thread (cho `CONFIG_NT532_PI_ADDR`)

Node gửi `/t`, `/a`, `/status` tới CoAP server của trạm (aiocoap, cổng 5683, bind `::`) trên Pi.

```sh
sudo ot-ctl ipaddr                        # mọi địa chỉ Thread của Pi
sudo ot-ctl ipaddr mleid                  # riêng mesh-local EID (lệnh con "mleid": CHƯA KIỂM)
ip -6 addr show wpan0                     # địa chỉ như Linux thấy
```

- Chọn địa chỉ **mesh-local EID**: bắt đầu bằng mesh-local prefix (`fd00:db8:a0:0:...` nếu dùng cách B) và **không** có đuôi `:0:ff:fe00:xxxx` (đó là RLOC, đổi khi mạng đổi cấu trúc). Link-local `fe80::` không dùng được (không qua hop).
- Điền vào `CONFIG_NT532_PI_ADDR` trong menuconfig, ví dụ `fd00:db8:a0:0:1234:5678:9abc:def0`.
- Địa chỉ OMR (prefix do border router quảng bá, thường `fd..` khác prefix mesh-local, thấy trong `ipaddr` sau khi `ot-ctl br omrprefix` có giá trị) cũng tới được nhưng node phải có route; dùng mesh-local EID cho chắc.
- Mesh-local EID của Pi có thể đổi nếu xóa dữ liệu OTBR (thư mục lưu trạng thái Thread của otbr-agent, `/var/lib/thread` `CHƯA KIỂM`). Đổi thì phải nạp lại `NT532_PI_ADDR` cho cả hai node. Đặt cố định mesh-local prefix (cách B) giúp prefix không đổi nhưng phần đuôi vẫn có thể đổi.
- Kiểm tra Pi nghe được cổng 5683 khi trạm chạy: `ss -ulnp | grep 5683`. Chưa chạy trạm thì dùng server in bản tin ở mục "Thử tay từ Pi" của sensor-h2/README.md.

## 6. Node gắn vào mạng và địa chỉ node cho `config/site.yaml`

Sau khi nạp node (xem [../sensor-h2/README.md](../sensor-h2/README.md)):

```sh
sudo ot-ctl state                          # vẫn leader
sudo ot-ctl child table                    # node gắn vai trò child hiện ở đây (có RLOC16, Ext Addr)
sudo ot-ctl router table                   # khi node lên router
sudo ot-ctl neighbor table
sudo ot-ctl childip                        # địa chỉ IPv6 con đã đăng ký (với FTD CHƯA KIỂM có hiện không)
sudo ot-ctl ping ff03::1                   # ping multicast mesh: mỗi node đáp, thấy địa chỉ nguồn (CHƯA KIỂM)
```

Cách chắc nhất lấy địa chỉ node: **monitor của chính node** (`idf.py monitor`) in `địa chỉ ... [mesh-local EID  <- dùng cho site.yaml]` lúc khởi động. Chép vào `config/site.yaml`:

```yaml
network:
  nodes:
    s1: "fd00:db8:a0:0:...."
    s2: "fd00:db8:a0:0:...."
```

Kiểm tra từ Pi:

```sh
ping -c3 <địa chỉ node>                    # hoặc: sudo ot-ctl ping <địa chỉ node>
```

Pass: ping đáp, `ot-ctl state` là `leader` trên Pi, node ở `child` hoặc `router`. Tiếp theo: thử CoAP tay (runbook, bước 4).

## 7. Khởi động lại và chạy bền

- `otbr-agent` tự chạy cùng máy (systemd). Sau khi khởi động lại Pi, `ot-ctl state` phải về `leader` (dataset đã lưu bằng `commit active`). Kiểm tra một lần bằng `sudo reboot`.
- Rút DevKit khi Pi đang chạy làm `otbr-agent` chết; cắm lại rồi `sudo systemctl restart otbr-agent`.
- Lấy dataset khi đã quên: `sudo ot-ctl dataset active -x`.
