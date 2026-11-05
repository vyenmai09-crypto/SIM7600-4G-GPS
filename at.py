#!/usr/bin/env python3
"""Send AT commands to the SIM7600G-H and print the responses.

Usage:
    sudo python3 at.py /dev/ttyUSB2 "AT" "AT+CSQ" "AT+CGPSINFO"

Note: stop ModemManager first, it holds the AT port open:
    sudo systemctl stop ModemManager
"""
import sys
import time

import serial

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)

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
