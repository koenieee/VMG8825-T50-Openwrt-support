# Known limitations / open items

## Packages

`DEVICE_PACKAGES` for `zyxel_vmg8825-t50` (`image/en751627.mk`) only ever
listed the WiFi/USB-controller kmods — none of what a "normal" OpenWrt
router image ships was actually in there. Added, with ~46MiB of unused
headroom in the 56MiB `FACTORY_SIZE` budget (current build is ~10MiB) so
none of this is size-constrained:

- `luci` — web UI. Never on by default in upstream OpenWrt; every device
  that ships it adds it explicitly, this one didn't.
- `openssh-sftp-server` — dropbear can *run* an SFTP server
  (`DROPBEAR_SFTPSERVER` defaults on here, this device isn't
  `SMALL_FLASH`) but doesn't provide one itself; it execs
  `/usr/libexec/sftp-server`, which only this package installs.
- `coreutils-base64` — busybox's own `base64` applet defaults OFF
  upstream (`BUSYBOX_DEFAULT_BASE64 := n`), so it was genuinely missing,
  not just a cut-down version.
- `kmod-usb-storage`, `kmod-fs-ext4`, `kmod-fs-vfat`, `kmod-nls-cp437`,
  `kmod-nls-iso8859-1`, `block-mount`, `usbutils` — USB mass storage
  previously only worked via a manual `apk add` during testing (see the
  iperf3 saga under "LAN throughput" below); nothing in
  `DEVICE_PACKAGES` actually shipped it.

**Verified on real hardware** (2026-09-20): first build had a stale
`.config` problem — `make defconfig` had frozen `# CONFIG_PACKAGE_x is not
set` for these from before this change existed, and re-running `defconfig`
doesn't override an already-recorded value even after `DEVICE_PACKAGES`
adds a `select DEFAULT_x`. Had to clear those specific lines from `.config`
by hand before `defconfig` would pick the new defaults up -- this is local
build state only (`.config` is gitignored), not a repo bug. After that fix,
flashed and booted: `apk info -e` confirms all six installed, `base64`
works, `/usr/libexec/sftp-server` resolves, `lsusb` lists a real attached
device, `/www/luci-static` is populated.

## Memory

The devicetree now maps 448MB (`memory@0`, `reg = <0x0 0x1c000000>`),
bumped from the original conservative 256MB. The board has 512MB DRAM
per the OEM bootlog, but the vendor kernel itself only mapped ~432MB
(reason for the reserved ~80MB unknown); 448MB was picked as the safer
of the two previously-proposed candidates (vs. the untested full
`0x20000000`/512MB), only ~16MB past the vendor's own proven figure.
**Verified on real hardware** (2026-09-20): flashed and booted clean,
`MemTotal: 443152 kB` in `/proc/meminfo` (the ~5MB gap from 448MB is normal
kernel/reserved-memory overhead) -- no crash, no panic, boot flag stayed
at 0.

## Ethernet

- **WAN (`gmac1`/ETHWAN) needs re-testing with a cable in WAN — this is
  the single highest-priority open item.** Sequence of bugs found and
  fixed on the WAN path, in order, only the first of which was actually
  confirmed against real sustained traffic afterward:
  1. PHY calibration value fixed (`0x1900`, not `0x1d00`) — confirmed:
     PHY links, autonegotiates, and a small amount of real RX traffic
     was seen (`/proc/net/dev`, ~6 packets/994 bytes).
  2. Attempting an actual throughput/stability test on top of that
     exposed that RX stays at 0 under real traffic despite a solid PHY
     link. Found: `en75_get_sport_dev()`'s `id == 2` (gmac1/WAN) branch
     used `&eth->regs->port0.regs` instead of `&eth->regs->port1.regs`
     — a real bug in the upstream vendored driver, not something this
     project introduced — making `eth1` alias `eth0`'s GDM hardware
     registers. Fixed (one-line register-struct swap).
  3. While chasing (2), also added the OEM's per-port PVC/VLAN register
     write sequence, but applied it to *all* switch ports (copied
     verbatim from the OEM's own init loop) — this broke LAN (no ping
     at all, confirmed by reflashing and testing). Narrowed to touch
     only port 4 (WAN) and dropped the CPU-port PVC write entirely (it
     duplicates what `en75_probe()` already sets via
     `EN75_MFC_CONFIG`/`PMCR`) — LAN re-confirmed working (3/3 ping)
     after this fix.
  4. With (2) and (3) both in place, TX now worked (confirmed via qdma1
     debugfs TX-done counters) but RX was *still* stuck at 0 — a
     different bug from calibration or from the GDM alias: cross-checked
     against upstream `drivers/net/dsa/mt7530.c`'s `mt7530_port_enable()`
     and found port 4 was missing its PCR "matrix" (forwarding-mask)
     register write, so the switch was dropping ingress frames on that
     port instead of forwarding them to the CPU/GDM queue (`PMCR`/`PCR`
     are different registers at different offsets; ports 5/6 got their
     PMCR write from `en75_probe()`, port 4 never got a PCR write at
     all). Added a single, port-4-only PCR_MATRIX write. Built, flashed,
     booted successfully — **and the session ended there**, before
     re-testing WAN RX with this fix in place. All four fixes above are
     already in `002-mt7530-embedded-phy-init.patch` in this repo; only
     the final re-test of (4) is missing.
  - **What to actually do:** flash the current build, put a cable in
    WAN, and repeat the throughput/stability test from step 2 (`iperf3`
    or even just a large sustained ping/transfer) — not just a link-up
    check, since link-up already passed at every stage above without
    catching bugs (2) or (4).
- Only LAN port 1 has been individually tested with a cable; LAN2-4 sit
  on the same switch and should come up automatically once the switch is
  released from the CPU port, but this hasn't been separately confirmed
  port by port.
- The switch's embedded PHYs need an MDIO calibration sequence at
  startup (`en75_mt7530_embedded_phy_init()` in
  `package/kernel/econet-eth/patches/002-mt7530-embedded-phy-init.patch`)
  — without it the PHYs never link, even though the MAC registers
  (PMCR/MFC) are otherwise configured correctly. The vendor driver picks
  between two calibration values (`0x1900`/`0x1d00`) based on
  board-strap registers outside the driver's own register window;
  `0x1900` is confirmed correct for this board via live PHY register
  readback (`BMCR`/`BMSR`/`ANLPAR`) after boot.

- `dscp_byte_swap = true` for `en751627_soc_data` is unverified (copied
  from EN751221 as the closest relative) — only relevant once
  QoS/DSCP marking is in play.
- No DSA (`mediatek,mt7530`) support for this target — the switch runs
  as an unmanaged/dumb switch in hardware (all LAN ports bridged, no
  per-port VLAN control from Linux). Same approach as
  `en751221_tplink_archer-vr1200v-v2.dts`.

### LAN throughput (measured, 2026-09-17)

First real throughput numbers for LAN (previously only ping-confirmed).
Measured with `iperf3` between a single LAN-connected host and the
router, gigabit link on both ends, one TCP stream unless noted:

| Test                          | Result                                  |
|--------------------------------|------------------------------------------|
| Sustained ping, 200 packets     | 0% loss, 0.17–0.5 ms RTT                 |
| TCP, host → router              | ~525 Mbit/s, ~230 retransmits/10s        |
| TCP, router → host (reverse)    | ~350–410 Mbit/s, 0 retransmits           |
| TCP, 4 parallel streams          | ~350 Mbit/s combined (no better than 1 stream) |
| UDP, 100 Mbit/s target           | 100 Mbit/s achieved, 0.014% loss, ~0.01 ms jitter |

Stable under load (no crashes, no drops at moderate UDP rate), but the
TCP ceiling sits well under gigabit line rate, with retransmits
appearing specifically on the higher-throughput (host→router)
direction. Reads as a CPU/interrupt-handling bottleneck on this
single-/dual-core embedded SoC rather than a PHY or switch-config
problem, but that's an interpretation, not confirmed via profiling —
worth revisiting if someone wants to chase real gigabit LAN throughput.

**Getting `iperf3` onto the device at all was its own small saga**,
worth documenting since it'll bite the next person too: this OpenWrt
snapshot uses `apk`, not the older `opkg`, and `apk update`/`apk add`
partially fail because the target-specific package feed
(`targets/econet/en751627/packages/packages.adb`) 404s upstream — this
target isn't (yet) building official target-specific binary packages.
That feed is where an architecture-specific dependency (`libatomic1`
for `libiperf3`) lives, so a plain `apk add iperf3` fails on a missing
dependency that simply isn't published anywhere reachable. Worked
around by cross-compiling `iperf3` locally from the already-checked-out
`openwrt/` + `openwrt-overlay/` tree (`make package/iperf3/{clean,compile}`)
and copying the resulting binary plus `libiperf.so`/`libatomic.so`
straight onto the device's writable overlay over SSH (`scp` doesn't
work — dropbear here has no `sftp-server`; pipe the file through
`ssh ... "cat > /path"` instead). This is a one-off, not persisted in
the shipped image — expect to redo it (or add `iperf3` to
`DEVICE_PACKAGES` and rebuild) if you need it again after a reflash.

## Writable overlay partition placement

The persistent `/overlay` volume (`rootfs_data`) lives on its own MTD
partition, separate from `tclinux`'s own UBI instance — this keeps
`tclinux` byte-static after flashing, which the bootloader's boot-time
CRC check (see `BOOTLOADER-PATCH.md`) depends on. Two other placements
were tried and rejected before landing on the current one:

- The `wwan` partition (1 MiB) is too small: UBIFS needs at least 17
  LEBs (~2.1 MiB) to mount at all.
- The full `misc`/`reservearea` partition (118 MiB) was rejected because
  its first ~36 MiB holds the WiFi calibration blob (still referenced by
  the `&factory` node for the MT7615 radios) plus OEM data — overwriting
  it would destroy WiFi calibration.
- The current choice is the tail of `misc` (`0x98e0000`-`0xeae0000`,
  82 MiB), well past both the calibration data and OEM data (which stay
  on the unmodified, read-only `reservearea` node,
  `0x74e0000`-`0x98e0000`).

If the `rootfs_data` UBI instance ever fails to attach, OpenWrt's
`mount_root` falls back to creating an autoresize `rootfs_data` volume
*inside* `tclinux`'s own UBI instance — exactly the problem this
partition split avoids. That fallback must never trigger in practice, so
give `rootfs_data` a healthy margin above the 17-LEB minimum.

## WiFi

Both MT7615 radios default to `5g`; a board-specific uci-defaults script
(`base-files/etc/uci-defaults/05_vmg8825-t50-wifi-band-split`) now flips
`radio1` to `2g` on first boot for real dual-band instead of two
overlapping 5 GHz APs — untested on real hardware yet, verify after
flashing.

The STA-mode hang below is a separate, upstream `mt76`/mt7615
firmware-stability class of bug (see
https://github.com/openwrt/mt76/issues/690,
https://github.com/openwrt/mt76/issues/897), not this project's own bug
or something a config change fixes. Mitigation is operational (avoid
sustained STA mode, soak-test AP-only use), not a code fix.

### Client-mode (STA) radio hang, observed under real use (2026-09-17)

One radio was configured as a WiFi client (STA mode, joining an
existing home AP) to give the device an internet route for testing.
Association and DHCP worked fine initially, but roughly 13 minutes into
uptime the radio produced a kernel warning:

```
WARNING: ... __ieee80211_stop_tx_ba_session+0x210/0x2a8 [mac80211]
```

immediately followed by continuous, recurring firmware-communication
timeouts from the mt7615e driver (`Message 0000XXed (seq N) timeout`,
roughly every 20 seconds, indefinitely — never recovered on its own).
The practical symptom was severe: even `ip link show`/`ip addr show`
(which enumerate *all* net devices, not just the wifi ones) started
hanging indefinitely, which looks like the kernel blocking on a lock
held by the wedged firmware-command path.

A fresh reboot recovered it immediately (STA re-associated fine, no
repeat within the shorter test window that followed) — so this reads as
a firmware/driver stability issue under sustained STA-mode operation,
not a permanent fault. Only observed once, only in STA mode, only after
sustained runtime, and not correlated with a specific trigger (no
heavy traffic on that radio at the time) — needs a longer, deliberate
soak test to characterize (does it always happen around ~13 minutes?
Is it STA-mode specific, or would a busy AP-mode radio hit the same
firmware bug?) rather than a real fix yet.

## Dev loop

`bldr-patch/dev_flash_cycle.py`'s MTD3 block range (`ATER
<lo>,<hi>`) is currently a hardcoded constant matching the current
image's size and partition layout. It should instead be derived from
`/proc/mtd`/`ATSH` output at runtime so a future larger image doesn't
silently write past its own partition boundary.
