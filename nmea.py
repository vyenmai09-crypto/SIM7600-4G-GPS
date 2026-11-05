#!/usr/bin/env python3
"""Read raw NMEA sentences from the SIM7600G-H GNSS port.

Usage:
    sudo python3 nmea.py /dev/ttyUSB1 6

Enable NMEA output first:
    sudo python3 at.py /dev/ttyUSB2 "AT+CGPS=1,1" "AT+CGPSNMEARATE=1"

Reading the output:
    $GPGSV / $GLGSV / $BDGSV -> satellites in view and SNR (SNR > 35 is good)
    $GNGSA field 2           -> 1 = no fix, 2 = 2D fix, 3 = 3D fix
    $GPGGA                   -> fix quality, satellite count, altitude
"""
import sys
import time

import serial

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)

port = sys.argv[1]
dur = float(sys.argv[2])

try:
    s = serial.Serial(port, 115200, timeout=0.5)
except Exception as e:
    print("Failed to open %s: %s" % (port, e))
    sys.exit(1)

buf = b""
end = time.time() + dur
while time.time() < end:
    n = s.in_waiting
    if n:
        buf += s.read(n)
    else:
        time.sleep(0.1)
s.close()

lines = [l for l in buf.decode(errors="replace").splitlines() if l.startswith("$")]
print("%s: %d NMEA sentences in %.0fs" % (port, len(lines), dur))
for l in lines[:20]:
    print("  " + l)

if not lines:
    print("\nNo NMEA data. Check that GPS is on:  AT+CGPS?  (expect +CGPS: 1,1)")
