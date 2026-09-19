# Flashing the patched bootloader directly from `ZHAL>` (no Linux) — DO NOT USE

## ⛔ CONFIRMED UNSAFE — this route bricks the bootloader. Do not use it.

Tested on real hardware (2026-09-19): `ATWF` writes NAND **data only** and
does **not** program the hardware ECC/OOB parity. Proof — `ATWF` a known
pattern into a throwaway block of mtd3, then read it back from RAM Linux
with `nanddump --oob`:

```
ATWF-written block OOB (page 0, should hold per-512B ECC parity):
ff ff ff ff ff ff ff ff  00 00 00 00 00 00 00 00   <- zero-filled, not ECC
(repeats to end of the 64-byte OOB)

Pristine block OOB (kernel-written, same mtd3, offset 0):
ff ff ff ff ff ff ff ff  0e 1c 3f 03 32 20 00 00   <- real per-sector ECC
ff ff ff ff ff ff ff ff  2e e1 12 32 df 0c 78 78
ff ff ff ff ff ff ff ff  5c c4 45 8e 1b 47 18 18
ff ff ff ff ff ff ff ff  cb 4a 53 da 5f 02 78 78
```

Every real write has genuine, varied, per-sector ECC in all four OOB
groups. The `ATWF`-written block has only the first 8 free bytes intact;
everything else is zero — not blank (`0xff`), not computed ECC, just
zeros. (`nanddump`'s own "ECC failed/corrected" counters read 0 for both
blocks — that check is bypassed by `--oob`'s raw read mode, so it does not
validate anything here; the OOB *content* is the real signal.)

The bootloader region is read by the SoC **mask-ROM** with strict hardware
ECC. A bootloader written this way would fail that check on boot —
**hard brick**, recoverable only by desoldering the NAND and reflashing it
with an external programmer (CH341A or similar).

**Use the netboot/`nandwrite` route instead** (main guide §4–§7):
`nandwrite` goes through the kernel's NAND stack, which computes ECC
correctly, and you keep a live RAM shell to recover from if something
goes wrong. That route flashes both MAIN and the bootloader safely and is
the only one this project recommends.

The rest of this document is kept for the record (the reasoning that led
to the test, and the mechanics that were disproven) — do not follow it as
instructions.

---

## Original text (historical — describes the route now proven unsafe)

An alternative to the netboot/`nandwrite` route in
`install-guide/README.md` §4–§7. This writes the patched bootloader to
flash using only the zloader's own raw NAND primitives (`ATER`/`ATWF`),
straight from the `ZHAL>` prompt — no initramfs build, no USB stick, no
booted Linux.

## ⚠️ Read this before doing anything

- **This is the one irreversible write.** The bootloader partition has
  **no recovery slot**. If the write is bad, the SoC mask-ROM can no
  longer load the bootloader and the device is **hard-bricked** —
  recoverable only by desoldering the NAND and reflashing it with an
  external programmer (CH341A or similar).
- **`ATWF` is proven for MAIN (mtd3), not for the bootloader.** MAIN is
  read by the zloader; the bootloader region is read by the SoC
  **mask-ROM**, which may expect a different hardware ECC/OOB layout than
  `ATWF` produces. **Now confirmed**: `ATWF` does not write real ECC (see
  the test result above). This is a real brick, not a theoretical one.
- **A passing `ATRF` readback does not prove the device will boot.**
  `ATRF` reads back the data bytes; it does not tell you whether the
  ECC/OOB the mask-ROM checks is correct. The only definitive test is the
  reboot — which is also the brick-or-boot moment.
- Do this only on a device you have a **full, ECC-correct backup of** and
  ideally with a CH341A + NAND-clip recovery kit on hand.

For most people the netboot/`nandwrite` route (main guide §4–§7) is the
safer choice: `nandwrite` uses the kernel's NAND stack, which handles ECC
correctly, and you keep a live RAM shell to recover from. Use *this* doc
only if you understand and accept the brick risk above.

## What you need

- `ZHAL>` reachable over serial (main guide §2), 115200 8N1, CR-only.
- `tools/atenv3/atenv3_passwd` built (main guide §1).
- `atftp` on the PC; PC on `192.168.1.0/24` (not `.1`).
- `firmware/vmg8825-t50-bootloader-patched.bin` (or your own self-patched
  bootloader — see main guide §11).
- **A pristine backup of your own bootloader partition** (see step 1).

## Step 1 — make a pristine backup you can restore from

Do **not** skip this. You want a byte-exact copy of your current
bootloader so you can put it back if the `ATRF` verify (step 5) fails
while the running zloader is still alive in RAM.

The reliable way is one netboot into RAM Linux (main guide §4) and:
```
nanddump -f /mnt/mtd-bootloader-pristine.bin /dev/mtd1   # the "bootloader" partition
md5sum /mnt/mtd-bootloader-pristine.bin
```
Copy this off the device. This is your restore image and your reference.
(If you refuse to netboot at all, you can instead capture `ATRF 0x0,
0x40000` output over serial — but that gives you the data bytes only, not
a guaranteed ECC-correct restore image, so it is a weaker backup.)

## Step 2 — reach `ZHAL>` and unlock

Power-cycle, catch `ZHAL>` (main guide §2), then debug-unlock:
```
ZHAL> ATSE VMG8825-T50
<36-hex seed>
```
On the PC:
```
tools/atenv3/atenv3_passwd <SEED>     # prints the numeric password
```
Back on the console:
```
ZHAL> ATEN 1,<password>
```

## Step 3 — load the patched bootloader into RAM (TFTP)

The bootloader lives at flash offset `0x0`–`0x40000` (256 KiB), blocks
`0`–`1` inclusive (block size `0x20000`). Load the patched image into RAM
via the zloader's own TFTP server:
```
ZHAL> ATLD bl.bin
```
Then on the PC (the router is the TFTP *server* here):
```
atftp --put --local-file firmware/vmg8825-t50-bootloader-patched.bin \
      --remote-file bl.bin 192.168.1.1
```
Wait for the console to confirm the download. Note the RAM address the
image landed at (`ATLD` defaults to `0x80020000` on this zloader —
`bldr-patch/flash_mtd3_via_ater_atwf.py` uses this same value; use
whatever your `ATLD`/`ATHELP` reports).

## Step 4 — erase and write the bootloader

From here the running zloader stays alive in RAM, so a mismatch in step 5
is still recoverable **until you reboot**.
```
ZHAL> ATER 0,1                          # erase blocks 0-1 (bootloader) ONLY
ZHAL> ATWF 0x80020000,0x0,0x40000       # RAM -> flash offset 0x0, len 0x40000
```
- `ATER x,y` erases blocks `x`..`y` **inclusive**. `ATER 0,1` erases
  exactly `0x0`–`0x40000` and nothing else — do not widen this range.
- `ATWF ram,flash_off,len` writes `len` bytes from RAM to absolute flash
  offset. Keep `flash_off = 0x0` and `len = 0x40000`.

## Step 5 — verify, and restore if it fails

```
ZHAL> ATRF 0x0,0x40000
```
Compare the readback against `firmware/vmg8825-t50-bootloader-patched.bin`
byte-for-byte.

- **If it does not match:** do **not** reboot. Reload the *pristine*
  backup from step 1 into RAM (`ATLD` + `atftp --put
  mtd-bootloader-pristine.bin`), then `ATER 0,1` + `ATWF
  0x80020000,0x0,0x40000` to restore, and `ATRF`-verify again. Once it
  matches the pristine image you are back where you started and can
  reboot safely. Investigate before retrying the patch.
- **If it matches:** the data bytes are correct. This still does **not**
  guarantee the mask-ROM will accept the ECC on reboot (see the warning
  at the top). The next step is the point of no return.

## Step 6 — reboot: the moment of truth

```
ZHAL> ATSR
```
A good result — patched bootloader accepted, gates removed:
```
main tclinux.bin have ZYXEL trx header!
main tclinux.bin Start to decrypt RSA!
==> boot flag = 0
from main
```
with no `ATSE`/`ATEN` needed on this or any future boot.

- **If you get no banner / no `ZHAL>` / dead serial:** the mask-ROM
  rejected the boot region. This is the brick case — recovery is CH341A
  reflash of the NAND. This is the risk you accepted at the top.
- **If it boots the slave / `==> boot flag = 1`:** the bootloader write
  is fine but the boot flag latched from an earlier failed boot. Clear it
  (main guide §8a): catch `ZHAL>`, unlock, `ATBT 1`, `ATSW`, `ATGO`.

## After the bootloader is in — flash MAIN

With the bootloader patched, MAIN boots on an already-patched bootloader,
so you no longer depend on any unsigned image booting from a stock
bootloader. Flash `era-signed.bin` to the `tclinux` partition using the
same `ATER`/`ATLD`/`ATWF` mechanics (this is the **proven** mtd3 path):
```
ZHAL> ATER <lo>,<hi>                     # MAIN block range, see below
ZHAL> ATLD main.bin                      # + atftp --put vmg8825-t50-era-signed.bin ...
ZHAL> ATWF 0x80020000,0x80000,<len>      # <len> = size of era-signed.bin
ZHAL> ATRF 0x80000,<len>                 # verify against source
ZHAL> ATGO
```
Compute the MAIN block range from your own layout — never copy blindly:
```
lo = mtd3_start // 0x20000
hi = mtd3_end   // 0x20000 - 1           # inclusive
assert (hi + 1) * 0x20000 == slave_start # must land exactly on the boundary
```
On this project's unit that is blocks `4`–`451`.
`bldr-patch/flash_mtd3_via_ater_atwf.py` is a working reference for this
MAIN write with the boundary math kept as hard asserts — copy it and set
the constants for your device.

Do **not** use `ATUR` for either write — it silently stops writing NAND
after a device's first successful flash.

## Status of this route

The MAIN (mtd3) half of this is proven on hardware. The bootloader (mtd0)
half is **not yet verified** — see the warning at the top. If you run it
and it boots, please report back (banner, exact commands, whether the
`ATRF` readback matched): a hardware-confirmed success is what would let
this become the recommended default instead of the netboot route.
