#!/bin/sh
#
# Check the PC side before starting the install guide, so a missing tool or
# a corrupt download shows up now and not halfway through a ~90s ZHAL>
# window. Only reads; changes nothing on the PC or the router.
#
# Usage:
#   tools/preflight.sh [SERIAL_DEVICE]     # default /dev/ttyUSB0
#   KERNEL=out/vmg8825-t50-initramfs-kernel.bin STUB=out/netboot-stub-manual.txt \
#       tools/preflight.sh                 # check a self-built set instead
#
# Exit status is the number of failed checks.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TTY="${1:-/dev/ttyUSB0}"
KERNEL="${KERNEL:-$REPO/firmware/vmg8825-t50-initramfs-kernel.bin}"
STUB="${STUB:-$REPO/bldr-patch/netboot-stub-manual.txt}"
fails=0

ok()   { echo "  ok    $*"; }
bad()  { echo "  FAIL  $*"; fails=$((fails + 1)); }
warn() { echo "  warn  $*"; }

echo "tools"
for t in python3 atftp md5sum; do
	if command -v $t >/dev/null; then ok "$t"; else bad "$t not installed"; fi
done

echo "serial console ($TTY)"
if [ ! -e "$TTY" ]; then
	bad "$TTY does not exist -- USB-serial adapter plugged in? (ls /dev/ttyUSB* /dev/ttyACM*)"
elif [ ! -r "$TTY" ] || [ ! -w "$TTY" ]; then
	grp=$(stat -c %G "$TTY")
	bad "no read/write access to $TTY -- add yourself to group '$grp' and log in again: sudo usermod -aG $grp $USER"
else
	ok "$TTY accessible"
	if command -v fuser >/dev/null && fuser "$TTY" >/dev/null 2>&1; then
		warn "$TTY is open in another program (screen/minicom/picocom?) -- close it before netboot.py"
	fi
fi

echo "unlock tool"
pw=$("$REPO/tools/atenv3/atenv3_passwd" 2E01C00309E01B14300B07B36A59F271010A 2>&1)
if [ "$pw" = 97760452113818812721555971724 ]; then
	ok "atenv3_passwd runs and derives the test vector"
else
	bad "atenv3_passwd: '$pw' -- build it from source: cc -O2 -o tools/atenv3/atenv3_passwd tools/atenv3/atenv3_passwd.c"
fi

echo "downloads"
if [ -f "$REPO/firmware/SHA256SUMS" ]; then
	if (cd "$REPO/firmware" && sha256sum --quiet -c SHA256SUMS) >/dev/null 2>&1; then
		ok "firmware/ matches SHA256SUMS"
	else
		bad "firmware/ does not match SHA256SUMS (git-lfs pointer, partial clone, or edited file?):"
		(cd "$REPO/firmware" && sha256sum -c SHA256SUMS 2>&1 | grep -v ': OK$' | sed 's/^/        /')
	fi
fi
if [ -f "$KERNEL" ] && [ -f "$STUB" ]; then
	tmp=$(mktemp)
	python3 "$REPO/bldr-patch/build_combined_kernel_stub.py" "$KERNEL" "$tmp.txt" >/dev/null 2>&1
	if cmp -s "$tmp.txt" "$STUB"; then
		ok "$(basename "$STUB") fits $(basename "$KERNEL")"
	else
		bad "$(basename "$STUB") does not fit $(basename "$KERNEL") -- regenerate: python3 bldr-patch/build_combined_kernel_stub.py $KERNEL $STUB"
	fi
	rm -f "$tmp" "$tmp.txt"
else
	bad "missing $KERNEL or $STUB"
fi

echo "network for TFTP (router is 192.168.1.1)"
if ip -4 -o addr show 2>/dev/null | grep -q 'inet 192\.168\.1\.\([02-9]\|[1-9][0-9]\)'; then
	ok "this PC has an address in 192.168.1.0/24"
else
	warn "no 192.168.1.x address on this PC -- set one before §4b (e.g. 192.168.1.50/24)"
fi

echo
if [ "$fails" -eq 0 ]; then echo "all checks passed"; else echo "$fails check(s) failed"; fi
exit "$fails"
