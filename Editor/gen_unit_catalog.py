#!/usr/bin/env python3
"""Generate Editor/x2_units.json — the retail player-unit baseline.

Same contract as gen_enemy_catalog.py: values come from the discs themselves,
so the write is gated on both discs agreeing field-for-field. The stronger
external check (save records byte-identical to these values at join time) is
recorded in x2fields' UNIT block and in the notes.

Usage:  python3 Editor/gen_unit_catalog.py DISC1.iso DISC2.iso [--check]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import x2fields as F      # noqa: E402
import x2patch as P       # noqa: E402

OUT = os.path.join(HERE, "x2_units.json")


def extract(path):
    with P.Iso(path, "rb") as iso:
        P.require_version(iso)
        out = {}
        for i in range(F.UNIT_COUNT):
            u = P.read_unit(iso, i)
            u["name"] = P.unit_name(iso, i)
            out[i] = u
        return out


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    check = "--check" in argv[1:]
    if len(args) != 2:
        print(__doc__.strip())
        return 2
    d1, d2 = extract(args[0]), extract(args[1])
    bad = [f"  unit {i} {k}: disc1={d1[i][k]} disc2={d2[i][k]}"
           for i in d1 for k in d1[i] if d1[i][k] != d2[i][k]]
    if bad:
        print(f"discs disagree on {len(bad)} field(s) — refusing to write:")
        print("\n".join(bad))
        return 1
    text = json.dumps({str(i): d1[i] for i in sorted(d1)},
                      indent=1, ensure_ascii=False) + "\n"
    if check:
        stale = not os.path.exists(OUT) or text != open(OUT, encoding="utf-8").read()
        print("stale — rerun without --check" if stale else "up to date")
        return 1 if stale else 0
    open(OUT, "w", encoding="utf-8").write(text)
    print(f"both discs agree on all {len(d1)} unit records; wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
