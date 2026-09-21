platform_check_image() {
	local board=$(board_name)

	case "$board" in
	zyxel,vmg8825-t50)
		# This board boots from a single era-0x174 image in "tclinux",
		# not from a kernel/rootfs pair, so the file to feed sysupgrade
		# is the era-wrapped one (build_era_trx.py, magic "2RDH" or
		# "HDR2" with the header length 0x174 right behind it) -- not
		# the plain -squashfs-sysupgrade.bin, whose magic is "sysu".
		# Handing the bootloader anything else gets a "Wrong image
		# checksum" at the next cold boot and a device that only comes
		# back over serial, so say so here rather than write it.
		local magic="$(get_magic_long "$1")"
		local hdrlen="$(dd if="$1" bs=4 skip=1 count=1 2>/dev/null | \
			hexdump -v -n 4 -e '1/1 "%02x"')"

		case "$magic" in
		32524448|48445232) ;;
		*)
			echo "Not an era image (magic $magic). Wrap the" \
				"-squashfs-tclinux.trx with build_era_trx.py first."
			return 1
			;;
		esac

		[ "$hdrlen" = "00000174" ] || {
			echo "Unexpected era header length $hdrlen, refusing."
			return 1
		}

		# 56MiB slot, 0x80000-0x3880000. An oversized image would be
		# truncated by mtd and boot to nothing.
		[ "$(wc -c < "$1")" -le 58720256 ] || {
			echo "Image is larger than the 56MiB tclinux partition."
			return 1
		}

		return 0
		;;
	chinamobile,gs3101|\
	dasan,h660gm-a-airtel|\
	dasan,h660gm-a-generic|\
	jiofiber,jcow407|\
	jiofiber,jcow414)
		return 0
		;;
	esac

	return 1
}

platform_do_upgrade() {
	local board=$(board_name)

	case "$board" in
	zyxel,vmg8825-t50)
		# Erase the whole partition and write the image at offset 0.
		# The full erase is deliberate: it is exactly what the manual
		# install does (flash_erase + nandwrite, install-guide), and it
		# leaves no tail of an older, larger image behind the new one.
		#
		# The writable overlay is a separate UBI on the "rootfs_data"
		# partition and is not touched here, so configuration survives
		# by construction. That also means `sysupgrade -n` does NOT
		# wipe it -- follow up with `firstboot -y && reboot` if a clean
		# configuration is what you wanted.
		mtd -e tclinux write "$1" tclinux
		;;
	chinamobile,gs3101|\
	dasan,h660gm-a-airtel|\
	dasan,h660gm-a-generic|\
	jiofiber,jcow407|\
	jiofiber,jcow414)
		CI_KERNPART="tclinux_kernel"
		nand_do_upgrade "$1"
		;;
	esac
}
