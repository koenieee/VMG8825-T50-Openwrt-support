#!/bin/sh
# Flash the patched VMG8825-T50 zloader bootloader (mtd1) that ships
# baked into this OpenWrt image at /root/vmg8825-t50-bootloader-patched.bin
# -- no USB stick, no separate transfer step needed. See
# install-guide/README.md SS4 and BOOTLOADER-PATCH.md for the background.
#
# mtd0/mtd1 has NO fallback slot. This script backs up and verifies at
# every step and refuses to continue on any mismatch, but the underlying
# risk (a bad bootloader write bricking the device) cannot be fully
# eliminated by software. Read install-guide/README.md SS4 before running
# this, especially if you'd rather build your own patched file from your
# own mtd0 dump (SS7) instead of using the placeholder-genericized one
# shipped here.
set -e

BIN=/root/vmg8825-t50-bootloader-patched.bin
BACKUP=/tmp/mtd1-backup.bin
VERIFY=/tmp/mtd1-verify.bin

if [ ! -f "$BIN" ]; then
	echo "ERROR: $BIN not found -- this script only works on the prebuilt image that ships it." >&2
	exit 1
fi

SIZE=$(wc -c < "$BIN")
SRC_MD5=$(md5sum "$BIN" | cut -d' ' -f1)
echo "Bootloader image: $BIN ($SIZE bytes, md5 $SRC_MD5)"

MTD_LINE=$(grep -i 'bootloader' /proc/mtd)
MTD_DEV=$(echo "$MTD_LINE" | cut -d: -f1)
if [ "$MTD_DEV" != "mtd1" ]; then
	echo "ERROR: /proc/mtd names the 'bootloader' partition '$MTD_DEV', not mtd1." >&2
	echo "Partition numbering differs from what this script assumes -- refusing" >&2
	echo "to guess. /proc/mtd:" >&2
	cat /proc/mtd >&2
	exit 1
fi

MTD_HEXSIZE=$(echo "$MTD_LINE" | awk '{print $2}')
MTD_SIZE=$((0x$MTD_HEXSIZE))
if [ "$SIZE" -gt "$MTD_SIZE" ]; then
	echo "ERROR: $BIN is $SIZE bytes, larger than the mtd1 partition ($MTD_SIZE bytes)." >&2
	exit 1
fi

WRITABLE=$(mtdinfo /dev/mtd1 2>/dev/null | grep -ic 'writable.*true')
if [ "$WRITABLE" != "1" ]; then
	echo "ERROR: /dev/mtd1 is not writable (mtdinfo). Refusing to continue." >&2
	echo "This should not happen on this image -- the bootloader DTS node" >&2
	echo "is deliberately not read-only. See BOOTLOADER-PATCH.md." >&2
	exit 1
fi

printf 'This will ERASE and REWRITE mtd1 (the bootloader). There is NO fallback\nif this goes wrong. Backup first, then flash. Continue? [y/N] '
read ans
case "$ans" in
	y|Y) ;;
	*) echo "Aborted, nothing touched."; exit 1 ;;
esac

echo "### Backing up current mtd1 -> $BACKUP"
dd if=/dev/mtd1 of="$BACKUP" bs=4096 2>/dev/null
BACKUP_MD5=$(md5sum "$BACKUP" | cut -d' ' -f1)
echo "Backup md5: $BACKUP_MD5"
echo "!!! Copy $BACKUP off this device now (scp/usb) before continuing if you want to keep it !!!"
printf 'Backup taken. Proceed with the actual flash? [y/N] '
read ans
case "$ans" in
	y|Y) ;;
	*) echo "Aborted after backup, mtd1 untouched."; exit 1 ;;
esac

echo "### flash_erase /dev/mtd1"
flash_erase /dev/mtd1 0x0 0

echo "### nandwrite -p /dev/mtd1 $BIN"
nandwrite -p /dev/mtd1 "$BIN"

echo "### Verifying..."
dd if=/dev/mtd1 of="$VERIFY" bs="$SIZE" count=1 2>/dev/null
DST_MD5=$(md5sum "$VERIFY" | cut -d' ' -f1)

if [ "$DST_MD5" = "$SRC_MD5" ]; then
	echo "OK: mtd1 verified, byte-for-byte match."
	echo "Safe to reboot now. After reboot, no ATSE/ATEN debug-unlock is"
	echo "needed for any future boot -- see BOOTLOADER-PATCH.md."
else
	echo "!!! MISMATCH ($DST_MD5 != $SRC_MD5) -- DO NOT REBOOT !!!"
	echo "Restore the backup immediately:"
	echo "  flash_erase /dev/mtd1 0x0 0 && nandwrite -p /dev/mtd1 $BACKUP"
	exit 1
fi
