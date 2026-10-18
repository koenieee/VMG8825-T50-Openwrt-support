#!/bin/sh
#
# One command from a fresh clone to flashable images.
#
# Does what firmware/README.md "Building your own" describes by hand:
# fetch OpenWrt at the tested commit, copy the overlay in, feeds, the
# shipped config, build, era-wrap both device variants, and regenerate the
# by-hand netboot stub for the new initramfs (its size is baked into the
# stub, so a stub from another build boots into "XZ-compressed data is
# corrupt"). Everything lands in out/ under the same names as firmware/.
#
# Usage:
#   tools/build-firmware.sh             # build into out/
#   OUT=/some/dir tools/build-firmware.sh
#   JOBS=4 tools/build-firmware.sh      # default: all CPUs
#
# Reruns are incremental. An existing openwrt/.config is kept (delete it
# to start over from firmware/diffconfig). Host prerequisites are OpenWrt's
# own: https://openwrt.org/docs/guide-developer/toolchain/install-buildsystem

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$REPO/out}"
JOBS="${JOBS:-$(nproc)}"
# The OpenWrt commit everything here was tested against (README.md,
# "Hardware"). Override to try a newer one; expect some drift.
OPENWRT_REF="${OPENWRT_REF:-928cd26bd938b8ac46b79e14f5f9f4b1d772abe8}"
BIN="$REPO/openwrt/bin/targets/econet/en751627"
P=openwrt-econet-en751627-zyxel_vmg8825-t50

die() { echo "build-firmware: $*" >&2; exit 1; }
step() { echo; echo "=== $*"; }

# --- OpenWrt source ----------------------------------------------------
if [ ! -f "$REPO/openwrt/Makefile" ]; then
	step "fetching OpenWrt @ ${OPENWRT_REF%"${OPENWRT_REF#???????}"}"
	git -C "$REPO" submodule update --init --depth 1 openwrt 2>/dev/null || true
	if [ ! -f "$REPO/openwrt/Makefile" ]; then
		# No usable gitlink (or a shallow fetch by sha refused): plain clone.
		rm -rf "$REPO/openwrt"
		git clone https://github.com/openwrt/openwrt.git "$REPO/openwrt"
	fi
	git -C "$REPO/openwrt" fetch --depth 1 origin "$OPENWRT_REF" 2>/dev/null || true
	git -C "$REPO/openwrt" checkout -q "$OPENWRT_REF"
fi

# --- overlay -----------------------------------------------------------
step "copying openwrt-overlay/ into openwrt/"
"$REPO/tools/sync-overlay.sh" push >/dev/null

cd "$REPO/openwrt"

# --- feeds + config ----------------------------------------------------
if [ ! -d feeds/packages ]; then
	step "feeds"
	./scripts/feeds update -a
	./scripts/feeds install -a
fi

if [ ! -f .config ]; then
	step "config from firmware/diffconfig"
	grep '^CONFIG_' "$REPO/firmware/diffconfig" > .config
fi
make defconfig >/dev/null

# --- build -------------------------------------------------------------
step "building with $JOBS jobs (first run: about an hour or more)"
# Target base-files are only repackaged when their package is rebuilt, so
# an overlay edit under base-files/ would otherwise never reach the image.
make package/base-files/clean >/dev/null
make -j"$JOBS" || die "build failed -- rerun with: make -j1 V=s  (inside openwrt/) to see why"

# --- wrap + collect ----------------------------------------------------
step "era-wrapping and collecting into $OUT"
mkdir -p "$OUT"
# build_era_trx.py finds its header template relative to the repo root.
cd "$REPO"
python3 "$REPO/build_era_trx.py" --slim "$BIN/$P-squashfs-tclinux.trx" \
	-o "$OUT/vmg8825-t50-era-signed.bin"
python3 "$REPO/build_era_trx.py" --slim "$BIN/$P-locked-squashfs-tclinux.trx" \
	-o "$OUT/vmg8825-t50-era-signed-locked.bin"
cp "$BIN/$P-initramfs-kernel.bin" "$OUT/vmg8825-t50-initramfs-kernel.bin"
cp "$BIN/$P-squashfs-rootfs-data.bin" "$OUT/vmg8825-t50-rootfs-data.bin"
python3 "$REPO/bldr-patch/build_combined_kernel_stub.py" \
	"$OUT/vmg8825-t50-initramfs-kernel.bin" "$OUT/netboot-stub-manual.txt" >/dev/null

step "done"
cd "$OUT" && md5sum vmg8825-t50-* netboot-stub-manual.txt
cat <<EOF

Use these instead of the files in firmware/. For the by-hand netboot
(install-guide §4b) paste $OUT/netboot-stub-manual.txt, not the copy in
bldr-patch/ -- that one only fits the shipped initramfs.
EOF
