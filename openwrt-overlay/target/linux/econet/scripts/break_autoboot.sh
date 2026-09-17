#!/usr/bin/env bash
# Watches a socat/cat log of the serial console for the zloader "Hit any
# key to stop autoboot" banner and sends CR to break it, landing at ZHAL>.
#
# Only works if nothing else (e.g. minicom) has the serial device open at
# the same time -- two readers on one tty silently split/steal bytes.
#
# Usage:
#   socat -u /dev/ttyUSB0,rawer,b115200,cs8 - > uart.log &
#   ./break_autoboot.sh [uart.log] [/dev/ttyUSB0]
set -u
LOG="${1:-uart.log}"
DEV="${2:-/dev/ttyUSB0}"

prev=$(stat -c %s "$LOG")
echo "[armed] watching from offset $prev at $(date +%T)"
state=idle

while :; do
  sleep 0.2
  size=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
  [ "$size" -le "$prev" ] && continue
  new=$(tail -c +$((prev+1)) "$LOG")
  prev=$size
  case "$state" in
    idle)
      if printf '%s' "$new" | grep -q "Hit any key"; then
        printf '\r' > "$DEV"
        state=broken
        echo "[*] break sent at $(date +%T)"
      fi
      ;;
    broken)
      if printf '%s' "$new" | grep -qE "ZHAL>"; then
        echo "[*] ZHAL prompt reached at $(date +%T) -- done"
        exit 0
      fi
      ;;
  esac
done
