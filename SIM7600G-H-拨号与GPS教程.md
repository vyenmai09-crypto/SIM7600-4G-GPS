# SIM7600G-H 4G 模块 拨号上网 与 GPS 定位 完整教程

> 适用平台：Raspberry Pi CM5 / Pi 5 + SIMCom SIM7600G-H
> 系统：Raspberry Pi OS (Debian 12 bookworm)
> 本文所有命令与输出均在实机验证通过。

---

## 目录

1. [模块识别与端口说明](#1-模块识别与端口说明)
2. [准备 AT 命令工具](#2-准备-at-命令工具)
3. [检查 SIM 卡与网络注册](#3-检查-sim-卡与网络注册)
4. [切换 USB 模式产生 usb0](#4-切换-usb-模式产生-usb0)
5. [配置 usb0 上网](#5-配置-usb0-上网)
6. [网络测试](#6-网络测试)
7. [GPS / GNSS 定位](#7-gps--gnss-定位)
8. [开机自启配置](#8-开机自启配置)
9. [故障排查](#9-故障排查)
10. [AT 命令速查表](#10-at-命令速查表)

---

## 1. 模块识别与端口说明

### 1.1 确认模块已识别

```bash
lsusb | grep -i 1e0e
```

预期输出：

```
Bus 004 Device 002: ID 1e0e:9001 Qualcomm / Option SimTech, Incorporated
```

* `1e0e` 是 SIMCom 的厂商 ID
* `9001` 是当前的**产品 ID (PID)**，决定了模块以哪种 USB 模式工作

### 1.2 查看当前 PID

```bash
for d in /sys/bus/usb/devices/*/; do
  [ -f "$d/idVendor" ] || continue
  [ "$(cat $d/idVendor)" = "1e0e" ] || continue
  echo "路径=$d  PID=$(cat $d/idProduct)"
done
```

### 1.3 PID 与工作模式对照

| PID | 网络接口 | 内核驱动 | 说明 |
|---|---|---|---|
| `9001` | `wwan0` | `qmi_wwan` | QMI 模式（默认），需 ModemManager/qmicli 拨号 |
| `9011` | `usb0` | `rndis_host` | **RNDIS 模式，模块自动拨号，usb0 直接 DHCP 取 IP（最省事）** |

> 本教程使用 **9011 (RNDIS)**，因为它不需要手动拨号，模块内部完成 PDP 激活并对外做 NAT。

### 1.4 串口节点说明

```bash
ls -l /dev/ttyUSB*
```

SIM7600G-H 会创建 5 个串口，用途如下：

| 节点 | 用途 | 备注 |
|---|---|---|
| `/dev/ttyUSB0` | DIAG 诊断口 | 通常被占用，无法直接打开 |
| `/dev/ttyUSB1` | **NMEA (GPS 数据输出)** | GPS 定位数据从这里流出 |
| `/dev/ttyUSB2` | **AT 命令口（主用）** | 本教程主要使用 |
| `/dev/ttyUSB3` | AT 命令口（备用 / PPP） | 也能响应 AT |
| `/dev/ttyUSB4` | 音频 / 其它 | — |

> **注意**：切换 PID 后节点编号可能变化，请用第 2.2 节的方法重新确认。

---

## 2. 准备 AT 命令工具

### 2.1 安装依赖

```bash
sudo apt update
sudo apt install -y python3-serial
```

### 2.2 创建 AT 命令脚本

```bash
cat > ~/at.py <<'PYEOF'
import sys, time, serial

port = sys.argv[1]
cmds = sys.argv[2:]
try:
    s = serial.Serial(port, 115200, timeout=0.4)
except Exception as e:
    print("打开失败 %s: %s" % (port, e))
    sys.exit(1)
s.reset_input_buffer()
for c in cmds:
    s.write((c + "\r\n").encode())
    time.sleep(0.6)
    buf = b""
    deadline = time.time() + 4
    while time.time() < deadline:
        n = s.in_waiting
        if n:
            buf += s.read(n)
            deadline = time.time() + 0.8
        else:
            time.sleep(0.1)
    txt = buf.decode(errors="replace").strip()
    print("--- %s ---" % c)
    print(txt if txt else "(无响应)")
s.close()
PYEOF
chmod +x ~/at.py
```

用法：

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT" "AT+CSQ"
```

### 2.3 停止 ModemManager（避免端口争用）

ModemManager 会占用 AT 口，手动发命令前先停掉：

```bash
sudo systemctl stop ModemManager
```

> 调试结束后如需恢复：`sudo systemctl start ModemManager`
> RNDIS 模式下模块自动拨号，**不依赖 ModemManager**，可以让它一直停着。

### 2.4 确认哪个口能响应 AT

```bash
for p in /dev/ttyUSB1 /dev/ttyUSB2 /dev/ttyUSB3 /dev/ttyUSB4; do
  echo "### $p -> $(sudo timeout 8 python3 ~/at.py $p AT 2>&1 | tail -n 1)"
done
```

预期：`ttyUSB2` 和 `ttyUSB3` 返回 `OK`。

---

## 3. 检查 SIM 卡与网络注册

**在拨号之前必须确认这几项，否则后面一定失败。**

```bash
sudo python3 ~/at.py /dev/ttyUSB2 \
  "ATI" "AT+CPIN?" "AT+CSQ" "AT+COPS?" "AT+CREG?" "AT+CGREG?" "AT+CPSI?"
```

### 逐项解读

| 命令 | 正常输出 | 含义 |
|---|---|---|
| `ATI` | `Model: SIMCOM_SIM7600G-H` | 型号与固件版本 |
| `AT+CPIN?` | `+CPIN: READY` | **SIM 卡就绪**；若为 `SIM PIN` 说明需要解 PIN 码 |
| `AT+CSQ` | `+CSQ: 24,99` | 信号强度，见下表 |
| `AT+COPS?` | `+COPS: 0,0,"CHN-UNICOM",7` | 运营商；末位 `7` = LTE |
| `AT+CREG?` | `+CREG: 0,1` | **第二个数字 1=本地注册 / 5=漫游**，0 或 2 表示未注册 |
| `AT+CGREG?` | `+CGREG: 0,1` | 数据业务已注册 |
| `AT+CPSI?` | `+CPSI: LTE,Online,460-01,...` | 详细制式与小区信息 |

**`AT+CSQ` 信号对照：**

| 返回值 | dBm | 评价 |
|---|---|---|
| 0-9 | -113 ~ -95 | 很差 |
| 10-14 | -93 ~ -85 | 一般 |
| 15-19 | -83 ~ -75 | 良好 |
| **20-31** | **-73 ~ -51** | **优秀** |
| 99 | — | 未知/无信号 |

> `AT+CSQ` 返回 `+CSQ: 24,99` 即 RSSI 约 **-65 dBm**，信号优秀。

**若 `AT+CREG?` 返回 0 或 2（未注册）**，检查：
1. SIM 卡是否插好、是否欠费
2. 天线（主集 MAIN）是否接好
3. 等待 30-60 秒再查，刚上电需要时间搜网

---

## 4. 切换 USB 模式产生 usb0

### 4.1 查看当前模式

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CUSBPIDSWITCH?"
```

输出 `+CUSBPIDSWITCH: 9001` 表示当前是 QMI 模式（接口为 `wwan0`）。

### 4.2 切换到 RNDIS 模式（产生 usb0）

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CUSBPIDSWITCH=9011,1,1"
```

**参数说明：** `AT+CUSBPIDSWITCH=<PID>,<1>,<1>`

| 参数 | 值 | 含义 |
|---|---|---|
| 第 1 个 | `9011` | 目标 PID（RNDIS 模式） |
| 第 2 个 | `1` | 立即生效 |
| 第 3 个 | `1` | **保存到 NVRAM，掉电不丢失** |

返回 `OK` 后模块会**自动重新枚举 USB**，约需 10-20 秒。

### 4.3 验证切换结果

```bash
sleep 20
lsusb | grep 1e0e                    # PID 应变为 9011
ip -br link show usb0                # 应出现 usb0
sudo dmesg | grep -iE 'rndis|usb0' | tail -n 3
```

预期 dmesg：

```
usb 4-1: New USB device found, idVendor=1e0e, idProduct=9011
rndis_host 4-1:1.0 usb0: register 'rndis_host' at usb-xhci-hcd.1-1, RNDIS device, 9e:6b:1f:6e:60:bf
```

> **注意**：虽然很多资料称 9011 是 "ECM 模式"，但 SIM7600G-H 实际注册的是
> **RNDIS**（内核驱动 `rndis_host`）。功能等价，都会生成 `usb0`。

### 4.4 如需切回 QMI 模式

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CUSBPIDSWITCH=9001,1,1"
```

---

## 5. 配置 usb0 上网

### 5.1 RNDIS 模式会自动拨号

切换完成后模块内部自动激活 PDP 上下文并启动 DHCP 服务器，
`usb0` 通常由 NetworkManager 自动取得 IP，**无需任何拨号命令**。

```bash
ip -br addr show usb0
ip route | grep default
```

预期：

```
usb0   UNKNOWN   192.168.225.53/24   2408:8456:xxxx::/64
default via 192.168.225.1 dev usb0 proto dhcp src 192.168.225.53 metric 100
```

> `192.168.225.x` 是模块内部的 NAT 网段，`192.168.225.1` 就是模块本身。

### 5.2 若没有自动获取 IP

手动触发 DHCP：

```bash
sudo ip link set usb0 up
sudo dhclient -v usb0
```

或交给 NetworkManager：

```bash
sudo nmcli device set usb0 managed yes
sudo nmcli device connect usb0
```

### 5.3 确认运营商已分配 IP

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGDCONT?" "AT+CGACT?" "AT+CGPADDR"
```

关键看 `AT+CGACT?`（上下文激活状态）和 `AT+CGPADDR`（分配到的 IP）：

```
+CGACT: 1,1                      <- 第1个上下文已激活
+CGPADDR: 1,10.34.196.185        <- 运营商分配的内网 IP
```

### 5.4 手动设置 APN（仅在自动拨号失败时需要）

```bash
# 中国联通: 3gnet   中国移动: cmnet   中国电信: ctnet
sudo python3 ~/at.py /dev/ttyUSB2 \
  "AT+CGDCONT=1,\"IP\",\"3gnet\"" \
  "AT+CGACT=1,1" \
  "AT+CGPADDR"
```

### 5.5 设置路由优先级（重要！）

默认情况下 `usb0` 的 metric 是 100，会**抢占成为主出口**，
导致所有流量（包括系统更新）都走流量计费的蜂窝网络。

**让 WiFi/有线优先，蜂窝仅作备份：**

```bash
# 查出 usb0 对应的连接名
CON=$(nmcli -t -f NAME,DEVICE connection show --active | grep ':usb0$' | cut -d: -f1)
echo "usb0 连接名: $CON"

# 改名并调高 metric（数值越大优先级越低）
sudo nmcli connection modify "$CON" \
  ipv4.route-metric 800 ipv6.route-metric 800 connection.id "lte-usb0"
sudo nmcli connection up lte-usb0
```

验证（WiFi 的 600 应排在蜂窝的 800 前面）：

```bash
ip route | grep default
```

```
default via 192.168.88.1   dev wlan0 metric 600    <- 优先走 WiFi
default via 192.168.225.1  dev usb0  metric 800    <- 蜂窝备份
```

---

## 6. 网络测试

> 以下命令用 `--interface usb0` / `-I usb0` **强制走蜂窝**，
> 这样即使默认路由是 WiFi 也能准确测到 4G 链路。

### 6.1 连通模块本身

```bash
ping -I usb0 -c 5 192.168.225.1
```

### 6.2 连通公网（跳过 DNS）

```bash
ping -I usb0 -c 8 223.5.5.5
```

参考值：延迟 **40-60 ms**，丢包 0%。

### 6.3 DNS 与 HTTPS

```bash
curl -sS --interface usb0 --max-time 25 -o /dev/null \
  -w 'http=%{http_code} dns=%{time_namelookup}s tls=%{time_appconnect}s\n' \
  https://www.baidu.com/
```

### 6.4 查看蜂窝出口公网 IP

```bash
curl -sS --interface usb0 --max-time 25 https://ifconfig.me/ip; echo
```

### 6.5 测速（注意消耗流量）

```bash
# 只下载 8MB，避免跑掉太多流量
curl -sS --interface usb0 --max-time 120 -o /dev/null -r 0-8388607 \
  -w '4G: %{size_download} 字节 / %{time_total}s = %{speed_download} B/s\n' \
  http://archive.raspberrypi.com/debian/pool/main/l/linux/linux-image-6.12.109+rpt-rpi-2712_6.12.109-1+rpt1_arm64.deb
```

参考值：LTE Band 3 实测约 **1.2 MB/s ≈ 10 Mbit/s**（随基站负载、信号变化）。

### 6.6 查看流量统计

```bash
echo "usb0 接收: $(cat /sys/class/net/usb0/statistics/rx_bytes) 字节"
echo "usb0 发送: $(cat /sys/class/net/usb0/statistics/tx_bytes) 字节"
```

---

## 7. GPS / GNSS 定位

### 7.1 硬件前提

**GPS 需要单独的 GNSS 天线**，接在模块标注 `GNSS` / `GPS` 的天线座上
（不是 4G 的 MAIN/AUX 天线座）。室内信号弱，建议天线靠窗或放到室外。

### 7.2 查询 GPS 状态

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS?" "AT+CGNSSMODE?"
```

* `+CGPS: 0,1` → 第 1 位 0 表示**未启动**
* `+CGNSSMODE: 3,1` → 多星座模式已开启

### 7.3 启动 GPS

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS=1,1"
```

`AT+CGPS=<开关>,<模式>`，`1,1` = 开启 + 独立定位(standalone)。

确认已启动：

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS?"      # 应返回 +CGPS: 1,1
```

### 7.4 用 AGPS 加速首次定位（强烈推荐）

冷启动需要下载卫星星历，室内可能十几分钟都定不了位。
开启 gpsOneXTRA 后，模块通过 **4G 网络**下载星历，可在 **30 秒内完成定位**。

> 前提：第 5 节的 `usb0` 网络必须已经通。

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSXE=1" "AT+CGPSXD=0"
```

* `AT+CGPSXE=1` 启用 gpsOneXTRA
* `AT+CGPSXD=0` 立即下载星历文件

看到 `+CGPSXD: 2` 表示下载完成。

### 7.5 读取定位结果

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSINFO"
```

**未定位**时返回空字段：

```
+CGPSINFO: ,,,,,,,,
```

**定位成功**：

```
+CGPSINFO: 2238.580113,N,11402.601084,E,051126,053717.0,71.4,0.0,
```

字段含义：

| 位置 | 示例值 | 含义 |
|---|---|---|
| 1-2 | `2238.580113,N` | 纬度 `ddmm.mmmmmm` + 南北 |
| 3-4 | `11402.601084,E` | 经度 `dddmm.mmmmmm` + 东西 |
| 5 | `051126` | UTC 日期 DDMMYY = 2026-11-05 |
| 6 | `053717.0` | UTC 时间 HHMMSS.s |
| 7 | `71.4` | 海拔（米） |
| 8 | `0.0` | 速度（节） |
| 9 | — | 航向角 |

**坐标换算成十进制度：**

```
纬度 2238.580113 → 22 + 38.580113/60 = 22.643002° N
经度 11402.601084 → 114 + 2.601084/60 = 114.043352° E
```

一行命令完成换算：

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSINFO" | awk -F'[:,]' '/CGPSINFO/ && $2!="" {
  lat=substr($2,1,2)+substr($2,3)/60; lon=substr($4,1,3)+substr($4,4)/60;
  printf "纬度 %.6f %s  经度 %.6f %s\n", lat, $3, lon, $5
}'
```

### 7.6 获取详细信息（含卫星数与精度）

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGNSSINFO"
```

```
+CGNSSINFO: 2,06,02,04,2238.580989,N,11402.600904,E,051126,053740.0,69.8,0.0,,1.4,1.1,0.9
```

| 位置 | 示例 | 含义 |
|---|---|---|
| 1 | `2` | 定位模式：**2=二维定位，3=三维定位**，空=未定位 |
| 2 | `06` | 使用的 **GPS** 卫星数 |
| 3 | `02` | 使用的 **GLONASS** 卫星数 |
| 4 | `04` | 使用的 **北斗** 卫星数 |
| 5-8 | — | 经纬度 |
| 9-10 | — | UTC 日期时间 |
| 11 | `69.8` | 海拔 |
| 14-16 | `1.4,1.1,0.9` | **PDOP / HDOP / VDOP 精度因子** |

> **DOP 值越小越准**：< 2 优秀，2-5 良好，> 5 较差。

### 7.7 读取原始 NMEA 数据（判断天线是否正常）

即使还没定位，只要能收到卫星信号就说明**天线和接收机正常**。

```bash
# 开启 NMEA 输出
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSNMEARATE=1"

# 创建 NMEA 读取脚本
cat > ~/nmea.py <<'PYEOF'
import sys, time, serial
port, dur = sys.argv[1], float(sys.argv[2])
s = serial.Serial(port, 115200, timeout=0.5)
buf, end = b"", time.time() + dur
while time.time() < end:
    n = s.in_waiting
    if n: buf += s.read(n)
    else: time.sleep(0.1)
s.close()
lines = [l for l in buf.decode(errors="replace").splitlines() if l.startswith("$")]
print("%s: 收到 %d 条 NMEA 语句" % (port, len(lines)))
for l in lines[:14]: print("  " + l)
PYEOF

# 读取 6 秒（NMEA 数据在 ttyUSB1）
sudo python3 ~/nmea.py /dev/ttyUSB1 6
```

**关键语句解读：**

```
$GPGSV,1,1,04,03,,,42,26,,,48,28,,,46,31,,,54   <- 可见4颗GPS卫星，信噪比42/48/46/54
$GLGSV,1,1,02,,,,41,,,,38                        <- 可见2颗GLONASS
$GNGSA,A,1,,,,,,                                 <- 第2个字段: 1=未定位 2=二维 3=三维
$GPGGA,053724.00,2238.580245,N,11402.601066,E,1,05,2.0,71.0,M
                                                 ^定位质量 ^卫星数 ^HDOP ^海拔
```

| 语句 | 作用 |
|---|---|
| `$GPGSV` / `$GLGSV` / `$BDGSV` | 可见卫星数与**信噪比**（GPS/GLONASS/北斗） |
| `$GNGSA` / `$GPGSA` / `$BDGSA` | 参与定位的卫星编号与 DOP |
| `$GPGGA` | 定位质量、卫星数、海拔 |
| `$GPRMC` | 推荐最小定位信息（含有效性标志 A/V） |

> **信噪比 > 35 即为良好信号。** 若 GSV 里完全没有卫星或信噪比都很低，
> 说明 **GNSS 天线没接好或位置被遮挡**。

### 7.8 关闭 GPS

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS=0"
```

> GPS 会持续耗电，不用时建议关闭。

---

## 8. 开机自启配置

### 8.1 USB 模式已持久化

`AT+CUSBPIDSWITCH=9011,1,1` 的第 3 个参数 `1` 已写入 NVRAM，
**重启后仍是 9011**，无需重新设置。验证：

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CUSBPIDSWITCH?"
```

### 8.2 GPS 开机自动启动

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSAUTO=1" "AT+CGPSAUTO?"
```

返回 `+CGPSAUTO: 1` 即设置成功，此后每次上电自动开启 GPS。

### 8.3 usb0 开机自动连接

NetworkManager 默认会自动连接。确认：

```bash
nmcli -t -f NAME,DEVICE,AUTOCONNECT connection show | grep usb0
```

若 `AUTOCONNECT` 不是 `yes`：

```bash
sudo nmcli connection modify lte-usb0 connection.autoconnect yes
```

### 8.4 重启后完整验证

```bash
sudo reboot
# 等待约 60 秒后
ip -br addr show usb0
ping -I usb0 -c 3 223.5.5.5
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSINFO"
```

---

## 9. 故障排查

### 9.1 `lsusb` 看不到 1e0e 设备

* 检查 USB 线与供电：SIM7600 峰值电流可达 **2A**，供电不足会反复掉线
* 检查 dmesg：`sudo dmesg | grep -i usb | tail -n 20`
* 部分载板需要拉高 `PWRKEY` 才能开机，参考你的载板文档

### 9.2 AT 命令无响应

```bash
# 1) ModemManager 抢占了端口
sudo systemctl stop ModemManager

# 2) 确认端口权限（当前用户需在 dialout 组，或用 sudo）
ls -l /dev/ttyUSB2
groups

# 3) 换 ttyUSB3 试试
sudo python3 ~/at.py /dev/ttyUSB3 "AT"
```

### 9.3 `+CPIN: SIM PIN` （卡被锁）

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CPIN=1234"     # 换成你的 PIN
```

### 9.4 `AT+CREG?` 一直返回 0 或 2（搜不到网）

| 原因 | 排查 |
|---|---|
| 天线未接 | 检查 MAIN 天线座 |
| 信号太弱 | `AT+CSQ` 若 < 10 需换位置 |
| 卡欠费/未开通 | 换手机验证 SIM 卡 |
| 锁了错误频段 | `AT+CNBP?` 查看频段配置 |

手动搜网（耗时 30-60 秒）：

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+COPS=?"
```

### 9.5 切换 PID 后没有出现 usb0

```bash
# 确认 PID 真的变了
lsusb | grep 1e0e

# 确认驱动已加载
lsmod | grep -E 'rndis_host|cdc_ether'
sudo modprobe rndis_host

# 看内核是怎么处理的
sudo dmesg | tail -n 30
```

### 9.6 usb0 有 IP 但上不了网

```bash
# 1) 确认 PDP 上下文已激活
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGACT?" "AT+CGPADDR"

# 2) 确认走的是 usb0 而不是别的网卡
ping -I usb0 -c 3 223.5.5.5

# 3) DNS 问题：直接用 IP 能通但域名不行
cat /etc/resolv.conf
ping -I usb0 -c 3 223.5.5.5 && nslookup www.baidu.com 223.5.5.5
```

### 9.7 GPS 长时间不定位

| 现象 | 处理 |
|---|---|
| NMEA 完全没有 `$` 语句 | GPS 未启动，执行 `AT+CGPS=1,1` |
| 有 GSV 但信噪比都 < 30 | 天线没接好 / 被遮挡，移到窗边或室外 |
| 有信号但 `GSA` 始终是 1 | 冷启动中，开启 AGPS（7.4 节）或耐心等 5-15 分钟 |
| 室内完全无信号 | GPS 信号穿透力弱，**必须把天线放到窗边或室外** |

检查 GPS 是否真的开着：

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS?"     # 必须是 +CGPS: 1,1
```

### 9.8 蜂窝流量被意外消耗

检查默认路由是否被 usb0 抢占：

```bash
ip route | grep default
```

若 usb0 的 metric 比 WiFi 小，按 **5.5 节** 调高即可。

---

## 10. AT 命令速查表

### 模块与 SIM

| 命令 | 说明 |
|---|---|
| `ATI` | 型号与固件版本 |
| `AT+CGMR` | 固件版本号 |
| `AT+CPIN?` | SIM 卡状态 |
| `AT+CIMI` | IMSI 号 |
| `AT+CCID` | ICCID（卡号） |

### 网络

| 命令 | 说明 |
|---|---|
| `AT+CSQ` | 信号强度 |
| `AT+COPS?` | 当前运营商 |
| `AT+CREG?` | 语音网络注册状态 |
| `AT+CGREG?` | 数据网络注册状态 |
| `AT+CPSI?` | 详细制式/频段/小区信息 |
| `AT+CGDCONT?` | 查看 APN 配置 |
| `AT+CGACT?` | PDP 上下文激活状态 |
| `AT+CGPADDR` | 运营商分配的 IP |

### USB 模式

| 命令 | 说明 |
|---|---|
| `AT+CUSBPIDSWITCH?` | 查询当前 PID |
| `AT+CUSBPIDSWITCH=9011,1,1` | 切到 RNDIS（usb0），永久保存 |
| `AT+CUSBPIDSWITCH=9001,1,1` | 切回 QMI（wwan0），永久保存 |

### GPS

| 命令 | 说明 |
|---|---|
| `AT+CGPS?` | 查询 GPS 开关状态 |
| `AT+CGPS=1,1` | 启动 GPS（独立定位） |
| `AT+CGPS=0` | 关闭 GPS |
| `AT+CGPSINFO` | 获取定位（简要） |
| `AT+CGNSSINFO` | 获取定位（含卫星数/DOP） |
| `AT+CGNSSMODE?` | 查询多星座模式 |
| `AT+CGPSNMEARATE=1` | 开启 NMEA 输出（1Hz） |
| `AT+CGPSXE=1` | 启用 AGPS (gpsOneXTRA) |
| `AT+CGPSXD=0` | 下载 AGPS 星历 |
| `AT+CGPSAUTO=1` | GPS 开机自启 |

---

## 附：一键检查脚本

```bash
#!/usr/bin/env bash
# sim7600-check.sh — 一键体检
AT=/dev/ttyUSB2

echo "===== USB 设备 ====="
lsusb | grep 1e0e || { echo "未检测到模块"; exit 1; }

echo "===== 模块状态 ====="
sudo python3 ~/at.py $AT "ATI" "AT+CPIN?" "AT+CSQ" "AT+COPS?" "AT+CGREG?"

echo "===== USB 模式 ====="
sudo python3 ~/at.py $AT "AT+CUSBPIDSWITCH?"

echo "===== 网络接口 ====="
ip -br addr show usb0 2>/dev/null || echo "usb0 不存在"
ip route | grep default

echo "===== 连通性 ====="
ping -I usb0 -c 3 -W 3 223.5.5.5 2>&1 | tail -n 2

echo "===== GPS ====="
sudo python3 ~/at.py $AT "AT+CGPS?" "AT+CGPSINFO" "AT+CGNSSINFO"
```
