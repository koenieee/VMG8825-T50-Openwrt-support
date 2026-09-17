# Append this block to target/linux/econet/image/en751627.mk in your
# OpenWrt checkout (below the existing zyxel_ex3301-t0 definition).

define Device/zyxel_vmg8825-t50
  DEVICE_VENDOR := Zyxel
  DEVICE_MODEL := VMG8825-T50
  DEVICE_DTS := en751627_zyxel_vmg8825-t50
  IMAGES := tclinux.trx
  IMAGE/tclinux.trx := append-kernel | lzma | tclinux-trx
  # MT7615 (Wi-Fi 5) instead of the EX3301-T0's MT7915.
  DEVICE_PACKAGES := kmod-usb3 kmod-mt7615e kmod-mt7615-firmware
endef
TARGET_DEVICES += zyxel_vmg8825-t50
