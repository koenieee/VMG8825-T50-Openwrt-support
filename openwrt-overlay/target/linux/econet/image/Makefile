include $(TOPDIR)/rules.mk
include $(INCLUDE_DIR)/image.mk

DEVICE_VARS += TRX_MODEL TRX_LOADADDR

define Target/Description
	Build firmware images for EcoNet MIPS based boards.
endef

# tclinux-trx is the default format used in the SDK
define Build/tclinux-trx
  ./tclinux-trx.sh --kernel $@ --rootfs $(IMAGE_ROOTFS) \
    --version $(VERSION_DIST)-$(REVISION) --endian $(TRX_ENDIAN) \
    $(if $(TRX_MODEL),--model $(TRX_MODEL)) \
    $(if $(TRX_LOADADDR),--loadaddr $(TRX_LOADADDR)) > $@.new
	mv $@.new $@
endef

# Build TRX without rootfs, for use with UBI root
define Build/kernel-trx
  ./tclinux-trx.sh --kernel $@ \
    --version $(VERSION_DIST)-$(REVISION) --endian $(TRX_ENDIAN) \
    $(if $(TRX_MODEL),--model $(TRX_MODEL)) \
    $(if $(TRX_LOADADDR),--loadaddr $(TRX_LOADADDR)) > $@.new
	mv $@.new $@
endef

# If TCSUPPORT_FREE_BOOTBASE is enabled, bootloader will decompress the kernel
# at 0x80002000 instead of 0x80020000.
#
# You can tell it by looking at the bootloader version string
# " at %s version %s free bootbase".
define Build/tclinux-free-bootbase-jump
	@[ "$(KERNEL_LOADADDR)" = 0x80020000 ] || { \
		$(call ERROR_MESSAGE,WARNING: KERNEL_LOADADDR changed); \
		rm -f $@; \
	}
	printf '$(if $(CONFIG_BIG_ENDIAN),\010\000\200\000,\000\200\000\010)' > $@.new
	dd if=/dev/zero bs=122876 count=1 >> $@.new
	cat $@ >> $@.new
	mv $@.new $@
endef

# tclinux bootloader requires LZMA, BUT only provides 7.5MB of space
# to decompress into. So we use vmlinuz and decompress twice.
define Device/Default
  DEVICE_DTS_DIR := ../dts
  BLOCKSIZE := 128k
  PAGESIZE := 2048
  # decompression buffer limit, the size of LZMA-compressed data is read from
  # the trx header
  KERNEL_SIZE := 7480k
  KERNEL_NAME := vmlinuz.bin
  KERNEL_LOADADDR := 0x80020000
  KERNEL := kernel-bin | append-dtb
  UBINIZE_OPTS := -E 5
  TRX_MODEL :=
  TRX_LOADADDR :=
endef

# Kernel in a raw TRX partition, root squashfs in UBI. FACTORY_SIZE must be the
# size of the partition the .trx is written into; that write is linear, so an
# oversized image overruns it into the next partition.
define Device/tclinux-ubi
  KERNEL_SIZE := 4096k
  KERNEL := kernel-bin | append-dtb | lzma | kernel-trx
  KERNEL_INITRAMFS := kernel-bin | append-dtb
  IMAGES := tclinux.trx sysupgrade.bin
  IMAGE/tclinux.trx := append-kernel | pad-to $$$$(KERNEL_SIZE) | append-ubi | \
    check-size $$$$(FACTORY_SIZE)
  IMAGE/sysupgrade.bin := sysupgrade-tar | append-metadata
endef

# Like Build/append-ubi, but WITHOUT the implicit dynamic/autoresize
# "rootfs_data" volume that scripts/ubinize-image.sh's ubilayout() normally
# auto-appends after a squashfs rootfs (`[ "$rootfs_type" = "ubifs" ] ||
# ubivol $vol_id rootfs_data "" 1`). That auto-appended volume has
# vol_flags=autoresize, which makes UBI claim ALL remaining free PEBs in the
# instance the first time it attaches -- not lazily as the overlay fills up,
# but immediately on first mount. On a board whose bootloader boot-time-
# checksums the whole raw partition (e.g. zyxel_vmg8825-t50), that silently
# and permanently invalidates the checksum after the very first successful
# boot. See NEXT_STEPS.md for the full root-cause writeup this exists to fix.
#
# Use this instead of Build/append-ubi when the writable overlay volume is
# relocated to its own separate MTD partition/UBI instance (see
# Build/rootfs-data-ubi below) and this UBI image should therefore contain
# ONLY the static "rootfs" volume and stay byte-for-byte unchanged forever
# after flashing.
define Build/append-ubi-static-rootfs
  sh $(TOPDIR)/scripts/ubinize-image.sh \
    $(if $(UBOOTENV_IN_UBI),--uboot-env) \
    $(if $(KERNEL_IN_UBI),--kernel $(IMAGE_KERNEL)) \
    $(foreach part,$(UBINIZE_PARTS),--part $(part)) \
    --part rootfs=$(IMAGE_ROOTFS) \
    $@.tmp \
    -p $(BLOCKSIZE:%k=%KiB) -m $(PAGESIZE) \
    $(if $(SUBPAGESIZE),-s $(SUBPAGESIZE)) \
    $(if $(VID_HDR_OFFSET),-O $(VID_HDR_OFFSET)) \
    $(UBINIZE_OPTS)
	cat $@.tmp >> $@
	rm $@.tmp
	$(if $(and $(IMAGE_SIZE),$(NAND_SIZE)),\
		$(call Build/check-size,$(UBI_NAND_SIZE_LIMIT)))
endef

# Standalone UBI image holding ONLY an empty, autoresize dynamic volume
# named "rootfs_data" -- structurally identical to what Build/append-ubi
# would normally auto-append INSIDE the rootfs UBI instance, just built here
# as its own separate image for a separate MTD partition/UBI instance
# instead. Companion to Build/append-ubi-static-rootfs above.
# Standalone UBI image holding ONLY an empty, autoresize dynamic volume
# named "rootfs_data" -- structurally identical to what Build/append-ubi
# would normally auto-append INSIDE the rootfs UBI instance, just built here
# as its own separate image for a separate MTD partition/UBI instance
# instead. Companion to Build/append-ubi-static-rootfs above.
#
# vol_size is deliberately tiny (rounds up to a single LEB): with
# vol_flags=autoresize the volume grows to consume whatever's actually
# available on the real device at ATTACH time regardless of this build-time
# size, so this only needs to be small enough that ubinize doesn't reserve
# more LEBs than the target MTD partition can physically hold. This
# partition is unusually small (1MiB = 8 PEBs total, most of which stock
# OpenWrt UBI targets never hit since this pattern is normally only used
# for placeholder volumes inside a much larger shared instance) -- the
# generic "1MiB" default used by scripts/ubinize-image.sh for its own
# auto-appended rootfs_data (see append-ubi-static-rootfs above) rounds up
# to 9 reserved LEBs, which doesn't fit an 8-PEB device
# ("too large reserved_pebs 9, good PEBs 8" at attach time -- see
# NEXT_STEPS.md for the full story).
# Standalone UBI image holding ONLY an empty, autoresize dynamic volume
# named "rootfs_data" -- structurally identical to what Build/append-ubi
# would normally auto-append INSIDE the rootfs UBI instance, just built here
# as its own separate image for a separate MTD partition/UBI instance
# instead. Companion to Build/append-ubi-static-rootfs above.
#
# vol_size is a placeholder: with vol_flags=autoresize the volume grows to
# consume whatever's actually available on the real device at ATTACH time
# regardless of this build-time size. It only needs to be small enough that
# ubinize doesn't reserve more LEBs than the target MTD partition can
# physically hold, AND the partition itself must be large enough for UBIFS'
# own hard minimum of 17 LEBs (~2.1MiB) -- an earlier 1MiB target partition
# ("wwan") failed with "too few LEBs (1), min. is 17" for exactly this
# reason and had to be abandoned for a bigger partition instead (see
# NEXT_STEPS.md #2). 1MiB is a safe placeholder for any reasonably-sized
# (tens of MiB+) target partition.
define Build/rootfs-data-ubi
	rm -f $@.ini
	echo "[rootfs_data]" >> $@.ini
	echo "mode=ubi" >> $@.ini
	echo "vol_id=0" >> $@.ini
	echo "vol_type=dynamic" >> $@.ini
	echo "vol_name=rootfs_data" >> $@.ini
	echo "vol_size=1MiB" >> $@.ini
	echo "vol_flags=autoresize" >> $@.ini
	$(STAGING_DIR_HOST)/bin/ubinize -o $@.tmp \
		-p $(BLOCKSIZE:%k=%KiB) -m $(PAGESIZE) \
		$(if $(SUBPAGESIZE),-s $(SUBPAGESIZE)) \
		$(if $(VID_HDR_OFFSET),-O $(VID_HDR_OFFSET)) \
		$(UBINIZE_OPTS) $@.ini
	cat $@.tmp >> $@
	rm -f $@.tmp $@.ini
endef

include $(SUBTARGET).mk

$(eval $(call BuildImage))
