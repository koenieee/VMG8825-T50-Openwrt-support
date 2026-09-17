#!/usr/bin/env bash
# Push a .trx image to a Zyxel EN751627 zloader over TFTP, matching the
# ATUR upgrade command. See ../VMG8825-T50-FLASHING.md for the full
# procedure and prerequisites.
#
# On the router (serial console), before running this script:
#   ZHAL> ATDC
#   ZHAL> ATUR <file>.trx,<partition>
#   TFTP server is started, put your file '<file>.trx' to server (IP is 192.168.1.1)
#
# Then, from a PC on 192.168.1.0/24 (not .1):
#   ./flash-tftp.sh <file>.trx [router-ip]
set -euo pipefail

file="${1:?usage: flash-tftp.sh <file>.trx [router-ip]}"
ip="${2:-192.168.1.1}"

[ -f "$file" ] || { echo "not found: $file" >&2; exit 1; }

atftp --put --local-file "$file" --remote-file "$(basename "$file")" "$ip"
