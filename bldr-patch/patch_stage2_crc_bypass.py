#!/usr/bin/env python3
"""Patch the MAIN-image raw-byte CRC compare in the T50 zloader stage2, on
top of the already-applied RSA bypass patch
(`bldr-patch/patch_stage2_rsa_bypass.py`).

Found via headless Ghidra decompilation of the decompressed stage2 blob
(loaded at VA 0x83fb0000) of the shared check function `FUN_83fb5384`
(param_1==0 -> MAIN, param_1==1 -> SLAVE):

    iVar4 = FUN_83fb4f50(data_ptr, data_len, 0)   # <-- JAMCRC computation
    if (iVar3 == iVar4):                          # <-- THIS is the compare
        print("Start to decrypt RSA!")            #     (RSA gate, separate,
    else:                                          #      already patched)
        print("crc check error!")

The MAIN compare branch sits at decompressed-stage2 offset 0x577c
(VA 0x83fb577c): `beq $s4,$v0,+6` (opcode bytes `12820006`), where $s4 is
the CRC stored in the header and $v0 is the just-computed result. The
target (`0x83fb5798`, the success path into the RSA step) matches Ghidra's
decompilation of the success branch exactly.

Patch: same technique as the RSA bypass -- zero the rs/rt register fields
so the branch becomes unconditional (`beq $zero,$zero,+6`, same offset so
same target), without changing instruction length or further control
flow: `12820006` -> `10000006`.

Deliberately left UNTOUCHED: the SLAVE branch of the same function (around
VA 0x83fb5be0) -- SLAVE stays the unmodified OEM recovery copy and keeps
its own crc/RSA gates as a safety net.

Input: an mtd0 dump that already has the RSA bypass patch applied, so the
end result contains both patches. Use a fresh dump of your own device's
mtd0 as the source.

Does NOT touch any device. Writes a local patched .bin only.
"""
import lzma
import sys
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Auto-pick the newest matching mtd0 dump as source (already RSA-patched)
# unless a path is given explicitly on the command line.
_candidates = sorted(glob.glob(os.path.join(REPO, "t50-mtd0-dump-live-*.bin")))
SRC = sys.argv[1] if len(sys.argv) > 1 else (_candidates[-1] if _candidates else None)
OUT = os.path.join(HERE, "mtd0-rsa-and-crc-bypass-patched.bin")

STAGE2_OFF = 0x10000          # compressed stage2 start in mtd0
STAGE2_COMP_LEN = 0x1f681 - 0x10000
TRAILER_OFF = 0x1ffdc         # unidentified 36-byte trailer (unpatched, copied as-is)

# Patch 1 (already present in the source file, only verified here, not
# re-applied): RSA signature compare.
RSA_PATCH_OFF = 0x57f4
RSA_PATCH_NEW = bytes.fromhex("10000121")

# Patch 2 (new, applied by this script): MAIN raw-byte CRC compare.
CRC_PATCH_OFF = 0x577c
CRC_PATCH_ORIG = bytes.fromhex("12820006")   # beq s4,v0,+6  (rs=$s4=20, rt=$v0=2)
CRC_PATCH_NEW = bytes.fromhex("10000006")    # beq zero,zero,+6 (same offset/target)


def main():
    if not SRC:
        sys.exit("no source mtd0 dump found -- dump your own mtd0 first, "
                  "or pass a path as an argument")
    d = bytearray(open(SRC, "rb").read())
    if len(d) != 0x40000:
        sys.exit(f"FATAL: {SRC} is {len(d):#x} bytes, expected exactly 0x40000 "
                  "(256KB) -- this doesn't look like a full mtd0/bootloader "
                  "dump. Re-dump per install-guide/README.md SS5.")
    print(f"source: {SRC}")

    comp_orig = bytes(d[STAGE2_OFF:STAGE2_OFF + STAGE2_COMP_LEN])
    try:
        dec = bytearray(lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(comp_orig))
    except lzma.LZMAError as e:
        sys.exit(f"FATAL: stage2 at offset {STAGE2_OFF:#x} is not valid LZMA "
                  f"data ({e}). This usually means {SRC} isn't the RSA-patched "
                  "output of patch_stage2_rsa_bypass.py -- run that script "
                  "first and pass its bldr-patch/mtd0-rsa-bypass-patched.bin "
                  "output here.")

    # Sanity: the RSA patch must already be present in the source (we don't
    # re-apply it here, only add the CRC compare patch on top).
    rsa_now = bytes(dec[RSA_PATCH_OFF:RSA_PATCH_OFF + 4])
    if rsa_now != RSA_PATCH_NEW:
        sys.exit(f"FATAL: RSA patch not present in source @0x{RSA_PATCH_OFF:x} "
                  f"(found {rsa_now.hex()}, expected {RSA_PATCH_NEW.hex()}) -- "
                  "use a source that already has the RSA patch applied")
    print(f"RSA patch confirmed present @0x{RSA_PATCH_OFF:x}: {rsa_now.hex()}")

    crc_now = bytes(dec[CRC_PATCH_OFF:CRC_PATCH_OFF + 4])
    if crc_now == CRC_PATCH_NEW:
        sys.exit("CRC patch already present in source -- nothing to do, stop")
    if crc_now != CRC_PATCH_ORIG:
        sys.exit(f"FATAL: patch site shifted, found {crc_now.hex()} "
                  f"@0x{CRC_PATCH_OFF:x}, expected original {CRC_PATCH_ORIG.hex()}")

    dec[CRC_PATCH_OFF:CRC_PATCH_OFF + 4] = CRC_PATCH_NEW
    print(f"CRC patch applied @0x{CRC_PATCH_OFF:x}: {crc_now.hex()} -> {CRC_PATCH_NEW.hex()}")

    filt = [{"id": lzma.FILTER_LZMA1, "dict_size": 0x800000,
             "lc": 3, "lp": 0, "pb": 2, "mode": lzma.MODE_NORMAL,
             "nice_len": 273, "mf": lzma.MF_BT4, "depth": 0}]
    raw = lzma.compress(bytes(dec), format=lzma.FORMAT_RAW, filters=filt)
    header = bytes([0x5d]) + (0x800000).to_bytes(4, "little") + len(dec).to_bytes(8, "little")
    recomp = header + raw

    budget = TRAILER_OFF - STAGE2_OFF
    print(f"recompressed stage2: {len(recomp)} bytes (budget {budget}, orig {STAGE2_COMP_LEN})")
    if len(recomp) > budget:
        sys.exit("FATAL: recompressed stage2 does not fit before the trailer, abort")

    redec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(recomp)
    if (redec != bytes(dec)
            or redec[RSA_PATCH_OFF:RSA_PATCH_OFF + 4] != RSA_PATCH_NEW
            or redec[CRC_PATCH_OFF:CRC_PATCH_OFF + 4] != CRC_PATCH_NEW):
        sys.exit("FATAL: internal roundtrip mismatch, abort (this indicates "
                  "a bug in this script, not your input file -- please report it)")

    out = bytearray(d)
    out[STAGE2_OFF:STAGE2_OFF + STAGE2_COMP_LEN] = b"\x00" * STAGE2_COMP_LEN  # clear old region
    out[STAGE2_OFF:STAGE2_OFF + len(recomp)] = recomp

    assert len(out) == len(d)
    open(OUT, "wb").write(out)
    print(f"wrote {OUT} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
