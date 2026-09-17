TRX_ENDIAN := be

define Device/zyxel_ex3301-t0
  DEVICE_VENDOR := Zyxel
  DEVICE_MODEL := EX3301-T0
  DEVICE_DTS := en751627_zyxel_ex3301-t0
  IMAGES := tclinux.trx
  IMAGE/tclinux.trx := append-kernel | lzma | tclinux-trx
  DEVICE_PACKAGES := kmod-usb3 kmod-mt7915e kmod-mt7915-firmware
endef
TARGET_DEVICES += zyxel_ex3301-t0
# Append this block to target/linux/econet/image/en751627.mk in your
# OpenWrt checkout (below the existing zyxel_ex3301-t0 definition).

define Device/zyxel_vmg8825-t50
  $(call Device/tclinux-ubi)
  DEVICE_VENDOR := Zyxel
  DEVICE_MODEL := VMG8825-T50
  DEVICE_DTS := en751627_zyxel_vmg8825-t50
  # v1.4.4 "free bootbase" zloader LZMA-decompresses the bare kernel region to
  # 0x80002000 and jumps there (live bootlog "Decompress to 80002000"), exactly
  # like OEM. So the kernel is a plain LZMA-alone stream with the ZBOOT stub
  # linked at 0x80002000 (CONFIG_ZBOOT_LOAD_ADDRESS) -- no free-bootbase-jump
  # prefix. That prefix was prepended *after* lzma and cannot self-decompress;
  # it is why earlier builds never booted. See CHECKSUM_RESOLUTION.md.
  # The slim tclinux.trx here is only an intermediate; build_era_trx.py re-wraps
  # its kernel+UBI-rootfs into the era-0x174 header the v1.4.4 zloader requires.
  # Rootfs is UBI (Device/tclinux-ubi), not raw squashfs: mtdblock/JFFS2 can't
  # reliably serve this SPI NAND (Winbond W25M02GV), so persistent overlay
  # needs UBI+UBIFS instead.
  #
  # tclinux's UBI image is STATIC-ONLY (squashfs "rootfs" volume, no
  # dynamic/autoresize "rootfs_data" volume of its own -- see IMAGE/
  # tclinux.trx override below, which replaces stock append-ubi's implicit
  # rootfs_data-with-autoresize). The writable overlay volume lives on its
  # own separate UBI instance on the "rootfs_data" MTD partition (the
  # misc/reservearea tail, see the dts and NEXT_STEPS.md). This keeps
  # tclinux's raw NAND bytes 100% static after flashing, forever, so the
  # OEM bootloader's boot-time whole-partition checksum on tclinux never
  # breaks after the first real boot -- see BOOTLOADER-PATCH.md for why
  # that matters (an unattended coldboot needs both this AND the
  # bootloader's own CRC-check patch).
  TRX_LOADADDR := 0x80002000
  # tclinux partition is 56MiB (0x80000-0x3880000); the .trx (kernel+UBI
  # blob) must fit inside it, same math as the ATER/ATWF flash-range derivation.
  FACTORY_SIZE := 56m
  # MT7615 (Wi-Fi 5) instead of the EX3301-T0's MT7915.
  DEVICE_PACKAGES := kmod-usb3 kmod-mt7615e kmod-mt7615-firmware
  # Static UBI: only a "rootfs" volume, no auto-appended "rootfs_data"
  # (that would immediately claim all free PEBs on first attach -- see
  # append-ubi-static-rootfs below and NEXT_STEPS.md #2 for why).
  IMAGE/tclinux.trx := append-kernel | pad-to $$$$(KERNEL_SIZE) | \
    append-ubi-static-rootfs | check-size $$$$(FACTORY_SIZE)
  # Standalone UBI image for the separate 1MiB "rootfs_data" MTD partition
  # (see dts): a single empty, autoresize dynamic volume named "rootfs_data",
  # structurally identical to what append-ubi would normally auto-append
  # inside tclinux's own UBI -- just relocated to its own partition/UBI
  # instance instead. fstools' mount_root finds "rootfs_data" by volume name
  # across ALL attached UBI instances, so this works the same as the
  # traditional same-instance layout at runtime.
  IMAGES += rootfs-data.bin
  IMAGE/rootfs-data.bin := rootfs-data-ubi
endef
TARGET_DEVICES += zyxel_vmg8825-t50
