# openwrt-overlay

Drop-in copy of `target/linux/econet/` from our OpenWrt tree
(https://github.com/openwrt/openwrt.git, `main` branch), plus a small
addition under `package/kernel/econet-eth/`. It already contains
upstream's `en751221`/`en7528` boards plus our additions for the
Zyxel VMG8825-T50.

To use: clone stock OpenWrt, then copy this over the matching paths:

```sh
git clone https://github.com/openwrt/openwrt.git
cp -r openwrt-overlay/target/linux/econet/* openwrt/target/linux/econet/
cp -r openwrt-overlay/package/kernel/econet-eth/* openwrt/package/kernel/econet-eth/
```

## Changes vs. upstream

- `en751627/config-6.18` — kernel config tweaks for the T50, including
  builtin `CONFIG_PHY_ECONET_USB=y`.
- `image/en751627.mk` — the `zyxel_vmg8825-t50` image target, using
  `append-ubi-static-rootfs` (see below) instead of the default
  `append-ubi`.
- `image/Makefile` — `Build/append-ubi-static-rootfs` and
  `Build/rootfs-data-ubi`: build a `tclinux` UBI image with only the
  `rootfs` volume (no auto-appended, autoresizing `rootfs_data` volume
  inside it), plus a separate standalone UBI image containing just an
  empty `rootfs_data` volume for its own MTD partition. This keeps
  `tclinux` byte-static after flashing, which the bootloader's boot-time
  CRC check depends on — see `NEXT_STEPS.md`.
- `dts/en751627_zyxel_vmg8825-t50.dts` — the board devicetree: MT7615
  WiFi (both radios), USB, the `rootfs_data` partition node, `gmac0`
  (LAN) and `gmac1` (ETHWAN) enabled, and the `bootloader` partition
  node without `read-only;` (needed once to apply the bootloader patch
  from `BOOTLOADER-PATCH.md`; can be set back to read-only afterwards).
- `dts/en751627_zyxel_vmg8825-t50-mt7615-eeprom.dtsi` — a generic
  MT7615 calibration blob wired in via `mediatek,eeprom-data`, for
  boards whose radio has no factory calibration in eFuse or flash.
- `dts/en751627.dtsi` — added USB PHY devicetree nodes (`usb_xtal`,
  `usb_phy`/`usb2_phy_p0`/`usb3_phy_p0`), with
  `mediatek,u3p-dis-msk = <0x1>` on the `usb` xhci-mtk node (this board
  only has USB 2.0 physically wired, so U3 port 0 is masked off); and a
  new `ethernet@1fb50000` node (`compatible = "econet,en751627-eth"`,
  `reg = <0x1fb50000 0x10000>` — deliberately en751221-sized so the
  driver's `has_switch_regs` path programs the switch CPU port; a
  smaller, 0x8000-sized region leaves `eth0` link-up but passing zero
  traffic) with `gmac0`/`gmac1` child nodes.
- `patches-6.18/916-phy-add-econet-usb-phy-driver.patch` — imports the
  not-yet-upstream "phy: econet: Add EcoNet USB PHY" driver (Caleb James
  DeLisle, linux-mips@vger.kernel.org), with EN7528 support stripped to
  avoid conflicting with the existing
  `914-phy-add-en7528-usb-phy-driver.patch`.
- `package/kernel/econet-eth/patches/001-add-en751627-support.patch` —
  the vendored `cjdelisle/econet_eth` driver only had `of_device_id`
  entries for `econet,en751221-eth`/`econet,en7528-eth`; adds
  `en751627_soc_data` + the `econet,en751627-eth` match. `dscp_byte_swap
  = true` is copied from EN751221 and unverified on real hardware (see
  `NEXT_STEPS.md`).
- `package/kernel/econet-eth/patches/002-mt7530-embedded-phy-init.patch`
  — adds `en75_mt7530_embedded_phy_init()`, the MDIO calibration
  sequence the switch's embedded WAN PHY needs at startup; without it
  `gmac1`/ETHWAN never links even though its MAC registers are
  configured correctly (see `NEXT_STEPS.md`).
- `VMG8825-T50-FLASHING.md` — flashing/boot notes for this board.
- `scripts/` — board-specific helper scripts (TFTP flash wrapper,
  autoboot watcher).
- `files/root/` — baked into the rootfs via OpenWrt's automatic
  `$(TOPDIR)/files` pickup (no `.config` change needed): the patched
  bootloader binary plus `flash-patched-bootloader.sh`, which does §7 of
  `install-guide/README.md` for you from a running shell. Only present
  in the `-installer` firmware variant — see `firmware/README.md`.

**Build pitfall:** after patching `econet-eth`'s driver source,
`package/kernel/econet-eth/{clean,compile}` + `target/install` alone
does **not** refresh `root.squashfs` — run `make package/install` first
(see `AGENTS.md`), or `strings` the resulting `.ko` inside the built
squashfs before flashing to confirm it's not stale.

See the top-level `README.md`, `BOOTLOADER-PATCH.md` and `NEXT_STEPS.md`
for the full picture.
