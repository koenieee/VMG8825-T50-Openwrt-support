#!/bin/sh
#
# tools/build-firmware.sh inside a pinned Ubuntu container, for when your
# own distro is missing OpenWrt's build dependencies or is too new/odd for
# them. Same arguments and environment (OUT, JOBS, OPENWRT_REF) as
# build-firmware.sh; output lands in out/ as usual, owned by you.
#
# On macOS: macOS host filesystem is case-insensitive, which fails OpenWrt's
# case-sensitive-fs requirement. We automatically use a persistent Docker named
# volume (ext4 inside the Docker VM) to build at native speed and copy the
# resulting artifacts back to out/.
#
# Needs Docker (or Podman: DOCKER=podman tools/build-in-docker.sh).

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
IMAGE=vmg8825-t50-build
VOLUME=vmg8825-build-vol

"$DOCKER" build -q -t "$IMAGE" "$REPO/tools/docker" >/dev/null

mkdir -p "$REPO/out"

if [ "$(uname)" = "Darwin" ]; then
	echo "macOS detected: using Docker named volume ($VOLUME) for case-sensitive ext4 build"
	"$DOCKER" volume inspect "$VOLUME" >/dev/null 2>&1 || "$DOCKER" volume create "$VOLUME" >/dev/null

	# Sync repo to container volume (skip git history and build output to be fast)
	echo "Syncing workspace into container volume..."
	tar --exclude='out/*' --exclude='.git' -cf - -C "$REPO" . | \
		"$DOCKER" run --rm -i -v "$VOLUME:/work" "$IMAGE" tar -xf - -C /work

	tty=; [ -t 0 ] && tty=-it
	"$DOCKER" run --rm $tty \
		-v "$VOLUME:/work" -w /work \
		-e OUT=/work/out -e JOBS -e OPENWRT_REF \
		"$IMAGE" tools/build-firmware.sh "$@"

	echo "Copying build artifacts to $REPO/out/..."
	"$DOCKER" run --rm -v "$VOLUME:/work" -v "$REPO/out:/host_out" "$IMAGE" \
		sh -c 'cp -a /work/out/. /host_out/ 2>/dev/null || true'
else
	tty=; [ -t 0 ] && tty=-it
	exec "$DOCKER" run --rm $tty \
		--user "$(id -u):$(id -g)" \
		-v "$REPO:$REPO" -w "$REPO" \
		-e OUT -e JOBS -e OPENWRT_REF \
		"$IMAGE" tools/build-firmware.sh "$@"
fi
