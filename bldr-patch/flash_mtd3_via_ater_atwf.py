#!/usr/bin/env python3
"""Flash MAIN (tclinux, mtd3) using the raw ATER (erase block X..Y inclusive)
+ ATWF (write RAM->flash) zloader primitives instead of ATUR/TFTP.

ATUR was proven (extensive testing) to silently no-op the image-body NAND
write on every invocation after the device's first flash -- it reports
full success but never touches physical flash. ATER/ATWF are raw,
un-whitelisted low-level NAND primitives (found via static RE of our own
dumped zloader) with NO such lockout -- smoke-tested live on HW
(test_ater_atwf.py) with a byte-exact write/readback on a throwaway block
deep inside mtd3.

Block size = 0x20000. mtd3 (tclinux) = flash offset 0x80000-0x3880000 =
blocks 4-451 INCLUSIVE (confirmed: (451+1-4)*0x20000 = 0x3800000 = mtd3 size,
and block 452*0x20000 = 0x3880000 = exactly where tclinux_slave/mtd5 starts).
This script ONLY ever issues ATER 4,451 -- never any other range. It NEVER
touches partition 2 (tclinux_slave / mtd5, the OEM recovery copy).

Usage: flash_mtd3_via_ater_atwf.py
"""
import os, sys, time, select, termios, subprocess, re

DEV = "/dev/ttyUSB0"
MODEL = "VMG8825-T50"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ATENV3 = os.path.join(REPO, "tools/atenv3/atenv3_passwd")
SERVER = "192.168.1.1"

FW = os.path.join(REPO, "firmware", "vmg8825-t50-era-signed-v2.bin")
FW_MD5 = "97e7eb1fed4286b08de1c72883db159d"
FW_NAME = "fw4.bin"
RAM_ADDR = 0x80020000

MTD3_OFF = 0x80000
MTD3_BLK_LO = 4
MTD3_BLK_HI = 451     # INCLUSIVE. NEVER change this without re-deriving from the partition map.
assert (MTD3_BLK_HI + 1 - MTD3_BLK_LO) * 0x20000 == 0x3800000, "block math sanity check failed"
assert (MTD3_BLK_HI + 1) * 0x20000 == 0x3880000, "must land exactly at mtd5 boundary, not past it"

fw_bytes = open(FW, "rb").read()
assert __import__("hashlib").md5(fw_bytes).hexdigest() == FW_MD5, "FW md5 mismatch, aborting"
FW_LEN = len(fw_bytes)
assert FW_LEN <= MTD3_BLK_HI * 0x20000 + 0x20000 - MTD3_OFF, "image too big for mtd3"

t0 = time.time()
def ts(): return "%6.2fs" % (time.time() - t0)

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
        os.write(fd, bytes([c])); time.sleep(0.02)
    os.write(fd, b"\r"); time.sleep(0.05)

def pump(t, tag="", until=None):
    buf = b""; end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                d = os.read(fd, 4096)
            except OSError:
                d = b""
            if d:
                buf += d
                sys.stdout.write(d.decode(errors="replace")); sys.stdout.flush()
                if until and until.encode() in buf:
                    break
    print("\n[%s pump done, tag=%s, got %d bytes]" % (ts(), tag, len(buf)))
    return buf

def die(msg, rc):
    print("\n[!] " + msg); os.close(fd); sys.exit(rc)

print(ts(), "### catching ZHAL> (90s CR-spam)")
end = time.time() + 90; got = False
buf = b""
while time.time() < end and not got:
    os.write(fd, b"\r")
    r, _, _ = select.select([fd], [], [], 0.1)
    if r:
        try:
            d = os.read(fd, 4096)
        except OSError:
            d = b""
        if d:
            buf += d
            sys.stdout.write(d.decode(errors="replace")); sys.stdout.flush()
            if b"ZHAL>" in buf:
                got = True
if not got:
    die("no ZHAL> within 90s", 2)
print("\n%s [+] ZHAL> reached" % ts())
send(""); pump(2, "prompt-clear", "ZHAL>")

print(ts(), "### ATSE %s" % MODEL)
send("ATSE %s" % MODEL)
b = pump(3, "atse")
m = re.search(rb"([0-9A-Fa-f]{36})", b)
if not m:
    die("no seed found", 3)
seed = m.group(1).decode().upper()
print(ts(), "[+] seed =", seed)
pw = subprocess.run([ATENV3, seed], capture_output=True, text=True).stdout.strip()
print(ts(), "[+] password =", pw)
if not pw or not pw.isdigit():
    die("no usable password derived", 4)
send("ATEN 1,%s" % pw)
b = pump(2.5, "aten")
if b"incorrect" in b.lower() or b"invalid" in b.lower() or b"wrong" in b.lower():
    die("ATEN rejected", 5)
print(ts(), "[+] ATEN unlock OK")

print(ts(), "### ATLD %s -> RAM 0x%x (%d / 0x%x bytes)" % (FW_NAME, RAM_ADDR, FW_LEN, FW_LEN))
send("ATLD %s" % FW_NAME)
pump(2, "atld-cmd")
r = subprocess.run(["atftp", "--put", "--local-file", FW, "--remote-file", FW_NAME, SERVER],
                    capture_output=True, text=True, timeout=180)
print(ts(), "atftp rc:", r.returncode, (r.stdout + r.stderr).strip()[:200])
b = pump(20, "tftp-confirm")
if b"File download" not in b:
    die("no TFTP confirmation for image", 7)
print(ts(), "[+] image staged in RAM")

print(ts(), "### ATER %d,%d  (erase mtd3 ONLY: 0x%x..0x%x)" % (MTD3_BLK_LO, MTD3_BLK_HI, MTD3_OFF, (MTD3_BLK_HI + 1) * 0x20000))
send("ATER %d,%d" % (MTD3_BLK_LO, MTD3_BLK_HI))
b = pump(60, "ater", "ZHAL>")
if b"ZHAL>" not in b:
    die("no ZHAL> after ATER -- STOP, investigate before writing anything", 8)
print(ts(), "[+] mtd3 erased")

print(ts(), "### ATWF 0x%x,0x%x,0x%x  (RAM -> flash, mtd3 body)" % (RAM_ADDR, MTD3_OFF, FW_LEN))
send("ATWF 0x%x,0x%x,0x%x" % (RAM_ADDR, MTD3_OFF, FW_LEN))
b = pump(30, "atwf", "ZHAL>")
if b"ZHAL>" not in b:
    die("no ZHAL> after ATWF -- STOP, investigate before rebooting", 9)
print(ts(), "[+] mtd3 write issued")

print(ts(), "### ATRF 0x%x,0x200 (verify header)" % MTD3_OFF)
send("ATRF 0x%x,0x200" % MTD3_OFF)
b = pump(4, "atrf-header", "ZHAL>")

tail_off = MTD3_OFF + FW_LEN - 0x40
print(ts(), "### ATRF 0x%x,0x40 (verify tail, near end of written image)" % tail_off)
send("ATRF 0x%x,0x40" % tail_off)
b = pump(4, "atrf-tail", "ZHAL>")

print(ts(), "[+] header/tail dumps above -- compare manually before deciding to reboot")
os.close(fd)
