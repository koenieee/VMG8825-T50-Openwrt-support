# firmware/

Prebuilt binaries: the current known-good build, its matching patched
bootloader, and an alternate build with that bootloader (plus the script
that flashes it) baked in. A pre-wrap `.trx` was removed from here because
it was a stale, older build than the shipped `era-signed.bin` (the two are
only consistent when produced together; keeping a mismatched pair around
is actively misleading). Build your own `.trx` from source if you want
one — see below.

> **All four images were rebuilt 2026-09-21** from the current tree:
> the fifth socket and the port names, hardware flow offload on by
> default, the factory MAC from the bootloader's board-info block,
> `sysupgrade`, the watchdog and the WiFi band split. The `-installer`
> variant is no longer a build behind. See the performance section of
> the top-level `README.md`.

- `vmg8825-t50-bootloader-patched.bin` — the patched zloader bootloader
  (mtd0): RSA-signature and CRC boot-time checks disabled, see
  `BOOTLOADER-PATCH.md`. Only flash this if your device's zloader banner
  matches exactly — see `install-guide/README.md`'s precondition section.
  No fallback if this write goes wrong; the install guide's backup steps
  are not optional. The per-unit board-info block mtd0 also carries
  (MAC/serial/other codes, separate from the boot-check patches) has
  been replaced with placeholders in this file — see `BOOTLOADER-PATCH.md`
  for what that means and the alternative of patching your own dump.
- `vmg8825-t50-era-signed.bin` — the OpenWrt build, wrapped in the
  era-0x174 header this board's zloader expects, ready to flash to MAIN.
  **Rebuilt 2026-09-21** with LuCI, SFTP (`openssh-sftp-server`), USB
  storage (`kmod-usb-storage`/`block-mount`/ext4+vfat+nls), `base64`,
  and the 448 MB RAM devicetree mapping now included by default. This
  exact header wrapping wasn't itself re-flashed this round, but its
  rootfs and kernel are byte-for-byte identical (same
  `kernelChksum`/`rootfsChksum` in the era header) to
  `era-signed-locked.bin` below, which *was* flash-tested and confirmed
  on real hardware with this same package set — full boot, `apk info -e`
  confirms all six new packages, `base64`/`sftp-server`/`lsusb` all
  work, `MemTotal: 443152 kB`. WAN (ETHWAN) should also work but its
  last fix was never re-tested after flashing (see `NEXT_STEPS.md`'s
  Ethernet section) — whether this exact file was built before or after
  that fix is not tracked; rebuild fresh (below) if you specifically
  need to test WAN. Both WiFi APs come up enabled on the
  OpenWrt defaults (SSID `OpenWrt`, no encryption) so the board is
  reachable without a serial cable — set an SSID and a passphrase before
  using it. No network name or passphrase is baked into the image.
- `vmg8825-t50-era-signed-installer.bin` — byte-identical to
  `era-signed.bin` above, except `/root/` also ships
  `vmg8825-t50-bootloader-patched.bin` and `flash-patched-bootloader.sh`
  (source: `openwrt-overlay/files/root/`). Once you're at a shell on
  this image (booted normally, or the §4 RAM shell), running that script
  does §7's erase/write/verify for you instead of typing it by hand — same
  underlying `flash_erase`/`nandwrite` steps, same no-fallback risk on
  mtd1, same backup/confirm/verify safety checks, plus a `/proc/mtd`
  partition-name/size check before it touches anything. Flashing the
  bootloader is still an explicit, optional choice either way — nothing
  in this image touches mtd0/mtd1 on its own. **Rebuilt 2026-09-21**
  alongside `era-signed.bin` (same rootfs/package set, same caveat: this
  specific installer wrapping wasn't itself re-flashed this round — see
  above). Confirmed present with matching md5 and `mtdinfo /dev/mtd1`
  writable=true as of the 2026-09-19 build; the actual bootloader flash
  was deliberately not run during that or this test.
- `vmg8825-t50-era-signed-locked.bin` — same rootfs/kernel as
  `era-signed.bin`, wrapped for the bootloader-locked (patched mtd0)
  boot path instead of plain MAIN. **Confirmed working on hardware**
  2026-09-20: this exact file was flashed and booted via
  `bldr-patch/dev_flash_cycle.py` — clean boot, flag stayed at 0, all
  new packages and the RAM bump verified live (see above).
- `vmg8825-t50-initramfs-kernel.bin` — a full OpenWrt kernel+rootfs that
  boots straight from RAM via `bldr-patch/netboot.py`, no flash writes
  at all. See `install-guide/README.md` §2b. Useful for trying WiFi/LAN/
  USB before deciding whether to flash anything. **Rebuilt 2026-09-21**
  with the same package/RAM changes as the images above; not
  independently netboot-tested this round.

## Building your own

```sh
git submodule update --init --depth 1        # fetch upstream OpenWrt
cp -r openwrt-overlay/target/linux/econet/* openwrt/target/linux/econet/
cp -r openwrt-overlay/package/kernel/econet-eth/* openwrt/package/kernel/econet-eth/
cd openwrt
echo "CONFIG_TARGET_econet=y
CONFIG_TARGET_econet_en751627=y
CONFIG_TARGET_econet_en751627_DEVICE_zyxel_vmg8825-t50=y" > .config
./scripts/feeds update -a && ./scripts/feeds install -a
make defconfig
make -j$(nproc)
```
This builds successfully end to end (verified) and produces
`bin/targets/econet/en751627/openwrt-econet-en751627-zyxel_vmg8825-t50-squashfs-tclinux.trx`.
Wrap it into the era header with:
```sh
python3 build_era_trx.py --slim openwrt/bin/targets/econet/en751627/openwrt-econet-en751627-zyxel_vmg8825-t50-squashfs-tclinux.trx -o out.bin
```
The tool self-verifies its checksum math and reports `PASS` on all fields.
A build from current upstream OpenWrt will differ in exact size/checksums
from the shipped `era-signed.bin` above (upstream moves forward over
time) — that's expected, not a sign anything is wrong. It has **not**
been flashed/tested on real hardware; only the shipped `era-signed.bin`
carries that confirmation.

To reproduce the `-installer` variant instead of the plain one, also copy
`openwrt-overlay/files/` into `openwrt/files/` before `make` — OpenWrt
picks up `openwrt/files/` automatically and bakes its contents into the
rootfs, no `.config` change needed:
```sh
cp -r openwrt-overlay/files openwrt/
```
