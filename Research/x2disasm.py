#!/usr/bin/env python3
"""
Static MIPS analysis of the Xenosaga II battle overlay — PREP for a PCSX2 session.

This is a RESEARCH tool, not part of the shipped editor. It needs capstone, which
the engine deliberately does not (CI enforces stdlib-only for Editor/ and tests/),
which is why it lives here instead.

What it does, and why:

The battle constants everyone wants — stock cap, boost cost, the break multiplier,
Break expiring at end of turn — are code, not table data. Five independent data
searches came back empty and the x1.5 multiplier does not exist as a float
anywhere in the overlays, so it is integer math: an instruction pattern, not a
value. Reading it needs a disassembler with somewhere to start.

"Somewhere to start" is the whole problem: OV01.OVL is 446 KB of unsymbolised
R5900 code. But the disc-1 pnach pokes several EE addresses that land *inside*
the overlay's loaded range, and one of them is documented as the battle "Event
Slot" including an Accelerated Boost Gauge effect. Those are free anchors. This
tool reconstructs which functions touch them, so a breakpoint session starts at a
named-ish function instead of cold.

    python3 x2disasm.py --iso "../ISO/....(Disc 1).iso" --report out.md
"""
import argparse, os, re, struct, sys, collections

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Editor"))
import x2patch as X                                                  # noqa: E402

try:
    from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
    from capstone.mips import MIPS_OP_IMM, MIPS_OP_REG, MIPS_OP_MEM
except ImportError:                                                  # pragma: no cover
    sys.exit("this research tool needs capstone:  pip install capstone")


def load_overlay(iso, name):
    """(bytes, vaddr, file_off) for an overlay's single PT_LOAD segment."""
    data = iso.extract_file(name)
    if not data or data[:4] != b"\x7fELF":
        raise SystemExit(f"{name}: not an ELF")
    phoff = struct.unpack_from("<I", data, 28)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    for i in range(phnum):
        o = phoff + i * phentsize
        p_type, p_off, p_va, _pa, p_filesz = struct.unpack_from("<IIIII", data, o)
        if p_type == 1:
            return data[p_off:p_off + p_filesz], p_va, p_off
    raise SystemExit(f"{name}: no PT_LOAD segment")


def disassemble(blob, va):
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    # The overlay interleaves data (jump tables, constants) with code, and
    # capstone stops dead at the first word it cannot decode. skipdata makes it
    # emit a placeholder and carry on, which is what we want across 446 KB.
    md.skipdata = True
    return list(md.disasm(blob, va))


def _ops(i):
    """Operands, or () for a skipdata placeholder (which has no detail)."""
    try:
        return i.operands
    except Exception:
        return ()


def find_functions(insns):
    """Function starts = every jal/j target, plus whatever follows a jr $ra's
    delay slot. Crude but reliable enough on compiler output, and it does not
    need symbols."""
    starts, ret_after = set(), set()
    by_addr = {i.address: i for i in insns}
    for n, i in enumerate(insns):
        ops = _ops(i)
        if i.mnemonic in ("jal", "j", "b") and ops:
            op = ops[0]
            if op.type == MIPS_OP_IMM:
                starts.add(op.imm)
        if i.mnemonic == "jr" and i.op_str.strip() == "$ra" and n + 2 < len(insns):
            ret_after.add(insns[n + 2].address)     # skip the delay slot
    lo = insns[0].address
    hi = insns[-1].address
    starts = {a for a in (starts | ret_after) if lo <= a <= hi}
    starts.add(lo)
    return sorted(starts), by_addr


def effective_addrs(insns):
    """Reconstruct lui/ori|addiu|lw|sw address pairs -> {va: [(insn addr, text)]}.

    MIPS builds a 32-bit address as lui hi then a lo-half in the next use of that
    register. Tracking per-register hi halves catches most real references; it
    misses ones split across a branch, which is fine for anchoring."""
    hi = {}
    out = collections.defaultdict(list)
    for i in insns:
        m = i.mnemonic
        ops = _ops(i)
        if not ops:
            continue
        if m == "lui" and len(ops) == 2 and ops[1].type == MIPS_OP_IMM:
            hi[ops[0].reg] = (ops[1].imm & 0xFFFF) << 16
            continue
        # reg-form: addiu $d, $s, imm
        if m in ("addiu", "ori") and len(ops) == 3:
            src = ops[1].reg
            if src in hi and ops[2].type == MIPS_OP_IMM:
                out[(hi[src] + ops[2].imm) & 0xFFFFFFFF].append(
                    (i.address, f"{m} {i.op_str}"))
                hi.pop(ops[0].reg, None)
                continue
        # mem-form: lw $d, off($base) / sw $s, off($base)
        for op in ops:
            if op.type == MIPS_OP_MEM and op.mem.base in hi:
                va = (hi[op.mem.base] + op.mem.disp) & 0xFFFFFFFF
                out[va].append((i.address, f"{m} {i.op_str}"))
        # a load into the register retires that hi half
        if ops[0].type == MIPS_OP_REG and m.startswith(("lw", "lh", "lb")):
            hi.pop(ops[0].reg, None)
    return out


def owning_function(starts, addr):
    import bisect
    k = bisect.bisect_right(starts, addr) - 1
    return starts[k] if k >= 0 else None


# EE addresses the disc-1 pnach pokes that land inside OV01's loaded range.
# These are the anchors: someone already found them empirically, and the Event
# Slot one is documented as including an "Accelerated Boost Gauge" effect.
PNACH_ANCHORS = {
    0xAC2460: "Event Slot Modifier (slot effects incl. 1=BST Accelerated Boost Gauge)",
    0xAC2478: "Experience accumulator (Hella/Super/Ultra Exp codes)",
    0xAC247C: "Skill Points accumulator",
    0xAC2480: "Class Points accumulator",
    0xA9C768: "item-use guard (conditional in Infinite/Use Item codes)",
    0xA9C77C: "item-use count",
    0xA9C78C: "item-use count (Infinite Items)",
    0xA8FFD4: "Max C.Pts write target",
}


def build_report(iso, overlay="OV01.OVL"):
    blob, va, foff = load_overlay(iso, overlay)
    insns = disassemble(blob, va)
    starts, _by = find_functions(insns)
    ea = effective_addrs(insns)

    # jal call graph -> how many distinct callers each function has
    callers = collections.defaultdict(set)
    for i in insns:
        ops = _ops(i)
        if i.mnemonic == "jal" and ops and ops[0].type == MIPS_OP_IMM:
            callers[ops[0].imm].add(owning_function(starts, i.address))

    L = []
    A = L.append
    A(f"# {overlay} static map — PCSX2 prep\n")
    A(f"Generated by `Research/x2disasm.py`. **Nothing here is verified against a "
      f"running game** — it is scaffolding so a breakpoint session starts somewhere "
      f"specific instead of cold.\n")
    A("## Load map\n")
    A(f"| overlay | file offset | VA | size |")
    A(f"|---|---|---|---|")
    A(f"| `{overlay}` | `0x{foff:X}` | **`0x{va:X}`** | `0x{len(blob):X}` ({len(blob):,} bytes) |")
    A(f"\n`va -> file offset` = `va - 0x{va:X} + 0x{foff:X}`. Both overlays are ELFs "
      f"with one RWX PT_LOAD, so PCSX2's EE address == this VA at runtime.\n")
    A(f"- instructions decoded: **{len(insns):,}**")
    A(f"- candidate function starts: **{len(starts):,}**")
    A(f"- distinct reconstructed data addresses: **{len(ea):,}**\n")

    A("## Anchor cross-references\n")
    A("EE addresses the disc-1 pnach pokes that fall inside this overlay. The "
      "functions listed touch them directly — these are the breakpoint targets.\n")
    for addr in sorted(PNACH_ANCHORS):
        note = PNACH_ANCHORS[addr]
        A(f"\n### `0x{addr:08X}` — {note}\n")
        # exact hits, plus anything in the same 0x40-byte neighbourhood
        near = sorted(a for a in ea if abs(a - addr) <= 0x40)
        if not near:
            A("_no reconstructed reference (may be reached via a register-relative "
              "base pointer rather than an absolute `lui`/`lo` pair)._")
            continue
        A("| address | delta | function | instruction |")
        A("|---|---|---|---|")
        for a in near:
            for site, text in ea[a][:4]:
                fn = owning_function(starts, site)
                A(f"| `0x{a:08X}` | {a - addr:+d} | `0x{fn:08X}` | `{text}` |")
    return "\n".join(L), dict(insns=len(insns), funcs=len(starts), addrs=len(ea))
