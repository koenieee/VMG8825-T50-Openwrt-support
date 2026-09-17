# Known limitations / open items

## Memory

The devicetree maps a conservative 256MB (`memory@0`, `reg = <0x0
0x10000000>`) even though the board has 512MB DRAM and the OEM bootlog
reports it as such — the vendor kernel itself only mapped ~432MB. Bumping
to `0x1c000000` (448MB) or `0x20000000` (512MB) has not been tried; do so
only after confirming what your own zloader/kernel actually maps (e.g.
via a netboot session, see `bldr-patch/netboot.py`).

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

Both MT7615 radios default to `5g`. Both chips also support 2.4 GHz
(`iw phy phyN info`) — set one to `2g` in `/etc/config/wireless` for real
dual-band coverage instead of two overlapping 5 GHz APs.

## Dev loop

`bldr-patch/dev_flash_cycle.py`'s MTD3 block range (`ATER
<lo>,<hi>`) is currently a hardcoded constant matching the current
image's size and partition layout. It should instead be derived from
`/proc/mtd`/`ATSH` output at runtime so a future larger image doesn't
silently write past its own partition boundary.
