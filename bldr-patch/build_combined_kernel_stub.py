#!/usr/bin/env python3
"""Builds the single self-contained MIPS stub that boots a real, unsigned
OpenWrt kernel on VMG8825-T50 hardware via netboot -- no flash write,
fully recoverable via power-cycle.

Design rationale (see README.md for the full story): the kernel is TFTP'd via
ATLD to the only address bldr accepts (0x80020000) but must execute from its
real linked address (0x80002000, TRX_LOADADDR). A relocation copy + D$
writeback/I$ invalidate + jump must all happen in ONE `jump` call -- a SECOND
`jump` after a separate copy stub silently hung, most likely because the copy
stomped over bldr's own stack in low RAM before the dispatcher could recover.
"""
import struct

def R(op, rs, rt, rd, sh, fn): return (op<<26)|(rs<<21)|(rt<<16)|(rd<<11)|(sh<<6)|fn
def I(op, rs, rt, imm): return (op<<26)|(rs<<21)|(rt<<16)|(imm&0xFFFF)

zero,a0,a1,a2,a3,t0,t1,t2,t3,t4,t9,ra = 0,4,5,6,7,8,9,10,11,12,25,31

def hi(v): return (v >> 16) & 0xFFFF
def lo(v): return v & 0xFFFF

def uart_marker(msg):
    """LSR-polled UART marker writer using t1 (base), t2 (char), t3 (LSR status)."""
    words = [I(0xF, 0, t1, 0xbfbf)]  # lui t1, 0xbfbf (UART base, KSEG1)
    for c in msg:
        wait_idx = len(words)
        words.append(I(0xD, 0, t2, ord(c)))     # ori t2, zero, char
        words.append(I(0x23, t1, t3, 0x14))     # lw t3, 0x14(t1)  -- LSR
        words.append(I(0xC, t3, t3, 0x20))      # andi t3, t3, 0x20 -- THRE bit
        beq_idx = len(words)
        words.append(0)                          # beq t3,zero,wait (patched)
        words.append(0)                          # nop (delay slot)
        words.append(I(0x2B, t1, t2, 0))        # sw t2, 0(t1)
        words[beq_idx] = I(0x4, t3, 0, wait_idx - beq_idx - 1)
    return words

def build_combined(src, dst, length, entry):
    """ONE self-contained stub: word-copy (src->dst) + D$WB/I$inv over dst range +
    jr into entry -- all in a single jump call, so bldr's own command dispatcher
    is never asked to survive a SECOND `jump` after the low-RAM-stomping copy."""
    assert length % 4 == 0 and 0 < length <= 0xFFFFFFFF
    words = []

    words += uart_marker("STRT")

    # --- copy loop: src -> dst, forward (safe since dst < src) ---
    words.append(I(0xF, 0, a0, hi(src)))      # lui a0, src_hi
    words.append(I(0xD, a0, a0, lo(src)))     # ori a0, a0, src_lo
    words.append(I(0xF, 0, a1, hi(dst)))      # lui a1, dst_hi
    words.append(I(0xD, a1, a1, lo(dst)))     # ori a1, a1, dst_lo
    words.append(I(0xF, 0, a2, hi(length)))   # lui a2, len_hi
    words.append(I(0xD, a2, a2, lo(length)))  # ori a2, a2, len_lo
    words.append(R(0, a0, a2, a3, 0, 0x21))   # addu a3, a0, a2   (src end ptr)
    CLOOP = len(words)
    words.append(I(0x23, a0, t4, 0))          # CLOOP: lw t4, 0(a0)
    words.append(I(0x2B, a1, t4, 0))          # sw t4, 0(a1)
    words.append(I(0x9, a0, a0, 4))           # addiu a0, a0, 4
    words.append(I(0x9, a1, a1, 4))           # addiu a1, a1, 4
    cbne_idx = len(words)
    words.append(0)                            # bne a0, a3, CLOOP (patched)
    words.append(0)                            # nop (delay slot)
    words[cbne_idx] = I(0x5, a0, a3, CLOOP - cbne_idx - 1)

    words += uart_marker("CPOK")

    # --- cache invalidate loop over [dst, dst+length) ---
    words.append(I(0xF, 0, a0, hi(dst)))      # lui a0, dst_hi
    words.append(I(0xD, a0, a0, lo(dst)))     # ori a0, a0, dst_lo
    words.append(I(0xF, 0, a1, hi(length)))   # lui a1, len_hi
    words.append(I(0xD, a1, a1, lo(length)))  # ori a1, a1, len_lo
    words.append(R(0, a0, a1, a1, 0, 0x21))   # addu a1, a0, a1   (end ptr)
    ILOOP = len(words)
    words.append(I(0x2F, a0, 0x15, 0))        # ILOOP: cache 0x15, 0(a0)  (Hit WB-Invalidate D$)
    words.append(I(0x2F, a0, 0x10, 0))        # cache 0x10, 0(a0)        (Hit Invalidate I$)
    words.append(I(0x9, a0, a0, 0x20))        # addiu a0, a0, 0x20
    words.append(R(0, a0, a1, t0, 0, 0x2B))   # sltu t0, a0, a1
    ibne_idx = len(words)
    words.append(0)                            # bne t0, zero, ILOOP (patched)
    words.append(0)                            # nop (delay slot)
    words[ibne_idx] = I(0x5, t0, 0, ILOOP - ibne_idx - 1)

    words.append(R(0, 0, 0, 0, 0, 0x0F))      # sync

    words += uart_marker("JUMP")

    words.append(I(0xF, 0, t9, hi(entry)))    # lui t9, entry_hi
    words.append(I(0xD, t9, t9, lo(entry)))   # ori t9, t9, entry_lo
    words.append(R(0, t9, 0, 0, 0, 0x08))     # jr t9   (direct jump into KSEG0 entry)
    words.append(0)                            # nop (delay slot)
    return words

if __name__ == "__main__":
    import sys, os
    kernel_path = sys.argv[1] if len(sys.argv) > 1 else "kernel_decompressed.bin"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "combined_kernel_stub.bin"
    KLEN = os.path.getsize(kernel_path)
    SRC = 0x80020000   # only address bldr's ATLD/TFTP will accept
    DST = 0x80002000   # TRX_LOADADDR -- real linked kernel entry point
    words = build_combined(SRC, DST, KLEN, DST)
    print("word count:", len(words), " byte count:", len(words)*4)
    out = b"".join(struct.pack(">I", w) for w in words)
    open(out_path, "wb").write(out)
    print("wrote", len(out), "bytes to", out_path)
