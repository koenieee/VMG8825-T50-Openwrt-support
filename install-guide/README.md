# Installing OpenWrt on the Zyxel VMG8825-T50

This installs a bootloader you patch yourself (RSA-signature and CRC
checks disabled — see §5a) plus a prebuilt OpenWrt image. This is not an
OpenWrt-upstream install path — the T50 has no signed/official firmware
route, no web-UI upload, and no Firmware Selector. Expect soldering and a
serial console.

This is the one tutorial for everyone: whether you're comfortable driving
things with a script or would rather type every command by hand and see
exactly what's happening, both routes are covered inline (look for the
"by hand" boxes) — nobody needs a separate "beginner" doc for this.

## Is this for you?

- You've never done a serial-console recovery before, but you're
  comfortable with a terminal (typing commands, reading output).
- You're OK opening the router's case and soldering (or holding pogo pins
  steady) on a UART header.
- You accept that one step here (§7, flashing the bootloader) **cannot be
  undone if it goes wrong** and can only be fixed by desoldering the flash
  chip. Read §7 fully before you get there.

## Read this first — why there is a "step 0"

The stock bootloader enforces an RSA signature (and a CRC) on every boot.
An unsigned OpenWrt image fails that check, which **permanently latches a
"boot flag" to 1** ("boot the slave/recovery slot") — so the device just
boots the OEM slave, not your image, and stays that way across reboots
(see `BOOTLOADER-PATCH.md`). You cannot "flash OpenWrt to MAIN and boot
it" on a stock bootloader.

The only thing that removes those gates is a **patched bootloader**. You
build this patch yourself, against your own bootloader dump (§5a) — this
project ships no prebuilt bootloader binary. So the real order is:

1. Get a root shell **without booting from flash at all** — netboot an
   initramfs kernel straight into RAM (§4). RAM boot bypasses the gates.
2. From that RAM shell, back up flash and build your own patched
   bootloader from the backup (§5, §5a).
3. Flash OpenWrt to MAIN (safe) and your patched bootloader to mtd1
   (removes the gates) (§6, §7).
4. Reboot into the now-patched bootloader, which boots MAIN cleanly (§8).

Both flash writes happen from the same gate-free RAM shell, so you never
depend on an unsigned image booting from flash. §8 also covers clearing a
boot flag that latched during earlier experiments.

## 0. Vocabulary (skip this if you already know these)

- **Serial console**: a text-only connection over 3 wires (TX, RX, GND)
  directly to the router's boot chip, independent of Ethernet/WiFi. It
  works even when nothing else does — this is your safety net.
- **`ZHAL>`**: the prompt of the stock bootloader ("zloader"). Reachable
  within 5 seconds of power-on.
- **`bldr>`**: a lower-level prompt inside the same bootloader, reached
  from `ZHAL>` via the `ATGU` command. Used only for the RAM-boot step.
- **RAM boot / netboot**: loading a Linux kernel straight into RAM over
  the network and running it, without touching the flash chip at all.
  This is how you get a safe root shell on a device that has never been
  flashed with anything of yours yet.
- **MTD / `mtdN`**: "Memory Technology Device" — Linux's name for one
  flash partition. `mtd1` might be the bootloader on your unit and
  something else on another; you always read the real number from
  `/proc/mtd`, never assume it.
- **NAND / OOB / ECC**: the flash chip stores each 2KB page plus 64 bytes
  of "out-of-band" area holding error-correction codes. Writing data
  without correct ECC means whatever reads the page back later may reject
  it. This matters in §7.

## Precondition — check this first

This guide only applies to a device whose bootloader banner reads
**exactly**:
```
EN751627 at Mon Jan 4 14:53:36 CST 2021 version 1.1 free bootbase
...
ZyXEL zloader v1.4.4 (01/04/2021 - 14:53:34)
```
Get to this banner via the serial console (see §2). **If your banner
differs in any way, stop** — the two code offsets §5a's patch scripts
use are byte-for-byte specific to this exact zloader build and asserting
against them on a different one will fail loudly (which is the point —
see `BOOTLOADER-PATCH.md`) rather than silently corrupt the wrong
location. Reproducing the patch for a different version is not covered
by this guide.

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

Hardware:
- A 3.3V USB-TTL serial adapter (e.g. CP2102/FT232) + jumper wires. **Do
  not** use a USB-to-RS232 adapter — wrong voltage, will damage the board.
- Soldering iron or pogo pins to reach the UART header inside the case
  (see `openwrt.org/inbox/toh/zyxel/zyxel_vmg8825-t50` for header photos).
- A USB stick, FAT-formatted, to move images/backups to and from the
  running RAM shell.
- An Ethernet cable from your PC directly to the router's LAN port (not
  through a switch/other router — TFTP in §4 needs a direct link).

Software (Linux assumed below; on Windows, do this inside WSL):
- A serial terminal program: `screen` (used in the examples below,
  `sudo apt install screen`), `minicom`, or `picocom` all work.
- `python3` and `atftp` (`sudo apt install atftp`) if you're using the
  scripted route; just `atftp` if you're doing everything by hand.
- A C compiler (`cc`/`gcc`) to build the one password-derivation tool.
- This repo cloned, with `tools/atenv3/atenv3_passwd` built:
  ```
  cc -o tools/atenv3/atenv3_passwd tools/atenv3/atenv3_passwd.c
  ```
- Already in the repo, nothing to build:
  - `firmware/vmg8825-t50-initramfs-kernel.bin` — the RAM-boot kernel for
    §4. Generic (no personal data), works on any T50 matching the
    precondition above. (Building your own is only needed if you've
    changed kernel config — see §4a.)
  - `firmware/vmg8825-t50-era-signed.bin` — the OpenWrt build for MAIN
    (mtd3). WiFi ships disabled; no SSID/passphrase baked in.
  - `bldr-patch/patch_stage2_rsa_bypass.py` and
    `bldr-patch/patch_stage2_crc_bypass.py` — the two scripts that turn a
    dump of **your own** mtd0/bootloader partition into the patched
    bootloader you flash in §7. This project ships no prebuilt bootloader
    binary — see §5a for why and how.

## 2. Wire up serial and confirm the console

115200 8N1, **CR-only** — do not send LF, the zloader reprints its menu
if you do (`screen` does this correctly by default). Connect TX/RX/GND
(do **not** connect the adapter's own 3.3V/VCC pin — the board is already
powered by its own supply; two power sources at once can damage it). Only
one process may hold the port at a time — a logger and an interactive
terminal open together silently split the byte stream.

Find your adapter and open it:
```
ls /dev/ttyUSB*          # usually /dev/ttyUSB0
screen /dev/ttyUSB0 115200
```
Nothing prints until you power the router on. Do that now; you should see
boot messages ending in:
```
Hit any key to stop autoboot: 5..0
```
Send a CR (plain Enter) within that 5-second window to land on `ZHAL>`.
If you miss it, power-cycle and try again — nothing has been written to
flash yet, retrying is free. Confirm the banner matches the precondition
above. If `screen` looks frozen with no prompt, press Enter a couple more
times — the bootloader only echoes after each carriage return.

## 3. Debug-unlock (needed every boot until the bootloader is patched)

At `ZHAL>`:
```
ATSE VMG8825-T50
```
prints a 36-hex-char seed, e.g. `2E01C10309E01B14300B07B06A09FB71F10E`.
On your PC, in a second terminal (leave the serial one open), feed it to
the derivation tool:
```
tools/atenv3/atenv3_passwd 2E01C10309E01B14300B07B06A09FB71F10E
```
It prints a numeric password, e.g. `70631161228104069991704422457`. Back
on the serial console:
```
ATEN 1,70631161228104069991704422457
```
(use your own seed/password each time — they're per-session and change
every power cycle). No error means you're unlocked.

The unlock is per power cycle. `netboot.py` and `dev_flash_cycle.py` do
this for you automatically if you use the scripted route below; you only
do it by hand if you drive `ZHAL>` manually. After §7 (patched
bootloader) this is no longer needed for any boot.

## 4. Step 0 — bootstrap a root shell over RAM (netboot)

This boots an initramfs kernel entirely in RAM. It touches no flash and
does not go through the RSA/CRC gates, so it works on a stock, unpatched
device — this is how you get a shell without first patching anything.

### 4a. The initramfs kernel

`firmware/vmg8825-t50-initramfs-kernel.bin` (already in the repo) is the
kernel you netboot below — nothing to build for a normal install. You
only need to build your own if you've changed kernel config: in your
OpenWrt tree (see `firmware/README.md` for the base build setup), enable
```
CONFIG_TARGET_ROOTFS_INITRAMFS=y
CONFIG_TARGET_INITRAMFS_COMPRESSION_XZ=y
```
and rebuild; the output is under `bin/targets/econet/en751627/`. Make
sure any custom build includes the USB fix (already in this repo's
overlay) so you can mount the USB stick in §5–§7.

### 4b. Netboot it

Pick one:

**Scripted (recommended if you have `python3`):** `bldr-patch/netboot.py`
does the §3 unlock, TFTPs the kernel into RAM, relocates and jumps to it,
then waits for a live shell prompt. The `ZHAL>` catch window is only
~90s from power-on, so start the script and power-cycle together:
```
# start this, then power-cycle the router within a few seconds
python3 bldr-patch/netboot.py firmware/vmg8825-t50-initramfs-kernel.bin
```
`power_cycle.py` can do the power-cycle for you if your PDU/smart-plug is
configured; otherwise pull and reapply power by hand.

> **By hand, no scripts:** set a static IP on your PC first — an address
> on `192.168.1.0/24` that is not `.1` (the router is the TFTP server at
> `192.168.1.1`):
> ```
> nmcli con mod <your-ethernet-connection> ipv4.addresses 192.168.1.50/24 ipv4.method manual
> nmcli con up <your-ethernet-connection>
> ```
> At `ZHAL>` (after §3's unlock):
> ```
> ATLD vmg8825-t50-initramfs-kernel.bin
> ```
> then push the file from your PC — the router is the TFTP server here:
> ```
> atftp --put --local-file firmware/vmg8825-t50-initramfs-kernel.bin \
>       --remote-file vmg8825-t50-initramfs-kernel.bin 192.168.1.1
> ```
> Wait for `File download` with a byte count matching the file's real
> size. Then:
> ```
> ATGU
> ```
> This prints `bldr>`. The kernel is now in RAM but linked to run from a
> different address; a small fixed "move it, then jump" command block
> does that relocation. Rather than typing ~100 lines, open
> `bldr-patch/netboot-stub-manual.txt` and paste its entire contents into
> the terminal in one go (any serial terminal sends a multi-line paste as
> if you'd typed each line). It only matches the exact kernel file above
> byte-for-byte; if you rebuild your own with a different size, regenerate
> it with `bldr-patch/build_combined_kernel_stub.py` (see that file's
> comments) instead of reusing this one.
>
> The stub's last line is `jump a1000000` — the point of no return *for
> this boot only* (not a flash write; a botched attempt just means
> power-cycling and starting over at §2).

Either way, success looks like a normal kernel boot ending at
`root@OpenWrt:~#` over the same serial line. Nothing has been written to
flash at this point.

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
Copy these off the device onto your PC before continuing — the
`bootloader` one goes straight into §5a next; all three are your only
recovery path if a write lands in the wrong place.

## 5a. Build your own patched bootloader

This project ships no prebuilt bootloader binary — you build one now,
from the `mtd-bootloader-backup.bin` you just copied onto your PC in §5
(the RAM shell on the router stays open in the background; nothing here
needs it). This keeps your unit's own board-info block (MAC, serial, a
couple of unidentified codes — see `BOOTLOADER-PATCH.md`) completely
untouched: the scripts below only ever rewrite two known code offsets,
copying everything else through unmodified.

Run both commands **from the repo root, on your PC**, not the router
(the paths below assume that):

```sh
python3 bldr-patch/patch_stage2_rsa_bypass.py /path/to/mtd-bootloader-backup.bin
```
Expect:
```
source: /path/to/mtd-bootloader-backup.bin
recompressed stage2: ... bytes (budget ..., orig ...)
wrote bldr-patch/mtd0-rsa-bypass-patched.bin (262144 bytes)
patch site VA 0x83fb57f4: 10400121 -> 10000121
```
An `AssertionError`/`FATAL` here instead means the patch offset didn't
match — almost always the bootloader banner precondition wasn't met, or
`/path/to/mtd-bootloader-backup.bin` isn't a full 256KB dump. Do not
continue; go back and re-check §5/the precondition section instead of
re-running with `--force` (there is no such flag, on purpose).

```sh
python3 bldr-patch/patch_stage2_crc_bypass.py bldr-patch/mtd0-rsa-bypass-patched.bin
```
Expect:
```
source: bldr-patch/mtd0-rsa-bypass-patched.bin
RSA patch confirmed present @0x57f4: 10000121
CRC patch applied @0x577c: 12820006 -> 10000006
recompressed stage2: ... bytes (budget ..., orig ...)
wrote bldr-patch/mtd0-rsa-and-crc-bypass-patched.bin (262144 bytes)
```

`bldr-patch/mtd0-rsa-and-crc-bypass-patched.bin` is the file you flash in
§7. Sanity-check its size (a failed run can leave a 0-byte or truncated
file) and note its md5 — you'll compare against this in §7:
```sh
ls -l bldr-patch/mtd0-rsa-and-crc-bypass-patched.bin   # expect 262144 bytes
md5sum bldr-patch/mtd0-rsa-and-crc-bypass-patched.bin
```

Now copy this file onto the USB stick, alongside `era-signed.bin` (§1),
and plug the stick back into the router before continuing to §6 — the
RAM shell is still running, nothing was lost while you were on the PC.

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
No output from `cmp` means they match. This write is safe: if anything is
wrong you can redo it, and the OEM `tclinux_slave` recovery copy is
untouched as long as you never wrote to it.

## 7. Flash the patched bootloader (mtd1) — no fallback, read fully first

This is the one write with **no recovery slot**. Do it from the same live
RAM shell (so a bad write can still be fixed from the §5 backup without a
reboot), last, and only after the §6 readback matched, and only with the
file you built yourself in §5a. A bad write here means desoldering the
flash chip to fix it.

Do **not** try to do this from `ZHAL>` with `ATWF` instead of `nandwrite`
below — proven on real hardware to skip the flash's error-correction
data entirely, which the boot chip checks strictly for this exact
partition (see the appendix). It is not a shortcut, it's a guaranteed
brick for this specific partition.

1. Verify the file's md5 on the USB stick matches the one you noted in
   §5a for `mtd0-rsa-and-crc-bypass-patched.bin`.
2. Write it to the partition labelled `bootloader`:
   ```
   flash_erase /dev/mtd1 0 0
   nandwrite -p /dev/mtd1 /mnt/mtd0-rsa-and-crc-bypass-patched.bin
   ```
3. **Read back and compare md5** against the source. Do **not** reboot
   until it matches:
   ```
   nanddump -f /mnt/bl-readback.bin -l $(stat -c%s /mnt/mtd0-rsa-and-crc-bypass-patched.bin) /dev/mtd1
   md5sum /mnt/bl-readback.bin /mnt/mtd0-rsa-and-crc-bypass-patched.bin
   ```
4. If the readback does **not** match: do not reboot. Re-erase and
   restore mtd1 from `/mnt/mtd-bootloader-backup.bin` (§5) using the same
   `flash_erase`/`nandwrite`/readback sequence, then investigate before
   trying again.

## 7a. Lock mtd1 back down (recommended, do this once §7 is verified)

Once the bootloader patch from §7 is written and read back verified, there
is no more reason for `mtd1` (bootloader) to be writable from Linux at
all — leaving it writable is a real risk on the one partition with no
fallback slot: a stray `nandwrite`, bug, or compromised process could brick
the device with no recovery but desoldering.

The `zyxel_vmg8825-t50-locked` device build closes this off at the
devicetree level (`read-only;` on the bootloader partition node):

```
firmware/vmg8825-t50-era-signed-locked.bin
```

Flash it the same way as §6 (it's a normal MAIN/mtd3 image, same fallback
safety). After booting it, confirm the lock took:
```
mtdinfo /dev/mtd1
# Device is writable:  false
```

If you ever need to re-patch mtd1 again (a future patch update), go back
to the base `zyxel_vmg8825-t50` device build for that one operation
(§5a/§7 again), then reflash `-locked` afterwards.

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

A full real capture of this, ATGO through to a working shell, is in
[`example-boot-log.txt`](example-boot-log.txt) if you want something to
diff your own boot against.

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
The bands are assigned on first boot and both APs are enabled, so no
manual band change is needed.

Which card gets which band is not arbitrary. The two MT7615 modules are
calibrated differently, and the calibration is what caps transmit power:

| PCIe slot | calibration | band assigned |
|---|---|---|
| `1fb81000.pcie` | TSSI, 2.4GHz target 17dBm | `2g`, channel auto |
| `1fb83000.pcie` | external PA, 2.4GHz target byte is **zero** | `5g`, channel 36 |

Put 2.4GHz on `1fb83000` and the driver reads a target power of zero and
clamps that radio to 10dBm — a quarter of the range, for no visible
reason. Assigned the way the table shows it, both radios reach 20dBm, the
regulatory ceiling. The uci-defaults script keys off the PCIe path rather
than the radio index, because the index depends on probe order and the
calibration does not.

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
patches. Because §5a always patches **your own** bootloader dump, this
block is never touched — see `BOOTLOADER-PATCH.md`.

This matters more than it might seem: that block, at offset `0xff48` of
the `bootloader` partition, is where OpenWrt now reads the ethernet MAC
from. It is the only copy on the chip — `romfile`, `rom-d` and
`reservearea` were read byte for byte and hold no MAC at all. Following
§5a/§7 as written, your router comes up with its own real MAC, unchanged.

## 12. Upgrading later (`sysupgrade`)

Supported from this build on. The image to hand it is the **era-wrapped**
one — `vmg8825-t50-era-signed.bin`, or your own
`...-squashfs-tclinux.trx` run through `build_era_trx.py`. The plain
`...-squashfs-sysupgrade.bin` is a different container and is rejected
with a message rather than written; the bootloader would otherwise stop
at "Wrong image checksum" on the next cold boot and only serial would
get you back.

```sh
sysupgrade -v /tmp/vmg8825-t50-era-signed.bin
```

It erases `tclinux` and writes the image at offset 0 — the same thing §6
does by hand.

Your configuration is kept automatically, because the writable overlay is
a separate UBI on the `rootfs_data` partition and nothing in the upgrade
touches it. The flip side: `sysupgrade -n` does **not** wipe it either.
For a clean configuration, upgrade first and then:

```sh
firstboot -y && reboot
```

Flashing the `-locked` image over the base one (or the other way round)
is a normal upgrade — the only difference between them is whether mtd1
is writable from Linux.

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
instead of `ATWF`. Among raw debug primitives, no: `ATWF` is the only
raw-NAND-write primitive. `ATWM`/`ATWW`/`ATWZ` look similar but only
write fields into an in-RAM config struct (MAC address, misc flags,
memory pokes) — none of them touch flash pages, and there is nothing in
`ATWF` itself to patch: it tail-calls a shared low-level NAND driver
outside the zloader image we have, so the ECC-skipping logic isn't even
reachable in the binary we can inspect.

**Update (2026-09-19):** a *different*, higher-level command family
exists — `ATUB`/`ATUD`/`ATUM` ("upgrade ZLD/ROMD/ROMFILE image"), each
calling a shared validator/write dispatcher rather than `ATWF`'s code
path directly. Live-tested `ATUM` (writes to `romfile`, mtd2 — lowest
risk, no auto-reboot): it **writes real, correct ECC** (raw `nanddump
--noecc --oob` showed genuine non-zero per-sector parity, not `ATWF`'s
zero-filled signature). So an ECC-safe write path *does* exist in this
zloader.

**`ATUB` (writes the bootloader/ZLD image itself) is a dead end,
confirmed by static analysis, not by a live test.** Decompiling the
shared dispatcher both commands funnel into showed it takes a `mode`
flag that is hardcoded per caller: `ATUM` calls it with the mode that
takes the ECC-safe write path (matching the live result above); `ATUB`
(and `ATUD`) call it with the *other* mode, which resolves to the exact
same low-level write function `ATWF` uses — the one already proven live
to leave zero-filled OOB. `ATUB` also auto-reboots 2 seconds after a
successful write with no verify window. The combination — same broken
write primitive as `ATWF`, plus zero chance to check before it reboots —
means testing it live would almost certainly hard-brick the device for
no new information. **Not tested on hardware; do not test it.** Full
trace in `install-guide/bootloader-flash-from-zhal.md`.

`ATWF` remains fine for MAIN (mtd3) — that partition is read by the
kernel's own ECC-aware NAND driver later, not the mask-ROM, and is
covered by the slave fallback besides. Use the netboot + `nandwrite`
route (§4–§7 above) for the bootloader — it goes through the kernel's
NAND stack, which computes ECC correctly, and this is the only route
this project recommends for mtd1.
