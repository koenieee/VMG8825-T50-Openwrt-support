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
  #
  # Beyond the WiFi/USB-controller kmods, this pulls in what a "normal"
  # OpenWrt router image ships that a bare DEVICE_PACKAGES list otherwise
  # lacks -- there's 46+MiB of headroom in the 56MiB FACTORY_SIZE budget
  # above (current build is ~10MiB), so none of this is size-constrained:
  #   - luci: web UI (never on by default in upstream OpenWrt, every
  #     device that ships it adds it explicitly).
  #   - openssh-sftp-server: dropbear can *run* an SFTP server
  #     (DROPBEAR_SFTPSERVER is on by default here, this device isn't
  #     SMALL_FLASH) but doesn't provide one itself -- it execs
  #     /usr/libexec/sftp-server, which only this package installs.
  #   - coreutils-base64: busybox's base64 applet defaults to OFF
  #     upstream (BUSYBOX_DEFAULT_BASE64 := n) -- was actually missing,
  #     not just a minimal version.
  #   - kmod-usb-storage + kmod-fs-ext4/kmod-fs-vfat/kmod-nls-cp437/
  #     kmod-nls-iso8859-1 + block-mount + usbutils: USB mass storage
  #     was previously only working via manual opkg/apk installs during
  #     testing (see NEXT_STEPS.md's iperf3 saga) -- nothing in
  #     DEVICE_PACKAGES actually shipped it.
  DEVICE_PACKAGES := kmod-usb3 kmod-mt7615e kmod-mt7615-firmware \
    luci openssh-sftp-server coreutils-base64 \
    kmod-usb-storage kmod-fs-ext4 kmod-fs-vfat kmod-nls-cp437 \
    kmod-nls-iso8859-1 block-mount usbutils
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

# Identical to zyxel_vmg8825-t50 in every way except the DTS: mtd1
# (bootloader) is read-only here. Build/flash this once your own
# bootloader patch is applied (BOOTLOADER-PATCH.md) -- there's no more
# reason for mtd1 to be writable from Linux, and this closes that off.
# See install-guide/README.md §7a. Do NOT use this for the initial
# patch -- use the base zyxel_vmg8825-t50 device for that one operation
# (install-guide/README.md §5a/§7), since it needs to nandwrite mtd1.
define Device/zyxel_vmg8825-t50-locked
  $(Device/zyxel_vmg8825-t50)
  DEVICE_MODEL := VMG8825-T50 (bootloader-locked)
  DEVICE_DTS := en751627_zyxel_vmg8825-t50-locked
endef
TARGET_DEVICES += zyxel_vmg8825-t50-locked
