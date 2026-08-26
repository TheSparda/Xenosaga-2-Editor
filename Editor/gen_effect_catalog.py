#!/usr/bin/env python3
"""Generate the retail baselines for the passive, E.S. gear and skill-cost tables.

Same contract as gen_enemy_catalog.py and gen_unit_catalog.py: the values come
from the discs themselves and the write is gated on BOTH discs agreeing record
for record, so a bad read on one image cannot become the baseline everything
else is measured against.

Why this exists: "Compare to retail" could only answer for the enemy, unit and
skill tables, because those are the only ones with a shipped retail baseline. It
said nothing about passives, E.S. accessory effects or skill purchase costs —
not "these match", nothing at all — which reads as "there is nothing to report".
The tables have been editable since v1.10.0/v1.11.0, so the gap was real: you
could change an equip skill's magnitude and the editor could not tell you that
you had.

Writes:
  x2_skills.json     numeric.{kind,param,statMask} on catalog entries 110..173
  x2_es_equip.json   numeric.{kind,param,statMask} per named E.S. accessory
  x2_costs.json      the 112 purchase-cost records (new file)

Usage:  python3 Editor/gen_effect_catalog.py DISC1.iso DISC2.iso [--check]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import x2fields as F      # noqa: E402
import x2patch as P       # noqa: E402

SKILLS = os.path.join(HERE, "x2_skills.json")
GEAR = os.path.join(HERE, "x2_es_equip.json")
COSTS = os.path.join(HERE, "x2_costs.json")


def extract(path):
    """{'passive': {...}, 'gear': {...}, 'cost': {...}} read off one disc."""
    with P.Iso(path) as iso:
        P.require_version(iso)
        return {
            "passive": {i: P.read_passive(iso, i) for i in F.passive_indices()},
            "gear": {k: P.read_gear(iso, k) for k in range(F.GEAR_COUNT)},
            "cost": {k: P.read_cost(iso, k) for k in range(F.SKILL_COST_COUNT)},
        }


def disagreements(a, b):
    """[(table, index, field, disc1, disc2), ...] — empty when the discs agree."""
    out = []
    for table in a:
        for i in a[table]:
            for k, v in a[table][i].items():
                w = b[table].get(i, {}).get(k)
                if v != w:
                    out.append((table, i, k, v, w))
    return out


def render(one):
    """The three files' contents as {path: text}, from one disc's reading."""
    skills = json.load(open(SKILLS, encoding="utf-8"))
    for i, rec in one["passive"].items():
        e = skills.get(str(i))
        if e is None:
            continue
        num = dict(e.get("numeric") or {})
        num.update(kind=rec["Kind"], param=rec["Param"], statMask=rec["StatMask"])
        e["numeric"] = num

    gear = json.load(open(GEAR, encoding="utf-8"))
    for k, rec in one["gear"].items():
        es = F.GEAR_ES_ID.get(k)
        # placeholder records (予備) have no catalog id — nothing to hang a
        # baseline off, and inventing an entry for them would put spare slots in
        # the Gear pane's comparison
        if es is None or str(es) not in gear:
            continue
        gear[str(es)]["numeric"] = {"kind": rec["Kind"], "param": rec["Param"],
                                    "statMask": rec["StatMask"]}

    costs = {str(k): {"cost": r["Cost"], "type": r["Type"], "id": r["Id"],
                      "slot": r["Slot"]}
             for k, r in one["cost"].items()}

    # Each file keeps the indentation it already shipped with. Reformatting a
    # catalog while adding one key to it turns a 31-line diff into a 400-line one
    # and buries the actual change.
    dump = lambda o, n: json.dumps(o, indent=n, ensure_ascii=False) + "\n"
    return {
        SKILLS: dump({k: skills[k] for k in sorted(skills, key=int)}, 1),
        GEAR: dump({k: gear[k] for k in sorted(gear, key=int)}, 0),
        COSTS: dump({k: costs[k] for k in sorted(costs, key=int)}, 0),
    }


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    check = "--check" in argv[1:]
    if check and not args:
        # CI has no discs; the committed files are the source of truth there
        missing = [p for p in (SKILLS, GEAR, COSTS) if not os.path.exists(p)]
        if missing:
            print("missing: " + ", ".join(os.path.basename(p) for p in missing))
            return 1
        cat = json.load(open(SKILLS, encoding="utf-8"))
        blank = [i for i in F.passive_indices()
                 if not (cat.get(str(i), {}).get("numeric") or {}).get("kind") is not None]
        print(f"x2_costs.json present; {F.PASSIVE_COUNT - len(blank)} of "
              f"{F.PASSIVE_COUNT} passives carry a retail baseline")
        return 0
    if len(args) != 2:
        print(__doc__.strip())
        return 2

    one, two = extract(args[0]), extract(args[1])
    bad = disagreements(one, two)
    if bad:
        print(f"the two discs disagree on {len(bad)} value(s) — refusing to write:")
        for row in bad[:10]:
            print("   {} {} {}: disc1={} disc2={}".format(*row))
        return 1

    out = render(one)
    if check:
        stale = [p for p, text in out.items()
                 if text != open(p, encoding="utf-8").read()]
        for p in stale:
            print(f"{os.path.basename(p)} is stale — rerun without --check")
        if not stale:
            print("passive / gear / cost baselines are current")
        return 1 if stale else 0
    for p, text in out.items():
        open(p, "w", encoding="utf-8").write(text)
    coded = sum(1 for r in one["passive"].values() if r["Kind"] == 0)
    print(f"wrote {len(one['passive'])} passive, "
          f"{sum(1 for k in one['gear'] if F.GEAR_ES_ID.get(k) is not None)} gear "
          f"and {len(one['cost'])} cost baselines (both discs agree)")
    print(f"  {coded} passives read all-zero — behaviour is battle code, not table "
          f"data, and the editor already says so")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
