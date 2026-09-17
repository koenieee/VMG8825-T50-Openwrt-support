# firmware/

Prebuilt binaries: the current known-good build and its matching patched
bootloader. Only these two files are shipped — a pre-wrap `.trx` was
removed from here because it was a stale, older build than the shipped
`era-signed.bin` (the two are only consistent when produced together;
keeping a mismatched pair around is actively misleading). Build your own
`.trx` from source if you want one — see below.

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
  **Confirmed working on hardware**: full boot to squashfs+UBIFS-overlay
  userspace, live root shell, WiFi, USB storage, and LAN all functional.
  WAN (ETHWAN) should also work but its last fix was never re-tested
  after flashing (see `NEXT_STEPS.md`'s Ethernet section) — whether this
  exact file was built before or after that fix is not tracked; rebuild
  fresh (below) if you specifically need to test WAN. WiFi is disabled
  by default (see `install-guide/README.md` §5) — no network name or
  passphrase is baked into this image.

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
