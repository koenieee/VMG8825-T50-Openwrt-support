# Beginner manual: flashing OpenWrt on the Zyxel VMG8825-T50 (no scripts)

This is the same install as `install-guide/README.md`, rewritten for
someone doing this for the first time, with every step typed by hand into
a terminal. There is no automation script driving the serial console for
you — you watch the screen and type each command yourself, the same way
you'd follow instructions for any other piece of hardware.

The only non-interactive helpers used are two ordinary command-line
tools everyone in the OpenWrt/embedded world already relies on:
`atftp` (a file-transfer client, like `scp` but for the TFTP protocol)
and a tiny one-shot password calculator (`atenv3_passwd`) — you run each
once per step and read its output, nothing runs in the background.

If something here disagrees with `install-guide/README.md`, that file is
the terser/canonical version; this one is the hand-holding version.

## Is this for you?

- You've never done a serial-console recovery before but are comfortable
  with a terminal (typing commands, reading output).
- You're OK opening the router's case and soldering (or holding pogo
  pins steady) on a UART header.
- You accept that one of the steps here (§7, flashing the bootloader)
  **cannot be undone if it goes wrong** and can only be fixed by
  desoldering the flash chip. Read §7 fully before you get there.

## 0. Vocabulary, so the rest of this makes sense

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
  without correct ECC means the chip (or whatever reads it back) may
  reject the page later. This matters in §7.

## 1. What you need

Hardware:
- A 3.3V USB-to-serial (UART) adapter — e.g. one with a CP2102 or FT232
  chip. **Do not** use a USB-to-RS232 adapter (wrong voltage, will damage
  the board).
- Jumper wires, and either a soldering iron or pogo pins to touch the
  UART header inside the case (photos of the header location:
  `openwrt.org/inbox/toh/zyxel/zyxel_vmg8825-t50`).
- A USB stick, formatted FAT32, to carry files in and out of the RAM
  shell.
- An Ethernet cable from your PC directly to the router's LAN port (not
  through a switch/other router — you need a direct link for TFTP).

Software (Linux is assumed below; if you're on Windows, install WSL and
do everything inside it — the tools this guide uses are Linux tools):
- A serial terminal program. `screen` is used in the examples below
  (`sudo apt install screen` on Debian/Ubuntu); `minicom` or `picocom`
  work the same way.
- `atftp` (`sudo apt install atftp`).
- A C compiler (`sudo apt install gcc`) to build the one password tool.
- This repo cloned:
  ```
  git clone <this repo's URL>
  cd VMG8825-T50-Openwrt-support
  cc -o tools/atenv3/atenv3_passwd tools/atenv3/atenv3_passwd.c
  ```

Files (already in this repo, nothing to build):
- `firmware/vmg8825-t50-initramfs-kernel.bin` — the RAM-boot kernel used
  in §4. Generic (no personal data), works on any T50 matching the
  precondition below.
- `firmware/vmg8825-t50-era-signed.bin` — OpenWrt for MAIN. WiFi ships
  off; no SSID/password is baked in.
- `firmware/vmg8825-t50-bootloader-patched.bin` — the patched bootloader.
- `bldr-patch/netboot-stub-manual.txt` — a plain text file of commands
  you paste in §4c. It only works together with the exact kernel file
  above (its content depends on that file's exact byte size); if you
  swap in a different kernel build, this file is invalid.

## Before you touch anything — read this

- **This can brick your router.** MAIN has a recovery fallback; the
  bootloader write in §7 does not. Do the backup in §5 before §6/§7, not
  "if something goes wrong."
- Only **one** program may have the serial port open at a time. If your
  terminal shows nothing at all, check nothing else (a logger, a second
  terminal window) is also holding `/dev/ttyUSB0`.
- The zloader wants **carriage-return only**, not the usual
  Enter-sends-CRLF some terminals default to. `screen` does this
  correctly out of the box; if you use something else and the bootloader
  reprints its menu after every command, that's why.

## Precondition — check this first

This guide and the prebuilt files only apply to a device whose bootloader
banner reads **exactly**:
```
EN751627 at Mon Jan 4 14:53:36 CST 2021 version 1.1 free bootbase
...
ZyXEL zloader v1.4.4 (01/04/2021 - 14:53:34)
```
You'll see this in §2 below. If yours differs at all, stop — these files
are byte-for-byte for this exact build and are not safe on a different
one.

## 2. Open the serial console

Wire TX/RX/GND from your adapter to the router's UART header. **Do not**
connect the adapter's own 3.3V/VCC pin — the board is already powered by
its own supply; connecting two power sources can damage it.

Find your adapter's device name:
```
ls /dev/ttyUSB*
```
(usually `/dev/ttyUSB0`). Open it:
```
screen /dev/ttyUSB0 115200
```
Nothing will print until you power on the router. Do that now (plug the
router's own power adapter in). You should see boot messages ending in:
```
Hit any key to stop autoboot: 5..0
```
Press Enter (a plain CR) within that 5-second countdown. If you miss the
window, unplug and replug the router's power and try again — this step
writes nothing to flash, so retrying is free.

Confirm the banner matches the precondition above, then confirm you're at
the prompt:
```
ZHAL>
```
If `screen` looks frozen with no prompt, press Enter a couple more times
— the bootloader only echoes what you type after each carriage return.

## 3. Unlock the debug shell

You'll redo this every power cycle until §7 is done. At `ZHAL>`, type:
```
ATSE VMG8825-T50
```
It prints a 36-character hex string, e.g.:
```
2E01C10309E01B14300B07B06A09FB71F10E
```
On your PC, in a **second** terminal (leave the serial one open), feed
that string to the password tool:
```
tools/atenv3/atenv3_passwd 2E01C10309E01B14300B07B06A09FB71F10E
```
It prints a numeric password, e.g. `70631161228104069991704422457`. Back
in the serial terminal, type:
```
ATEN 1,70631161228104069991704422457
```
(use your own seed/password — they're per-session and change every power
cycle). No error means you're unlocked.

## 4. Get a root shell without touching flash (RAM boot)

### 4a. Set a static IP on your PC

Your PC needs an address on `192.168.1.0/24` that is **not** `.1` (the
router will act as the TFTP server at `192.168.1.1`). On Linux with
NetworkManager, for example:
```
nmcli con mod <your-ethernet-connection> ipv4.addresses 192.168.1.50/24 ipv4.method manual
nmcli con up <your-ethernet-connection>
```
Adjust for whatever networking tool you use — the only requirement is a
fixed `192.168.1.x` address (x ≠ 1) on the interface plugged into the
router's LAN port.

### 4b. Load the kernel into RAM over TFTP

At `ZHAL>`, tell the bootloader you're about to send it a file:
```
ATLD vmg8825-t50-initramfs-kernel.bin
```
It replies asking you to push a file to it — the **router** is the TFTP
server here, so on your PC you push the file to it:
```
atftp --put --local-file firmware/vmg8825-t50-initramfs-kernel.bin \
      --remote-file vmg8825-t50-initramfs-kernel.bin 192.168.1.1
```
Wait for the serial terminal to print `File download` with the byte
count matching the file's real size (7927880 bytes / `0x78F848`). If it
times out, double check §4a's IP and that the Ethernet cable goes
straight PC↔router.

### 4c. Drop to `bldr>` and start the kernel

```
ATGU
```
This should print `bldr>`. The kernel is now sitting in RAM at the only
address the loader accepted, but the kernel is actually linked to run
from a different address — a small fixed block of "move it, then jump"
commands does that relocation. Rather than type ~104 lines by hand,
**open** `bldr-patch/netboot-stub-manual.txt` in a text editor, select
its entire contents, and paste the whole block into the `screen` window
in one go (any serial terminal will happily accept a multi-line paste —
it just sends each line followed by Enter, exactly like you typing it).

This file only works with the exact `vmg8825-t50-initramfs-kernel.bin`
in this repo — if you rebuild your own kernel with a different size,
regenerate it (`bldr-patch/build_combined_kernel_stub.py`, see that
file's own comments) instead of pasting this one.

The very last line of the file is `jump a1000000` — this is the
point-of-no-return for *this boot only* (not a flash write, so a botched
attempt just means power-cycling and starting over from §2). Right after
it you should see a normal Linux boot ending at:
```
root@OpenWrt:~#
```
Nothing has touched flash yet.

## 5. Back up flash — do not skip this

Still in the RAM shell. List the real partitions (numbers can differ
from unit to unit — always read them here, never assume):
```
cat /proc/mtd
```
Note which `mtdN` number is labelled `bootloader`, `tclinux`, and
`tclinux_slave`. Plug in the USB stick and mount it:
```
mount -t vfat /dev/sda1 /mnt
```
Back up each partition (repeat the `dd` line for all three, substituting
the real `mtdN` and a distinct output filename each time):
```
dd if=/dev/mtdN of=/mnt/mtd-bootloader-backup.bin
md5sum /mnt/mtd-bootloader-backup.bin
```
Copy these off the USB stick onto your PC before continuing. They are
your only way back if a write goes to the wrong place.

## 6. Flash OpenWrt to MAIN (safe — has a fallback)

Copy `firmware/vmg8825-t50-era-signed.bin` onto the USB stick, then in
the RAM shell (using the `tclinux` partition number from §5):
```
flash_erase /dev/mtdX 0 0
nandwrite -p /dev/mtdX /mnt/vmg8825-t50-era-signed.bin
```
NAND bits only flip 1→0 — you must erase before writing, or the write
silently does nothing. Read it back and compare before moving on:
```
nanddump -f /mnt/readback.bin -l $(stat -c%s /mnt/vmg8825-t50-era-signed.bin) /dev/mtdX
cmp /mnt/readback.bin /mnt/vmg8825-t50-era-signed.bin
```
No output from `cmp` means they match. If this one goes wrong, the stock
`tclinux_slave` copy is still there — just redo it.

## 7. Flash the patched bootloader — read this whole section before typing anything

This is the one write with **no recovery slot**. A bad write here means
desoldering the flash chip to fix it. Do it now, from the same live RAM
shell, only after §6's `cmp` matched.

Do **not** try to do this from `ZHAL>` with `ATWF` instead of this
`nandwrite` step — that command has been proven on real hardware to skip
the flash's error-correction data entirely, which the boot chip checks
strictly for this exact partition. See the appendix in
`install-guide/README.md` for the evidence; it is not a shortcut, it's a
guaranteed brick for this specific partition.

1. Copy `firmware/vmg8825-t50-bootloader-patched.bin` to the USB stick;
   check its md5 matches your local copy.
2. Write it to the partition labelled `bootloader` in §5:
   ```
   flash_erase /dev/mtd1 0 0
   nandwrite -p /dev/mtd1 /mnt/vmg8825-t50-bootloader-patched.bin
   ```
3. Read back and compare **before doing anything else**:
   ```
   nanddump -f /mnt/bl-readback.bin -l $(stat -c%s /mnt/vmg8825-t50-bootloader-patched.bin) /dev/mtd1
   md5sum /mnt/bl-readback.bin /mnt/vmg8825-t50-bootloader-patched.bin
   ```
4. If the two md5s do **not** match: do not reboot. Re-erase mtd1 and
   write your §5 backup back instead, verify that matches, and only then
   investigate what went wrong before trying again.

> This file replaces a small block of per-unit info (MAC address, serial
> number) with generic placeholders, so it's safe to share/reuse. If you
> want to keep your own device's values, see `BOOTLOADER-PATCH.md` for
> patching your own §5 backup instead of using this prebuilt file.

## 8. Reboot and check

Power-cycle the router. With the patched bootloader, it now boots MAIN
directly, no unlocking needed:
```
main tclinux.bin have ZYXEL trx header!
main tclinux.bin Start to decrypt RSA!
==> boot flag = 0
from main
```
then normal Linux boot messages, ending at a root shell on the same
serial line (no password).

### If it boots the *slave* instead (`==> boot flag = 1`)

This is a separate, independent switch, not a bad flash. Clear it by
hand: catch `ZHAL>` again (§2), redo the unlock (§3), then:
```
ATBT 1
ATSW
ATGO
```
This should now boot MAIN.

Once it's up, you don't have to stay on serial — SSH (dropbear) is on by
default, reachable at `192.168.1.1` on the LAN.

## 9. Turn WiFi on

```
uci set wireless.default_radio0.disabled=0
uci set wireless.default_radio1.disabled=0
uci commit wireless
wifi
```
Both radios come up open, SSID `OpenWrt`. Set a real password before
using it normally:
```
uci set wireless.default_radio0.ssid=YourNetworkName
uci set wireless.default_radio0.encryption=psk2
uci set wireless.default_radio0.key=YourPassword
uci commit wireless
wifi
```

## If something goes wrong

- **MAIN won't boot**: get back to `ZHAL>` or redo §4's RAM boot, then
  redo §6.
- **Bootloader write's readback didn't match**: see §7 step 4 — restore
  from your §5 backup, do not reboot until it verifies.
- **Stuck on the slave slot**: that's §8's boot-flag case, not a bad
  flash — don't reflash, just clear the flag.
