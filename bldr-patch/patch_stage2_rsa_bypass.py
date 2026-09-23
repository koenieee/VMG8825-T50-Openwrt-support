#!/usr/bin/env python3
"""Patch the kernel/TRX RSA-SHA256 signature compare in the T50 zloader stage2
to an unconditional pass, and repack it back into a full mtd0-sized image.

Input: a raw 256KB dump of YOUR OWN device's mtd0/bootloader partition
(see install-guide/README.md SS5a for how to take it). This project ships
no prebuilt bootloader binary -- you always start from your own dump so
your unit's own board-info block (MAC/serial) is preserved untouched.

Does NOT touch any device. Writes a local patched .bin only.
"""
import glob
import os
import lzma
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Auto-pick the newest matching mtd0 dump as source unless a path is given
# explicitly on the command line.
_candidates = sorted(glob.glob(os.path.join(REPO, "t50-mtd0-dump-live-*.bin")))
SRC = sys.argv[1] if len(sys.argv) > 1 else (_candidates[-1] if _candidates else None)
OUT = os.path.join(HERE, "mtd0-rsa-bypass-patched.bin")

STAGE2_OFF = 0x10000          # compressed stage2 start in mtd0
STAGE2_COMP_LEN = 0x1f681 - 0x10000
TRAILER_OFF = 0x1ffdc         # unidentified 36-byte trailer (unpatched, copied as-is)
PATCH_OFF = 0x57f4            # VA 0x83fb57f4, inside decompressed stage2
PATCH_ORIG = bytes.fromhex("10400121")   # beq v0,zero,0x83fb5c7c
PATCH_NEW = bytes.fromhex("10000121")    # beq zero,zero,... (unconditional)


def main():
    if not SRC:
        sys.exit("no source mtd0 dump found -- dump your own mtd0/bootloader "
                  "partition first (install-guide/README.md SS5a), or pass "
                  "a path as an argument")
    d = bytearray(open(SRC, "rb").read())
    assert len(d) == 0x40000, f"unexpected mtd0 size {len(d):#x}"
    print(f"source: {SRC}")

    comp_orig = bytes(d[STAGE2_OFF:STAGE2_OFF + STAGE2_COMP_LEN])
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(comp_orig)

    off = dec.find(PATCH_ORIG, PATCH_OFF - 4, PATCH_OFF + 4)
    assert off == PATCH_OFF, f"patch site moved, found at {off:#x} expected {PATCH_OFF:#x}"

    patched = bytearray(dec)
    patched[PATCH_OFF:PATCH_OFF + 4] = PATCH_NEW

    filt = [{"id": lzma.FILTER_LZMA1, "dict_size": 0x800000,
             "lc": 3, "lp": 0, "pb": 2, "mode": lzma.MODE_NORMAL,
             "nice_len": 273, "mf": lzma.MF_BT4, "depth": 0}]
    raw = lzma.compress(bytes(patched), format=lzma.FORMAT_RAW, filters=filt)
    header = bytes([0x5d]) + (0x800000).to_bytes(4, "little") + len(patched).to_bytes(8, "little")
    recomp = header + raw

    budget = TRAILER_OFF - STAGE2_OFF
    print(f"recompressed stage2: {len(recomp)} bytes (budget {budget}, orig {STAGE2_COMP_LEN})")
    if len(recomp) > budget:
        sys.exit("FATAL: recompressed stage2 does not fit before the trailer, abort")

    redec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(recomp)
    assert redec == bytes(patched), "roundtrip mismatch, abort"

    out = bytearray(d)
    out[STAGE2_OFF:STAGE2_OFF + STAGE2_COMP_LEN] = b"\x00" * STAGE2_COMP_LEN  # clear old region
    out[STAGE2_OFF:STAGE2_OFF + len(recomp)] = recomp
    # bytes from end of recomp up to TRAILER_OFF stay zero (matches original padding style)
    # trailer (0x1ffdc:0x20000) and everything else untouched/unexplained -> left as-is

    assert len(out) == len(d)
    open(OUT, "wb").write(out)
    print(f"wrote {OUT} ({len(out)} bytes)")
    print(f"patch site VA 0x83fb{PATCH_OFF:04x}: {PATCH_ORIG.hex()} -> {PATCH_NEW.hex()}")


if __name__ == "__main__":
    main()
