#!/bin/sh
#
# tools/build-firmware.sh inside a pinned Ubuntu container, for when your
# own distro is missing OpenWrt's build dependencies or is too new/odd for
# them. Same arguments and environment (OUT, JOBS, OPENWRT_REF) as
# build-firmware.sh; output lands in out/ as usual, owned by you.
#
# Pick one per checkout: host tools that openwrt/ already built on your PC
# are linked against your PC's libc and may not run in the container (and
# the other way round). To switch, start from a clean tree first:
#   rm -rf openwrt/build_dir openwrt/staging_dir openwrt/tmp
#
# Needs Docker (or Podman: DOCKER=podman tools/build-in-docker.sh).

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
IMAGE=vmg8825-t50-build

"$DOCKER" build -q -t "$IMAGE" "$REPO/tools/docker" >/dev/null

# Mounted at the same path as on the host, so paths printed by the build
# (and OUT if you set one inside the repo) mean the same thing outside.
# -it only with a terminal, so it also runs from CI or a cron job.
tty=; [ -t 0 ] && tty=-it
exec "$DOCKER" run --rm $tty \
	--user "$(id -u):$(id -g)" \
	-v "$REPO:$REPO" -w "$REPO" \
	-e OUT -e JOBS -e OPENWRT_REF \
	"$IMAGE" tools/build-firmware.sh "$@"
