# tools/

Host-side and on-device helpers for the VMG8825-T50 OpenWrt port. None of
these flash a device on their own; the two WiFi tools here only read.

## WiFi calibration (fixing weak radios)

The board reads each MT7615 radio's calibration from a **fixed** flash
offset (`wifi-caldata`: radio0 @0xeae0000, radio1 @0xeb00000). That offset
is **not the same on every unit** — a tester's VMG8825-T50 had its two
calibration blocks one 128 KiB block earlier, so each radio loaded the
wrong card's data and both came up at ~6–8 dBm instead of 20/23 dBm. These
two tools locate and extract the real per-unit calibration by **signature
scan** instead of trusting the fixed offset.

### `find-wifi-caldata-offset.sh` (on device, busybox only)

Scans the live flash for the MT7615 blocks and reports their offsets and
which radio each is, then says whether they match the DTS's hard-coded
offsets. No Python needed. Read-only.

```sh
sh tools/find-wifi-caldata-offset.sh /dev/mtd0          # whole chip, tail window
sh tools/find-wifi-caldata-offset.sh /dev/mtd0 0 0x10000000   # wider window
```

Classifies by the external-PA byte at `0xf3` (non-zero only on the 5 GHz
card). If the offsets **differ** from the hard-coded ones, that is why the
radios are weak — go to the extractor below.

### `extract-wifi-caldata.py` (on your PC)

Takes a whole-flash dump (`nanddump -f mtd-full.bin /dev/mtd0`, install
guide §5), scans it for the two MT7615 blocks, identifies radio0 (2.4 GHz,
TSSI) vs radio1 (5 GHz, external-PA), and writes:

- `eeprom_radio0.bin`, `eeprom_radio1.bin` — the raw 19880-byte blobs, and
- `<board>-unit-eeprom.dtsi` — a devicetree include that feeds each blob to
  mt76 inline via `mediatek,eeprom-data`.

```sh
python3 tools/extract-wifi-caldata.py mtd-full.bin -o wifi-caldata-out
python3 tools/extract-wifi-caldata.py mtd-full.bin --scan-only   # just report
```

The generated `.dtsi` embeds **this unit's** MAC and per-chain TX trim.
**Do not commit it and do not flash it onto another router.** Wire it into
the board DTS (replace each wifi node's `nvmem-cells`/`nvmem-cell-names`
with `mediatek,eeprom-data = /bits/ 8 <UNIT_EEPROM_RADIOx>`),
`make target/linux/clean`, and rebuild. See install-guide §8d for the
step-by-step.

> **Status:** hardware-confirmed for the per-unit path. The tester whose
> unit had shifted caldata ran both scripts against their full-flash backup
> and got the **same offsets and byte-identical `.dtsi`** they had already
> hand-made and been running (router stable ~24h on it). An automatic
> signature-scan-at-boot that would remove the per-unit rebuild is still
> open — `find-wifi-caldata-offset.sh` is the on-device building block for
> it, but nothing auto-rebinds the driver yet.

## Bootloader patch

See `../BOOTLOADER-PATCH.md`. `../bldr-patch/patch_stage2.py` is the
build-independent CRC/RSA stage2 patcher; the older
`patch_stage2_rsa_bypass.py` + `patch_stage2_crc_bypass.py` pair is a
fixed-offset reference fallback.

## Build / flash helpers

- `build-firmware.sh` — one-command firmware build.
- `build-in-docker.sh`, `docker/` — reproducible build in an Ubuntu
  container (for hosts whose native toolchain binaries don't run on the
  target — e.g. CachyOS).
- `flash-openwrt.sh` — flash an era-wrapped image via sysupgrade.
- `sync-overlay.sh` — copy overlay files into the `openwrt/` submodule
  (they do not reach a build otherwise).
- `preflight.sh` — read-only pre-install checks (tools, serial, image
  checksums, TFTP address).
- `atenv3/` — the debug-unlock password tool.
