# OpenWrt on the Zyxel VMG8825-T50

Reverse-engineered OpenWrt port for the **Zyxel VMG8825-T50** (EcoNet
EN7516 / EN751627 SoC), including a bootloader patch that removes the
vendor's RSA-signature and CRC checks so unsigned images boot unattended.

This is an unsupported device with no official OpenWrt install path.
Getting OpenWrt onto it requires opening the case, wiring up a serial
console, and following `install-guide/README.md` step by step.

## What works

- OpenWrt boots fully from MAIN (`tclinux`/mtd3): kernel + squashfs
  rootfs + a persistent UBIFS overlay on its own MTD partition, `procd`,
  root shell.
- WiFi (MediaTek MT7615, dualband) — AP and client (STA) mode both work.
  If your board's radio lacks factory calibration (no on-die eFuse data,
  no calibration in flash — a known issue on some MT7615E mPCIe cards
  without an onboard eeprom chip), a generic calibration blob is provided
  inline in the devicetree instead of pointing at flash.
- Ethernet: all five sockets run through the on-die MT7530-class switch
  as real DSA ports (`mediatek,mt7530` in-tree driver, port CREV register
  confirmed `0x7530`). The sockets are wired in physical order, switch
  port 0 at the USB end through port 4 at the blue one, while the case
  counts the LAN sockets the other way -- so the labels run backwards
  against the port numbers:

  | socket (silkscreen) | interface | switch port | PHY |
  | --- | --- | --- | --- |
  | LAN4 (nearest USB) | `lan4` | 0 | `mt7530-0:08` |
  | LAN3 | `lan3` | 1 | `mt7530-0:09` |
  | LAN2 | `lan2` | 2 | `mt7530-0:0a` |
  | LAN1 | `lan1` | 3 | `mt7530-0:0b` |
  | WAN (blue) | `wan` | 4 | `mt7530-0:0c` |

  Each socket was identified with a cable in hand and then benchmarked.
  Switch port 0 needed both a devicetree node and a driver fix: the
  EN751627 entry borrowed EN7528's port-capability table, which starts at
  port 1, so phylink rejected port 0 outright and that socket reached no
  interface at all
  (`783-net-dsa-mt7530-en751627-five-user-ports.patch`).

  `gmac1`/`eth1` is a separate MAC with no PHY polled; it reports a stale
  `carrier=1` and carries no traffic. It is not wired to any socket on
  this board.
- Hardware NAT offload (PPE) — see the performance section below.
- Hardware watchdog — the EN751627's timer3 block (same as the EN7581's,
  at `0x1fbf0100`) drives `/dev/watchdog0` through the in-tree
  `airoha_wdt` driver, and `procd` pets it, so a wedged kernel reboots on
  its own. Verified on hardware: the cdev is backed by platform device
  `1fbf0100.watchdog`.
- USB storage — `kmod-usb-storage`/`block-mount`/ext4+vfat+nls kmods now
  ship in the default image (previously only worked via a manual
  `apk add` during testing).
- LuCI web UI and SFTP (`openssh-sftp-server`, since dropbear only runs
  an SFTP server, it doesn't provide one) ship by default too — see
  `NEXT_STEPS.md` "Packages".
- RAM: the devicetree maps 448 MB of the board's 512 MB (up from a
  conservative 256 MB) — confirmed booting clean (`MemTotal: 443152 kB`).
- Persistent `/overlay` (UBIFS-backed on its own MTD partition, not
  tmpfs) — config survives reboots.
- Flashing MAIN via the raw `ATER`/`ATWF` primitives (not `ATUR`, which
  silently stops writing NAND after a device's first successful flash).
  `bldr-patch/dev_flash_cycle.py` automates the full
  build -> flash -> boot -> verify cycle.
- A fully unattended coldboot, once the bootloader has both patches from
  `BOOTLOADER-PATCH.md` applied: no `ATSE`/`ATEN` unlock needed, boot
  flag stays at 0, boots straight from main.

## Performance

Routed throughput, measured port by port on real hardware with iperf3
(router-on-a-stick, VLAN in / VLAN out, three runs per figure):

| | forwarded | router CPU |
| --- | --- | --- |
| hardware flow offload (PPE) | **937 Mbit/s** | 99.9% idle |
| software flow offload | ~300 Mbit/s | ~61% idle |
| no offload | ~310 Mbit/s | ~61% idle |

937 Mbit/s is the wire, both directions, on every one of the five
sockets. The prebuilt images turn this on by default -- the driver brings
the PPE up on load and `flow_offloading`/`flow_offloading_hw` are set in
the shipped firewall config -- so there is nothing to enable after
flashing.

The offload lives in `008-ppe-flow-offload-fixes.patch`. Three things in
it are worth knowing if you are porting this elsewhere:

- The FOE entry stride on EN751627 is **64 bytes**, not the 80 upstream
  `mtk_eth_soc` uses, and the table is little-endian while the CPU is
  big-endian. Entries are whole-word byte-swapped, so every `u16` pair
  has to be declared back-to-front relative to upstream.
- `act_dp`, the egress port, is a single **byte at offset 40** for the
  IPv4 class -- the old Ralink layout, where upstream has `udf_tsid`.
- `nf_flow_table_offload` builds its two MAC mangles arithmetically, so
  upstream's fixed `src += 2` reads the empty half on a big-endian host
  and every offloaded flow goes out addressed to `xx:xx:xx:xx:00:00`.
  **`mtk_eth_soc` has the identical defect**; it has simply never been
  hit, because MediaTek's targets are little-endian.

The RX descriptor's `ppe_entry` field does not address the slot the
engine wrote, so flows are bound by reproducing the vendor's own hash
(`FoeHashFun`, HASH_MODE 1) and writing the entry straight to the slot
the engine will look in. That reproduction is verified live: 42 flows
checked, 42 agreeing with the hardware.

## Known limitations

- **Resolved 2026-09-20:** the jack silkscreened "WAN" is switch port 4
  (`lan4`), confirmed by a live cable-swap test — see "What works"
  above and `NEXT_STEPS.md`'s Ethernet section for the full writeup.
  The old `002-mt7530-embedded-phy-init.patch` switch-forwarding fix
  (PCR matrix write for "port 4") is unreachable dead code under DSA;
  it was chasing bugs on this same physical jack. `gmac1`/`eth1`
  appears to be unused/dead hardware on this board.
- LAN1/2 (switch ports 1, 2) haven't been individually
  cable-tested since the DSA switch-over; `lan3` and `lan4` were both
  confirmed with a cable on 2026-09-20. `ip link show` confirms all four
  DSA netdevs exist. LAN throughput numbers below predate the DSA switch
  and were measured against the old unmanaged-bridge driver — see
  `NEXT_STEPS.md`.
- Both WiFi radios default to 5 GHz; a board-specific uci-defaults
  script now flips `radio1` to 2g on first boot for real dual-band
  instead of two overlapping 5 GHz APs — untested on real hardware yet.
- WiFi client (STA) mode was observed to wedge the radio's firmware
  after sustained runtime (recurring firmware-timeout errors, recovered
  by a reboot) — see `NEXT_STEPS.md` for the one occurrence recorded so
  far; not yet characterized as reproducible or STA-specific.
- `dev_flash_cycle.py`'s MTD3 block range is hardcoded for this image's
  current size/layout; re-derive it (`ATSH`/`/proc/mtd`) if you change
  partition sizes or the image grows past the current boundary.

## The bootloader's three gates

Every boot of MAIN goes through three independent checks; failing any of
them makes the bootloader permanently set `boot flag=1` (boot slave),
which then survives every following reboot until explicitly cleared:

1. **CRC/checksum** (`main tclinux.bin crc check error!`) — still
   requires a correctly built image (`build_era_trx.py` self-verifies
   this at build time), but is also patched out of the bootloader itself
   (see `BOOTLOADER-PATCH.md`). That patch is needed because UBI's own
   ECC scrubbing can physically rewrite a block inside `tclinux` over
   time (normal NAND behavior), which would otherwise make even a
   correctly built image fail this check on a later boot.
2. **RSA signature** (`Wrong image hash value ...`) — patched out of the
   bootloader itself (see `BOOTLOADER-PATCH.md`).
3. **Boot flag** — a persistent byte, independent of 1 and 2. Once set to
   1 by a failed boot it stays that way until reset with `ATBT 1` +
   `ATSW` (after `ATSE`/`ATEN` debug-unlock). `dev_flash_cycle.py`
   automates this recovery with a single retry. With both checksum gates
   patched, a clean flash keeps the flag at 0 and this gate is no longer
   hit in normal use.

## Development workflow

`bldr-patch/dev_flash_cycle.py` is the iteration script for
kernel/rootfs changes:
```
python3 bldr-patch/dev_flash_cycle.py --slim <fresh .trx>   # wrap, flash, boot-test
python3 bldr-patch/dev_flash_cycle.py --era <already-wrapped .bin>
python3 bldr-patch/dev_flash_cycle.py --boot-only            # power-cycle + ATGO + analysis only
```
It power-cycles the router as one blocking step immediately before
catching the bootloader's `ZHAL>` prompt (the autoboot window is only
~5s, so this can't be split across separate manual steps without missing
it). By default this is a manual prompt (`bldr-patch/power_cycle.py`);
set `POWER_BACKEND=ha` with `HA_URL`/`HA_TOKEN`/`HA_POWER_ENTITY` to
drive a Home Assistant-controlled smart plug instead, or adapt that
module for a different smart-plug API.

---

## Hardware (VMG8825-T50)

| Component | Detail |
|-----------|--------|
| SoC | EcoNet **EN7516** (EN751627 family), MIPS **1004Kc**, 2x900 MHz, big-endian |
| OpenWrt target | `econet` / subtarget **`en751627`** |
| Tested against | OpenWrt `main` @ [`928cd26`](https://github.com/openwrt/openwrt/commit/928cd26bd938b8ac46b79e14f5f9f4b1d772abe8) (2026-09-17), kernel **6.18** — `openwrt/` is a submodule tracking `main`, which moves; `git checkout 928cd26` in `openwrt/` to reproduce the exact tested combination, or use it as a starting point and expect some drift on a newer checkout |
| RAM | 512 MB DDR3 (devicetree maps 448 MB, untested — see `NEXT_STEPS.md`) |
| Flash | **SPI NAND** Winbond **W25M02GV**, 256 MiB, SLC, page 2048 / OOB 64 |
| Switch/ethernet | Integrated in SoC, 1xWAN + 4xLAN gigabit — all via in-tree `mediatek,mt7530` DSA driver; WAN jack = `lan4`, confirmed on HW 2026-09-20 (1Gbps/Full, live traffic). `gmac1`/`eth1` (out-of-tree `econet-eth`) is unused dead hardware on this board |
| WiFi | MediaTek **MT7615** (WiFi 5, dualband), PCIe, `mt76`/`kmod-mt7615e` |
| DSL | ADSL2+/VDSL2 (vendor `mt7510` module) — no mainline driver, not pursued |
| USB | 1x USB2.0 + 1x USB3.0, mass storage tested |
| Serial console | 115200 8N1, CR-only, `/dev/ttyUSB0`, 3.3V header. No JTAG. |
| Bootloader | Zyxel **zloader v1.4.4** (2021-01-04), legacy TRX+RSA, LZMA kernel |

Source: https://openwrt.org/inbox/toh/zyxel/zyxel_vmg8825-t50

---

## Getting started

Start at `install-guide/README.md` — it covers the precondition (which
exact zloader build this applies to), flashing the prebuilt patched
bootloader, and flashing the prebuilt OpenWrt image, in order.

For the bootloader patch's technical details (what it changes, why both
gates are needed) see `BOOTLOADER-PATCH.md`. For the vendor image
checksum format see `CHECKSUM_RESOLUTION.md`. For the story of how this
port was debugged end to end, see `HISTORY.md`.

---

## Repo layout

This repo holds our own changes, tools, and documentation — it is not a
full OpenWrt checkout:

- `openwrt/` — upstream OpenWrt, added as a git submodule
  (https://github.com/openwrt/openwrt.git). Run
  `git submodule update --init --depth 1` after cloning.
- `openwrt-overlay/` — all of our changes to `target/linux/econet/` and
  `package/kernel/econet-eth/`, applied on top of the upstream checkout
  (see its own README for the exact copy command).
- `bldr-patch/` — the flashing toolchain (`dev_flash_cycle.py`,
  `power_cycle.py`) and the bootloader-patch build scripts
  (`patch_stage2_rsa_bypass.py`, `patch_stage2_crc_bypass.py`).
- `build_era_trx.py` — wraps a slim OpenWrt `.trx` into the era-0x174
  header this board's zloader expects, with a self-check.
- `board-files/` — DTS/image scaffolding and the era-header template
  `build_era_trx.py` uses.
- `tools/atenv3/` — the supervisor-password derivation tool needed for
  the bootloader's `ATSE`/`ATEN` debug-unlock.
 - `firmware/` — the prebuilt artifacts you actually flash:
   `vmg8825-t50-bootloader-patched.bin` (patched bootloader, mtd1),
   `vmg8825-t50-era-signed.bin` (plain OpenWrt build, MAIN/mtd3),
   `vmg8825-t50-era-signed-installer.bin` (same, plus the §7 bootloader
   flashing script baked in — use this one first), and
   `vmg8825-t50-era-signed-locked.bin` (same build, but mtd1 is read-only
   at the kernel level and the flashing script is gone — reflash to this
   one once the bootloader patch is applied and verified; see
   `install-guide/README.md` §7a). Wireless is disabled by default in the
   OpenWrt image; no network name/passphrase is baked in.

## Roadmap / help wanted

This port boots and runs, but is not "finished" — the following are the
known gaps a contributor could pick up next, roughly in priority order:

- **Per-port LAN1/2 cable verification.** DSA netdevs for all four
  ports exist and `lan3`/`lan4` (the physical WAN jack) both passed real
  traffic on 2026-09-20, but LAN1/2 haven't been individually
  cable-tested since the switch to DSA.
- **Verify the 448 MB RAM bump on real hardware** (up from 256 MB) —
  untested, see `NEXT_STEPS.md`. Full 512 MB remains untried too.
- **General stability soak-testing.** Nothing here has been run for
  days under load; reboot loops, overlay-fs behavior under low disk,
  and WiFi throughput/stability over time are all unverified.
- **A second confirmed unit.** Everything here comes from one physical
  board; the partition layout and bootloader banner match in theory
  across the same firmware revision (see `install-guide/README.md`'s
  precondition), but a second independent confirmation would harden
  that assumption considerably.
- ~~A full, scrubbed boot log.~~ Done —
  `install-guide/example-boot-log.txt` has a full, redacted capture from
  ATGO through a working OpenWrt shell to compare your own boot against.

Contributions on any of the above are welcome — open an issue or PR.

## Sources

- Wiki (specs): https://openwrt.org/inbox/toh/zyxel/zyxel_vmg8825-t50
- econet target (base): https://github.com/openwrt/openwrt/tree/main/target/linux/econet
- EN75xx platform PR: https://github.com/openwrt/openwrt/pull/19021
- EcoNet Linux project: https://econet-linux.pkt.wiki/
