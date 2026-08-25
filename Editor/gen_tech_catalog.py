#!/usr/bin/env python3
"""Append the dual-tech name/description entries to Editor/x2_skills.json.

The 174-entry skill catalog covers ethers and doubles only; the techs keep their
own string pool at TECH_NAME_POOL. Rather than build a parallel catalog and a
parallel set of machinery to read it, the techs are appended to the same file at
TECH_TEXT0, so SKILL_BLOCKS can point at them and every existing consumer — the
CLI, the web Skills tab, the retail baseline — works unchanged.

Entries are laid out exactly like the skill pool: NAME \0 "TARGET/TYPE\\nDESC" \0.

Usage:  python3 Editor/gen_tech_catalog.py DISC1.iso [--check]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import x2fields as F      # noqa: E402
import x2patch as P       # noqa: E402

OUT = os.path.join(HERE, "x2_skills.json")


def read_pool(iso, base, count):
    """[(name, meta), ...] from `count` NUL-terminated pairs at `base`."""
    raw = iso.read(base, 0x1800)
    parts = raw.split(b"\x00")
    out, k = [], 0
    while k + 1 < len(parts) and len(out) < count:
        name = parts[k].decode("latin1")
        meta = parts[k + 1].decode("latin1")
        out.append((name, meta))
        k += 2
    if len(out) != count:
        raise SystemExit(f"read {len(out)} pool entries, expected {count}")
    return out


# The menu-string pool groups names in ITS order (all characters, then E.S.
# attacks, then E.S. specials, then duals, then KOS-MOS specials), padded to 7
# per character with explicit "<name> reserve" placeholders. Each entry below is
# (pool group size, names to take) mapped to the block that uses them, in the
# record order the HardType cross-check verified 71/71.
POOL_PLAN = [   # (block label, tokens in this pool group, tokens that are real)
    ("chaos tech",      7, 7),
    ("KOS-MOS tech",    7, 7),
    ("Shion tech",      7, 3),
    ("Jin tech",        7, 7),
    ("Ziggy tech",      7, 6),
    ("MOMO tech",       7, 3),
    ("Jr. tech",        7, 7),
    ("Dinah attack",    3, 3),
    ("Dinah special",   7, 7),
    ("Zebulun attack",  3, 3),
    ("Zebulun special", 7, 7),
    ("Asher attack",    3, 3),
    ("Asher special",   7, 7),
    ("dual tech",      16, 0),      # named from their own pool, skipped here
    ("KOS-MOS special", 4, 4),
]


def read_single_pool(iso, disc):
    """{block label: [names]} from the menu-string pool."""
    raw = iso.read(F.SINGLE_NAME_POOL[disc], 0x600)
    toks = [t.decode("latin1") for t in raw.split(b"\x00")]
    out, k = {}, 0
    for label, group, real in POOL_PLAN:
        out[label] = toks[k:k + real]
        k += group
    return out


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    check = "--check" in argv[1:]
    if len(args) != 1:
        print(__doc__.strip())
        return 2

    with P.Iso(args[0]) as iso:
        serial, disc = P.require_version(iso)
        block = next(b for b in F.skill_blocks(disc) if b[0] == "dual tech")
        _label, dual_base, dual_count, dual_text0 = block
        pool = read_pool(iso, F.TECH_NAME_POOL[disc], dual_count)
        recs = [P.read_skill_at(iso, dual_base + i * F.SKILL_STRIDE)
                for i in range(dual_count)]

    with P.Iso(args[0]) as iso:
        singles = read_single_pool(iso, disc)
        by_label = {}
        for lbl, b, count, text0 in F.skill_blocks(disc):
            if lbl in ("ether", "double", "dual tech"):
                continue
            names = singles.get(lbl, [])
            if len(names) != count:
                raise SystemExit(f"{lbl}: pool gave {len(names)} names for "
                                 f"{count} records — refusing to write")
            by_label[lbl] = [(text0 + k, names[k],
                              P.read_skill_at(iso, b + k * F.SKILL_STRIDE))
                             for k in range(count)]

    cat = json.load(open(OUT, encoding="utf-8"))
    for lbl, rows in by_label.items():
        for idx, name, rec in rows:
            cat[str(idx)] = {
                "desc": "", "ep": rec["EP"] or None, "name": name,
                "placeholder": False, "tags": [lbl], "target": "",
                "numeric": {"ep": rec["EP"], "element": rec["Element"],
                            "power": rec["Power"], "effPct": rec["EffPct"],
                            "effMask": rec["EffMask"], "target": rec["Target"]},
            }
    for i, ((name, meta), rec) in enumerate(zip(pool, recs)):
        target, _, desc = meta.partition("\\n")
        cat[str(dual_text0 + i)] = {
            "desc": desc,
            "ep": rec["EP"] or None,
            "name": name,
            "placeholder": False,
            "tags": ["dual tech"],
            "target": target,
            # the retail baseline the editor compares against
            "numeric": {"ep": rec["EP"], "element": rec["Element"],
                        "power": rec["Power"], "effPct": rec["EffPct"],
                        "effMask": rec["EffMask"], "target": rec["Target"]},
        }
    text = json.dumps({k: cat[k] for k in sorted(cat, key=int)},
                      indent=1, ensure_ascii=False) + "\n"
    if check:
        stale = text != open(OUT, encoding="utf-8").read()
        print("stale — rerun without --check" if stale else "up to date")
        return 1 if stale else 0
    open(OUT, "w", encoding="utf-8").write(text)
    nsingles = sum(len(v) for v in by_label.values())
    print(f"{serial}: appended {dual_count} dual techs at {dual_text0}.. and "
          f"{nsingles} techs/attacks/specials at {F.TECH_TEXT0_SINGLE}..")
    for i, (name, _m) in enumerate(pool):
        print(f"   {dual_text0 + i}  {name:<22} power {recs[i]['Power']:>4}  "
              f"element {F.skill_element_text(recs[i]['Element'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
