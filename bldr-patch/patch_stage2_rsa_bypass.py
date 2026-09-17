#!/usr/bin/env python3
"""Patch the kernel/TRX RSA-SHA256 signature compare in the T50 zloader stage2
to an unconditional pass, and repack it back into a full mtd0-sized image.

Does NOT touch any device. Writes a local patched .bin only.
"""
import lzma
import sys

SRC = "firmware/extracted/bootloader.bin"   # full 256KB mtd0 dump
OUT = "bldr-patch/mtd0-rsa-bypass-patched.bin"

STAGE2_OFF = 0x10000          # compressed stage2 start in mtd0
STAGE2_COMP_LEN = 0x1f681 - 0x10000
TRAILER_OFF = 0x1ffdc         # unidentified 36-byte trailer (unpatched, copied as-is)
PATCH_OFF = 0x57f4            # VA 0x83fb57f4, inside decompressed stage2
PATCH_ORIG = bytes.fromhex("10400121")   # beq v0,zero,0x83fb5c7c
PATCH_NEW = bytes.fromhex("10000121")    # beq zero,zero,... (unconditional)


def main():
    d = bytearray(open(SRC, "rb").read())
    assert len(d) == 0x40000, f"unexpected mtd0 size {len(d):#x}"

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
