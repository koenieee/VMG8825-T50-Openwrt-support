# Installing OpenWrt on the Zyxel VMG8825-T50

This installs a patched bootloader (RSA-signature and CRC checks
disabled) plus a prebuilt OpenWrt image. This is not an OpenWrt-upstream
install path — the T50 has no signed/official firmware route, no web-UI
upload, and no Firmware Selector. Expect soldering and a serial console.

## Read this first — why there is a "step 0"

The stock bootloader enforces an RSA signature (and a CRC) on every boot.
An unsigned OpenWrt image fails that check, which **permanently latches a
"boot flag" to 1** ("boot the slave/recovery slot") — so the device just
boots the OEM slave, not your image, and stays that way across reboots
(see `BOOTLOADER-PATCH.md`). You cannot "flash OpenWrt to MAIN and boot
it" on a stock bootloader.

The only thing that removes those gates is the **patched bootloader**.
So the real order is:

1. Get a root shell **without booting from flash at all** — netboot an
   initramfs kernel straight into RAM (§4). RAM boot bypasses the gates.
2. From that RAM shell, flash OpenWrt to MAIN (safe) and the patched
   bootloader to mtd1 (removes the gates) (§6, §7).
3. Reboot into the now-patched bootloader, which boots MAIN cleanly (§8).

Both flash writes happen from the same gate-free RAM shell, so you never
depend on an unsigned image booting from flash. §8 also covers clearing a
boot flag that latched during earlier experiments.

## Precondition — check this first

This guide, and the prebuilt `firmware/vmg8825-t50-bootloader-patched.bin`,
only apply to a device whose bootloader banner reads **exactly**:
```
EN751627 at Mon Jan 4 14:53:36 CST 2021 version 1.1 free bootbase
...
ZyXEL zloader v1.4.4 (01/04/2021 - 14:53:34)
```
Get to this banner via the serial console (see §2). **If your banner
differs in any way, stop** — the prebuilt patched bootloader is a
byte-for-byte binary for this exact zloader build and will not work (and
may not be safe to flash) on a different one. Reproducing the patch for a
different version is not covered by this guide.

## Before you start

- **This can brick your router.** MAIN (`tclinux`) has a slave/recovery
  fallback, but the **bootloader (mtd1) write in §7 has no fallback**.
  Take the backups in §5 *before* you start, not "if something goes
  wrong."
- **You need to open the case and solder/probe a 3.3V UART header.**
  There is no network-reachable recovery path. Serial is the only way in.
- Every step up to and including the §5 backup is non-destructive. The
  first write to flash is §6.

## 1. What you need

- A 3.3V USB-TTL serial adapter (e.g. CP2102/FT232) + jumper wires.
- Soldering iron or pogo pins to reach the UART header inside the case
  (see `openwrt.org/inbox/toh/zyxel/zyxel_vmg8825-t50` for header photos).
- A PC with `python3`, `atftp`, and a C compiler (`cc`).
- A static IP on `192.168.1.0/24` (not `.1`) on the interface connected
  to the router's LAN port.
- A USB stick, FAT-formatted, to move images/backups to and from the
  running RAM shell.
- This repo cloned, with `tools/atenv3/atenv3_passwd` built:
  ```
  cc -o tools/atenv3/atenv3_passwd tools/atenv3/atenv3_passwd.c
  ```
- Three images:
  - **An initramfs kernel** for the netboot bootstrap (§4). This is
    **not** prebuilt in this repo — you build it yourself, see §4.
  - `firmware/vmg8825-t50-era-signed.bin` — the OpenWrt build for MAIN
    (mtd3). WiFi ships disabled; no SSID/passphrase baked in.
  - `firmware/vmg8825-t50-bootloader-patched.bin` — the patched
    bootloader (mtd1).

## 2. Wire up serial and confirm the console

115200 8N1, **CR-only** (do not send LF — the zloader reprints its menu
if you do). Connect TX/RX/GND (do **not** connect the adapter's VCC — the
board is already powered). Only one process may hold the port at a time
(a logger and an interactive terminal at once silently split the byte
stream). Power on the router; you should see the banner from the
precondition section, then:
```
Hit any key to stop autoboot: 5..0
```
Send a CR within that 5-second window to land on the `ZHAL>` prompt. If
you miss it, power-cycle and try again — this is safe, nothing is written
yet.

## 3. Debug-unlock (needed every boot until the bootloader is patched)

At `ZHAL>`:
```
ATSE VMG8825-T50
```
prints a 36-hex-char seed. Feed it to the derivation tool:
```
tools/atenv3/atenv3_passwd <SEED>
```
then send the resulting numeric password:
```
ATEN 1,<password>
```
The unlock is per power cycle. `netboot.py` and `dev_flash_cycle.py` do
this for you automatically; you only do it by hand if you drive `ZHAL>`
manually. After §7 (patched bootloader) this is no longer needed for any
boot.

## 4. Step 0 — bootstrap a root shell over RAM (netboot)

This boots an initramfs kernel entirely in RAM. It touches no flash and
does not go through the RSA/CRC gates, so it works on a stock, unpatched
device — this is how you get a shell without first patching anything.

### 4a. Build the initramfs kernel

The prebuilt `era-signed.bin` is a squashfs MAIN image, not a RAM kernel,
so you need a separate initramfs build. In your OpenWrt tree (see
`firmware/README.md` for the base build setup), enable initramfs and
rebuild the kernel:
```
CONFIG_TARGET_ROOTFS_INITRAMFS=y
CONFIG_TARGET_INITRAMFS_COMPRESSION_XZ=y
```
The output initramfs kernel image (under
`bin/targets/econet/en751627/`) is what you netboot below. Make sure the
build includes the USB fix (it is in this repo's overlay) so you can
mount the USB stick from the RAM shell in §5–§7.

### 4b. Netboot it

`bldr-patch/netboot.py` is the one-shot ritual: it catches `ZHAL>`, does
the §3 debug-unlock, TFTPs the kernel into RAM, relocates and jumps to
it, then waits for a live shell prompt. The `ZHAL>` catch window is only
~90s from power-on, so start the script and power-cycle together:
```
# start this, then power-cycle the router within a few seconds
python3 bldr-patch/netboot.py <your-initramfs-kernel.bin>
```
`power_cycle.py` can do the power-cycle for you if your PDU/smart-plug is
configured; otherwise pull and reapply power by hand. Success looks like
a normal kernel boot ending at `root@OpenWrt:~#` over the same serial
line. Nothing has been written to flash at this point.

## 5. Back up flash (from the RAM shell) — do not skip

You are now in a RAM shell with the real flash exposed as MTD devices.
**Identify partitions by name, not number** — numbering can vary:
```
cat /proc/mtd
```
Note the `mtdN` number next to each label. Mount the USB stick and back
up the `bootloader`, `tclinux` (MAIN), and `tclinux_slave` partitions
before writing anything (replace `mtdN` with the number you read):
```
mount -t vfat /dev/sda1 /mnt
dd if=/dev/mtdN of=/mnt/mtd-bootloader-backup.bin   # the "bootloader" partition
md5sum /mnt/mtd-bootloader-backup.bin
# repeat dd for the "tclinux" (MAIN) and "tclinux_slave" partitions
```
Keep these backups **off the device**. They are the only recovery path if
a write lands in the wrong place.

## 6. Flash OpenWrt to MAIN (mtd3) — safe, has a slave fallback

Still in the RAM shell. Find the partition labelled `tclinux` in
`/proc/mtd` (this is MAIN / mtd3). NAND only flips 1→0, so you **must
erase before writing** — `nandwrite` does not auto-erase, and skipping
the erase silently no-ops the write:
```
# copy era-signed.bin to the USB stick first, then from the RAM shell:
flash_erase /dev/mtdX 0 0          # mtdX = the "tclinux" partition
nandwrite -p /dev/mtdX /mnt/vmg8825-t50-era-signed.bin
```
Read it back and compare against the source before moving on:
```
nanddump -f /mnt/readback.bin -l $(stat -c%s /mnt/vmg8825-t50-era-signed.bin) /dev/mtdX
cmp /mnt/readback.bin /mnt/vmg8825-t50-era-signed.bin
```
This write is safe: if anything is wrong you can redo it, and the OEM
`tclinux_slave` recovery copy is untouched as long as you never wrote to
it.

## 7. Flash the patched bootloader (mtd1) — no fallback, read fully first

This is the one write with **no recovery slot**. Do it from the same live
RAM shell (so a bad write can still be fixed from the §5 backup without a
reboot), last, and only after the §6 readback matched.

1. Verify the file's md5 on the USB stick against your local copy.
2. Write it to the partition labelled `bootloader`:
   ```
   flash_erase /dev/mtd1 0 0
   nandwrite -p /dev/mtd1 /mnt/vmg8825-t50-bootloader-patched.bin
   ```
3. **Read back and compare md5** against the source. Do **not** reboot
   until it matches:
   ```
   nanddump -f /mnt/bl-readback.bin -l $(stat -c%s /mnt/vmg8825-t50-bootloader-patched.bin) /dev/mtd1
   md5sum /mnt/bl-readback.bin /mnt/vmg8825-t50-bootloader-patched.bin
   ```
4. If the readback does **not** match: do not reboot. Re-erase and
   restore mtd1 from `/mnt/mtd-bootloader-backup.bin` (§5) using the same
   `flash_erase`/`nandwrite`/readback sequence, then investigate before
   trying again.

> The prebuilt patched bootloader replaces a small per-unit board-info
> block (MAC/serial) with placeholders. Fine for most people. To keep
> your own device's values, build your own patched file from your own
> bootloader backup (§5) with `patch_stage2_rsa_bypass.py` then
> `patch_stage2_crc_bypass.py`, and use that in place of the prebuilt one
> here — same write/verify steps. See §11 and `BOOTLOADER-PATCH.md`.

## 8. Reboot, boot, and verify

Power-cycle (or `reboot`). With the patched bootloader, MAIN now boots
with no `ATSE`/`ATEN` unlock and no keypresses:
```
main tclinux.bin have ZYXEL trx header!
main tclinux.bin Start to decrypt RSA!
==> boot flag = 0
from main
```
followed by OpenWrt userspace and `VFS: Mounted root (squashfs filesystem)
readonly on device 31:4.`, then a root shell (no password) on the same
serial line.

### 8a. If it boots the slave / `==> boot flag = 1` — clear the boot flag

The boot flag is a **third, independent gate**. If any earlier boot
failed a check (very common while experimenting on a stock bootloader),
the flag latches to 1 and the bootloader keeps booting the slave slot —
**even now that the gates are patched and MAIN is fine**. You must clear
it once, by hand:
```
# catch ZHAL> (power-cycle + CR), then:
ATSE VMG8825-T50        # unlock again
ATEN 1,<password>
ATBT 1                  # set boot flag
ATSW                    # switch/commit -> flag back to 0
ATGO                    # boot MAIN
```
`bldr-patch/dev_flash_cycle.py` automates exactly this
(`ATBT 1` + `ATSW`) with a single retry if it sees a slave boot. With
both bootloader gates patched, a clean flash keeps the flag at 0 and you
should not hit this again in normal use.

Once booted, you do not need to stay on serial: dropbear (SSH) is enabled
by default like any stock OpenWrt build, reachable on the LAN IP
(`192.168.1.1`, `cat /etc/config/network` to confirm) once `gmac0` links
up. Serial stays the fallback for a broken-network state.

## 9. Bring up WiFi

WiFi ships disabled:
```
uci set wireless.default_radio0.disabled=0
uci set wireless.default_radio1.disabled=0
uci commit wireless
wifi
```
Both radios come up as open APs (SSID `OpenWrt`). **Before real use**, set
an SSID/passphrase:
```
uci set wireless.default_radio0.ssid=...
uci set wireless.default_radio0.encryption=psk2
uci set wireless.default_radio0.key=...
uci commit wireless
wifi
```
Both radios default to `5g`/channel 36 — see `NEXT_STEPS.md` for switching
one to `2g` for real dual-band coverage.

## 10. Recovering from a bad flash

- **MAIN (mtd3) is safe.** If it won't boot, get back to a RAM shell (§4)
  or `ZHAL>` (always returns — the zloader itself is untouched) and
  reflash. The `tclinux_slave` OEM copy is intact as long as you never
  wrote to it.
- **The bootloader (mtd1) is not safe** — no fallback. If §7's readback
  mismatched, do not reboot; restore mtd1 from your §5 backup in the same
  RAM shell.
- **Booting the slave / flag stuck at 1** is not a bad flash — it's the
  boot flag. Clear it per §8a; do not reflash blindly.

## 11. Reproducibility

Very likely on any device with the exact same zloader banner (see the
precondition). The netboot ritual, the two code patches, and the
partition layout are properties of this firmware build (factory NAND
layout + bootloader code), not of one physical unit. Still, always read
partition names/sizes from your own `/proc/mtd` (§5) rather than assuming
fixed numbers.

**One exception:** mtd0/mtd1 also holds a small per-unit board-info block
(MAC, serial, a couple of unidentified codes) separate from the code
patches. The prebuilt patched bootloader has this genericised; flashing
it overwrites your unit's values with placeholders. To keep your own,
patch your own bootloader backup yourself — see the note in §7 and
`BOOTLOADER-PATCH.md`.

## Appendix: all-from-`ZHAL>` route — CONFIRMED UNSAFE for the bootloader, do not use for mtd0

> Full write-up, including the OOB/ECC evidence and the Ghidra
> reverse-engineering that closed this off for good, is in
> `install-guide/bootloader-flash-from-zhal.md`.

**Do not use this for the bootloader (mtd1).** Proven on hardware
(2026-09-19): `ATWF` writes NAND page data only — it leaves the OOB/ECC
area zero-filled instead of real per-sector ECC parity. The bootloader
region is read by the SoC mask-ROM with strict hardware ECC, so a
bootloader written via `ATWF` fails that check on boot: **hard brick**,
recoverable only with a CH341A/NAND-clip. This is no longer a theoretical
risk — it's measured.

We also checked whether some other zloader command writes ECC correctly
instead. It doesn't exist: reverse-engineering the zloader's AT-command
dispatch table (Ghidra, `zld_stage2_decompressed.bin`) shows `ATWF` is the
**only** raw-NAND-write primitive. `ATWM`/`ATWW`/`ATWZ` look similar but
only write fields into an in-RAM config struct (MAC address, misc flags,
memory pokes) — none of them touch flash pages. There is no substitute
command and nothing in the zloader itself to patch: `ATWF` tail-calls a
shared low-level NAND driver at an address outside the zloader image we
have, so the ECC-skipping logic isn't even reachable in the binary we can
inspect.

`ATWF` remains fine for MAIN (mtd3) — that partition is read by the
kernel's own ECC-aware NAND driver later, not the mask-ROM, and is
covered by the slave fallback besides. Use the netboot + `nandwrite` route
(§4–§7 above, or the fully hand-typed version in
`install-guide/beginner-manual.md`) for the bootloader — it goes through
the kernel's NAND stack, which computes ECC correctly, and this is the
only route this project recommends for mtd1.
