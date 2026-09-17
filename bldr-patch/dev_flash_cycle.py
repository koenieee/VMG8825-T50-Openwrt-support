#!/usr/bin/env python3
"""One-command dev loop for VMG8825-T50 OpenWrt kernel/image iteration:
build (optional) -> power-cycle (see power_cycle.py -- manual by default,
optional Home Assistant backend) -> ATSE/ATEN debug-unlock (fresh every
run; unnecessary if your bootloader has the RSA+CRC patches from
BOOTLOADER-PATCH.md installed, harmless to run anyway) -> flash MAIN via
ATER/ATWF (NOT ATUR, which silently no-ops after first use) -> ATGO ->
parse the boot console -> auto-recover a stuck boot flag once -> clear
PASS/FAIL verdict.

Safety invariants preserved from flash_mtd3_via_ater_atwf.py /
restore_slave_from_dump.py, unchanged and re-asserted here:
  - ONLY mtd3 (tclinux/MAIN, blocks 4-451 inclusive) is ever erased/written.
  - tclinux_slave is never referenced by any erase/write call in this file.

Usage:
    # Wrap + flash + boot-test an existing slim OpenWrt build:
    python3 dev_flash_cycle.py --slim \
        openwrt/bin/targets/econet/en751627/openwrt-econet-en751627-zyxel_vmg8825-t50-squashfs-tclinux.trx

    # Flash + boot-test an already-era-wrapped image (skip build_era_trx.py):
    python3 dev_flash_cycle.py --era vmg8825-t50-era.bin

    # Only re-run the boot-test against whatever is currently in MAIN
    # (no flash step -- useful for re-checking the boot flag / retrying ATGO):
    python3 dev_flash_cycle.py --boot-only
"""
import argparse
import hashlib
import os
import re
import select
import subprocess
import sys
import termios
import time

import power_cycle

DEV = "/dev/ttyUSB0"
MODEL = "VMG8825-T50"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ATENV3 = os.path.join(REPO, "tools/atenv3/atenv3_passwd")
BUILD_ERA_TRX = os.path.join(REPO, "build_era_trx.py")
SERVER = "192.168.1.1"

RAM_ADDR = 0x80020000
MTD3_OFF = 0x80000
MTD3_BLK_LO = 4
MTD3_BLK_HI = 451  # INCLUSIVE. Re-derive (do not copy) if your unit's layout differs.
assert (MTD3_BLK_HI + 1 - MTD3_BLK_LO) * 0x20000 == 0x3800000, "block math sanity check failed"
assert (MTD3_BLK_HI + 1) * 0x20000 == 0x3880000, "must land exactly at mtd5 (tclinux_slave) boundary, not past it"
MTD3_MAX_LEN = (MTD3_BLK_HI + 1) * 0x20000 - MTD3_OFF

t0 = time.time()
def ts(): return "%6.2fs" % (time.time() - t0)


def die(msg, rc):
    print("\n[!] " + msg)
    sys.exit(rc)


class Serial:
    def __init__(self, dev=DEV):
        self.fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(self.fd)
        a[0] = a[1] = a[3] = 0
        a[4] = a[5] = termios.B115200
        a[6][termios.VMIN] = 0
        a[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        logpath = os.path.join(HERE, "dev_flash_cycle_%d.log" % int(time.time()))
        self.logf = open(logpath, "wb")
        print(ts(), "[+] full raw session log: %s" % logpath)

    def send(self, s):
        for c in s.encode():
            os.write(self.fd, bytes([c])); time.sleep(0.02)
        os.write(self.fd, b"\r"); time.sleep(0.05)

    def pump(self, t, tag="", until=None):
        buf = b""; end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if r:
                try:
                    d = os.read(self.fd, 4096)
                except OSError:
                    d = b""
                if d:
                    buf += d
                    self.logf.write(d); self.logf.flush()
                    sys.stdout.write(d.decode(errors="replace")); sys.stdout.flush()
                    if until and until.encode() in buf:
                        break
        print("\n[%s pump done, tag=%s, got %d bytes]" % (ts(), tag, len(buf)))
        return buf

    def pump_until_settled(self, max_t, settle_markers, quiet_s=8, tag=""):
        """Like pump(), but stops early once any of settle_markers has been
        seen AND the line has then gone quiet for quiet_s seconds -- avoids
        sitting through the full max_t just to let post-boot wifi-auth-retry
        noise scroll by. Still hard-caps at max_t either way."""
        buf = b""; end = time.time() + max_t
        seen_marker_at = None
        last_data = time.time()
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            now = time.time()
            if r:
                try:
                    d = os.read(self.fd, 4096)
                except OSError:
                    d = b""
                if d:
                    buf += d
                    last_data = now
                    self.logf.write(d); self.logf.flush()
                    sys.stdout.write(d.decode(errors="replace")); sys.stdout.flush()
                    if seen_marker_at is None and any(m.encode() in buf for m in settle_markers):
                        seen_marker_at = now
            if seen_marker_at is not None and (now - last_data) >= quiet_s:
                break
        print("\n[%s pump_until_settled done, tag=%s, got %d bytes, early=%s]"
              % (ts(), tag, len(buf), seen_marker_at is not None))
        return buf

    def catch_zhal(self, window):
        end = time.time() + window; got = False
        buf = b""
        while time.time() < end and not got:
            os.write(self.fd, b"\r")
            r, _, _ = select.select([self.fd], [], [], 0.1)
            if r:
                try:
                    d = os.read(self.fd, 4096)
                except OSError:
                    d = b""
                if d:
                    buf += d
                    self.logf.write(d); self.logf.flush()
                    sys.stdout.write(d.decode(errors="replace")); sys.stdout.flush()
                    if b"ZHAL>" in buf:
                        got = True
        return buf if got else None

    def close(self):
        os.close(self.fd)
        self.logf.close()


def unlock(ser):
    print(ts(), "### ATSE %s (debug-unlock seed)" % MODEL)
    ser.send("ATSE %s" % MODEL)
    b = ser.pump(3, "atse")
    m = re.search(rb"([0-9A-Fa-f]{36})", b)
    if not m:
        die("no seed found in ATSE response", 3)
    seed = m.group(1).decode().upper()
    pw = subprocess.run([ATENV3, seed], capture_output=True, text=True).stdout.strip()
    print(ts(), "[+] seed=%s password=%s" % (seed, pw))
    if not pw or not pw.isdigit():
        die("no usable password derived from seed", 4)
    ser.send("ATEN 1,%s" % pw)
    b = ser.pump(2.5, "aten").lower()
    if b"incorrect" in b or b"invalid" in b or b"wrong" in b:
        die("ATEN rejected -- unlock failed", 5)
    print(ts(), "[+] ATEN unlock OK")


def flash_main(ser, image_path):
    fw_bytes = open(image_path, "rb").read()
    fw_len = len(fw_bytes)
    fw_md5 = hashlib.md5(fw_bytes).hexdigest()
    if fw_len > MTD3_MAX_LEN:
        die("image (%d bytes) too big for mtd3 (%d bytes)" % (fw_len, MTD3_MAX_LEN), 6)
    fw_name = "devfw.bin"
    print(ts(), "### ATLD %s -> RAM 0x%x (%d bytes, md5 %s)" % (fw_name, RAM_ADDR, fw_len, fw_md5))
    ser.send("ATLD %s" % fw_name)
    ser.pump(2, "atld-cmd")
    r = subprocess.run(["atftp", "--put", "--local-file", image_path, "--remote-file", fw_name, SERVER],
                        capture_output=True, text=True, timeout=180)
    print(ts(), "atftp rc:", r.returncode, (r.stdout + r.stderr).strip()[:200])
    b = ser.pump(20, "tftp-confirm")
    if b"File download" not in b:
        die("no TFTP confirmation for image", 7)

    print(ts(), "### ATER %d,%d (erase mtd3 ONLY: 0x%x..0x%x)" % (MTD3_BLK_LO, MTD3_BLK_HI, MTD3_OFF, (MTD3_BLK_HI + 1) * 0x20000))
    ser.send("ATER %d,%d" % (MTD3_BLK_LO, MTD3_BLK_HI))
    b = ser.pump(60, "ater", "ZHAL>")
    if b"ZHAL>" not in b:
        die("no ZHAL> after ATER -- STOP, investigate before writing anything", 8)

    print(ts(), "### ATWF 0x%x,0x%x,0x%x (RAM -> flash, mtd3 body)" % (RAM_ADDR, MTD3_OFF, fw_len))
    ser.send("ATWF 0x%x,0x%x,0x%x" % (RAM_ADDR, MTD3_OFF, fw_len))
    b = ser.pump(30, "atwf", "ZHAL>")
    if b"ZHAL>" not in b:
        die("no ZHAL> after ATWF -- STOP, investigate before rebooting", 9)
    print(ts(), "[+] mtd3 flashed (%d bytes)" % fw_len)


def fix_boot_flag(ser):
    print(ts(), "### boot flag stuck on slave -- power-cycling back to ZHAL> to fix it")
    if power_cycle.power_cycle_and_catch_zhal(lambda: ser.catch_zhal(90)) is None:
        die("no ZHAL> within 90s while trying to fix a stuck boot flag", 11)
    ser.send(""); ser.pump(1.0)
    unlock(ser)
    ser.send("ATBT 1")
    ser.pump(2.0, "atbt")
    ser.send("ATSW")
    ser.pump(3.0, "atsw")


def boot_and_analyze(ser, allow_flag_fix=True):
    print(ts(), "### ATGO -- capturing boot console")
    ser.send("ATGO")
    # Settle on any definitive end-state: successfully mounted root, a reached
    # shell/login, or a kernel panic -- whichever comes first, then a short
    # quiet window (wifi-auth-retry noise keeps the line "active" for ~130s
    # otherwise even though the outcome is already known by ~15-20s in).
    settle_on = ("Mounted root", "root@OpenWrt", "login:", "Kernel panic")
    full = ser.pump_until_settled(150, settle_on, quiet_s=8, tag="atgo").decode(errors="replace")

    flags = re.findall(r"==> boot flag = (\d+)", full)
    booted_main = "from main" in full
    booted_slave = "from slave" in full
    hash_fail = "Wrong image hash" in full
    panic = "Kernel panic" in full
    mounted = "Mounted root" in full
    shell = "root@OpenWrt" in full or "login:" in full

    print("\n========== BOOT ANALYSIS ==========")
    print("boot flag occurrences:", flags)
    print("booted_main=%s booted_slave=%s hash_fail=%s panic=%s mounted=%s shell=%s"
          % (booted_main, booted_slave, hash_fail, panic, mounted, shell))

    if booted_slave and allow_flag_fix:
        print("[!] fell back to SLAVE -- attempting one automatic boot-flag fix + retry")
        fix_boot_flag(ser)
        unlock(ser)  # fresh unlock right before the retry ATGO
        return boot_and_analyze(ser, allow_flag_fix=False)

    ok = booted_main and mounted and not panic and not hash_fail
    print("VERDICT: %s" % ("PASS" if ok else "FAIL"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slim", help="slim OpenWrt .trx to wrap via build_era_trx.py before flashing")
    ap.add_argument("--era", help="already era-wrapped image to flash directly (skips build step)")
    ap.add_argument("--boot-only", action="store_true", help="skip flashing, just power-cycle + ATGO + analyze")
    ap.add_argument("--off-seconds", type=int, default=15)
    args = ap.parse_args()

    if not args.boot_only and not args.slim and not args.era:
        die("need --slim, --era, or --boot-only", 1)

    image_path = args.era
    if args.slim:
        image_path = os.path.join(HERE, "dev-flash-era.bin")
        print(ts(), "### building era image from %s" % args.slim)
        r = subprocess.run([sys.executable, BUILD_ERA_TRX, "--slim", args.slim, "-o", image_path])
        if r.returncode != 0:
            die("build_era_trx.py failed (rc=%d)" % r.returncode, 10)

    ser = Serial()

    def do_cycle():
        return ser.catch_zhal(90)

    print(ts(), "### power-cycling router and catching ZHAL>")
    if power_cycle.power_cycle_and_catch_zhal(do_cycle, off_seconds=args.off_seconds) is None:
        die("no ZHAL> within 90s of power-on", 2)
    print("\n%s [+] ZHAL> reached" % ts())
    ser.send(""); ser.pump(1.0)

    if not args.boot_only:
        unlock(ser)
        flash_main(ser, image_path)

    unlock(ser)  # fresh unlock right before ATGO -- required every boot, see README §4
    ok = boot_and_analyze(ser)
    ser.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
