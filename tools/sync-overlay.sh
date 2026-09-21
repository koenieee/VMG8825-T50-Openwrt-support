#!/bin/sh
#
# Keep openwrt-overlay/ and the openwrt/ submodule in step.
#
# openwrt/ is a git submodule, so nothing in openwrt-overlay/ reaches a build
# until it is copied across. That copy has been manual, and forgetting it
# costs a full build cycle chasing a change that was never in the image --
# or, worse, an edit made directly in openwrt/ that never gets committed
# because only openwrt-overlay/ is tracked by this repo.
#
# Three modes:
#   push    overlay -> openwrt/   (before building; the default)
#   pull    openwrt/ -> overlay   (after editing in the build tree)
#   check   compare only, exit 1 on drift, print what differs
#
# "files/" is deliberately not synced: those are payloads baked into an image
# by the image recipe, not sources the build tree reads from a fixed path.

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-push}"

# overlay path : tree path, relative to REPO
PAIRS="
target/linux/econet:openwrt/target/linux/econet
package/kernel/econet-eth:openwrt/package/kernel/econet-eth
"

die() { echo "sync-overlay: $*" >&2; exit 1; }

[ -d "$REPO/openwrt/target/linux" ] || die "openwrt/ is empty -- run: git submodule update --init"

drift=0

for pair in $PAIRS; do
	src="$REPO/openwrt-overlay/${pair%%:*}"
	dst="$REPO/${pair#*:}"

	[ -d "$src" ] || die "missing overlay dir: $src"
	mkdir -p "$dst"

	case "$MODE" in
	push)
		cp -a "$src/." "$dst/"
		echo "push  ${pair%%:*}"
		;;
	pull)
		cp -a "$dst/." "$src/"
		echo "pull  ${pair%%:*}"
		;;
	check)
		# Only compare files the overlay actually owns. The build tree
		# carries upstream boards and build artifacts we do not track,
		# so a plain recursive diff would be all noise.
		(cd "$src" && find . -type f) | while read -r f; do
			if ! cmp -s "$src/$f" "$dst/$f"; then
				echo "DRIFT ${pair%%:*}/${f#./}"
			fi
		done
		;;
	*)
		die "unknown mode '$MODE' (push|pull|check)"
		;;
	esac
done

if [ "$MODE" = check ]; then
	# The subshell above cannot set drift, so recount here.
	for pair in $PAIRS; do
		src="$REPO/openwrt-overlay/${pair%%:*}"
		dst="$REPO/${pair#*:}"
		n=$( (cd "$src" && find . -type f) | while read -r f; do
			cmp -s "$src/$f" "$dst/$f" || echo x
		done | wc -l )
		drift=$((drift + n))
	done
	if [ "$drift" -gt 0 ]; then
		echo "sync-overlay: $drift file(s) differ -- run '$0 push' or '$0 pull'" >&2
		exit 1
	fi
	echo "sync-overlay: overlay and build tree agree"
fi

case "$MODE" in
push)
	echo
	echo "Note: target base-files changes need an explicit rebuild before"
	echo "they reach an image:"
	echo "  make package/base-files/clean && make package/install && make target/linux/install"
	;;
esac
