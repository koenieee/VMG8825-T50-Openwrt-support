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
- WiFi (MediaTek MT7615, two cards, dualband) — AP and client (STA)
  mode both work, and the board splits the bands itself on first boot:
  2.4 GHz on the TSSI-calibrated card at `1fb81000.pcie`, 5 GHz on the
  external-PA card at `1fb83000.pcie`, both reaching 20 dBm. The binding
  is by PCIe path, not radio index — the calibration follows the card
  while the index follows probe order, and the other way round leaves
  2.4 GHz clamped to 10 dBm by its own zeroed target-power byte.
  Verified on a cold boot: both APs come up unattended, `phy0-ap0` on
  2.4 GHz, `phy1-ap0` on channel 36.
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
- Stable MAC address — taken from the bootloader's board-info block at
  offset `0xff48` through an nvmem cell, so the board no longer comes up
  under a random address and asks for a fresh DHCP lease on every boot.
  `wan` gets the next address up. That block is the only copy on the
  chip: `romfile`, `rom-d` and `reservearea` were read byte for byte and
  carry no MAC anywhere.
- `sysupgrade` — accepts the era-wrapped image (magic `2RDH`/`HDR2`,
  header length `0x174`) and refuses anything else with a message,
  rather than writing something zloader rejects at the next cold boot.
  Upgrading no longer means the serial netboot-and-`nandwrite` ritual.
  Proven on hardware: upgraded from a netbooted initramfs and rebooted
  into the result.
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

- **DSL does not work and is not being pursued.** ADSL2+/VDSL2 needs the
  vendor's `mt7510` module; there is no mainline driver. Treat this as a
  router with an ethernet WAN port, not as a modem.
- **Both APs come up enabled on first boot, on the OpenWrt defaults** —
  SSID `OpenWrt`, no encryption — deliberately, because a board whose
  only other way in is a serial cable should not ship with no way in at
  all. Set an SSID and a passphrase before putting it on a desk.
- **TX checksum offload is off.** The descriptor bits
  (`ETX_ICO`/`ETX_TCO`/`ETX_UCO`) are wired up, but every variant tried
  stalls a TCP transfer after a few tens of kilobytes — the frames stop
  arriving rather than arriving wrong, and the peer's `TcpInCsumErrors`
  never moves. Only affects traffic terminating on the router; forwarded
  traffic never reaches the CPU.
- **Traffic to the router's own IP is much slower than through it.**
  ~640 Mbit/s RX and ~270 Mbit/s TX at `-P4`, against 937 Mbit/s
  forwarded. That is the nature of the hardware — the vendor firmware
  does the same thing, and its own driver set gives it away (it ships a
  software-RPS module only for WiFi↔LAN, the one path its hardware NAT
  cannot offload).
- **WiFi client (STA) mode wedged the radio's firmware once** under
  sustained use: a command timeout ~13 minutes in, after which even
  `ip link show` blocks. A reboot recovered it. This is the known
  upstream `mt76`/mt7615 stability class of bug (openwrt/mt76#690,
  openwrt/mt76#897), not something this port introduces — but it has not
  been soak-tested either way.
- `gmac1`/`eth1` reports a stale `carrier=1` and carries no traffic; it
  is not wired to any socket on this board. The old
  `002-mt7530-embedded-phy-init.patch` switch-forwarding fix is
  unreachable dead code under DSA — it was chasing bugs on what turned
  out to be a jack the switch already owned.
- `dscp_byte_swap` for `en751627_soc_data` is copied from EN751221 and
  unverified — only matters once QoS/DSCP marking is in play.
- `dev_flash_cycle.py`'s MTD3 block range is hardcoded for this image's
  current size/layout; re-derive it (`ATSH`/`/proc/mtd`) if you change
  partition sizes or the image grows past the current boundary.
- Nothing here is upstream. Everything lives in this repo as patches on
  top of a submodule checkout; no part of it has been submitted.

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

Two helpers cover the normal loop once the board already runs OpenWrt:

```
tools/sync-overlay.sh          # push openwrt-overlay/ into the openwrt/ submodule
                               # (or --pull edits back; non-zero exit on drift)
tools/flash-openwrt.sh <host>  # wrap the built .trx into an era image, verify the
                               # md5 across the wire and the board name, sysupgrade
```
`openwrt-overlay/` is the tracked source of truth but nothing syncs it
automatically — a file added there reaches no built image until
`sync-overlay.sh` has run.

`bldr-patch/dev_flash_cycle.py` is the serial-console script, for when
the board is not reachable over the network — bring-up, a bad flash, or
a kernel change that does not boot:
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
| RAM | 512 MB DDR3 (devicetree maps 448 MB; confirmed booting, `MemTotal: 443152 kB`) |
| Flash | **SPI NAND** Winbond **W25M02GV**, 256 MiB, SLC, page 2048 / OOB 64 |
| Switch/ethernet | Integrated in SoC, 1xWAN + 4xLAN gigabit — all five via the in-tree `mediatek,mt7530` DSA driver, each socket confirmed with a cable and forwarding 937 Mbit/s through the PPE. `gmac1`/`eth1` (out-of-tree `econet-eth`) is unused dead hardware on this board |
| WiFi | MediaTek **MT7615** (WiFi 5, dualband), PCIe, `mt76`/`kmod-mt7615e` |
| DSL | ADSL2+/VDSL2 (vendor `mt7510` module) — no mainline driver, not supported |
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
   `install-guide/README.md` §7a). All four were rebuilt 2026-09-21 from
   the current tree. Both WiFi APs come up enabled on the OpenWrt
   defaults (SSID `OpenWrt`, open) so the board is reachable without a
   serial cable — set an SSID and a passphrase before using it; no
   network name or passphrase is baked in.

## Roadmap / help wanted

This port boots, routes at line rate and upgrades itself, but it is not
"finished". Roughly in priority order, and all of it is work somebody
with a T50 — or a sibling EN751627 board — can pick up:

- **A second board.** Everything here comes from one unit. The port
  order, the two MT7615 cards' calibration, the `0x1900` PHY calibration
  value and the `0xff48` board-info MAC offset are all "true on this
  board"; a second one either confirms them or finds the strap the
  driver should be reading instead.
- **General stability soak-testing.** Nothing has been run for days
  under load. Reboot loops, overlay behaviour when the filesystem fills,
  and WiFi throughput over time are all unverified.
- **Soak-test WiFi client (STA) mode** and characterise the firmware
  hang recorded under "Known limitations": is it always around 13
  minutes, is it STA-specific, would a busy AP hit the same bug?
- **TX checksum offload.** The scaffolding is in
  `003-checksum-offload.patch` with the failed attempts recorded beside
  it. The bit numbering is known good — `ETX_FPORT` lives in the same
  word and works — so the question is what else the GDM wants set.
- **Derive the flash block range at runtime** in `dev_flash_cycle.py`
  from `/proc/mtd`/`ATSH` rather than the hardcoded constant, so a
  larger image cannot write past its own partition.
- **Upstreaming.** The five-user-ports DSA fix and the PPE work are
  bugs and gaps in code that exists upstream; the big-endian MAC-mangle
  defect in `nf_flow_table_offload` affects `mtk_eth_soc` identically
  and has simply never been hit there. None of it has been submitted.
- **DSL**, if anyone wants it. The vendor's `mt7510` module is the only
  known route and nobody here has looked at it.
- ~~Per-port cable verification.~~ Done — all five sockets tested
  individually, which is how the labels were derived.
- ~~Verify the 448 MB RAM bump on real hardware.~~ Done —
  `MemTotal: 443152 kB`. The full 512 MB remains untried.
- ~~A full, scrubbed boot log.~~ Done —
  `install-guide/example-boot-log.txt` has a full, redacted capture from
  ATGO through a working OpenWrt shell to compare your own boot against.
