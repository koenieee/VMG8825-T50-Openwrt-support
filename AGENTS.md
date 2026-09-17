# Agent instructions for this repo

## Flashing / hardware testing: ALWAYS use `bldr-patch/dev_flash_cycle.py`

Do **not** hand-roll UART control (raw `termios`/`stty`/`cat /dev/ttyUSB0`,
manual plug toggling, manual `ATER`/`ATWF`/`ATGO` sequences, manual netboot
rituals, etc.) to flash or boot-test the VMG8825-T50.

`bldr-patch/dev_flash_cycle.py` already implements the full, safety-checked
cycle end to end:
- Power-cycle (`bldr-patch/power_cycle.py`, manual prompt by default,
  optional Home Assistant backend)
- ATSE/ATEN debug-unlock (fresh every boot, required)
- Flash MAIN via ATER/ATWF (not ATUR — that silently no-ops after first use)
- ATGO + boot console parsing
- Auto-recovery of a stuck boot flag (one retry)
- Clear PASS/FAIL verdict
- Hard safety invariant: only mtd3 (tclinux/MAIN, blocks 4-451 inclusive) is
  ever erased/written; tclinux_slave is never touched.

Usage:
```
# Build a fresh slim .trx first (make in openwrt/), then:
python3 bldr-patch/dev_flash_cycle.py --slim <fresh .trx>   # wrap + flash + boot-test
python3 bldr-patch/dev_flash_cycle.py --era <already-wrapped .bin>
python3 bldr-patch/dev_flash_cycle.py --boot-only            # re-test current MAIN, no flash
```

If this script doesn't cover what you need (e.g. a genuinely new kind of
hardware interaction), extend it or write a new script following its
patterns — do not bypass it with ad-hoc one-off UART code. Manual UART
fumbling has previously left the device in a silent/stuck state requiring
a fresh, correctly-timed power-cycle to recover.

See `README.md` §"Development workflow" for more context.

## Rebuilding a target image after a kernel/package patch

`make package/kernel/<pkg>/{clean,compile}` only rebuilds the `.ko`/`.apk`
into `staging_dir/target-.../root-<profile>/` — it does **NOT** refresh
`build_dir/target-.../root-<profile>/`, which is the actual source
`mksquashfs` reads from. Skipping this step silently flashes a **stale**
module even though the package compile step succeeded and the timestamps
look right. Full sequence to get a patch into a flashable `.trx`:

```
make package/kernel/<pkg>/{clean,compile}   # rebuild the package
make package/install                        # sync into build_dir/.../root-<profile>/ (the missing step)
rm -f build_dir/target-*/<subtarget>/root.squashfs \
      bin/targets/<target>/<subtarget>/*-squashfs-tclinux.trx
make -j$(nproc) target/install              # regenerate squashfs + trx
```
Verify before flashing: `unsquashfs -d /tmp/check build_dir/.../root.squashfs lib/modules/<ver>/<pkg>.ko`
and `md5sum`/`strings` it against what you expect. Then flash with
`dev_flash_cycle.py` as above.

