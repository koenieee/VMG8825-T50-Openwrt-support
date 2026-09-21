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

## `openwrt-overlay/` does NOT reach a build by itself

`openwrt/` is a **git submodule with its own working copy**. `openwrt-overlay/`
is the tracked source of truth, but nothing syncs it automatically — a file
added under `openwrt-overlay/` is in *no* built image until it is copied into
`openwrt/` — use `tools/sync-overlay.sh` for that. This has silently
invalidated work that was committed and looked correct: two base-files
scripts had never once run on hardware despite being in the repo.

Second trap: target `base-files/` changes
(`openwrt/target/linux/econet/base-files/`) are baked into the **base-files
package**. `make package/install` alone will not pick them up:

```
make package/base-files/{clean,compile} -j$(nproc)   # required for base-files/ changes
make package/install -j$(nproc)
make target/install -j$(nproc)
```

Always confirm the file actually landed before booting:

```
ls -l openwrt/build_dir/target-mips_24kc_musl/root-econet/etc/init.d/<name> \
      openwrt/build_dir/target-mips_24kc_musl/root-econet/etc/rc.d/S??<name>
```

## Out-of-tree `econet-eth` driver: patches only, never edit `build_dir`

The driver source is fetched from `github.com/cjdelisle/econet_eth.git` by
`package/kernel/econet-eth/Makefile`. Hand-edits to
`build_dir/.../econet-eth-*/` are **wiped whenever the prepare step re-runs**,
which happens on the next `compile` — the build will succeed and produce a
module without your change. Durable changes must be numbered patches in
`package/kernel/econet-eth/patches/`, installed into **both** `openwrt/` and
`openwrt-overlay/`.

Generate patches with `diff`, don't hand-write hunk headers (hand-written
`@@` line counts fail with "malformed patch"):

```
cp econet_qdma.c /tmp/base.c          # from a freshly prepared tree
# ...edit econet_qdma.c...
diff -u --label a/econet_qdma.c --label b/econet_qdma.c /tmp/base.c econet_qdma.c > NNN-name.patch
```

For throughput/behaviour questions, a **temporary** `9NN-DEBUG-*.patch` adding
`module_param_named()` counters is far cheaper than repeated boots: it makes
both sides of an A/B comparison measurable within a single boot, at runtime.
Delete it before committing. Verify it built with
`strings <root-econet>/lib/modules/*/econet-eth.ko | grep <param>`.
