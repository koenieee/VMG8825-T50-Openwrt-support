#!/usr/bin/env python3
"""Build-independent zloader stage2 patcher for the VMG8825-T50.

Older helpers (`patch_stage2_rsa_bypass.py`, `patch_stage2_crc_bypass.py`)
hard-coded the patch offsets of ONE zloader build. A tester with a different
build (banner "ZyXEL zloader v1.4.4 (01/14/2020)") hit
"FATAL: patch site moved" because the gate had moved AND used a different
register. That build also turned out to have NO RSA gate at all -- only the
CRC check needed bypassing.

This script locates the gates by a build-independent landmark instead of a
fixed offset: the human-readable failure strings that the boot checker prints.
For each gate it

  1. finds the "... crc check error" / RSA failure string in the decompressed
     stage2, computes its load VA (base 0x83fb0000),
  2. finds the `lui/addiu` pair that materialises that VA into $a0 (the
     failure-print setup), and
  3. takes the `beq` that guards it (the compare whose taken-branch skips the
     print) -- for CRC that is a `beq $sX, $v0, ...` right before the print,
     where $v0 holds the freshly computed JAMCRC result.

It then neutralises the guard by zeroing the two register fields
(`beq $zero,$zero,imm`, same immediate so same target, same length), exactly
the technique the hand-written per-build scripts used, and reports whether an
RSA gate was present at all (CRC-only vs CRC+RSA build).

The SLAVE image's own gates are deliberately NEVER touched -- SLAVE stays the
unmodified OEM recovery copy as a safety net.

Safe by default: if a landmark cannot be found, or the guard it lands on does
not look like a neutralisable compare, the script refuses to write anything
and tells you to re-disassemble your own dump. It never writes to a device.

Usage:
    python3 patch_stage2.py <mtd0-dump.bin> [-o out.bin] [--dry-run]

<mtd0-dump.bin> must be a raw 256 KiB dump of your own unit's mtd0/bootloader.
"""
import argparse
import lzma
import re
import sys

STAGE2_OFF = 0x10000                 # compressed stage2 start in mtd0
STAGE2_COMP_LEN = 0x1F681 - 0x10000  # compressed region length
TRAILER_OFF = 0x1FFDC                # 36-byte trailer, copied as-is
BASE_VA = 0x83FB0000                 # stage2 load VA
MTD0_LEN = 0x40000

OP_BEQ = 0x04
OP_LUI = 0x0F
OP_ADDIU = 0x09
REG_V0 = 2


def _w(b, i):
    return int.from_bytes(b[i:i + 4], "big")


def _decode_beq(w):
    if (w >> 26) & 0x3F != OP_BEQ:
        return None
    return (w >> 21) & 0x1F, (w >> 16) & 0x1F, w & 0xFFFF  # rs, rt, imm


def _neutralise_beq(w):
    """beq rs,rt,imm -> beq $zero,$zero,imm (unconditional, same target)."""
    return (OP_BEQ << 26) | (w & 0xFFFF)


def _is_neutralised_beq(w):
    return (w >> 26) & 0x3F == OP_BEQ and (w >> 16) & 0x3FF == 0  # rs==rt==0


def _find_lui_addiu_for_va(dec, va):
    """Return list of (lui_off, addiu_off, reg) materialising `va`."""
    hits = []
    for i in range(0, len(dec) - 4, 4):
        w = _w(dec, i)
        if (w >> 26) & 0x3F != OP_LUI:
            continue
        reg = (w >> 16) & 0x1F
        hi = w & 0xFFFF
        for k in range(1, 7):
            j = i + 4 * k
            if j + 4 > len(dec):
                break
            w2 = _w(dec, j)
            if (w2 >> 26) & 0x3F == OP_ADDIU and (w2 >> 21) & 0x1F == reg and (w2 >> 16) & 0x1F == reg:
                lo = w2 & 0xFFFF
                lo_s = lo - 0x10000 if lo & 0x8000 else lo
                if ((hi << 16) + lo_s) & 0xFFFFFFFF == va:
                    hits.append((i, j, reg))
                break
    return hits


def _locate_guard(dec, string_regex, label, want_v0=True):
    """Find the beq that guards the failure-print for a given error string.

    Returns (off, orig_word, already_patched) or None.
    """
    m = re.search(string_regex, dec)
    if not m:
        print(f"  {label}: failure string not found -> gate absent in this build")
        return None
    sva = BASE_VA + m.start()
    refs = _find_lui_addiu_for_va(dec, sva)
    if not refs:
        print(f"  {label}: string @0x{m.start():05x} but no lui/addiu loads its VA "
              f"0x{sva:08x} -> cannot anchor, skipping")
        return None
    # Pick the ref whose preceding window holds a usable guard beq closest to it.
    for lui_off, _addiu_off, _reg in refs:
        best = None
        for back in range(1, 6):
            bi = lui_off - 4 * back
            if bi < 0:
                break
            w = _w(dec, bi)
            dec_beq = _decode_beq(w)
            if dec_beq is None:
                continue
            rs, rt, _imm = dec_beq
            if _is_neutralised_beq(w):
                best = (bi, w, True)
                break
            if not want_v0 or rs == REG_V0 or rt == REG_V0:
                best = (bi, w, False)
                break
        if best:
            return best
    print(f"  {label}: found failure-print but no neutralisable guard beq before it")
    return None


def _recompress(dec):
    filt = [{"id": lzma.FILTER_LZMA1, "dict_size": 0x800000,
             "lc": 3, "lp": 0, "pb": 2, "mode": lzma.MODE_NORMAL,
             "nice_len": 273, "mf": lzma.MF_BT4, "depth": 0}]
    raw = lzma.compress(bytes(dec), format=lzma.FORMAT_RAW, filters=filt)
    header = bytes([0x5D]) + (0x800000).to_bytes(4, "little") + len(dec).to_bytes(8, "little")
    recomp = header + raw
    budget = TRAILER_OFF - STAGE2_OFF
    if len(recomp) > budget:
        sys.exit(f"FATAL: recompressed stage2 {len(recomp)} > budget {budget}, abort")
    # Bound the verify to the known size: a FORMAT_RAW LZMA1 end-of-stream
    # marker wrapped in an ALONE header trips strict xz builds even though
    # every decoded byte is correct.
    redec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(recomp, max_length=len(dec))
    if redec != bytes(dec):
        sys.exit("FATAL: internal roundtrip mismatch, abort (script bug, not your input)")
    return recomp


def main():
    ap = argparse.ArgumentParser(description="Build-independent zloader stage2 CRC/RSA bypass patcher")
    ap.add_argument("src", help="raw 256 KiB mtd0/bootloader dump of YOUR unit")
    ap.add_argument("-o", "--out", help="output path (default: <src>-stage2-patched.bin)")
    ap.add_argument("--dry-run", action="store_true", help="locate and report gates, write nothing")
    args = ap.parse_args()

    d = bytearray(open(args.src, "rb").read())
    if len(d) != MTD0_LEN:
        sys.exit(f"FATAL: {args.src} is {len(d):#x} bytes, expected {MTD0_LEN:#x} (256 KiB) "
                 "-- this is not a full mtd0/bootloader dump.")
    try:
        dec = bytearray(lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(
            bytes(d[STAGE2_OFF:STAGE2_OFF + STAGE2_COMP_LEN])))
    except lzma.LZMAError as e:
        sys.exit(f"FATAL: stage2 at {STAGE2_OFF:#x} is not valid LZMA ({e}).")
    print(f"source: {args.src}  (stage2 decompressed {len(dec):#x} bytes)")

    # MAIN CRC gate (mandatory on every known build).
    crc = _locate_guard(dec, rb"\r\n main tclinux\.bin crc check error", "MAIN CRC gate", want_v0=True)
    # RSA gate (present on some builds only). Anchored on the hash-mismatch
    # failure string that the RSA step prints; its load setup sits right after
    # the RSA guard beq ("beq $v0,$zero,ok"). want_v0=False because the guard
    # compares $v0 to $zero as rs, and on an already-patched dump neither field
    # is $v0 anymore.
    rsa = _locate_guard(dec, rb"Wrong image hash value", "MAIN RSA gate", want_v0=False)

    if crc is None:
        sys.exit("FATAL: could not locate the MAIN CRC gate -- refusing to guess. "
                 "Re-disassemble your own decompressed stage2 (load VA 0x83fb0000) before patching.")

    changed = False
    for off, w, done, name in (
        (crc[0], crc[1], crc[2], "CRC"),
        *(((rsa[0], rsa[1], rsa[2], "RSA"),) if rsa else ()),
    ):
        va = BASE_VA + off
        if done:
            print(f"  {name} gate @0x{off:05x} (VA 0x{va:08x}) already neutralised "
                  f"({w.to_bytes(4,'big').hex()}) -- nothing to do")
            continue
        new = _neutralise_beq(w)
        dec[off:off + 4] = new.to_bytes(4, "big")
        changed = True
        print(f"  {name} gate @0x{off:05x} (VA 0x{va:08x}): "
              f"{w.to_bytes(4,'big').hex()} -> {new.to_bytes(4,'big').hex()} "
              f"(beq -> unconditional, same target)")

    print("build type:", "CRC+RSA" if rsa else "CRC-only (no RSA gate found)")

    if args.dry_run:
        print("dry-run: no file written")
        return
    if not changed:
        print("nothing to patch (already bypassed); no file written")
        return

    recomp = _recompress(dec)
    out = bytearray(d)
    out[STAGE2_OFF:STAGE2_OFF + STAGE2_COMP_LEN] = b"\x00" * STAGE2_COMP_LEN
    out[STAGE2_OFF:STAGE2_OFF + len(recomp)] = recomp
    assert len(out) == len(d)
    outpath = args.out or (args.src.rsplit(".", 1)[0] + "-stage2-patched.bin")
    open(outpath, "wb").write(out)
    print(f"wrote {outpath} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
