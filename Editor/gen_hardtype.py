#!/usr/bin/env python3
"""Generate web/hardtype.json from the HardType mod's own PPF patches.

The web editor ships two one-click preset buttons that stage the HardType
mod's table edits without the user supplying the .ppf. This script derives
that data: it parses the mod's four patches (Normal/Hard difficulty x two
discs, in the gitignored `Hard Mode Mod/` folder), keeps only the records
that land wholly inside a buffer the editor maps (enemy stats, rewards,
skill blocks, unit table, and the skill text span — the same extents web
bufferMap() stages into), and writes them as disc-1-layout records.

Included since the passive/equip table was located: the mod's skill renames
and rewritten descriptions, and its passive effect changes (Ice Coat -> STR+4
and friends), both of which live inside the skill text span.

What is still EXCLUDED, and why — 13 records, 64 bytes:
  - 9 duplicate copies of a renamed skill's battle caption ("$zoom13;Miracle
    Star") sitting far out in the disc, outside every located table. Applying
    them means writing at raw offsets nothing has confirmed, which is exactly
    what the notes forbid. Consequence: a renamed skill keeps its retail name
    in battle captions.
  - 4 bytes in an unidentified low table around 0x35EA9D (two id values
    swapped). Not mapped, so not written.

Per-disc nuance: the mod's D1 and D2 patches are the usual -0x800 shift of
each other EXCEPT one byte per variant where the two discs get different
values. The editor's staged buffers are disc-agnostic (one set, synced to
both discs), so the D1 value wins and the divergence is recorded in
`discNotes` for the UI to say so.

Usage:  python3 Editor/gen_hardtype.py [--check]
        --check  exit non-zero if the committed file is stale; exits 0 with a
                 notice when the mod PPFs are not present (they are gitignored,
                 so CI machines don't have them)
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import x2fields as F  # noqa: E402

OUT = os.path.join(HERE, "..", "web", "hardtype.json")
MOD_DIR = os.path.join(HERE, "..", "Hard Mode Mod", "XS2HTv3.91945")
VARIANTS = (
    ("normal", "Normal Difficulty Version", "Normal difficulty"),
    ("hard", "Hard Difficulty Version", "Hard difficulty"),
)
SOURCE = "Xenosaga Episode II HardType v3.9 by Landon Ray (1945)"


def parse_ppf(path):
    with open(path, "rb") as fh:
        b = fh.read()
    if b[:5] != b"PPF30":
        raise SystemExit(f"{path}: not a PPF3.0 patch")
    blockcheck, undo = b[57], b[58]
    p = 60 + (1024 if blockcheck else 0)
    recs = []
    while p + 9 <= len(b):
        off = struct.unpack_from("<Q", b, p)[0]
        p += 8
        n = b[p]
        p += 1
        recs.append((off, bytes(b[p:p + n])))
        p += n
        if undo:
            p += n
    return recs


def extents():
    """Disc-1 extents of every buffer the web editor stages into (bufferMap).

    `text` is last for the same reason bufferMap checks it last: the skill text
    span is a bounding box wide enough to swallow the enemy and skill tables,
    so a record is only attributed to it once every precise table has declined.
    It carries the mod's skill renames and descriptions AND — because the
    passive/equip table sits inside that span — its passive effect edits.
    """
    et = F.ENEMY_TABLES[1]
    return {
        "stats": (et["stats"], 125 * 0x5C),
        "rewards": (et["rewards"], 125 * F.REWARD_STRIDE),
        "skills": (F.skill_base(1), F.skill_span(1)),
        "units": (F.UNIT_TABLES[1], F.UNIT_COUNT * 92),
        "text": F.skill_text_span(1),
    }


def _region_of(off, n, ext):
    for name, (a, s) in ext.items():
        if off >= a and off + n <= a + s:
            return name
    return None


def _describe(off, ext):
    """Human label for a divergent byte — enemy+field when it's in stats."""
    region = _region_of(off, 1, ext)
    if region == "stats":
        rel = off - ext["stats"][0]
        i, fo = divmod(rel, 0x5C)
        try:
            with open(os.path.join(HERE, "x2_enemies.json")) as fh:
                name = json.load(fh)[str(i)]["name"]
        except Exception:
            name = f"enemy {i}"
        field = ""
        for f in getattr(F, "ENEMY_FIELDS", ()):
            if isinstance(f[1], int) and f[1] == fo:
                field = f" {f[0]}"
                break
        return f"{name}{field} (record {i}, +0x{fo:X})"
    return f"{region or 'unmapped'} region, 0x{off:X}"


def build():
    ext = extents()
    variants = {}
    for key, folder, label in VARIANTS:
        d1 = parse_ppf(os.path.join(MOD_DIR, folder, "XS2HTv3.9D1.ppf"))
        d2 = {off: data for off, data in
              parse_ppf(os.path.join(MOD_DIR, folder, "XS2HTv3.9D2.ppf"))}
        records, notes = [], []
        skipped = skipped_bytes = staged_bytes = 0
        for off, data in d1:
            if _region_of(off, len(data), ext) is None:
                skipped += 1
                skipped_bytes += len(data)
                continue
            records.append([off, data.hex()])
            staged_bytes += len(data)
            other = d2.get(off - 0x800)
            if other is None:
                notes.append(f"Disc 2's patch has no counterpart for 0x{off:X} "
                             f"— the disc-1 value is staged on both discs.")
            elif other != data:
                notes.append(
                    f"The mod gives {_describe(off, ext)} a different value on "
                    f"disc 2 ({other.hex()} vs {data.hex()}); the staged buffers "
                    f"are shared across discs, so the disc-1 value wins.")
        variants[key] = {
            "label": label,
            "records": records,
            "recordCount": len(records),
            "byteCount": staged_bytes,
            "skippedRecords": skipped,
            "skippedBytes": skipped_bytes,
            "discNotes": notes,
        }
    return {
        "_generated": "by Editor/gen_hardtype.py — do not edit by hand",
        "source": SOURCE,
        "layout": 1,
        "variants": variants,
    }


def render():
    return json.dumps(build(), indent=1) + "\n"


def main(argv):
    check = "--check" in argv
    if not os.path.isdir(MOD_DIR):
        if check:
            print("gen_hardtype: mod PPFs not present (gitignored) — skipping "
                  "freshness check")
            return 0
        raise SystemExit(f"mod folder not found: {MOD_DIR}")
    text = render()
    if check:
        try:
            with open(OUT) as fh:
                committed = fh.read()
        except FileNotFoundError:
            print("web/hardtype.json is missing — run Editor/gen_hardtype.py")
            return 1
        if committed != text:
            print("web/hardtype.json is stale — run Editor/gen_hardtype.py")
            return 1
        print("web/hardtype.json is current")
        return 0
    with open(OUT, "w") as f:
        f.write(text)
    tot = sum(v["recordCount"] for v in build()["variants"].values())
    print(f"wrote {OUT} ({tot} records across "
          f"{len(VARIANTS)} variants)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
