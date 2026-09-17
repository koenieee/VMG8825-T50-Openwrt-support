# Vendor image checksum format (era header)

The zloader's TFTP flash gate (`ATUR`) and the OEM flashing tooling use a
different, older header format than what upstream OpenWrt's build
tooling emits by default. This document is the fully reverse-engineered
format and checksum formula; `build_era_trx.py` implements it.

## 1. The flash-gate formula

The zloader's TFTP check compares the BE32 field at offset `0x0C`
against:

```
BE32(header[0x0C]) == JAMCRC(received[0x174 : received_len - 256])
```

where `received` is the exact file as downloaded over TFTP.

**Evidence — 4 independent data points (2x pristine OEM firmware, 2x
live zloader rejections):**

| File (pristine OEM) | stored `@0x0C` (BE) | zloader "should be" | verified |
|---|---|---|---|
| `V550ABOM8.5C0.bin` (0x1984F68 B) | `0x44582DED` | `0x9B3E5EBD` | `JAM(file[0x174:len-256]) = 0x9B3E5EBD` ✓ |
| `V550ABOM7C0.bin` (0x1FC0000 B) | `0x6EDCD980` | `0x267D7BC4` | `JAM(file[0x174:len-256]) = 0x267D7BC4` ✓ |

Matching live zloader errors:
```
Wrong image checksum 0x44582DED! should be 0x9B3E5EBD   (ABOM 8.5, pristine!)
Wrong image checksum 0x6EDCD980! should be 0x267D7BC4   (ABOM 7C0, pristine!)
```

Notably, even **official pristine OEM firmware fails this same check**:
those `.zip`/`.bin` files are web-UI upgrade files, not zloader-TFTP
files. The web-UI upgrade path rewrites `@0x0C` itself when flashing a
slot; the OEM build fills `@0x0C` with a different formula than what the
zloader's TFTP gate computes. So a checksum mismatch here does not by
itself indicate a bad build — the flash gate is safe either way: a
rejected image is refused *before* any flash write (no-op, not a brick —
confirmed by the device returning to the `ZHAL>` prompt with no write).

## 2. The hash algorithm

**CRC-32/JAMCRC**, per mjn3's `crc32buf` from the GPL `econet-trx` source
(`econet-trx-7.3.245.300/tools/trx/trx.c`):

- reflected CRC-32, poly `0xEDB88320`, init `0xFFFFFFFF`, **no final XOR**
  (= CRC-32/JAMCRC in the reveng catalogue; check value `0x340BC6D9` for
  `"123456789"`)
- in Python: `(zlib.crc32(buf) ^ 0xFFFFFFFF) & 0xFFFFFFFF`

## 3. Why OpenWrt's default slim-TRX header doesn't work here

OpenWrt's stock `tclinux-trx.sh` build step emits a **slim, 0x100-byte**
header with `@0x0C = JAMCRC(file[0x100:])` — a different formula (start
offset `0x100` vs `0x174`, and missing the `-256` tail-skip) than the
zloader's `JAMCRC(received[0x174:len-256])`. So even an internally
consistent slim-TRX header computes a different value than what the
zloader expects, and gets rejected.

## 4. Era header layout (0x174 bytes, big-endian on the wire)

Confirmed via pristine OEM firmware (ABOM 8.5):

| Offset | Field | Value |
|---|---|---|
| `0x00` | magic | `"2RDH"` (BE32 `0x32524448`, TRX_MAGIC2) |
| `0x04` | hdrsize | BE32 `0x174` |
| `0x08` | totlen | BE32, full file size |
| `0x0C` | checksum | BE32 `JAMCRC(received[0x174:len-256])` — **the flash gate** |
| `0x10` | SDK version string | `"7.3.245.300_v007\n"` (template) |
| `0x50` | kernel length | BE32 (= squashfs offset relative to payload start) |
| `0x54` | rootfs length | BE32 (incl. trailing padding, to EOF) |
| `0x5C` | model string | `"3 6035 122 0\n"` (template, ABPY branch) |
| `0x7C` | load address | BE32 `0x80002000` (matches boot log "Decompress to 80002000") |
| `0x100` | ZyXEL extension | `chipId "en7516"`, boardId 16x0, modelId `{4,5,5,1}`, `swInt`/`swExt` 32-byte version strings |
| `0x164` | rootfs checksum | BE32, see vendor quirk below |
| `0x168` | kernel checksum | BE32, see vendor quirk below |
| `0x16C` | (reserved extension field) | |
| `0x170` | header checksum | BE32, see below |

**Vendor bug (must be bug-compatible):** the kernel/rootfs extension
checksums only cover the **last 100 KiB** of their region (the vendor's
build tool overwrites the running CRC every 100 KiB chunk instead of
folding it in) — confirmed on pristine OEM:
- `@0x168` kernel checksum (BE) = `JAMCRC(kernel's last 100 KiB chunk)` —
  verified on pristine 8.5: `0x3ab85151`
- `@0x164` rootfs checksum (BE) = same last-100KiB-chunk JAMCRC over the
  rootfs region — `0xa21653ee`
- `@0x170` header checksum = `JAMCRC(header[0:0x174])` with that field
  itself zeroed — `0xbc833460`

All extension checksums are big-endian on the wire, same as the
top-level fields.

## 5. Kernel region format

The OEM kernel region is a bare **LZMA-ALONE stream**, starting directly
at `0x174` — no jump/stub prefix needed. Confirmed: the props byte at
`0x174` is a valid LZMA-alone props byte, and `python3`'s
`lzma.FORMAT_ALONE` decompresses it starting at `0x174` into valid MIPS
code with a normal function prologue
(`27bdffe8 afbf0014` = `addiu $sp,$sp,-24; sw $ra`). The zloader
decompresses this itself at boot time (`"Decompress to 80002000"` in the
live boot log).

## 6. Quick CRC recheck snippet

```python
import zlib
data = open("image.bin", "rb").read()
content = data[0x174 : len(data) - 256]
jam_crc = (zlib.crc32(content) & 0xFFFFFFFF) ^ 0xFFFFFFFF
embedded = int.from_bytes(data[0x0C:0x10], "big")
print(f"computed={jam_crc:08X} embedded={embedded:08X} match={jam_crc==embedded}")
```

---

*Sources: GPL `econet-trx-7.3.245.300` (mjn3's `crc32buf`), 2 pristine OEM
`.bin` data points, 2 live zloader rejection messages, live ATSH/ATUR
sessions.*
