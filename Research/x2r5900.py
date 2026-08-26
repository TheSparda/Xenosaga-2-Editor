"""R5900 (PS2 EE) decode shim over capstone MIPS64.

Research tool -- needs capstone, so it lives here and not in Editor/.


capstone has no R5900 model. Two families it gets wrong matter for damage math:
  * the 3-operand `mult rd, rs, rt` / `multu` / `div` forms (rd != 0), which
    capstone's MIPS64 rejects outright because the arch requires rd == 0;
  * the MMI opcode space (op 0x1C), which carries the second multiplier
    pipeline -- mult1/div1/mflo1/mfhi1 -- used constantly in this overlay.
Both decode to `.byte` runs, and they sit in the middle of every formula.
"""
import struct
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS64, CS_MODE_LITTLE_ENDIAN

R = ["zero","at","v0","v1","a0","a1","a2","a3","t0","t1","t2","t3","t4","t5",
     "t6","t7","s0","s1","s2","s3","s4","s5","s6","s7","t8","t9","k0","k1",
     "gp","sp","fp","ra"]

# SPECIAL (op 0) funcs capstone refuses when rd != 0
_SPECIAL3 = {0x18: "mult", 0x19: "multu", 0x1A: "div", 0x1B: "divu"}
# MMI (op 0x1C) minor funcs we care about: pipeline-1 multiply/divide + moves
_MMI = {
    0x00: "madd", 0x01: "maddu", 0x10: "mfhi1", 0x11: "mthi1", 0x12: "mflo1",
    0x13: "mtlo1", 0x18: "mult1", 0x19: "multu1", 0x1A: "div1", 0x1B: "divu1",
    0x20: "madd1", 0x21: "maddu1", 0x28: "mmi1", 0x29: "mmi3",
}


def _fields(w):
    return ((w >> 26) & 0x3F, (w >> 21) & 0x1F, (w >> 16) & 0x1F,
            (w >> 11) & 0x1F, (w >> 6) & 0x1F, w & 0x3F)


def _is_r5900_form(w):
    """True for encodings capstone decodes but renders wrongly for the R5900."""
    op, rs, rt, rd, sa, fn = _fields(w)
    if op == 0x00 and fn in _SPECIAL3 and rd:
        return True
    return op == 0x1C and fn in _MMI


def decode_r5900(w):
    """Return (mnemonic, op_str) for a word capstone could not decode, or None."""
    op, rs, rt, rd, sa, fn = _fields(w)
    if op == 0x00 and fn in _SPECIAL3:
        m = _SPECIAL3[fn]
        if rd:                                   # R5900 3-operand form
            return m, f"${R[rd]}, ${R[rs]}, ${R[rt]}"
        return m, f"${R[rs]}, ${R[rt]}"
    if op == 0x1C and fn in _MMI:
        m = _MMI[fn]
        if m in ("mfhi1", "mflo1"):
            return m, f"${R[rd]}"
        if m in ("mthi1", "mtlo1"):
            return m, f"${R[rs]}"
        if m in ("div1", "divu1"):
            return m, f"${R[rs]}, ${R[rt]}"
        if rd:
            return m, f"${R[rd]}, ${R[rs]}, ${R[rt]}"
        return m, f"${R[rs]}, ${R[rt]}"
    return None


class Insn:
    __slots__ = ("address", "mnemonic", "op_str", "bytes", "word", "cs")

    def __init__(self, address, mnemonic, op_str, raw, cs=None):
        self.address, self.mnemonic, self.op_str = address, mnemonic, op_str
        self.bytes, self.cs = raw, cs
        self.word = struct.unpack("<I", raw)[0] if len(raw) == 4 else None

    @property
    def decoded(self):
        return self.mnemonic != ".word"

    def __str__(self):
        return f"0x{self.address:08X}  {self.mnemonic:<9} {self.op_str}"


def disassemble(blob, va):
    """Word-aligned disassembly. Never resyncs off-boundary, unlike skipdata."""
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    out = []
    for off in range(0, len(blob) & ~3, 4):
        raw = blob[off:off + 4]
        addr = va + off
        w = struct.unpack("<I", raw)[0]
        # Take our decoding FIRST for the 3-operand SPECIAL forms: capstone
        # decodes them, but renders rd as a DSP accumulator ($ac2) when on the
        # R5900 it is an ordinary GPR destination. Reading `mult $ac2, $v0, $a1`
        # as "writes ac2" instead of "writes $v0" silently breaks every formula.
        forced = decode_r5900(w) if _is_r5900_form(w) else None
        if forced:
            out.append(Insn(addr, forced[0], forced[1], raw))
            continue
        got = next(md.disasm(raw, addr, 1), None)
        if got is not None:
            out.append(Insn(addr, got.mnemonic, got.op_str, raw, got))
            continue
        alt = decode_r5900(w)
        if alt:
            out.append(Insn(addr, alt[0], alt[1], raw))
        else:
            out.append(Insn(addr, ".word", f"0x{w:08X}", raw))
    return out
