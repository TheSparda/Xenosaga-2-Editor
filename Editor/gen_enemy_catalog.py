#!/usr/bin/env python3
"""Extend Editor/x2_enemies.json with every field the editor can write.

The catalog started as the eleven stat/reward numbers that could be cross-checked
against a printed guide. Everything verified since — break sequences, breakable
zones, damage affinities, status resistances, item drops — went into the editor
without going into the catalog, and "Compare to retail" only reports fields the
catalog knows. So the editor could change a boss from a 4-hit break to a 1-hit
break and then tell you the disc matched retail.

This rebuilds the catalog from the discs themselves. The retail claim rests on
two checks, and it refuses to write if either fails:

  * both discs are read, and every field of every record must agree — the two
    pressings carry independent copies of these tables, so a disagreement means
    one image is modified (or the offsets are wrong), not that retail is ambiguous
  * the eleven guide-verified numbers already in the catalog must still match
    what the discs hold — that is the tie back to the external source, and it is
    the check that catches a bad offset silently producing plausible bytes

Usage:  python3 Editor/gen_enemy_catalog.py ISO/disc1.iso ISO/disc2.iso [--check]
        --check  report what would change, write nothing
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import x2fields as F      # noqa: E402
import x2patch as P       # noqa: E402

OUT = os.path.join(HERE, "x2_enemies.json")


def extract(path):
    """{index: {label: value}} for every editable field on one disc."""
    with P.Iso(path, "rb") as iso:
        P.require_version(iso)
        return {i: P.read_enemy(iso, i) for i in range(F.ENEMY_COUNT)}


def cross_check(a, b):
    """Every field of every record must read the same on both discs."""
    bad = []
    for i in sorted(a):
        for label, value in a[i].items():
            if b[i][label] != value:
                bad.append(f"  record {i} {label}: disc1={value} disc2={b[i][label]}")
    return bad


def guide_check(discs, catalog):
    """The committed guide-verified numbers must survive untouched."""
    bad = []
    for i, rec in sorted(catalog.items()):
        for label, key in F.ENEMY_CATALOG_KEY.items():
            if key in rec and rec[key] != discs[i].get(label):
                bad.append(f"  record {i} ({rec.get('name','?')}) {key}: "
                           f"catalog={rec[key]} disc={discs[i].get(label)}")
    return bad


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    check = "--check" in argv[1:]
    if len(args) != 2:
        print(__doc__.strip())
        return 2

    d1, d2 = extract(args[0]), extract(args[1])
    bad = cross_check(d1, d2)
    if bad:
        print(f"discs disagree on {len(bad)} field(s) — refusing to write:")
        print("\n".join(bad[:20]))
        return 1

    catalog = {int(k): v for k, v in json.load(open(OUT)).items()}
    bad = guide_check(d1, catalog)
    if bad:
        print(f"{len(bad)} guide-verified value(s) no longer match the disc "
              f"— refusing to write:")
        print("\n".join(bad[:20]))
        return 1

    added = set()
    for i, rec in catalog.items():
        for label, key in F.ENEMY_CATALOG_KEY.items():
            if key not in rec and label in d1[i]:
                rec[key] = d1[i][label]
                added.add(key)

    text = json.dumps({str(i): catalog[i] for i in sorted(catalog)},
                      indent=1, ensure_ascii=False) + "\n"
    if check:
        stale = text != open(OUT, encoding="utf-8").read()
        print("stale — rerun without --check" if stale else "up to date")
        return 1 if stale else 0

    open(OUT, "w", encoding="utf-8").write(text)
    print(f"both discs agree on all {len(d1)} records; "
          f"{len(F.ENEMY_CATALOG_KEY)} fields per record "
          f"({len(added)} newly added: {', '.join(sorted(added)) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
