# SIM7600G-H 4G Module — Data Connection & GPS Guide

> **Platform:** Raspberry Pi CM5 / Pi 5 + SIMCom SIM7600G-H
> **OS:** Raspberry Pi OS (Debian 12 bookworm)
> All commands and outputs in this guide were verified on real hardware.

---

## Table of Contents

1. [Identifying the Module and Its Ports](#1-identifying-the-module-and-its-ports)
2. [Preparing an AT Command Tool](#2-preparing-an-at-command-tool)
3. [Checking SIM and Network Registration](#3-checking-sim-and-network-registration)
4. [Switching USB Mode to Create usb0](#4-switching-usb-mode-to-create-usb0)
5. [Bringing usb0 Online](#5-bringing-usb0-online)
6. [Network Testing](#6-network-testing)
7. [GPS / GNSS Positioning](#7-gps--gnss-positioning)
8. [Persisting Across Reboots](#8-persisting-across-reboots)
9. [Troubleshooting](#9-troubleshooting)
10. [AT Command Cheat Sheet](#10-at-command-cheat-sheet)

---

## 1. Identifying the Module and Its Ports

### 1.1 Confirm the module is detected

```bash
lsusb | grep -i 1e0e
```

Expected:

```
Bus 004 Device 002: ID 1e0e:9001 Qualcomm / Option SimTech, Incorporated
```

* `1e0e` is SIMCom's vendor ID
* `9001` is the current **product ID (PID)**, which determines the USB mode

### 1.2 Read the current PID

```bash
for d in /sys/bus/usb/devices/*/; do
  [ -f "$d/idVendor" ] || continue
  [ "$(cat $d/idVendor)" = "1e0e" ] || continue
  echo "path=$d  PID=$(cat $d/idProduct)"
done
```

### 1.3 PID to mode mapping

| PID | Network interface | Kernel driver | Notes |
|---|---|---|---|
| `9001` | `wwan0` | `qmi_wwan` | QMI mode (default). Needs ModemManager / qmicli to dial. |
| `9011` | `usb0` | `rndis_host` | **RNDIS mode. The module dials by itself; usb0 just gets DHCP. Easiest.** |

> This guide uses **9011 (RNDIS)** because no manual dialling is required — the module
> activates the PDP context internally and NATs for the host.

### 1.4 Serial port layout

```bash
ls -l /dev/ttyUSB*
```

The SIM7600G-H creates five serial ports:

| Node | Purpose | Notes |
|---|---|---|
| `/dev/ttyUSB0` | DIAG / diagnostics | Usually busy, cannot be opened directly |
| `/dev/ttyUSB1` | **NMEA (GPS data output)** | GPS sentences stream from here |
| `/dev/ttyUSB2` | **AT command port (primary)** | Used throughout this guide |
| `/dev/ttyUSB3` | AT command port (secondary / PPP) | Also responds to AT |
| `/dev/ttyUSB4` | Audio / other | — |

> **Note:** Node numbering can change after a PID switch. Re-check with section 2.4.

---

## 2. Preparing an AT Command Tool

### 2.1 Install the dependency

```bash
sudo apt update
sudo apt install -y python3-serial
```

### 2.2 Create the AT helper script

```bash
cat > ~/at.py <<'PYEOF'
import sys, time, serial

port = sys.argv[1]
cmds = sys.argv[2:]
try:
    s = serial.Serial(port, 115200, timeout=0.4)
except Exception as e:
    print("Failed to open %s: %s" % (port, e))
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
    print(txt if txt else "(no response)")
s.close()
PYEOF
chmod +x ~/at.py
```

Usage:

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT" "AT+CSQ"
```

### 2.3 Stop ModemManager (avoid port contention)

ModemManager holds the AT port open. Stop it before sending AT commands manually:

```bash
sudo systemctl stop ModemManager
```

> To restore later: `sudo systemctl start ModemManager`
> In RNDIS mode the module dials on its own, so **ModemManager is not required** and can
> stay stopped.

### 2.4 Find which port answers AT

```bash
for p in /dev/ttyUSB1 /dev/ttyUSB2 /dev/ttyUSB3 /dev/ttyUSB4; do
  echo "### $p -> $(sudo timeout 8 python3 ~/at.py $p AT 2>&1 | tail -n 1)"
done
```

Expected: `ttyUSB2` and `ttyUSB3` return `OK`.

---

## 3. Checking SIM and Network Registration

**Verify these before attempting a data connection — otherwise it will certainly fail.**

```bash
sudo python3 ~/at.py /dev/ttyUSB2 \
  "ATI" "AT+CPIN?" "AT+CSQ" "AT+COPS?" "AT+CREG?" "AT+CGREG?" "AT+CPSI?"
```

### What each result means

| Command | Healthy output | Meaning |
|---|---|---|
| `ATI` | `Model: SIMCOM_SIM7600G-H` | Model and firmware revision |
| `AT+CPIN?` | `+CPIN: READY` | **SIM ready.** `SIM PIN` means a PIN is required |
| `AT+CSQ` | `+CSQ: 24,99` | Signal strength, see table below |
| `AT+COPS?` | `+COPS: 0,0,"CHN-UNICOM",7` | Operator; trailing `7` = LTE |
| `AT+CREG?` | `+CREG: 0,1` | **Second number: 1 = registered home, 5 = roaming.** 0 or 2 = not registered |
| `AT+CGREG?` | `+CGREG: 0,1` | Packet-data service registered |
| `AT+CPSI?` | `+CPSI: LTE,Online,460-01,...` | Detailed RAT / band / cell info |

**`AT+CSQ` signal reference:**

| Value | dBm | Quality |
|---|---|---|
| 0-9 | -113 to -95 | Poor |
| 10-14 | -93 to -85 | Fair |
| 15-19 | -83 to -75 | Good |
| **20-31** | **-73 to -51** | **Excellent** |
| 99 | — | Unknown / no signal |

> `+CSQ: 24,99` corresponds to roughly **-65 dBm** — excellent.

**If `AT+CREG?` returns 0 or 2 (not registered)**, check:
1. SIM seated correctly and account active
2. The **MAIN** antenna is connected
3. Wait 30-60 seconds and re-check — network search takes time after power-up

---

## 4. Switching USB Mode to Create usb0

### 4.1 Query the current mode

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CUSBPIDSWITCH?"
```

`+CUSBPIDSWITCH: 9001` means QMI mode (interface is `wwan0`).

### 4.2 Switch to RNDIS mode (creates usb0)

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CUSBPIDSWITCH=9011,1,1"
```

**Parameters:** `AT+CUSBPIDSWITCH=<PID>,<1>,<1>`

| Position | Value | Meaning |
|---|---|---|
| 1st | `9011` | Target PID (RNDIS mode) |
| 2nd | `1` | Apply immediately |
| 3rd | `1` | **Save to NVRAM — survives power cycles** |

After `OK` the module **re-enumerates on USB**, which takes roughly 10-20 seconds.

### 4.3 Verify the switch

```bash
sleep 20
lsusb | grep 1e0e                    # PID should now be 9011
ip -br link show usb0                # usb0 should exist
sudo dmesg | grep -iE 'rndis|usb0' | tail -n 3
```

Expected dmesg:

```
usb 4-1: New USB device found, idVendor=1e0e, idProduct=9011
rndis_host 4-1:1.0 usb0: register 'rndis_host' at usb-xhci-hcd.1-1, RNDIS device, 9e:6b:1f:6e:60:bf
```

> **Note:** Many references call 9011 "ECM mode", but the SIM7600G-H actually registers as
> **RNDIS** (kernel driver `rndis_host`). Functionally equivalent — both produce `usb0`.

### 4.4 Switching back to QMI

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CUSBPIDSWITCH=9001,1,1"
```

---

## 5. Bringing usb0 Online

### 5.1 RNDIS mode dials automatically

After the switch the module activates the PDP context and runs a DHCP server internally.
NetworkManager normally picks `usb0` up on its own — **no dialling commands needed**.

```bash
ip -br addr show usb0
ip route | grep default
```

Expected:

```
usb0   UNKNOWN   192.168.225.53/24   2408:8456:xxxx::/64
default via 192.168.225.1 dev usb0 proto dhcp src 192.168.225.53 metric 100
```

> `192.168.225.x` is the module's internal NAT subnet; `192.168.225.1` is the module itself.

### 5.2 If no IP is assigned

Trigger DHCP manually:

```bash
sudo ip link set usb0 up
sudo dhclient -v usb0
```

Or hand it to NetworkManager:

```bash
sudo nmcli device set usb0 managed yes
sudo nmcli device connect usb0
```

### 5.3 Confirm the carrier assigned an IP

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGDCONT?" "AT+CGACT?" "AT+CGPADDR"
```

Focus on `AT+CGACT?` (context activation) and `AT+CGPADDR` (assigned IP):

```
+CGACT: 1,1                      <- context 1 is active
+CGPADDR: 1,10.34.196.185        <- carrier-assigned IP
```

### 5.4 Setting the APN manually (only if auto-dial fails)

```bash
# China Unicom: 3gnet   China Mobile: cmnet   China Telecom: ctnet
sudo python3 ~/at.py /dev/ttyUSB2 \
  "AT+CGDCONT=1,\"IP\",\"3gnet\"" \
  "AT+CGACT=1,1" \
  "AT+CGPADDR"
```

### 5.5 Set route priority (important!)

By default `usb0` gets metric 100, which makes it the **primary route**. All traffic —
including system updates — would then go over metered cellular data.

**Prefer Wi-Fi / Ethernet, keep cellular as backup:**

```bash
# Find the connection profile bound to usb0
CON=$(nmcli -t -f NAME,DEVICE connection show --active | grep ':usb0$' | cut -d: -f1)
echo "usb0 profile: $CON"

# Rename it and raise the metric (higher number = lower priority)
sudo nmcli connection modify "$CON" \
  ipv4.route-metric 800 ipv6.route-metric 800 connection.id "lte-usb0"
sudo nmcli connection up lte-usb0
```

Verify (Wi-Fi at 600 should come before cellular at 800):

```bash
ip route | grep default
```

```
default via 192.168.88.1   dev wlan0 metric 600    <- Wi-Fi preferred
default via 192.168.225.1  dev usb0  metric 800    <- cellular backup
```

---

## 6. Network Testing

> The commands below use `--interface usb0` / `-I usb0` to **force traffic over cellular**,
> so results are accurate even when the default route is Wi-Fi.

### 6.1 Reach the module itself

```bash
ping -I usb0 -c 5 192.168.225.1
```

### 6.2 Reach the public internet (bypassing DNS)

```bash
ping -I usb0 -c 8 223.5.5.5
```

Reference: **40-60 ms** latency, 0% loss.

### 6.3 DNS and HTTPS

```bash
curl -sS --interface usb0 --max-time 25 -o /dev/null \
  -w 'http=%{http_code} dns=%{time_namelookup}s tls=%{time_appconnect}s\n' \
  https://www.google.com/
```

### 6.4 Public IP as seen from the cellular link

```bash
curl -sS --interface usb0 --max-time 25 https://ifconfig.me/ip; echo
```

### 6.5 Throughput (consumes data!)

```bash
# Only fetch 8 MB to limit data usage
curl -sS --interface usb0 --max-time 120 -o /dev/null -r 0-8388607 \
  -w '4G: %{size_download} bytes in %{time_total}s = %{speed_download} B/s\n' \
  http://archive.raspberrypi.com/debian/pool/main/l/linux/linux-image-6.12.109+rpt-rpi-2712_6.12.109-1+rpt1_arm64.deb
```

Reference: around **1.2 MB/s ≈ 10 Mbit/s** on LTE Band 3 (varies with cell load and signal).

### 6.6 Data counters

```bash
echo "usb0 RX: $(cat /sys/class/net/usb0/statistics/rx_bytes) bytes"
echo "usb0 TX: $(cat /sys/class/net/usb0/statistics/tx_bytes) bytes"
```

---

## 7. GPS / GNSS Positioning

### 7.1 Hardware prerequisite

**GPS needs its own GNSS antenna**, connected to the connector labelled `GNSS` / `GPS`
(not the 4G MAIN/AUX connectors). Indoor reception is weak — place the antenna near a
window or outdoors.

### 7.2 Query GPS state

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS?" "AT+CGNSSMODE?"
```

* `+CGPS: 0,1` → the leading `0` means **not running**
* `+CGNSSMODE: 3,1` → multi-constellation mode enabled

### 7.3 Start GPS

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS=1,1"
```

`AT+CGPS=<on/off>,<mode>` — `1,1` means enable in standalone mode.

Confirm:

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS?"      # expect +CGPS: 1,1
```

### 7.4 Use AGPS to speed up first fix (strongly recommended)

A cold start must download satellite ephemeris, which can take 10+ minutes indoors.
With gpsOneXTRA the module fetches ephemeris over **4G**, typically achieving a fix in
**under 30 seconds**.

> Prerequisite: the `usb0` data connection from section 5 must be working.

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSXE=1" "AT+CGPSXD=0"
```

* `AT+CGPSXE=1` enables gpsOneXTRA
* `AT+CGPSXD=0` downloads the ephemeris file now

`+CGPSXD: 2` indicates the download finished.

### 7.5 Read the fix

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSINFO"
```

**No fix** returns empty fields:

```
+CGPSINFO: ,,,,,,,,
```

**Fix acquired:**

```
+CGPSINFO: 2238.580113,N,11402.601084,E,051126,053717.0,71.4,0.0,
```

| Field | Example | Meaning |
|---|---|---|
| 1-2 | `2238.580113,N` | Latitude `ddmm.mmmmmm` + hemisphere |
| 3-4 | `11402.601084,E` | Longitude `dddmm.mmmmmm` + hemisphere |
| 5 | `051126` | UTC date DDMMYY = 2026-11-05 |
| 6 | `053717.0` | UTC time HHMMSS.s |
| 7 | `71.4` | Altitude (metres) |
| 8 | `0.0` | Speed (knots) |
| 9 | — | Course over ground |

**Converting to decimal degrees:**

```
Latitude  2238.580113 → 22 + 38.580113/60 = 22.643002° N
Longitude 11402.601084 → 114 + 2.601084/60 = 114.043352° E
```

One-liner that does the conversion:

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSINFO" | awk -F'[:,]' '/CGPSINFO/ && $2!="" {
  lat=substr($2,1,2)+substr($2,3)/60; lon=substr($4,1,3)+substr($4,4)/60;
  printf "Lat %.6f %s  Lon %.6f %s\n", lat, $3, lon, $5
}'
```

### 7.6 Detailed fix info (satellite counts and accuracy)

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGNSSINFO"
```

```
+CGNSSINFO: 2,06,02,04,2238.580989,N,11402.600904,E,051126,053740.0,69.8,0.0,,1.4,1.1,0.9
```

| Field | Example | Meaning |
|---|---|---|
| 1 | `2` | Fix mode: **2 = 2D, 3 = 3D**, empty = no fix |
| 2 | `06` | **GPS** satellites used |
| 3 | `02` | **GLONASS** satellites used |
| 4 | `04` | **BeiDou** satellites used |
| 5-8 | — | Latitude / longitude |
| 9-10 | — | UTC date and time |
| 11 | `69.8` | Altitude |
| 14-16 | `1.4,1.1,0.9` | **PDOP / HDOP / VDOP** dilution of precision |

> **Lower DOP is better:** < 2 excellent, 2-5 good, > 5 poor.

### 7.7 Reading raw NMEA (to confirm the antenna works)

Even without a fix, receiving satellite signals proves **the antenna and receiver are fine**.

```bash
# Enable NMEA output
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSNMEARATE=1"

# Create an NMEA reader
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
print("%s: %d NMEA sentences" % (port, len(lines)))
for l in lines[:14]: print("  " + l)
PYEOF

# Read for 6 seconds (NMEA comes out of ttyUSB1)
sudo python3 ~/nmea.py /dev/ttyUSB1 6
```

**How to read the key sentences:**

```
$GPGSV,1,1,04,03,,,42,26,,,48,28,,,46,31,,,54   <- 4 GPS sats visible, SNR 42/48/46/54
$GLGSV,1,1,02,,,,41,,,,38                        <- 2 GLONASS sats visible
$GNGSA,A,1,,,,,,                                 <- 2nd field: 1=no fix, 2=2D, 3=3D
$GPGGA,053724.00,2238.580245,N,11402.601066,E,1,05,2.0,71.0,M
                                                 ^quality ^sats ^HDOP ^altitude
```

| Sentence | Purpose |
|---|---|
| `$GPGSV` / `$GLGSV` / `$BDGSV` | Satellites in view and **SNR** (GPS / GLONASS / BeiDou) |
| `$GNGSA` / `$GPGSA` / `$BDGSA` | Satellites used in the fix, plus DOP |
| `$GPGGA` | Fix quality, satellite count, altitude |
| `$GPRMC` | Recommended minimum data, includes validity flag A/V |

> **SNR above 35 is good.** If GSV shows no satellites at all, or all SNRs are very low,
> the **GNSS antenna is not connected properly or the view is obstructed**.

### 7.8 Stop GPS

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS=0"
```

> GPS draws power continuously — turn it off when not needed.

---

## 8. Persisting Across Reboots

### 8.1 USB mode is already persistent

The third parameter `1` in `AT+CUSBPIDSWITCH=9011,1,1` writes to NVRAM, so the module
**stays at 9011 after a reboot**. Verify:

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CUSBPIDSWITCH?"
```

### 8.2 Auto-start GPS at boot

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSAUTO=1" "AT+CGPSAUTO?"
```

`+CGPSAUTO: 1` confirms GPS will start automatically on every power-up.

### 8.3 usb0 auto-connect

NetworkManager connects it automatically by default. Verify:

```bash
nmcli -t -f NAME,DEVICE,AUTOCONNECT connection show | grep usb0
```

If `AUTOCONNECT` is not `yes`:

```bash
sudo nmcli connection modify lte-usb0 connection.autoconnect yes
```

### 8.4 Full post-reboot verification

```bash
sudo reboot
# wait ~60 seconds, then:
ip -br addr show usb0
ping -I usb0 -c 3 223.5.5.5
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPSINFO"
```

---

## 9. Troubleshooting

### 9.1 `lsusb` does not show a 1e0e device

* Check USB cable and power: the SIM7600 can draw **2 A peaks**; insufficient power causes
  repeated disconnects
* Check dmesg: `sudo dmesg | grep -i usb | tail -n 20`
* Some carrier boards require pulling `PWRKEY` to power the module on — see your board docs

### 9.2 AT commands return nothing

```bash
# 1) ModemManager is holding the port
sudo systemctl stop ModemManager

# 2) Check permissions (user must be in dialout, or use sudo)
ls -l /dev/ttyUSB2
groups

# 3) Try ttyUSB3 instead
sudo python3 ~/at.py /dev/ttyUSB3 "AT"
```

### 9.3 `+CPIN: SIM PIN` (card is locked)

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CPIN=1234"     # replace with your PIN
```

### 9.4 `AT+CREG?` keeps returning 0 or 2 (no network)

| Cause | Check |
|---|---|
| Antenna not connected | Inspect the MAIN antenna connector |
| Signal too weak | `AT+CSQ` below 10 means you need a better location |
| SIM inactive / no credit | Test the SIM in a phone |
| Wrong band locked | `AT+CNBP?` to inspect band configuration |

Manual network search (takes 30-60 s):

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+COPS=?"
```

### 9.5 usb0 does not appear after the PID switch

```bash
# Confirm the PID actually changed
lsusb | grep 1e0e

# Confirm the driver is loaded
lsmod | grep -E 'rndis_host|cdc_ether'
sudo modprobe rndis_host

# See what the kernel did
sudo dmesg | tail -n 30
```

### 9.6 usb0 has an IP but no internet

```bash
# 1) Confirm the PDP context is active
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGACT?" "AT+CGPADDR"

# 2) Confirm traffic really leaves via usb0
ping -I usb0 -c 3 223.5.5.5

# 3) DNS issue: IP works but names don't
cat /etc/resolv.conf
ping -I usb0 -c 3 223.5.5.5 && nslookup www.google.com 223.5.5.5
```

### 9.7 GPS never gets a fix

| Symptom | Action |
|---|---|
| No `$` sentences in NMEA at all | GPS is off — run `AT+CGPS=1,1` |
| GSV present but all SNR < 30 | Antenna loose or obstructed — move near a window or outdoors |
| Signal present but GSA stays at `1` | Cold start — enable AGPS (section 7.4) or wait 5-15 minutes |
| No signal indoors at all | GPS signals barely penetrate buildings — **the antenna must go near a window or outside** |

Confirm GPS is actually enabled:

```bash
sudo python3 ~/at.py /dev/ttyUSB2 "AT+CGPS?"     # must be +CGPS: 1,1
```

### 9.8 Unexpected cellular data usage

Check whether usb0 hijacked the default route:

```bash
ip route | grep default
```

If usb0's metric is lower than Wi-Fi's, raise it as shown in **section 5.5**.

---

## 10. AT Command Cheat Sheet

### Module and SIM

| Command | Purpose |
|---|---|
| `ATI` | Model and firmware revision |
| `AT+CGMR` | Firmware version string |
| `AT+CPIN?` | SIM status |
| `AT+CIMI` | IMSI |
| `AT+CCID` | ICCID (card number) |

### Network

| Command | Purpose |
|---|---|
| `AT+CSQ` | Signal strength |
| `AT+COPS?` | Current operator |
| `AT+CREG?` | Circuit-switched registration |
| `AT+CGREG?` | Packet-data registration |
| `AT+CPSI?` | Detailed RAT / band / cell info |
| `AT+CGDCONT?` | APN configuration |
| `AT+CGACT?` | PDP context activation state |
| `AT+CGPADDR` | Carrier-assigned IP |

### USB mode

| Command | Purpose |
|---|---|
| `AT+CUSBPIDSWITCH?` | Query current PID |
| `AT+CUSBPIDSWITCH=9011,1,1` | Switch to RNDIS (usb0), persist |
| `AT+CUSBPIDSWITCH=9001,1,1` | Switch back to QMI (wwan0), persist |

### GPS

| Command | Purpose |
|---|---|
| `AT+CGPS?` | Query GPS on/off state |
| `AT+CGPS=1,1` | Start GPS (standalone) |
| `AT+CGPS=0` | Stop GPS |
| `AT+CGPSINFO` | Get fix (brief) |
| `AT+CGNSSINFO` | Get fix (with satellite counts and DOP) |
| `AT+CGNSSMODE?` | Query multi-constellation mode |
| `AT+CGPSNMEARATE=1` | Enable NMEA output at 1 Hz |
| `AT+CGPSXE=1` | Enable AGPS (gpsOneXTRA) |
| `AT+CGPSXD=0` | Download AGPS ephemeris |
| `AT+CGPSAUTO=1` | Auto-start GPS at boot |

---

## Appendix: Health-Check Script

```bash
#!/usr/bin/env bash
# sim7600-check.sh — one-shot health check
AT=/dev/ttyUSB2

echo "===== USB device ====="
lsusb | grep 1e0e || { echo "Module not detected"; exit 1; }

echo "===== Module status ====="
sudo python3 ~/at.py $AT "ATI" "AT+CPIN?" "AT+CSQ" "AT+COPS?" "AT+CGREG?"

echo "===== USB mode ====="
sudo python3 ~/at.py $AT "AT+CUSBPIDSWITCH?"

echo "===== Network interface ====="
ip -br addr show usb0 2>/dev/null || echo "usb0 does not exist"
ip route | grep default

echo "===== Connectivity ====="
ping -I usb0 -c 3 -W 3 223.5.5.5 2>&1 | tail -n 2

echo "===== GPS ====="
sudo python3 ~/at.py $AT "AT+CGPS?" "AT+CGPSINFO" "AT+CGNSSINFO"
```
