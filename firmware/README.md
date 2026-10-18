# firmware/

Prebuilt binaries: the current known-good OpenWrt build for MAIN, its
locked-bootloader variant, and the RAM-boot kernel. This project ships
**no prebuilt bootloader binary** — the patched bootloader (mtd0/mtd1)
is built by each user from their own device's bootloader dump, so it
never carries anyone else's board-info block (MAC/serial). See
`BOOTLOADER-PATCH.md` and `install-guide/README.md` §5a. A pre-wrap
`.trx` was removed from here because it was a stale, older build than
the shipped `era-signed.bin` (the two are only consistent when produced
together; keeping a mismatched pair around is actively misleading).
Build your own `.trx` from source if you want one — see below.

> **All images were rebuilt 2026-10-18** from the current tree, with
> `tools/build-firmware.sh`. Against the 2026-09-21 images that were
> flashed and tested on hardware, the kernel is **byte-for-byte
> identical** (compared inside the era images). The rootfs differs only
> in three new scripts plus their package metadata:
> `/etc/uci-defaults/08_drop-missing-target-feed` (no more apk 404),
> `/sbin/vmg8825-format-overlay` (install §6a without the `.bin`) and
> `/sbin/vmg8825-backup-flash` (install §5 in one command). Also,
> `modules.builtin` now lists the watchdog driver, which was already in
> the kernel. These exact files have not been booted on hardware
> themselves. If one misbehaves, the 2026-09-21 images are in git history
> (`git log -- firmware/`). Check your download with
> `sha256sum -c SHA256SUMS` in this directory, or run `tools/preflight.sh`.

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
- `vmg8825-t50-era-signed-locked.bin` — same rootfs/kernel as
  `era-signed.bin`, wrapped for the bootloader-locked (patched mtd0)
  boot path instead of plain MAIN. **Confirmed working on hardware**
  2026-09-20: this exact file was flashed and booted via
  `bldr-patch/dev_flash_cycle.py` — clean boot, flag stayed at 0, all
  new packages and the RAM bump verified live (see above).
- `vmg8825-t50-initramfs-kernel.bin` — a full OpenWrt kernel+rootfs that
  boots straight from RAM via `bldr-patch/netboot.py`, no flash writes
  at all. See `install-guide/README.md` §4. Useful for trying WiFi/LAN/
  USB before deciding whether to flash anything. Its RAM shell also has
  `vmg8825-backup-flash` and `vmg8825-format-overlay` for §5 and §6a.
  `bldr-patch/netboot-stub-manual.txt` is generated for exactly this
  file. Before 2026-10-18 that stub was sized for an older, smaller
  kernel, so the by-hand netboot ended in `XZ-compressed data is
  corrupt`; `tools/preflight.sh` now catches a stub/kernel mismatch.
- `vmg8825-t50-rootfs-data.bin` — an empty UBI image holding only the
  `rootfs_data` volume, written once to the `rootfs_data` partition with
  `ubiformat` (`install-guide/README.md` §6a). Without it OpenWrt keeps
  its settings in RAM and forgets them on every reboot. Not added until
  2026-10-18; the guide did not have the step before that either.
  (`ubinize` stamps a random image sequence number into it, so it differs
  from build to build; that is expected.)
- `diffconfig` — the OpenWrt build config these images were built with
  (see below).
- `SHA256SUMS` — checksums of everything above.

## Building your own

```sh
git submodule update --init --depth 1        # fetch upstream OpenWrt
cp -r openwrt-overlay/target/linux/econet/* openwrt/target/linux/econet/
cp -r openwrt-overlay/package/kernel/econet-eth/* openwrt/package/kernel/econet-eth/
cd openwrt
./scripts/feeds update -a && ./scripts/feeds install -a
cp ../firmware/diffconfig .config
make defconfig
make -j$(nproc)
```
`firmware/diffconfig` is the exact config of the shipped images, from
`scripts/diffconfig.sh`: the target, both device profiles (`-t50` and
`-t50-locked`), the RAM-boot initramfs, and `iperf3` for testing. The
EN751627 support itself is not in that file and not a separate package
feed: it is the target code in `openwrt-overlay/target/linux/econet/`
(device trees, the `en751627` subtarget and its kernel config
`en751627/config-6.18`, kernel patches, base-files) plus the ethernet
driver in `openwrt-overlay/package/kernel/econet-eth/`, which the two `cp`
lines put into the OpenWrt tree. The modules come out of the normal build.
The device's package list (WiFi, USB storage, LuCI, ...) is
`DEVICE_PACKAGES` in `openwrt-overlay/target/linux/econet/image/en751627.mk`.
After changing the overlay later, `tools/sync-overlay.sh` copies it over
again.
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
