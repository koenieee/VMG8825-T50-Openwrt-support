# VMG8825-T50 — flashing over the zloader's TFTP interface

Live-verified against real hardware: bootbase V1.44 (01/04/2021), ZyXEL
zloader v1.4.4 (01/04/2021), Multiboot client 2.4.

## Serial console

`/dev/ttyUSB0`, 115200 8N1, **CR-only** (no LF — sending LF makes zloader
reprint its menu). Only **one process may hold the port**: running
`socat` (logging) and `minicom` (interactive) at the same time silently
splits/steals bytes between them and neither sees a clean stream. Pick
one.

`Hit any key to stop autoboot: 5..0` — a 5 second window to break
autoboot by sending CR.

## ZHAL> command reference (from live `help`)

```
ATEN    x[,y]         set BootExtension Debug Flag (y=password)
ATSE    x             show the seed of password generator
ATDC                  disable check model mechanism
ATSH                  dump manufacturer related data in ROM
ATRT    [x,y,z,u]     RAM read/write test (x=level, y=start addr, z=end addr, u=iterations)
ATGO                  boot up whole system
ATSR    [x]           system reboot
ATUR    x[,y]         upgrade RAS image (filename, partition number)
```

No other classic ZyXEL AT-commands exist on this firmware (tried
~45 of them: `ATDU`/`ATRM`/`ATBR`/`ATCB`/`ATCU`/`ATVD`/`ATML`/etc.). No
hidden memory-dump command, no boot-slot-select argument on
`ATSR`/`ATGO`.

The bootloader always validates/boots **partition 1 ("main")
first**, unconditionally, regardless of which partition you last wrote
with `ATUR`. To boot-test an image you must write it to partition 1.

## TFTP flash procedure

On the router (serial console):
```
ZHAL> ATDC
Model ID check: disabled
ZHAL> ATUR <file>.trx,<partition>
Upgrade rootfs partition <partition>
TFTP server is started, put your file '<file>.trx' to server (IP is 192.168.1.1).
```
`<partition>` = `1` (main) or `2` (slave).

From the PC (must be on 192.168.1.0/24, **not** .1 — the router is the
TFTP *server* here, not a client):
```
atftp --put --local-file <file>.trx --remote-file <file>.trx 192.168.1.1
```

See `scripts/flash-tftp.sh` for a wrapper, `scripts/break_autoboot.sh`
for a watcher that auto-sends CR when it sees the autoboot banner (only
useful when nothing else — e.g. minicom — also has the port open).

`ATUR` is known to silently stop writing NAND after a device's first
successful flash (reports success but doesn't touch flash on later
calls) — `bldr-patch/flash_mtd3_via_ater_atwf.py` uses the raw
`ATER`/`ATWF` primitives instead; see `install-guide/README.md` §6.

### Getting back to ZHAL> at all

The router's web UI can normally reboot itself (`POST /cgi-bin/Reboot`),
but if the login-session limit is already exhausted ("Maximum number of
login account has reached") all TCP ports go filtered and there is no
software path back in — a physical power cycle is the only way back to
the bootloader.

## The checksum gate

`ATUR`/TFTP rejects an image before writing it if its embedded checksum
doesn't match what the zloader computes over the received bytes:
```
Wrong image checksum 0xB327F51D! should be 0x38771F2A
Failed(-3)!
ZHAL>
```
This is a safe, no-op rejection, not a brick — the router returns
straight to the prompt without writing anything. See
`../../../CHECKSUM_RESOLUTION.md` for the full checksum format and
formula (`build_era_trx.py` implements it); notably, even pristine OEM
firmware fails this exact gate for unrelated reasons documented there.
