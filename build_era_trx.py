#!/usr/bin/env python3
"""Build a zloader-v1.4.4 (ABPY / "free bootbase") compatible era-0x174 TRX image.

The upstream econet/en751627 target (tclinux-trx.sh) emits a *slim* 0x100-byte
TRX header aimed at the EX3301-T0's v1.4.5 zloader:

    @0x04 = 0x100   header length
    @0x0C = JAMCRC(file[0x100:])            <- slim formula

Our VMG8825-T50 ships zloader v1.4.4, which wants the older *era* 0x174 header
and checks a DIFFERENT crc region at ATUR/TFTP time:

    @0x04 = 0x174   header length
    @0x0C = JAMCRC(file[0x174 : len-256])   <- era/gate formula (last 256 = RSA sig, skipped)

That formula was proven against two pristine OEM firmwares and two live zloader
"Wrong image checksum ...! should be ..." error messages (see CHECKSUM_RESOLUTION.md).
This tool re-wraps an OpenWrt build's kernel+rootfs into the era layout, using the
pristine OEM ABOM 8.5 header as a template so every static field / ZyXEL extension
matches a known-good image (model-ID 4551 is identical for ABOM and ABPY).

Usage:
    # easiest: reuse an existing slim .trx's payload (no rebuild needed)
    ./build_era_trx.py --slim openwrt/bin/targets/econet/en751627/openwrt-...-vmg8825-t50-squashfs-tclinux.trx \
                       -o vmg8825-era.bin

    # or supply kernel-lzma + rootfs directly
    ./build_era_trx.py --kernel kernel.lzma --rootfs root.squashfs -o vmg8825-era.bin

    # optional: real 256-byte RSA signature (default: 256 zero bytes)
    ./build_era_trx.py --slim ... --sig sig.bin -o out.bin
"""

import argparse
import struct
import sys
import zlib

HDRLEN = 0x174          # era header length
SLIM_HDRLEN = 0x100     # tclinux-trx.sh's header length (its kernel padding assumes this)
SIG_LEN = 256           # trailing RSA signature region (skipped by the gate CRC)
CHUNK = 0x19000         # 100 KiB -- vendor's buggy per-chunk CRC window
TEMPLATE = "board-files/era_header_template.bin"


def jam(buf: bytes) -> int:
    """CRC-32/JAMCRC: reflected CRC32, poly 0xEDB88320, init 0xFFFFFFFF, no final XOR."""
    return (zlib.crc32(buf) ^ 0xFFFFFFFF) & 0xFFFFFFFF


def last_chunk_jam(buf: bytes) -> int:
    """Reproduce the vendor loop bug: crc is recomputed (not accumulated) per
    CHUNK-sized read, so the stored value only ever covers the final chunk."""
    if not buf:
        return jam(b"")
    off = 0
    last = b""
    while off < len(buf):
        last = buf[off:off + CHUNK]
        off += CHUNK
    return jam(last)


def load_payload(args):
    """Return (kernel_padded, rootfs_data) as raw bytes."""
    if args.slim:
        d = open(args.slim, "rb").read()
        if d[0:4] not in (b"2RDH", b"HDR2"):
            sys.exit(f"{args.slim}: not a TRX (magic {d[0:4]!r})")
        klen = struct.unpack(">I", d[0x50:0x54])[0]
        rlen = struct.unpack(">I", d[0x54:0x58])[0]
        slim_hdr = struct.unpack(">I", d[0x04:0x08])[0]
        payload = d[slim_hdr:]
        kernel = payload[:klen]
        # UBI rootfs devices (Device/tclinux-ubi) use Build/kernel-trx, which
        # never passes --rootfs to tclinux-trx.sh -- rlen is 0 in the slim
        # header even though append-ubi appended a real UBI image after the
        # padded kernel. Fall back to "everything past the kernel" in that case.
        rootfs = payload[klen:klen + rlen] if rlen else payload[klen:]
        # tclinux-trx.sh pads the kernel so rootfs lands at a FIXED absolute
        # offset from the start of the TRX file (PAD_ROOTFS_OFFSET_TO), assuming
        # its own SLIM_HDRLEN (0x100) header precedes it. Our era header is
        # HDRLEN (0x174) bytes -- 0x74 bytes longer -- so if we keep klen as-is,
        # rootfs would land 0x74 bytes too far into the flashed image, breaking
        # the DTS's fixed nested "rootfs" MTD partition offset and causing a
        # VFS root-mount panic/reboot loop on persistent (non-netboot) boot.
        # Trim that many trailing 0xff padding bytes off the kernel to compensate.
        trim = HDRLEN - SLIM_HDRLEN
        if trim > 0:
            assert kernel[-trim:] == b"\xff" * trim, "expected trailing 0xff padding to trim"
            kernel = kernel[:-trim]
        return kernel, rootfs
    if not args.kernel:
        sys.exit("need --slim OR --kernel [--rootfs]")
    kernel = open(args.kernel, "rb").read()
    rootfs = open(args.rootfs, "rb").read() if args.rootfs else b""
    return kernel, rootfs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_argument_group("payload source")
    src.add_argument("--slim", help="existing slim OpenWrt .trx to re-wrap")
    src.add_argument("--kernel", help="kernel LZMA stream")
    src.add_argument("--rootfs", help="rootfs squashfs")
    ap.add_argument("--sig", help="256-byte RSA signature (default: zeros)")
    ap.add_argument("--template", default=TEMPLATE,
                    help=f"OEM 0x174 header template (default: {TEMPLATE})")
    ap.add_argument("-o", "--output", required=True, help="output era image")
    args = ap.parse_args()

    hdr = bytearray(open(args.template, "rb").read())
    if len(hdr) != HDRLEN:
        sys.exit(f"template must be {HDRLEN:#x} bytes, got {len(hdr):#x}")

    kernel, rootfs = load_payload(args)
    sig = open(args.sig, "rb").read() if args.sig else b"\x00" * SIG_LEN
    if len(sig) != SIG_LEN:
        sys.exit(f"signature must be {SIG_LEN} bytes, got {len(sig)}")

    # Layout mirrors OEM: kernel(padded) | rootfs | sig(256), PLUS 256 trailing
    # pad bytes for ATUR compatibility. Two zloader checks read @0x0C over
    # DIFFERENT-length regions, and (proven on hardware) both end at @0x08 totlen:
    #   boot-time CRC  : JAM(file[0x174 : @0x08])         -> kernel+rootfs+sig
    #   ATUR/TFTP gate : JAM(file[0x174 : recv_len-256])  -> blind last-256 skip
    # OEM sets @0x0C = JAM(kernel+rootfs+sig) so BOOT passes, but that fails ATUR
    # (recv_len-256 cuts into the sig). We keep @0x0C = JAM(kernel+rootfs+sig) for
    # BOOT and append 256 pad bytes BEYOND @0x08 so ATUR's blind -256 lands exactly
    # on @0x08 (end of sig), making ATUR cover the same region. One @0x0C satisfies
    # both. @0x08 totlen points at end-of-sig; the pad sits past it, ignored by boot.
    ATUR_PAD = 256
    klen = len(kernel)
    rlen = len(rootfs) + SIG_LEN     # rootfs + sig, exactly like OEM
    full_payload = kernel + rootfs + sig
    full_len = HDRLEN + len(full_payload)   # @0x08 = end of sig (NOT incl the pad)

    # --- patch the dynamic header fields ---
    struct.pack_into(">I", hdr, 0x08, full_len)          # total length (end of sig)
    struct.pack_into(">I", hdr, 0x50, klen)              # kernel length
    struct.pack_into(">I", hdr, 0x54, rlen)              # rootfs length (incl. sig)

    # extension checksums (vendor last-100KiB-chunk bug), BE on the wire
    kern_region = full_payload[:klen]
    root_region = full_payload[klen:klen + rlen]
    struct.pack_into(">I", hdr, 0x164, last_chunk_jam(root_region))  # rootfsChksum
    struct.pack_into(">I", hdr, 0x168, last_chunk_jam(kern_region))  # kernelChksum
    struct.pack_into(">I", hdr, 0x16C, 0)                            # constant 0

    # THE gate: @0x0C = JAMCRC(kernel+rootfs+sig) = JAM(file[0x174 : @0x08]).
    # This is the boot-time value; the 256-byte pad below makes ATUR match it too.
    gate = jam(full_payload)
    struct.pack_into(">I", hdr, 0x0C, gate)

    # header checksum LAST: JAMCRC over the whole header with @0x170 itself zeroed.
    # Must run after every other header field (incl. @0x0C) is final, since the
    # zloader recomputes it over the received header and rejects a stale value.
    struct.pack_into(">I", hdr, 0x170, 0)
    struct.pack_into(">I", hdr, 0x170, jam(bytes(hdr)))

    out = bytes(hdr) + full_payload + (b"\x00" * ATUR_PAD)
    open(args.output, "wb").write(out)

    # --- self-verify BOTH regions the zloader recomputes against @0x0C ---
    stored = struct.unpack(">I", out[0x0C:0x10])[0]
    atur = jam(out[0x174:len(out) - 256])            # flash-time: blind last-256 skip
    boot = jam(out[0x174:full_len])                  # boot-time: [0x174 : @0x08]
    recomputed = atur
    ok = (atur == stored) and (boot == stored)

    print(f"wrote {args.output}: {len(out):#x} bytes ({len(out)} = {len(out)/1024/1024:.2f} MiB)")
    print(f"  klen@0x50 = {klen:#x}   rlen@0x54 = {rlen:#x}")
    print(f"  gate @0x0C (stored)        = {stored:#010x}")
    print(f"  ATUR JAM(file[0x174:len-256])      = {atur:#010x}   -> {'PASS' if atur==stored else 'MISMATCH'}")
    print(f"  BOOT JAM(file[0x174:0x174+k+r])    = {boot:#010x}   -> {'PASS' if boot==stored else 'MISMATCH'}")
    print(f"  kernelChksum @0x168 = {struct.unpack('>I', out[0x168:0x16c])[0]:#010x}")
    print(f"  rootfsChksum @0x164 = {struct.unpack('>I', out[0x164:0x168])[0]:#010x}")
    print(f"  headerChksum @0x170 = {struct.unpack('>I', out[0x170:0x174])[0]:#010x}")
    if not ok:
        sys.exit("SELF-CHECK FAILED -- would be rejected by zloader")


if __name__ == "__main__":
    main()
