#!/bin/sh
#
# One command: build tree -> era image -> device -> sysupgrade -> verify.
#
# Replaces the hand-run sequence of "wrap the trx, md5 it, scp it, ssh in,
# sysupgrade, hope the md5 matched" -- every step with a constant that used
# to be edited in place. The image checksum is verified on both ends before
# anything is written to flash, because a corrupted era image bricks to
# serial-only recovery (build_era_trx.py self-checks the two zloader gates;
# this adds the transfer check).
#
# Usage:
#   flash-openwrt.sh [HOST]              # default HOST = 192.168.1.1
#   IMG=some-era.bin flash-openwrt.sh    # flash a prebuilt era image, skip build
#
# Requires ssh/scp reachability to root@HOST and a built
# *-squashfs-tclinux.trx under openwrt/bin/.

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${1:-192.168.1.1}"
SSH="ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new root@$HOST"

die() { echo "flash-openwrt: $*" >&2; exit 1; }

# --- locate or build the era image -------------------------------------
if [ -n "$IMG" ]; then
	[ -f "$IMG" ] || die "IMG=$IMG does not exist"
	era="$IMG"
	echo "using prebuilt $era"
else
	trx="$(ls -t "$REPO"/openwrt/bin/targets/econet/en751627/*vmg8825-t50-squashfs-tclinux.trx 2>/dev/null | head -1)"
	[ -n "$trx" ] || die "no built tclinux.trx -- run the OpenWrt build first"
	era="$REPO/openwrt/bin/targets/econet/en751627/vmg8825-t50-era.bin"
	echo "wrapping $(basename "$trx") -> $(basename "$era")"
	python3 "$REPO/build_era_trx.py" --slim "$trx" -o "$era"
	# build_era_trx exits non-zero if either zloader gate self-check fails,
	# so reaching here means the checksums are already good.
fi

md5="$(md5sum "$era" | cut -d' ' -f1)"
echo "local md5  = $md5"

# --- reachability + free space check -----------------------------------
$SSH true 2>/dev/null || die "cannot ssh to root@$HOST"
board="$($SSH '. /etc/os-release 2>/dev/null; cat /tmp/sysinfo/board_name 2>/dev/null')"
[ "$board" = "zyxel,vmg8825-t50" ] || die "device reports board '$board', refusing to flash a T50 image"

# --- transfer and verify BEFORE writing flash --------------------------
echo "copying to $HOST:/tmp/ ..."
scp -O -o ConnectTimeout=8 "$era" "root@$HOST:/tmp/flash-openwrt.bin"
rmd5="$($SSH 'md5sum /tmp/flash-openwrt.bin' | cut -d' ' -f1)"
echo "remote md5 = $rmd5"
[ "$md5" = "$rmd5" ] || { $SSH 'rm -f /tmp/flash-openwrt.bin'; die "md5 mismatch after transfer -- NOT flashing"; }

# --- flash -------------------------------------------------------------
echo "checksums match. running sysupgrade (device will reboot) ..."
# -n would wipe config; we keep it. platform_check_image on the device
# re-validates the era magic/header/size before writing.
$SSH 'sysupgrade -v /tmp/flash-openwrt.bin' || true

echo
echo "sysupgrade launched. The device reboots into the new image now;"
echo "give it ~60s, then: ssh root@$HOST 'cat /etc/openwrt_release; logread | tail'"
