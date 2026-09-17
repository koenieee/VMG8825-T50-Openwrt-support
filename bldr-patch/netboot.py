#!/usr/bin/env python3
"""Canonical, one-shot netboot ritual for the VMG8825-T50 bldr-patch chain.

Consolidates every proven step into a single reusable script:
  ZHAL> catch -> ATSE/ATENv3/ATEN debug unlock -> ATLD+TFTP kernel to
  0x80020000 -> ATGU to bldr> -> memwl combined relocate+cache+jump stub
  to KSEG1 -> jump -> poll for a live shell prompt (instead of a blind
  fixed-length capture).

PREREQUISITE (not automated here): power-cycle the device first (see
power_cycle.py) -- this script must be started in the SAME turn/
near-immediately after power-on, since the ZHAL> catch window is only
~90s from boot and any delay risks missing it.

Usage: netboot.py <kernel_initramfs.bin> [max_wait_seconds]
Exit codes: 0 = shell prompt reached, 2 = no ZHAL>, 3 = seed not found,
4 = no password, 5 = ATEN rejected, 6 = no bldr>, 7 = TFTP failed,
8 = jumped but no shell prompt within max_wait_seconds (may still be
booting -- check the log).
"""
import os, sys, time, select, termios, subprocess, re, struct

DEV = "/dev/ttyUSB0"
MODEL = "VMG8825-T50"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ATENV3 = os.path.join(REPO, "tools/atenv3/atenv3_passwd")
STUB_BASE = 0xa1000000
SHELL_MARKER = b"root@OpenWrt:~#"

if len(sys.argv) < 2:
    raise SystemExit(__doc__)
KERNEL = sys.argv[1]
MAX_WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 180
STUB = os.path.join(HERE, "combined_kernel_stub.bin")

sys.path.insert(0, HERE)
import build_combined_kernel_stub as bks

t0 = time.time()
def ts(): return "%6.2fs" % (time.time() - t0)

LOGPATH = os.path.join(HERE, "netboot.log")
logf = open(LOGPATH, "wb")

def load_words(path):
    b = open(path, "rb").read()
    return [struct.unpack(">I", b[i:i+4])[0] for i in range(0, len(b), 4)]

klen = os.path.getsize(KERNEL)
print(ts(), "kernel size = %d (0x%x) bytes" % (klen, klen))
words = bks.build_combined(0x80020000, 0x80002000, klen, 0x80002000)
open(STUB, "wb").write(b"".join(struct.pack(">I", w) for w in words))
stub_words = load_words(STUB)
print(ts(), "combined stub: %d words (%d bytes)" % (len(stub_words), len(stub_words)*4))

fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[4] = a[5] = termios.B115200
a[6][termios.VMIN] = 0
a[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIOFLUSH)  # drop stale buffered bytes from prior sessions

def send(s):
    for c in s.encode():
        os.write(fd, bytes([c])); time.sleep(0.01)
    os.write(fd, b"\r")

def pump(t, tag="", keepalive_every=0, keepalive_bytes=b"\r", stop_marker=None):
    buf = b""; end = time.time() + t
    last_keepalive = time.time()
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.15)
        if r:
            try:
                d = os.read(fd, 4096)
            except OSError:
                d = b""
            if d:
                buf += d
                logf.write(d); logf.flush()
                sys.stdout.write(d.decode(errors="replace")); sys.stdout.flush()
                if stop_marker and stop_marker in buf:
                    break
        if keepalive_every and (time.time() - last_keepalive) > keepalive_every:
            os.write(fd, keepalive_bytes)
            last_keepalive = time.time()
    print("\n[%s pump done, tag=%s, got %d bytes]" % (ts(), tag, len(buf)))
    return buf

def die(msg, rc):
    print("\n[!] " + msg); os.close(fd); logf.close(); sys.exit(rc)

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
            logf.write(d); logf.flush()
            sys.stdout.write(d.decode(errors="replace")); sys.stdout.flush()
            if b"ZHAL>" in buf:
                got = True
if not got:
    die("no ZHAL> within 90s -- device power-cycle likely started too late relative to this script", 2)

print("\n%s [+] ZHAL> reached" % ts())
send(""); pump(1.5, "prompt-clear")

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
low = b.lower()
if b"incorrect" in low or b"invalid" in low or b"wrong" in low:
    die("ATEN rejected", 5)

print(ts(), "### ATLD %s (to 0x80020000, TFTP)" % os.path.basename(KERNEL))
send("ATLD %s" % os.path.basename(KERNEL))
pump(2, "atld-cmd")
r = subprocess.run(["atftp", "--put", "--local-file", KERNEL,
                     "--remote-file", os.path.basename(KERNEL), "192.168.1.1"],
                    capture_output=True, text=True, timeout=300)
print(ts(), "atftp rc:", r.returncode)
b = pump(15, "tftp-confirm")
if b"File download" not in b:
    die("no TFTP confirmation for kernel", 7)

print(ts(), "### ATGU (to bldr>)")
send("ATGU")
b = pump(3, "atgu")
if b"bldr>" not in b:
    die("did NOT see bldr> after ATGU", 6)

print(ts(), "### memwl combined stub (%d words) to 0x%x (KSEG1)" % (len(stub_words), STUB_BASE))
for i, w in enumerate(stub_words):
    addr = STUB_BASE + i * 4
    send("memwl %x %x" % (addr, w))
    pump(1.3, "memwl-%d" % i)

print(ts(), "### JUMP %x  <<<<< SINGLE-SHOT: COPY + INVALIDATE + JUMP INTO KERNEL >>>>>" % STUB_BASE)
send("jump %x" % STUB_BASE)

print(ts(), "### polling for shell prompt (max %ds, CR keepalive every 10s)" % MAX_WAIT)
b = pump(MAX_WAIT, "boot-to-shell", keepalive_every=10, stop_marker=SHELL_MARKER)

os.close(fd)
logf.close()

if SHELL_MARKER in b:
    print(ts(), "[+][+][+] SHELL PROMPT REACHED -- netboot ritual complete")
    sys.exit(0)
elif b"Undefined Exception" in b or b"Exception happen" in b:
    print(ts(), "[!] EXCEPTION seen -- see", LOGPATH)
    sys.exit(8)
elif b"Linux version" in b or b"Booting" in b:
    print(ts(), "[~] kernel booted but shell prompt not seen within window -- may just need more time, see", LOGPATH)
    sys.exit(8)
else:
    print(ts(), "[!] unexpected/silent -- see", LOGPATH)
    sys.exit(8)
