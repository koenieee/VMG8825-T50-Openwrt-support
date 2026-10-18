# Build environment for tools/build-firmware.sh, so the firmware builds the
# same on any distro (or macOS/Windows with Docker). Package list is
# OpenWrt's own for Debian/Ubuntu:
# https://openwrt.org/docs/guide-developer/toolchain/install-buildsystem
FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
	build-essential clang flex bison g++ gawk gcc-multilib g++-multilib \
	gettext git libncurses-dev libssl-dev python3 python3-setuptools \
	rsync swig unzip zlib1g-dev file wget ca-certificates xz-utils \
	bzip2 patch perl time && \
	rm -rf /var/lib/apt/lists/*

# OpenWrt refuses to build as root; build-in-docker.sh runs as your own uid.
ENV HOME=/tmp/home
RUN mkdir -m 1777 /tmp/home && git config --system --add safe.directory '*'
