# Bootloader patch: RSA + CRC gate bypass in zloader stage2 (mtd0)

Technical reference for the patch shipped as
`firmware/vmg8825-t50-bootloader-patched.bin`. For flashing instructions
see `install-guide/README.md` §4.

## What the patch does

The zloader (v1.4.4, `01/04/2021 - 14:53:34`) has one shared check
function for both MAIN (`tclinux`/mtd3) and SLAVE (`tclinux_slave`/mtd5)
on every boot:

```
iVar4 = JAMCRC(payload, len, 0)        // computed checksum
if (stored_crc == iVar4):              // <-- CRC GATE
    print "Start to decrypt RSA!"
    ... RSA signature comparison ...   // <-- RSA GATE
    if (mismatch): "Wrong image hash value ...!"
else:
    print "crc check error!"
```

Failing either gate permanently sets the boot flag to 1 ("boot slave"),
which then survives every following reboot until explicitly reset.

Two independent 1-instruction MIPS patches, both inside the LZMA-
compressed stage2 blob of mtd0 (offset `0x10000`-`0x1f681` in the 256KB
mtd0 image), both following the same pattern: zero out a `beq` branch's
`rs`/`rt` register fields so it becomes unconditional, without touching
the offset/target:

| Patch | Decompressed-stage2 offset | VA | Original | Patched |
|---|---|---|---|---|
| RSA signature compare | `0x57f4` | `0x83fb57f4` | `10400121` (`beq v0,zero,...`) | `10000121` (`beq zero,zero,...`) |
| CRC compare (MAIN branch) | `0x577c` | `0x83fb577c` | `12820006` (`beq s4,v0,+6`) | `10000006` (`beq zero,zero,+6`) |

The SLAVE branch of the same function (crc check around `0x83fb5be0`,
RSA check further on) is **left untouched** — SLAVE stays the unmodified
OEM recovery copy with its own gates intact, as a safety net.

With both patches applied, a coldboot of MAIN with no `ATSE`/`ATEN`
unlock and no keypresses at all gives:
```
main tclinux.bin have ZYXEL trx header!
main tclinux.bin Start to decrypt RSA!
==> boot flag = 0
from main
```

## Why both gates need patching

Patching only the RSA compare is not enough for a fully unattended
coldboot: UBI can rewrite a physical flash block inside `tclinux` on its
own (ECC scrubbing on a correctable read error — normal, unavoidable NAND
behavior). The zloader's CRC check reads MAIN linearly at raw flash
offsets, not through UBI, so a single UBI-rewritten block makes even a
correctly built image fail the boot-time CRC check on a later boot.
Patching the CRC compare out removes that failure mode entirely,
independent of any NAND-level rewrite. (The writable overlay volume also
lives on its own, separate MTD partition rather than inside `tclinux`'s
UBI instance, for the same reason — see `NEXT_STEPS.md`.)

## Applying it: from a running Linux, not via ATER/ATWF

mtd0 has no slave fallback — the highest-risk write in this project.
It's written from **inside an already-running OpenWrt session** with
`flash_erase`/`nandwrite` (which handle ECC/OOB per NAND page correctly
— more robust than the raw `ATWF` primitive used for mtd3), not via the
bootloader's own flash commands. This requires the `bootloader` DTS
partition node to not have `read-only;` (already the case in the
prebuilt image in `firmware/`) so the MTD layer allows the write. See
`install-guide/README.md` §4 for the exact steps and required backups.

This means installing the patched bootloader takes two steps (flash
OpenWrt to MAIN first, boot it, then flash the bootloader from that
running Linux) rather than one direct `ATER`/`ATWF` write to mtd0 from
`ZHAL>`. `ATWF` is the same primitive already used for mtd3, so a direct
write to mtd0 may well work too — this project never attempted it,
specifically because mtd0 has no fallback and `nandwrite`'s per-page
ECC/OOB handling was judged the safer default for the one write that
can't be undone. If you test the direct route yourself, do it with the
same backup/verify discipline as `install-guide/README.md` §4.

## Per-unit board-info block: what the prebuilt binary does and doesn't preserve

Around offset `0xff00`-`0xffff` (in stage1, before the compressed stage2
blob), mtd0 also holds a small board-info table — vendor/model strings
(not per-unit), plus several per-unit fields: a MAC address, a serial
number, and a couple of unidentified alphanumeric codes. This is
separate from the two 1-instruction code patches above (which live deep
inside the compressed stage2 blob at `0x10000`-`0x1f681`) and from the
partition table this project documents elsewhere (`romfile`/`reservearea`
etc., which hold additional per-unit data of their own).

`firmware/vmg8825-t50-bootloader-patched.bin` has this block's per-unit
fields replaced with generic placeholders (same technique as the MAC
placeholders in the devicetree) so the published file doesn't carry this
project's own unit's real MAC/serial. Whether the zloader validates or
checksums this block at boot (as opposed to just reporting it verbatim
via `ATSH`) has **not been tested** — the placeholder values booted fine
in this project's own testing, but that was on the unit whose real values
were only just replaced, not on a different physical unit.

Flashing this file onto your own device therefore overwrites your unit's
real MAC/serial/codes in this block with the placeholders — cosmetic in
the (untested) best case, unknown in the worst case. The higher-assurance
alternative: dump your **own** mtd0, run `patch_stage2_rsa_bypass.py`
then `patch_stage2_crc_bypass.py` against your own dump (see below), and
flash the result instead. That only ever touches the two known code
offsets and leaves your own board-info block completely untouched.

## Reproducing this patch for a different zloader build

Out of scope for the install guide — only attempt this if your device's
zloader banner does not match the one `firmware/vmg8825-t50-bootloader-patched.bin`
targets (see `install-guide/README.md`'s precondition section). The build
scripts:

- `bldr-patch/patch_stage2_rsa_bypass.py` — applies the RSA patch to a
  raw mtd0 dump.
- `bldr-patch/patch_stage2_crc_bypass.py` — applies the CRC patch on
  top of an already RSA-patched mtd0 dump.

Both scripts hard-assert the original bytes at their patch offset before
writing anything, and refuse to run if they don't match — so a different
zloader build fails loudly instead of silently corrupting the wrong
location. If that happens, the offsets in the table above do not apply
to your build and finding the new ones requires disassembling your own
dump; that process isn't documented here.
