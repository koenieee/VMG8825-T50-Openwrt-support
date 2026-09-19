# How this port came together

A rough, non-technical retelling of the debugging journey behind this
repo — for the full technical detail see `BOOTLOADER-PATCH.md`,
`NEXT_STEPS.md`, and the commit history. This is the short version.

## Getting in the door

The VMG8825-T50 has no official OpenWrt support and no signed/web-UI
install path, so step one was just getting a foothold: opening the case,
wiring up a 3.3V serial console, and finding the bootloader's hidden
debug menu (`ZHAL>`, behind a challenge/response password scheme —
`tools/atenv3/` is the tool that answers that challenge).

From there, the vendor bootloader's own "master-loader" shell (`bldr>`)
turned out to be more powerful than the flashing menu it presents on the
surface — it could load and jump to arbitrary code over TFTP without any
signature check at all. A small relocate + cache-invalidate + jump stub,
hand-assembled and loaded through that shell, proved the whole approach
by booting a real unsigned Linux kernel straight from RAM, no flash
write involved. That was the first real proof this device could run
OpenWrt at all.

## From "kernel boots" to "shell works"

Booting a kernel is not the same as having a usable system. The first
successful boots panicked almost immediately — no rootfs, no network —
which was expected for a kernel-only test, but the next goal was an
actual interactive shell. That took a longer, more patient netboot
session (holding the bootloader's autoboot window open with repeated
keepalives) and eventually landed on a live `root@OpenWrt:~#` prompt.

With a shell in hand, the device's own flash partitions could be read
directly instead of guessed at from outside — dumping raw NAND regions
byte-for-byte and comparing checksums to build a real picture of the
factory partition layout, including the region expected to hold WiFi
calibration data (which, on this particular unit, turned out to be
empty/uninitialized rather than holding real calibration — leading
later to shipping a generic calibration blob in the devicetree instead
of trusting flash).

USB came next, mostly as a means to an end: getting `kmod-usb-storage`
working (which needed integrating an out-of-tree USB PHY driver and a
devicetree quirk for this board's USB wiring) made it possible to move
large dumps — eventually a full raw NAND image — off the device for
offline analysis, instead of everything having to go over a slow serial
line.

## The flashing bug that looked like a rootfs bug

Getting OpenWrt to actually boot from flash (not just RAM) hit a wall
that looked at first like a rootfs/offset problem — the kernel panicked
looking for its root filesystem. The real cause turned out to be much
sneakier: the bootloader's normal "flash a file" command silently stops
writing to NAND after a device's very first successful flash ever (it
still *reports* success). The fix was dropping down to the bootloader's
raw erase/write primitives instead of its higher-level flashing command
— unglamorous, but it's the reason `bldr-patch/flash_mtd3_via_ater_atwf.py`
and `dev_flash_cycle.py` exist and don't use the "obvious" flash command.

Once that was sorted, WiFi turned out to have never actually been
broken — it just ships disabled by default on a fresh OpenWrt install,
like on any device. Enabling it worked immediately. That's also roughly
when the first install-guide draft was written, so the process so far
could be reproduced by someone other than the one unit being tested on.

## Removing the vendor's boot-time gates

A fully unattended boot — no debug-unlock needed on every power cycle —
needed patching the bootloader itself, and this was the most
investigation-heavy part of the project. The vendor bootloader checks
a boot image two independent ways before trusting it (an RSA signature
check, and a separate raw checksum check), plus tracks a persistent
"did the last boot fail" flag. All three needed to be understood and
handled:

- The RSA check was confirmed patched out via disassembly of a live
  memory/flash dump — one instruction turned into an unconditional
  branch.
- The checksum check reappeared intermittently even after that,
  which took a while to explain: flash wear-leveling (ECC scrubbing)
  can physically rewrite a block over time even on a partition that's
  logically read-only from Linux's side, which was silently breaking
  the raw, offset-based checksum on some later boots. Patching that
  check out too (same one-instruction technique, found via a headless
  disassembler pass over the dumped bootloader) finally gave a clean,
  unattended cold boot.
- The stuck-boot-flag case (a failed boot poisons every future boot
  until manually cleared) turned out to already have a bootloader
  command to reset it — mostly a matter of automating it correctly in
  the flashing tool.

## Ethernet: two very different fights

LAN and WAN share a chip but needed separate investigations. LAN came
up once the driver was told to also touch the embedded switch's control
registers (a devicetree-node-size detail decided whether it did) — the
fix, once found, was almost trivially small.

WAN was the opposite: registers looked correct, but the physical link
just never came up, no PHY activity at all. The driver had no logic at
all for managing that physical link, which pointed the investigation at
the vendor's own driver instead — decompiling it revealed an
on-chip MDIO calibration sequence the vendor performs at startup that
OpenWrt's driver simply never did. Porting that sequence over got close
but not quite there on the first attempt (one calibration constant was
guessed rather than confirmed, and turned out to be wrong); a second,
hardware-verified pass with the correct value finally produced a real
link with actual traffic. A full soak-test of that fix under sustained
load is still an open item (see `NEXT_STEPS.md`).

## Cleaning up for release

The last stretch was less about the device and more about the repo
itself: separating "how do I use this" from years of raw debugging
material (memory dumps, one-off probe scripts, private board data),
scrubbing anything device-specific or personal, and restructuring
everything into the overlay + install-guide + prebuilt-images shape
this repo has now — so that someone starting fresh doesn't have to
retrace any of the above, just follow `install-guide/README.md`.

## Scripting the bootloader-flash step

The bootloader-patch write in §7 was, until now, always typed by hand.
An alternate firmware build (`vmg8825-t50-era-signed-installer.bin`)
ships the patched bootloader plus a small script that does that same
backup/erase/write/verify sequence for you, with a partition sanity check
added on top. It changes nothing about *how* the write happens or how
safe it is — same commands, same no-fallback risk on mtd1 — it just saves
typing them out. Flashing the bootloader at all is still, and remains, an
explicit choice.

## Finding the script's own bug, and closing mtd1 back up

Live-testing that installer script (rather than just reading it) turned
up a real bug: its writable-check compared `mtdinfo`'s actual lowercase
"Device is writable:  true" against a capital-W pattern, so it never
matched — the script always aborted with a false "not writable" error
before touching flash. Fixed and reverified end-to-end against a rebuilt
image, not just a hand-patched copy: backup, erase, write, and readback
all passed, byte-exact.

While flashing MAIN this way it also became clear the earlier "Permission
denied" seen from a booted Linux shell trying to `nandwrite` MAIN (mtd3)
directly was never a UBI-attachment issue — mtd3 simply isn't meant to be
written from Linux at all here; the documented route is the bootloader's
own `ATER`/`ATWF` commands from `ZHAL>`, which is what `dev_flash_cycle.py`
already automates.

That same read-only-partition mechanism is what closes the loop on mtd1:
a second device build, `zyxel_vmg8825-t50-locked`, marks the bootloader
partition `read-only;` in the devicetree and drops the flashing
binary/script from its image (there's nothing for it to do once mtd1 is
read-only). Verified live — `mtdinfo` reports `Device is writable: false`,
and the new image's own squashfs (`/rom/root/`) ships no bootloader files.
One nuance worth remembering: the persistent overlay is a separate MTD
partition (`rootfs_data`) that survives a MAIN reflash by design, so a
unit that's been experimented on can still show old files under `/root/`
even on a fresh image — that's the overlay, not the new squashfs.
Recommended sequence is now: flash `-installer` once to patch the
bootloader, then reflash `-locked` for everyday use.
