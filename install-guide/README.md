# Installing OpenWrt on the Zyxel VMG8825-T50

This installs a patched bootloader (RSA-signature and CRC checks
disabled) plus a prebuilt OpenWrt image. This is not an OpenWrt-upstream
install path — the T50 has no signed/official firmware route, no web-UI
upload, and no Firmware Selector. Expect soldering and a serial console.

## Precondition — check this first

This guide, and the prebuilt `firmware/vmg8825-t50-bootloader-patched.bin`,
only apply to a device whose bootloader banner reads **exactly**:
```
EN751627 at Mon Jan 4 14:53:36 CST 2021 version 1.1 free bootbase
...
ZyXEL zloader v1.4.4 (01/04/2021 - 14:53:34)
```
Get to this banner via the serial console (see §2 below). **If your
banner differs in any way, stop** — the prebuilt patched bootloader is a
byte-for-byte binary for this exact zloader build and will not work
(and may not be safe to flash) on a different one. Reproducing the patch
for a different version is not covered by this guide.

## Before you start

- **This can brick your router.** The dual-slot design (`tclinux` main +
  `tclinux_slave` recovery) protects the OpenWrt flash step, but the
  bootloader flash step in §4 has **no fallback** — read it fully and
  take the backups it asks for before running it.
- **You need to open the case and solder/probe a 3.3V UART header.**
  There is no network-reachable recovery path. Serial is the only way in.
- **Take a full NAND backup before you start**, not "if something goes
  wrong" — see §6.

## 1. What you need

- A 3.3V USB-TTL serial adapter (e.g. CP2102/FT232) + jumper wires.
- Soldering iron or pogo pins to reach the UART header inside the case
  (see `openwrt.org/inbox/toh/zyxel/zyxel_vmg8825-t50` for header photos).
- A PC with `python3`, `atftp`, and a C compiler (`cc`).
- A static IP on `192.168.1.0/24` (not `.1`) on the interface connected
  to the router's LAN port.
- A USB stick, FAT-formatted, for transferring the bootloader image (§4).
- This repo cloned, with `tools/atenv3/atenv3_passwd` built:
  ```
  cc -o tools/atenv3/atenv3_passwd tools/atenv3/atenv3_passwd.c
  ```
- The two prebuilt images from `firmware/`:
  - `vmg8825-t50-bootloader-patched.bin` — the patched bootloader (mtd0).
  - `vmg8825-t50-era-signed.bin` — the OpenWrt build, ready to flash to
    MAIN (mtd3). WiFi ships disabled; no network name/passphrase is
    baked in.

## 2. Wire up serial and confirm the console

115200 8N1, **CR-only** (do not send LF — the zloader reprints its menu
if you do). Connect TX/RX/GND (do **not** connect the adapter's VCC — the
board is already powered). Power on the router; you should see the
banner from the precondition section above, then:
```
Hit any key to stop autoboot: 5..0
```
Send a CR within that 5-second window to land on the `ZHAL>` prompt. If
you miss it, power-cycle and try again — this is safe, nothing is
written yet.

## 3. Flash the OpenWrt image to MAIN

### 3a. Debug-unlock (needed every boot until the bootloader is patched)

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
Do this fresh every power cycle, immediately before both the flash step
and the boot step.

### 3b. Confirm your partition block range

This layout is fixed by our devicetree (part of the OpenWrt image you're
flashing) and matches the OEM factory NAND layout for this firmware
line — it should be identical on any unit that matches the precondition
banner, not something that varies randomly per unit. Still, verify it
against your own device before erasing anything (`ATSH`, or netboot an
initramfs kernel and read `/proc/mtd` — see `bldr-patch/netboot.py`, an
optional tool for reading MTD layout over RAM without touching flash).
Flash block size on this family is 128 KiB (`0x20000`). Compute the
inclusive block range for MAIN (`tclinux`):
```python
lo_block = mtd3_start_offset // 0x20000
hi_block = mtd3_end_offset  // 0x20000 - 1   # inclusive
assert (hi_block + 1) * 0x20000 == mtd_slave_start_offset  # must land exactly on the boundary
```
**Do not skip this check** — if it doesn't match, your unit's factory
partition table differs from what this guide assumes, and the rest of
this guide does not apply without further investigation on your part.
`bldr-patch/dev_flash_cycle.py` has this hardcoded to the value
confirmed on this project's own unit (blocks 4-451); it should match
yours too, but confirm rather than assume.

### 3c. Flash MAIN via `ATER`/`ATWF` (not `ATUR`)

The documented `ATUR <file>,<partition>` + TFTP flash command is known to
silently stop writing NAND after a device's first successful flash. Use
the raw primitives instead:
- `ATER <lo_block>,<hi_block>` — erase (inclusive range).
- `ATLD <name>` + `atftp --put` — load the image into RAM via the
  zloader's own TFTP server.
- `ATWF <ram_addr>,<flash_offset>,<len>` — write RAM to flash.

`bldr-patch/flash_mtd3_via_ater_atwf.py` is a working reference
implementation of this sequence — copy it and set the block-range and
image constants at the top for your device before running it. Keep its
hard asserts on the block-boundary math.

Sanity-check the write with `ATRF <offset>,<len>` (raw flash read)
against your local file before rebooting.

### 3d. Boot and verify

```
ZHAL> ATGO
```
Watch for `VFS: Mounted root (squashfs filesystem) readonly on device
31:4.` followed by normal OpenWrt userspace boot. Once you have a shell
(root, no password, over the same serial line):
```
cat /proc/mtd
mtdinfo /dev/mtd1 | grep writable   # must say "true" -- required for §4
```

## 4. Flash the patched bootloader

**No fallback for this step — read all of it before starting.** mtd0
(bootloader) has no slave copy, unlike mtd3. Also see §7 before you
start: the prebuilt binary below overwrites a small per-unit board-info
block (MAC/serial) with placeholders — fine for most people, but if you'd
rather preserve your own device's values there, build your own patched
file from your own mtd0 dump instead (§7 has the exact commands) and use
that in place of `vmg8825-t50-bootloader-patched.bin` below.

1. Put `firmware/vmg8825-t50-bootloader-patched.bin` on the USB stick,
   insert it into the running router, mount it:
   ```
   mount -t vfat /dev/sda1 /mnt/usb
   ```
2. **Verify the file's md5** on the stick against your local copy, before
   erasing anything.
3. **Back up and verify** the current live mtd1:
   ```
   dd if=/dev/mtd1 of=/mnt/usb/mtd1-backup.bin
   md5sum /mnt/usb/mtd1-backup.bin
   ```
   Keep this backup off the device too.
4. Write it:
   ```
   flash_erase /dev/mtd1 0x0 0
   nandwrite -p /dev/mtd1 /mnt/usb/vmg8825-t50-bootloader-patched.bin
   ```
5. **Read back and compare md5** against the source file — only proceed
   once it's an exact match. Do not reboot before this check passes.
6. Reboot. You should see:
   ```
   main tclinux.bin have ZYXEL trx header!
   main tclinux.bin Start to decrypt RSA!
   ==> boot flag = 0
   from main
   ```
   with no `ATSE`/`ATEN` unlock needed — the debug-unlock from §3a is no
   longer required for any future boot.

## 5. Bring up WiFi

WiFi ships disabled:
```
uci set wireless.default_radio0.disabled=0
uci set wireless.default_radio1.disabled=0
uci commit wireless
wifi
```
Both radios come up as open APs (SSID `OpenWrt`). **Before using this for
real**, set a real SSID/passphrase:
```
uci set wireless.default_radio0.ssid=...
uci set wireless.default_radio0.encryption=psk2
uci set wireless.default_radio0.key=...
uci commit wireless
wifi
```
Both radios default to `5g`/channel 36 — see `NEXT_STEPS.md` for
switching one to `2g` for real dual-band coverage.

## 6. Recovering from a bad flash

**MAIN (mtd3) is safe**: if it won't boot, power-cycle, catch `ZHAL>`
(it always comes back — the zloader itself is untouched by any of this),
and reflash with the same procedure. The slave slot (`tclinux_slave`)
still has stock OEM firmware as long as you never wrote to it.

**The bootloader (mtd0) is not safe** — it has no fallback. If §4 fails
partway through (readback mismatch, unexpected error), do not reboot;
restore from the mtd1 backup you took in step 3 using the same
`flash_erase`/`nandwrite`/verify sequence, using a second, independent
serial-console session if the first one is stuck.

**Take a full NAND dump before you start, not "if something goes
wrong."** This is the only recovery path if a block-math mistake writes
into the wrong partition.

## 7. Reproducibility

Very likely, on any device with the exact same zloader banner (see the
precondition section) — the `ATER`/`ATWF` primitives, the two code
patches, and the partition block numbers in this guide are properties of
this firmware build (factory NAND layout + bootloader code), not of one
physical unit. Confirm the block range against your own device anyway
(§3b) as part of confirming the precondition — don't skip that check,
but don't expect it to differ either if your banner matches.

**One exception:** mtd0 also holds a small per-unit board-info block
(MAC address, serial number, a couple of unidentified codes — see
`BOOTLOADER-PATCH.md`) separate from the code patches. The prebuilt
`vmg8825-t50-bootloader-patched.bin` has this block genericized/
placeholder-filled; flashing it onto your device overwrites your unit's
own values in that block with those placeholders. If you'd rather keep
your own device's real board-info block untouched, dump your own mtd0
and run `patch_stage2_rsa_bypass.py` then `patch_stage2_crc_bypass.py`
against it yourself (§4 covers the write/verify steps either way — same
procedure, just a self-built input file).

## Appendix: an untested, shorter route

**Not validated on hardware. Read the whole section before trying it, and
only on a device you've already fully backed up (§6).**

§3-4 above install the patched bootloader in two stages — flash OpenWrt
first (safe, mtd3 has a slave fallback), boot it, then write the patched
bootloader from that running Linux with `nandwrite` (which handles NAND
ECC/OOB per page correctly). That order was chosen deliberately: mtd0 has
no fallback at all, and `nandwrite`'s correct ECC handling was judged the
safer bet for the one write that can't be undone.

In principle, the same `ATER`/`ATWF` primitives already used for mtd3 in
§3c could write mtd0 too, directly from `ZHAL>`, with no OpenWrt boot and
no USB stick in between:

```
ZHAL> ATSE VMG8825-T50
... <seed> -> tools/atenv3/atenv3_passwd <seed> -> ATEN 1,<password>

ZHAL> ATER 0,1                 # mtd0 = flash 0x0-0x40000 = blocks 0-1 inclusive
ZHAL> ATLD <name> / atftp --put vmg8825-t50-bootloader-patched.bin ...
ZHAL> ATWF <ram_addr>,0x0,0x40000
ZHAL> ATRF 0x0,0x40000        # sanity-check the write before rebooting

ZHAL> ATSR                     # reboot -- bootloader should now be patched,
                                # no ATSE/ATEN needed from here on

ZHAL> ATER <lo_block>,<hi_block>          # from §3b, MAIN this time
ZHAL> ATLD <name> / atftp --put vmg8825-t50-era-signed.bin ...
ZHAL> ATWF <ram_addr>,0x80000,<len>
ZHAL> ATGO
```

This has never been run against real hardware in this project. The two
open questions are exactly the two things the two-stage method sidesteps:
whether `ATWF` handles NAND ECC/OOB robustly enough for mtd0 specifically
(unknown — it works fine for mtd3, but that partition has a recovery
path if something is subtly wrong; mtd0 does not), and whether writing
mtd0 while the currently-running zloader instance is still resident in
RAM/cache has any side effect before the next reboot (expected not to,
since the running instance doesn't re-read its own flash mid-session, but
untested).

If you try this: back up mtd0 *and* mtd1's current OEM/slave state first,
verify the `ATRF` readback byte-for-byte against your source file before
rebooting (exactly as in §4, just without the USB-stick/Linux detour),
and report back whether it worked — that would let this become the
documented default instead of an appendix.
