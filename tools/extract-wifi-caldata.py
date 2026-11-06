#!/usr/bin/env python3
"""Extract per-radio MT7615 WiFi calibration from a VMG8825-T50 full-flash
dump by SIGNATURE SCAN, so the offset does not have to be hard-coded.

Why this exists
---------------
The board DTS reads each radio's calibration from the `wifi-caldata`
partition at a *fixed* flash offset (radio0 @0xeae0000, radio1 @0xeb00000).
That offset is not the same on every unit: a tester's VMG8825-T50 had its
two calibration blocks one 128 KiB block earlier (@0xeac0000 / @0xeae0000),
so each radio loaded the wrong card's data and both came up at ~6-8 dBm.

This tool does not trust a fixed offset. It scans a whole-flash dump for the
MT7615 EEPROM signature (chip-id 0x7615, little-endian `15 76`, at the start
of a 0x4da8-byte block), identifies which block is the 2.4 GHz (TSSI) card
and which is the 5 GHz (external-PA) card from their calibration bytes, and
writes out:

  * the raw per-radio blobs (<out>/eeprom_radio0.bin, eeprom_radio1.bin), and
  * a devicetree include (<out>/<name>-unit-eeprom.dtsi) that feeds each
    blob to the mt76 driver inline via `mediatek,eeprom-data` -- the fix a
    tester verified on hardware (radios went to 20 dBm / 23 dBm).

It never touches a device and never flashes anything -- it only reads your
dump file and writes local output.

Get the dump first (install guide S5, from the RAM shell):
    nanddump -f /mnt/mtd-full.bin /dev/mtd0     # whole 256 MiB device

Usage:
    python3 tools/extract-wifi-caldata.py mtd-full.bin [-o outdir] [--name NAME]
    python3 tools/extract-wifi-caldata.py mtd-full.bin --scan-only   # just report

The generated .dtsi is specific to YOUR unit (it embeds that unit's MAC and
per-chain trim). Keep it out of git and build/flash it only for that router.
"""
import argparse
import os
import sys

SIG = b"\x15\x76"       # MT7615 chip-id 0x7615, little-endian, at block offset 0
BLOCK_LEN = 0x4da8      # MT7615 EEPROM block size (19880 bytes)
SCAN_STRIDE = 0x800     # calibration blocks are block-aligned well above this
# Calibration bytes used to sanity-check a block and tell the two cards apart.
OFF_2G = 0x58           # 2.4 GHz target power (TSSI card is non-trivial here)
OFF_5G = 0x70           # 5 GHz target power
OFF_F2 = 0xf2           # external-PA 2.4 GHz byte (0 on the 5 GHz ext-PA card)
OFF_F3 = 0xf3           # external-PA 5 GHz byte (non-zero only on the 5 GHz card)


def scan(dump):
    """Return a list of dicts for every distinct MT7615 block found."""
    found = []
    last_end = -1
    for o in range(0, len(dump) - BLOCK_LEN, SCAN_STRIDE):
        if dump[o:o + 2] != SIG:
            continue
        if o < last_end:            # inside a block we already took
            continue
        blk = dump[o:o + BLOCK_LEN]
        # A real block is not all-0x00 / all-0xff past the header.
        body = blk[2:]
        if body.count(0x00) > BLOCK_LEN - 16 or body.count(0xff) > BLOCK_LEN - 16:
            continue
        found.append({
            "off": o,
            "b2g": blk[OFF_2G], "b5g": blk[OFF_5G],
            "f2": blk[OFF_F2], "f3": blk[OFF_F3],
            "blk": blk,
        })
        last_end = o + BLOCK_LEN
    return found


def classify(found):
    """Map found blocks to radio0 (2.4 GHz TSSI) and radio1 (5 GHz ext-PA).

    Discriminator: the 5 GHz external-PA card carries a non-zero byte at
    0xf3; the 2.4 GHz TSSI card has 0x00 there. Returns (radio0, radio1) or
    raises on ambiguity.
    """
    r0 = [b for b in found if b["f3"] == 0]   # 2.4 GHz / TSSI
    r1 = [b for b in found if b["f3"] != 0]   # 5 GHz / external-PA
    if len(r0) == 1 and len(r1) == 1:
        return r0[0], r1[0]
    # Fallback: if exactly two blocks, the lower-offset one is usually radio0.
    if len(found) == 2:
        a, b = sorted(found, key=lambda x: x["off"])
        return a, b
    raise SystemExit(
        f"cannot unambiguously classify radios: found {len(found)} blocks "
        f"(f3==0: {len(r0)}, f3!=0: {len(r1)}). Inspect the --scan-only report "
        "and pick the two real per-radio blocks by hand.")


def fmt_blob(macro, blk):
    rows = []
    for i in range(0, len(blk), 12):
        rows.append("\t" + " ".join(f"0x{x:02x}" for x in blk[i:i + 12]))
    body = " \\\n".join(rows)
    return f"#define {macro} \\\n{body}\n"


def write_dtsi(path, name, r0, r1):
    hdr = (
        "/*\n"
        f" * Per-unit MT7615 calibration for one specific VMG8825-T50, extracted\n"
        f" * by signature scan from that unit's full-flash dump. DO NOT COMMIT and\n"
        f" * DO NOT flash onto a different router: it embeds this unit's MAC and\n"
        f" * per-chain TX trim.\n"
        f" *\n"
        f" * radio0 (2.4 GHz, TSSI)       found at flash offset 0x{r0['off']:x}\n"
        f" * radio1 (5 GHz, external-PA)  found at flash offset 0x{r1['off']:x}\n"
        f" *\n"
        f" * Wire it in (mutually exclusive with the nvmem-cells caldata path --\n"
        f" * mt76_get_of_eeprom() checks inline `mediatek,eeprom-data` first).\n"
        f" * At the top of the board .dts:  #include \"{name}-unit-eeprom.dtsi\"\n"
        f" * then in each wifi node, replacing its nvmem-cells/-cell-names:\n"
        f" *   mediatek,eeprom-data = /bits/ 8 <UNIT_EEPROM_RADIO0>;   (&slot0 wifi@0,0)\n"
        f" *   mediatek,eeprom-data = /bits/ 8 <UNIT_EEPROM_RADIO1>;   (&slot1 wifi@0,0)\n"
        " */\n\n"
    )
    out = hdr + fmt_blob("UNIT_EEPROM_RADIO0", r0["blk"]) + "\n" \
              + fmt_blob("UNIT_EEPROM_RADIO1", r1["blk"])
    open(path, "w").write(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", help="whole-flash dump of YOUR unit (nanddump /dev/mtd0)")
    ap.add_argument("-o", "--outdir", default="wifi-caldata-out",
                    help="output directory (default: wifi-caldata-out)")
    ap.add_argument("--name", default="en751627_zyxel_vmg8825-t50",
                    help="basename for the generated .dtsi")
    ap.add_argument("--scan-only", action="store_true",
                    help="report found blocks and exit, write nothing")
    args = ap.parse_args()

    dump = open(args.dump, "rb").read()
    if len(dump) < BLOCK_LEN:
        sys.exit(f"FATAL: {args.dump} is only {len(dump)} bytes -- not a flash dump.")
    print(f"scanning {args.dump} ({len(dump) / 2**20:.0f} MiB) for MT7615 blocks...")

    found = scan(dump)
    if not found:
        sys.exit("FATAL: no MT7615 calibration block (sig 15 76, 0x4da8) found. "
                 "Is this a full-flash dump (nanddump /dev/mtd0), not a single "
                 "partition?")
    for b in found:
        print(f"  block @0x{b['off']:08x}  2g@0x58=0x{b['b2g']:02x} "
              f"5g@0x70=0x{b['b5g']:02x} f2=0x{b['f2']:02x} f3=0x{b['f3']:02x}")

    r0, r1 = classify(found)
    print(f"\nradio0 (2.4 GHz, TSSI)      -> 0x{r0['off']:08x}")
    print(f"radio1 (5 GHz, external-PA) -> 0x{r1['off']:08x}")
    # Compare against the DTS's current hard-coded offsets for a quick verdict.
    DT_R0, DT_R1 = 0xeae0000, 0xeb00000
    if (r0["off"], r1["off"]) == (DT_R0, DT_R1):
        print("note: matches the DTS's hard-coded offsets -- your radios should "
              "already calibrate correctly; weak TX is likely something else.")
    else:
        print(f"note: DIFFERS from the DTS's hard-coded 0x{DT_R0:x}/0x{DT_R1:x} -- "
              "this is why radios come up weak; use the generated .dtsi.")

    if args.scan_only:
        return

    os.makedirs(args.outdir, exist_ok=True)
    open(os.path.join(args.outdir, "eeprom_radio0.bin"), "wb").write(r0["blk"])
    open(os.path.join(args.outdir, "eeprom_radio1.bin"), "wb").write(r1["blk"])
    dtsi = os.path.join(args.outdir, f"{args.name}-unit-eeprom.dtsi")
    write_dtsi(dtsi, args.name, r0, r1)
    print(f"\nwrote {args.outdir}/eeprom_radio0.bin, eeprom_radio1.bin")
    print(f"wrote {dtsi}")
    print("Next: see tools/README or install-guide WiFi section to wire the "
          ".dtsi into the board DTS and rebuild (do not commit the .dtsi).")


if __name__ == "__main__":
    main()
