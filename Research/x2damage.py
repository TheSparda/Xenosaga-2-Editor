#!/usr/bin/env python3
"""
Locate and dump the battle damage routine in OV01.OVL.

Research tool (needs capstone) -- not part of the shipped editor.

The damage formula is code, not table data, so no byte scan can find it. This
walks in from three anchors that a damage routine cannot avoid touching:

  * the 9999 / 99999 clamp pair (`slti ..., 0x2710` / `lui 1; ori 0x869f`),
  * every `div` by an immediate 100 -- the game does percent math with a real
    `div`, not a magic-number reciprocal, and there are only ~50 divides in the
    whole 446 KB overlay,
  * the four stat getters, identified by the offsets they read out of the
    runtime stat block (STR +0x08, VIT +0x0A, EATK +0x0C, EDEF +0x0E, pinned
    independently by the disc-1 pnach's per-character Max Str/Vit/Eatk/Edef
    addresses).

    python3 x2damage.py --iso "../ISO/....(Disc 1).iso"
    python3 x2damage.py --iso ... --func 0xA8C778     # dump one function
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Editor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x2patch as X                                                  # noqa: E402
import x2r5900                                                       # noqa: E402

# Verified: the routine at DAMAGE_FN returns the final damage number in $v0.
DAMAGE_FN = 0xA8C778

# Helpers it calls, named from what they read. The stat offsets are the proof --
# see the pnach cross-check in DAMAGE.md.
NAMED = {
    0xA8BDD8: "atk_phys      (STR x4, +/- buffs)",
    0xA8BFD0: "def_phys      (VIT x3, +/- buffs)",
    0xA8BED0: "atk_ether     (EATK x5, +/- buffs)",
    0xA8C0D0: "def_ether     (EDEF x4, +/- buffs)",
    0xA8C1C8: "attack_power  (u8 at +0x0E of the action descriptor)",
    0xA80D88: "rand_mod      (rand() % (n+1)  ->  0..n inclusive)",
    0xA8E0C8: "has_status    (unit, class, mask) -> bool",
    0xA8EF80: "has_flag      (unit, flag_id) -> bool",
    0xA91A00: "event_slot    -> current battle slot id (4 = ETR)",
    0xA9B3B8: "action_desc   (action) -> descriptor ptr",
}

# Offsets into the runtime stat block at `unit + 0x144`.
STAT_BLOCK = {
    0x02: "HP (u32, UNALIGNED -- read via lwl 5 / lwr 2)",
    0x08: "STR", 0x0A: "VIT", 0x0C: "EATK", 0x0E: "EDEF",
    0x10: "DEX", 0x11: "EVA", 0x12: "AGL",
    0x62: "unit id (11..15 = E.S., which raises the damage/HP cap to 99999)",
    0x70: "battle state (1 = Break, 2..3 = Air/Down)",
    0xF4: "elemental chain counter",
}


def load_overlay(iso, name="OV01.OVL"):
    """(instructions, va, file_offset) for the overlay's single PT_LOAD."""
    import struct
    data = iso.extract_file(name)
    if not data or data[:4] != b"\x7fELF":
        raise SystemExit(f"{name}: not an ELF")
    phoff = struct.unpack_from("<I", data, 28)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    for i in range(phnum):
        o = phoff + i * phentsize
        p_type, p_off, p_va, _pa, p_filesz = struct.unpack_from("<IIIII", data, o)
        if p_type == 1:
            blob = data[p_off:p_off + p_filesz]
            return x2r5900.disassemble(blob, p_va), p_va, p_off
    raise SystemExit(f"{name}: no PT_LOAD segment")


def function_starts(insns):
    """jal targets, plus every `addiu $sp, $sp, -N` prologue."""
    starts = set()
    for i in insns:
        if i.mnemonic == "jal":
            try:
                starts.add(int(i.op_str, 16))
            except ValueError:
                pass
        elif i.mnemonic == "addiu" and i.op_str.startswith("$sp, $sp, -"):
            starts.add(i.address)
    return sorted(starts)


def bounds(starts, insns, addr):
    lo = max((s for s in starts if s <= addr), default=insns[0].address)
    hi = min((s for s in starts if s > addr), default=insns[-1].address + 4)
    return lo, hi


def annotate(ins, statregs):
    """Right-hand comment for an instruction, or ''.

    Stat-block offsets are only meaningful off a register that was actually
    loaded from `unit + 0x144`; `statregs` is that (small) taint set. Without
    it every `lw $a0, 8($s6)` in the function gets mislabelled "STR".
    """
    if ins.mnemonic == "jal":
        try:
            t = int(ins.op_str, 16)
        except ValueError:
            return ""
        return f"   ; {NAMED[t]}" if t in NAMED else ""
    if ins.mnemonic in ("lb", "lbu", "lh", "lhu", "lw") and "(" in ins.op_str:
        disp, _, rest = ins.op_str.partition(",")[2].strip().partition("(")
        base = rest.rstrip(")")
        try:
            off = int(disp, 16) if disp.startswith(("0x", "-0x")) else int(disp or "0")
        except ValueError:
            return ""
        if off == 0x144:
            return "   ; -> runtime stat block"
        if off in STAT_BLOCK and base in statregs:
            return f"   ; {STAT_BLOCK[off]}"
    return ""


def track_statregs(insns, by_addr, lo, hi):
    """Registers holding a `unit + 0x144` stat-block pointer, per address.

    Linear scan, no CFG: a register is tainted by `lw rD, 0x144(...)` and
    cleared when anything else writes it. Good enough for straight-line
    formula code, and it fails closed (drops a label) rather than open.
    """
    live, out = set(), {}
    for a in range(lo, hi, 4):
        i = by_addr.get(a)
        if i is None:
            continue
        out[a] = frozenset(live)
        dst = i.op_str.split(",")[0].strip() if i.op_str.startswith("$") else None
        if dst:
            if i.mnemonic == "lw" and ", 0x144(" in i.op_str:
                live.add(dst)
            else:
                live.discard(dst)
    return out


def dump(insns, by_addr, lo, hi, out):
    out.append(f"; ===== 0x{lo:08X} .. 0x{hi:08X}  ({(hi - lo) // 4} instructions)")
    statregs = track_statregs(insns, by_addr, lo, hi)
    for a in range(lo, hi, 4):
        i = by_addr.get(a)
        if i is None:
            continue
        out.append(f"0x{a:08X}  {i.mnemonic:<9} {i.op_str}{annotate(i, statregs.get(a, ()))}")


def survey(insns, by_addr, starts, out):
    """The three anchors, so the derivation can be re-run from scratch."""
    out.append("; ---- anchor: divides by an immediate 100 ----")
    consts = {i.address for i in insns
              if i.mnemonic == "addiu" and "$zero" in i.op_str and i.op_str.endswith("0x64")}
    for i in insns:
        if i.mnemonic in ("div", "divu", "div1", "divu1"):
            if any(abs(i.address - c) <= 0x30 for c in consts):
                fn = bounds(starts, insns, i.address)[0]
                out.append(f";   0x{i.address:08X}  {i.mnemonic} {i.op_str}   in 0x{fn:08X}")

    out.append("; ---- anchor: 9999 / 99999 clamps ----")
    for i in insns:
        if i.mnemonic in ("addiu", "slti") and i.op_str.endswith(("0x270f", "0x2710")):
            fn = bounds(starts, insns, i.address)[0]
            out.append(f";   0x{i.address:08X}  {i.mnemonic} {i.op_str}   in 0x{fn:08X}")

    out.append("; ---- anchor: callers of the damage routine ----")
    callers = collections.Counter()
    for i in insns:
        if i.mnemonic == "jal" and i.op_str == hex(DAMAGE_FN):
            callers[bounds(starts, insns, i.address)[0]] += 1
    for fn, n in callers.most_common():
        out.append(f";   0x{fn:08X}  x{n}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--iso", required=True)
    ap.add_argument("--overlay", default="OV01.OVL")
    ap.add_argument("--func", help="dump one function by VA (hex), default = damage routine")
    ap.add_argument("--survey", action="store_true", help="print the anchor survey too")
    ap.add_argument("-o", "--out", help="write to a file instead of stdout")
    a = ap.parse_args()

    with X.Iso(a.iso) as iso:
        insns, va, foff = load_overlay(iso, a.overlay)
    by_addr = {i.address: i for i in insns}
    starts = function_starts(insns)
    undec = sum(1 for i in insns if not i.decoded)

    out = [f"; {a.overlay}  VA 0x{va:08X}  file offset 0x{foff:X}",
           f"; {len(insns):,} words, {undec:,} undecoded ({undec * 100.0 / len(insns):.2f}%), "
           f"{len(starts):,} function starts",
           f"; va -> file offset:  va - 0x{va:X} + 0x{foff:X}", ""]

    if a.survey:
        survey(insns, by_addr, starts, out)
        out.append("")

    target = int(a.func, 16) if a.func else DAMAGE_FN
    lo, hi = bounds(starts, insns, target)
    dump(insns, by_addr, lo, hi, out)

    text = "\n".join(out) + "\n"
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text)
        print(f"wrote {a.out} ({len(out)} lines)")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
