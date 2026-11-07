# Build environment for tools/build-firmware.sh, so the firmware builds the
# same on any distro (or macOS/Windows with Docker). Package list is
# OpenWrt's own for Debian/Ubuntu:
# https://openwrt.org/docs/guide-developer/toolchain/install-buildsystem
FROM ubuntu:24.04

RUN apt-get update && \
	PKGS="build-essential clang flex bison g++ gawk gettext git libncurses-dev libssl-dev python3 python3-setuptools rsync swig unzip zlib1g-dev file wget ca-certificates xz-utils bzip2 patch perl time" && \
	if [ "$(dpkg --print-architecture)" = "amd64" ]; then PKGS="$PKGS gcc-multilib g++-multilib"; fi && \
	DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $PKGS && \
	rm -rf /var/lib/apt/lists/*

# OpenWrt refuses to build as root; build-in-docker.sh runs as your own uid.
ENV HOME=/tmp/home
RUN mkdir -m 1777 /tmp/home && git config --system --add safe.directory '*'
