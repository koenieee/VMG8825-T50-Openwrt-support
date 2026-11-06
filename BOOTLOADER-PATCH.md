# Bootloader patch: RSA + CRC gate bypass in zloader stage2 (mtd0)

Technical reference for the two-instruction patch you apply to **your
own** mtd0/bootloader dump with the scripts in `bldr-patch/`. This
project does not ship a prebuilt patched bootloader binary — see "Why no
prebuilt binary" below. For the step-by-step flashing procedure see
`install-guide/README.md` §5a and §7.

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
partition node to not have `read-only;` (the case on the base
`zyxel_vmg8825-t50` build; only the `-locked` variant sets it) so the MTD
layer allows the write. See `install-guide/README.md` §5a/§7 for the
exact steps and required backups.

This means installing the patched bootloader takes two steps (flash
OpenWrt to MAIN first, boot it, then flash the bootloader from that
running Linux) rather than one direct `ATER`/`ATWF` write to mtd0 from
`ZHAL>`. `ATWF` is the same primitive already used for mtd3, so a direct
write to mtd0 may well work too — this project never attempted it,
specifically because mtd0 has no fallback and `nandwrite`'s per-page
ECC/OOB handling was judged the safer default for the one write that
can't be undone. If you test the direct route yourself, do it with the
same backup/verify discipline as `install-guide/README.md` §5.

## Why no prebuilt bootloader binary

Earlier revisions of this project shipped a prebuilt
`vmg8825-t50-bootloader-patched.bin` with its per-unit board-info block
(MAC/serial, see below) replaced by placeholders. That file — and the
`-installer` firmware build that baked it in — have been removed,
including from git history: distributing a patched bootloader binary
that flashes over another unit's real board-info block on the
highest-risk, no-fallback write in this project was the wrong trade-off.

**You build your own patched bootloader from your own mtd0 dump.** It's
one Python script run against a file you dump yourself (§5a of the
install guide), and it means:

- Your unit's own MAC/serial/board-info block is never touched — only
  the known code gate(s) are patched (see the per-unit section below).
- The patcher locates the gates by landmark (the checker's own error
  strings) rather than by a fixed offset, so it adapts across zloader
  builds — including ones where the offset moved or the RSA gate is
  absent. If it cannot locate a gate cleanly it refuses to write anything,
  so a truly unknown layout fails safe instead of corrupting the wrong
  location.

## Per-unit board-info block

Around offset `0xff00`-`0xffff` (in stage1, before the compressed stage2
blob), mtd0 also holds a small board-info table — vendor/model strings
(not per-unit), plus several per-unit fields: a MAC address, a serial
number, and a couple of unidentified alphanumeric codes. This is
separate from the two 1-instruction code patches above (which live deep
inside the compressed stage2 blob at `0x10000`-`0x1f681`) and from the
partition table this project documents elsewhere (`romfile`/`reservearea`
etc., which hold additional per-unit data of their own).

Because you always patch your own dump (see above), this block is never
touched by the process in this repo — `patch_stage2.py` only ever rewrites
the known code gate(s) inside the compressed stage2 blob, copying
everything else (including this board-info block) through unmodified.
Whether the
zloader validates or checksums this block at boot at all (as opposed to
just reporting it verbatim via `ATSH`) has not been tested — it's simply
never touched here.

## Building your own patched bootloader

`bldr-patch/patch_stage2.py`, run against a raw dump of **your own**
mtd0/bootloader partition (`install-guide/README.md` §5a):

```sh
python3 bldr-patch/patch_stage2.py your-mtd0-dump.bin --dry-run   # report only
python3 bldr-patch/patch_stage2.py your-mtd0-dump.bin \
    -o bldr-patch/mtd0-rsa-and-crc-bypass-patched.bin             # this is the file you flash in §7
```

How it finds the gates without hard-coded offsets: the stage2 boot checker
prints human-readable failure strings (`... main tclinux.bin crc check
error`, `Wrong image hash value`). The script finds each string in the
decompressed stage2, computes its load VA (base `0x83fb0000`), finds the
`lui/addiu` that materialises that VA into `$a0` (the failure-print setup),
and takes the `beq` guarding it — for CRC a `beq $sX,$v0,ok` where `$v0`
holds the freshly computed JAMCRC. It neutralises the guard by zeroing both
register fields (`beq $zero,$zero,imm`, same immediate → same target), then
recompresses and roundtrip-verifies.

This adapts across builds where the old fixed-offset scripts failed:

- **Moved offsets.** The landmark is the error string, not an address. A
  tester's v1.4.4 (01/14/2020) build moved the CRC gate from `0x577c` to
  `0x576c` and changed its register (`$s4`→`$s3`); the old
  `patch_stage2_rsa_bypass.py` stopped with "FATAL: patch site moved".
- **CRC-only builds.** If the `Wrong image hash value` string is absent the
  build has no RSA gate — the script reports **CRC-only** and patches just
  the CRC gate. (That same v1.4.4 01/14/2020 build.)
- **Idempotent.** An already-patched dump is recognised and left alone.
- **MAIN only.** The SLAVE image's gates are never touched, so SLAVE stays
  a pristine OEM recovery slot.

Safe by default: if a landmark can't be found, or the guard doesn't look
neutralisable, it writes nothing and tells you to disassemble your own
dump first. It never touches a device.

The old `patch_stage2_rsa_bypass.py` + `patch_stage2_crc_bypass.py` pair is
kept in `bldr-patch/` as a fixed-offset, single-build reference fallback.
