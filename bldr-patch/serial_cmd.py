#!/usr/bin/env python3
"""Send one or more shell commands over the already-booted serial console
and print the output. Assumes a live 'root@OpenWrt:~#'-style prompt is
already up (i.e. run right after netboot.py reports SHELL PROMPT REACHED).

Usage: serial_cmd.py "<cmd1>" ["<cmd2>" ...]
"""
import os, sys, time, select, termios

DEV = "/dev/ttyUSB0"

if len(sys.argv) < 2:
    raise SystemExit(__doc__)

fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[4] = a[5] = termios.B115200
a[6][termios.VMIN] = 0
a[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIOFLUSH)

def send(s):
    for c in s.encode():
        os.write(fd, bytes([c])); time.sleep(0.01)
    os.write(fd, b"\r")

def pump(t):
    buf = b""; end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.15)
        if r:
            try:
                d = os.read(fd, 4096)
            except OSError:
                d = b""
            if d:
                buf += d
    return buf

# wake the prompt / drain any pending banner
send("")
pump(1)

for cmd in sys.argv[1:]:
    send(cmd)
    out = pump(3)
    sys.stdout.write(out.decode(errors="replace"))
    sys.stdout.flush()

os.close(fd)
