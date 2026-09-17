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
- Ethernet: LAN (switch CPU port, `gmac0`) links up and passes traffic,
  confirmed with sustained ping. WAN (`gmac1`) links up (PHY autoneg
  completes) and a small amount of RX traffic was confirmed at one point
  in development, but the switch-forwarding fix that's currently in the
  driver (see `NEXT_STEPS.md`) was applied and flashed *after* that
  confirmation and has not itself been re-verified with a cable in WAN —
  treat WAN as "should work, not reconfirmed" rather than "confirmed".
  The switch runs as a single unmanaged bridge (no DSA/per-port VLAN
  support for this SoC yet in OpenWrt).
- USB storage.
- Persistent `/overlay` (UBIFS-backed on its own MTD partition, not
  tmpfs) — config survives reboots.
- Flashing MAIN via the raw `ATER`/`ATWF` primitives (not `ATUR`, which
  silently stops writing NAND after a device's first successful flash).
  `bldr-patch/dev_flash_cycle.py` automates the full
  build -> flash -> boot -> verify cycle.
- A fully unattended coldboot, once the bootloader has both patches from
  `BOOTLOADER-PATCH.md` applied: no `ATSE`/`ATEN` unlock needed, boot
  flag stays at 0, boots straight from main.

## Known limitations

- **WAN (`gmac1`/ETHWAN) needs a real re-test.** The switch-forwarding
  fix currently in `002-mt7530-embedded-phy-init.patch` (a PCR matrix
  write so port 4 forwards to the CPU) was written, built, and flashed,
  but the session that added it ended before confirming it with a cable
  in WAN — see `NEXT_STEPS.md` for the exact sequence of bugs found and
  fixed, and what to actually check.
- The devicetree currently maps a conservative 256 MB of the board's
  512 MB RAM; bumping this to the full amount hasn't been tried (see
  `NEXT_STEPS.md`).
- Only LAN port 1 has been individually hardware-tested; LAN2-4 share
  the same switch and should work but that hasn't been verified port by
  port.
- No DSA (`mediatek,mt7530`) support for this target in OpenWrt yet — the
  switch runs as a dumb, unmanaged bridge (all LAN ports together, no
  per-port VLAN control from Linux).
- Both WiFi radios default to 5 GHz; both chips also support 2.4 GHz —
  set one radio's `band` to `2g` in `/etc/config/wireless` for real
  dual-band instead of two overlapping 5 GHz APs.
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
| RAM | 512 MB DDR3 (devicetree currently maps a conservative 256 MB, see `NEXT_STEPS.md`) |
| Flash | **SPI NAND** Winbond **W25M02GV**, 256 MiB, SLC, page 2048 / OOB 64 |
| Switch/ethernet | Integrated in SoC, 1xWAN + 4xLAN gigabit — no mainline driver, out-of-tree `econet-eth` used |
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
checksum format see `CHECKSUM_RESOLUTION.md`.

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
 - `firmware/` — the two prebuilt artifacts you actually flash:
   `vmg8825-t50-bootloader-patched.bin` (patched bootloader, mtd0) and
   `vmg8825-t50-era-signed.bin` (OpenWrt build, ready for MAIN/mtd3).
   Wireless is disabled by default in the OpenWrt image; no network
   name/passphrase is baked in.

## Roadmap / help wanted

This port boots and runs, but is not "finished" — the following are the
known gaps a contributor could pick up next, roughly in priority order:

- **Re-test WAN (`gmac1`/ETHWAN) under real traffic.** The switch PCR
  matrix fix for port 4 is flashed but was never re-confirmed with a
  cable in WAN afterwards — see `NEXT_STEPS.md`'s Ethernet section for
  the exact fix history and what to actually test.
- **Per-port LAN2-4 verification.** Only LAN1 has been individually
  hardware-tested; the other three sit on the same switch and should
  work but haven't been checked port by port.
- **DSA support for this target.** The switch currently runs as one
  dumb, unmanaged bridge — no `mediatek,mt7530` DSA integration in
  OpenWrt yet, so no per-port VLAN/isolation control from Linux.
- **Bump mapped RAM from 256 MB to the full 512 MB** (or at least the
  ~432 MB the vendor kernel mapped) — untried, see `NEXT_STEPS.md`.
- **General stability soak-testing.** Nothing here has been run for
  days under load; reboot loops, overlay-fs behavior under low disk,
  and WiFi throughput/stability over time are all unverified.
- **A second confirmed unit.** Everything here comes from one physical
  board; the partition layout and bootloader banner match in theory
  across the same firmware revision (see `install-guide/README.md`'s
  precondition), but a second independent confirmation would harden
  that assumption considerably.
- **A full, scrubbed boot log.** `install-guide/README.md` documents
  the flashing procedure but there's no reference transcript of a full
  cold boot (bootloader banner through OpenWrt login prompt) to compare
  your own console output against. Adding one — with the per-unit
  MAC/serial/board-info lines redacted the same way
  `BOOTLOADER-PATCH.md` describes for the bootloader binary — would
  help newcomers confirm they're seeing a healthy boot vs. something
  gone wrong.

Contributions on any of the above are welcome — open an issue or PR.

## Sources

- Wiki (specs): https://openwrt.org/inbox/toh/zyxel/zyxel_vmg8825-t50
- econet target (base): https://github.com/openwrt/openwrt/tree/main/target/linux/econet
- EN75xx platform PR: https://github.com/openwrt/openwrt/pull/19021
- EcoNet Linux project: https://econet-linux.pkt.wiki/
