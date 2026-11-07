#!/usr/bin/env python3
"""Zero-dependency RFC 1350 TFTP client for VMG8825-T50 bootloader recovery.

Uploads firmware or kernel images directly to the router's ZHAL bootloader
(e.g., at 192.168.1.1) without requiring any external tools like `atftp` or
system tftp servers.

Handles RFC 1350 block number wrap-around (& 0xFFFF) for images > 32 MB.

CLI Usage:
    python3 tools/tftp_client.py <server_ip> <local_file> [remote_name]
    python3 tools/tftp_client.py 192.168.1.1 firmware/vmg8825-t50-initramfs-kernel.bin

Python API:
    from tftp_client import tftp_put
    tftp_put("192.168.1.1", "path/to/firmware.bin", remote_name="devfw.bin")
"""
import argparse
import os
import socket
import struct
import sys
import time


def tftp_put(server_ip, local_path, remote_name=None, port=69, timeout=5.0, retries=5, quiet=False):
    """RFC 1350 TFTP client for uploading files to the ZHAL bootloader.

    Args:
        server_ip: IP address of the TFTP server (e.g. "192.168.1.1")
        local_path: Path to the local file to send
        remote_name: Destination filename requested in WRQ (defaults to basename of local_path)
        port: Destination port (default: 69)
        timeout: Socket timeout in seconds (default: 5.0)
        retries: Number of retry attempts per block packet (default: 5)
        quiet: If True, suppress progress prints

    Returns:
        True on success. Raises TimeoutError or RuntimeError on failure.
    """
    if not os.path.isfile(local_path):
        raise FileNotFoundError(f"Local file not found: {local_path}")

    file_size = os.path.getsize(local_path)
    if remote_name is None:
        remote_name = os.path.basename(local_path)

    if not quiet:
        print(f"[*] TFTP upload: '{local_path}' ({file_size} bytes / {file_size / (1024 * 1024):.2f} MiB)")
        print(f"[*] Target: {server_ip}:{port} (remote name: '{remote_name}')")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    # WRQ: opcode 2, filename, 0, mode "octet", 0
    wrq = struct.pack(">H", 2) + remote_name.encode("ascii") + b"\x00octet\x00"

    # Send WRQ to port 69, wait for ACK 0 from server (which assigns dynamic port)
    t_start = time.time()
    server_addr = None

    for attempt in range(retries):
        try:
            sock.sendto(wrq, (server_ip, port))
            data, server_addr = sock.recvfrom(1024)
            if len(data) >= 4:
                opcode, block = struct.unpack(">HH", data[:4])
                if opcode == 4 and block == 0:
                    break
                elif opcode == 5:
                    err_code = block
                    err_msg = data[4:].decode(errors="replace").rstrip("\x00")
                    sock.close()
                    raise RuntimeError(f"TFTP server error {err_code}: {err_msg}")
        except socket.timeout:
            if attempt == retries - 1:
                sock.close()
                raise TimeoutError(f"Timed out waiting for initial TFTP ACK from {server_ip}:{port}")
            time.sleep(0.5)

    if not server_addr:
        sock.close()
        raise TimeoutError(f"Failed to connect to TFTP server at {server_ip}:{port}")

    if not quiet:
        print(f"[+] Connected to TFTP server at {server_addr[0]}:{server_addr[1]}")

    block_num = 1
    bytes_sent = 0
    total_packets = (file_size + 511) // 512

    with open(local_path, "rb") as f:
        while True:
            chunk = f.read(512)
            packet = struct.pack(">HH", 3, block_num & 0xFFFF) + chunk

            ack_received = False
            for attempt in range(retries):
                sock.sendto(packet, server_addr)
                try:
                    ack_data, _ = sock.recvfrom(1024)
                    if len(ack_data) >= 4:
                        ack_opcode, ack_block = struct.unpack(">HH", ack_data[:4])
                        if ack_opcode == 4 and ack_block == (block_num & 0xFFFF):
                            ack_received = True
                            break
                        elif ack_opcode == 5:
                            err_code = ack_block
                            err_msg = ack_data[4:].decode(errors="replace").rstrip("\x00")
                            sock.close()
                            raise RuntimeError(f"TFTP server error {err_code}: {err_msg}")
                except socket.timeout:
                    time.sleep(0.05 * (attempt + 1))

            if not ack_received:
                sock.close()
                raise TimeoutError(f"TFTP upload stalled: no ACK for block {block_num} after {retries} retries")

            bytes_sent += len(chunk)

            if not quiet and (block_num % 100 == 0 or len(chunk) < 512):
                pct = (bytes_sent / file_size) * 100 if file_size > 0 else 100.0
                elapsed = max(0.001, time.time() - t_start)
                speed_kbs = (bytes_sent / 1024.0) / elapsed
                sys.stdout.write(f"\r[*] Progress: {bytes_sent}/{file_size} bytes ({pct:5.1f}%) | {speed_kbs:6.1f} KB/s | block {block_num}/{total_packets}")
                sys.stdout.flush()

            if len(chunk) < 512:
                break
            block_num += 1

    sock.close()
    elapsed = max(0.001, time.time() - t_start)
    if not quiet:
        speed_kbs = (bytes_sent / 1024.0) / elapsed
        print(f"\n[+] TFTP upload complete! {bytes_sent} bytes transferred in {elapsed:.2f}s ({speed_kbs:.1f} KB/s).")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Zero-dependency RFC 1350 TFTP client for ZHAL bootloader file uploads."
    )
    parser.add_argument("server", help="TFTP server IP (e.g. 192.168.1.1)")
    parser.add_argument("file", help="Local file path to upload")
    parser.add_argument("remote_name", nargs="?", default=None,
                        help="Remote filename on server (defaults to basename of local file)")
    parser.add_argument("--port", type=int, default=69, help="TFTP UDP port (default: 69)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-packet timeout in seconds (default: 5.0)")
    parser.add_argument("--retries", type=int, default=5, help="Max retries per packet (default: 5)")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")

    args = parser.parse_args()

    try:
        tftp_put(
            server_ip=args.server,
            local_path=args.file,
            remote_name=args.remote_name,
            port=args.port,
            timeout=args.timeout,
            retries=args.retries,
            quiet=args.quiet,
        )
    except Exception as e:
        print(f"\n[-] TFTP Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
