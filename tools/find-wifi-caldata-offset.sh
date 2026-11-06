#!/bin/sh
# Find the MT7615 WiFi calibration offsets on a running VMG8825-T50, by
# SIGNATURE SCAN of the raw flash -- no Python, busybox only.
#
# Why: the board DTS reads caldata from a FIXED flash offset
# (radio0 @0xeae0000, radio1 @0xeb00000). That offset is not the same on
# every unit; when it is wrong, both radios come up weak (~6-8 dBm). This
# script tells you where YOUR unit's blocks actually are, so you can decide
# whether the hard-coded offset is the problem.
#
# It only READS flash. It never writes or flashes anything.
#
# Usage (on the device, from the RAM shell or a booted install):
#   sh find-wifi-caldata-offset.sh [mtd-device-or-file] [start_hex] [end_hex]
# Defaults: /dev/mtd0, scan 0x7000000..0x10000000 (the reservearea tail where
# calibration lives). Pass a wider window or a dump file to scan more.
#
# Example:
#   sh find-wifi-caldata-offset.sh /dev/mtd0
#   sh find-wifi-caldata-offset.sh mtd-full.bin 0 0x10000000   # a dump on your PC
set -u

DEV="${1:-/dev/mtd0}"
START="${2:-0x7000000}"
END="${3:-0x10000000}"
STRIDE=$((0x20000))     # MT7615 blocks sit 128 KiB apart
OFF_F3=$((0xf3))        # non-zero only on the 5 GHz external-PA card

start=$((START))
end=$((END))

if [ ! -e "$DEV" ]; then
	echo "FATAL: $DEV not found" >&2
	exit 1
fi

# Read N bytes at byte offset $1, length $2, as space-free hex.
rdhex() {
	dd if="$DEV" bs=1 skip="$1" count="$2" 2>/dev/null | od -An -tx1 | tr -d ' \n'
}

echo "scanning $DEV from $(printf 0x%x "$start") to $(printf 0x%x "$end") (stride 128 KiB)..."
r0off=""; r1off=""; n=0
off=$start
while [ "$off" -lt "$end" ]; do
	sig=$(rdhex "$off" 2)
	if [ "$sig" = "1576" ]; then
		f3=$(rdhex $((off + OFF_F3)) 1)
		b2g=$(rdhex $((off + 0x58)) 1)
		b5g=$(rdhex $((off + 0x70)) 1)
		printf '  block @0x%08x  2g@0x58=0x%s 5g@0x70=0x%s f3=0x%s' "$off" "$b2g" "$b5g" "$f3"
		if [ "$f3" = "00" ]; then
			echo "   -> radio0 (2.4 GHz, TSSI)"
			[ -z "$r0off" ] && r0off=$off
		else
			echo "   -> radio1 (5 GHz, external-PA)"
			[ -z "$r1off" ] && r1off=$off
		fi
		n=$((n + 1))
	fi
	off=$((off + STRIDE))
done

echo ""
if [ "$n" -eq 0 ]; then
	echo "no MT7615 block (sig 15 76) found in that window -- widen start/end."
	exit 2
fi
[ -n "$r0off" ] && printf 'radio0 (2.4 GHz, TSSI)      -> 0x%08x\n' "$r0off"
[ -n "$r1off" ] && printf 'radio1 (5 GHz, external-PA) -> 0x%08x\n' "$r1off"

DT_R0=$((0xeae0000)); DT_R1=$((0xeb00000))
if [ "${r0off:-0}" = "$DT_R0" ] && [ "${r1off:-0}" = "$DT_R1" ]; then
	echo "note: matches the DTS's hard-coded offsets -- weak TX is likely something else."
else
	echo "note: DIFFERS from the DTS's hard-coded 0xeae0000/0xeb00000."
	echo "      Dump full flash and run tools/extract-wifi-caldata.py on your PC"
	echo "      to generate a per-unit .dtsi, then rebuild (see the WiFi section)."
fi
