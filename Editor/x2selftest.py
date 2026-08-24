#!/usr/bin/env python3
"""
Self-test for the ISO engine — runs without any game data.

Builds a *synthetic* disc image (sparse, ~34 MB) that reproduces only the
structures we've verified on the retail disc: the ISO9660 volume id, the
SYSTEM.CNF serial, the enemy stat table at its known base, and the parallel
rewards table — populated from the verified catalog in x2_enemies.json. Then it
exercises the table locator, the rebalance planner/writer and the weak-zone
scanner against it, including a *planted* zone column so the scanner has a known
right answer to find.

This proves the code paths, not the game facts: the offsets themselves are
verified in Xenosaga2_ISO_offsets.md against the real discs.

    python3 x2selftest.py            # build in a temp dir, test, clean up
    python3 x2selftest.py --keep DIR # leave the fixture behind for poking at
"""
import argparse, os, struct, sys, tempfile, shutil
from pathlib import Path

import x2fields as F
import x2patch as X

# The offset we plant the fake zone data at (an undecoded byte in the record)
PLANT_OFF = 0x2A
# A parallel table elsewhere on the disc, to exercise the region sweep fallback
PLANT_TABLE_BASE, PLANT_TABLE_STRIDE, PLANT_TABLE_FIELD = 0x1FF0000, 0x10, 3

ZONE_STRINGS = ["BB", "CB", "CC", "AB", "BCBB", "AC", "BA"]
SYMBOL = {"A": 1, "B": 2, "C": 3}

# Break sequences written into the fixture at the VERIFIED offsets (+0x54..+0x57,
# one-hot, 0 = end) with a matching hittable-zone mask at +0x4C. Deliberately
# includes an empty one, so "cannot be broken" is covered too.
BREAK_SEQS = ["BB", "CB", "CC", "CBB", "BCBC", "AA", "", "CBAA", "AB"]

def break_for(i):
    """Deterministic per-record break sequence for the fixture."""
    return BREAK_SEQS[i % len(BREAK_SEQS)]


def zones_for(i):
    """Deterministic per-record weak-zone string for the fixture."""
    return ZONE_STRINGS[i % len(ZONE_STRINGS)]


def encode_zones(s):
    """Pack up to four zone symbols into one byte (2 bits each, 0 = end)."""
    v = 0
    for n, c in enumerate(s[:4]):
        v |= SYMBOL[c] << (2 * n)
    return v


SERIAL_LINE = {1: b"SLUS_208.92", 2: b"SLUS_211.33"}


def build_fixture(path, disc=1):
    """Write a synthetic disc holding the structures the engine reads.

    `disc` picks which image to imitate. Both retail discs carry the enemy
    tables — disc 2's copy sits 0x800 lower — so the fixture takes its bases from
    F.enemy_tables(disc) rather than hardcoding disc 1's."""
    cat = F.enemy_catalog()
    t = F.enemy_tables(disc)
    end = t["rewards"] + F.ENEMY_COUNT * F.REWARD_STRIDE + 0x800
    with open(path, "wb") as f:
        f.truncate(end)                                   # sparse — costs no real disk
        f.seek(16 * 2048 + 40)
        f.write(b"XENOSAGA_II".ljust(32, b" "))           # PVD volume id
        f.seek(0x9000)
        f.write(b"BOOT2 = cdrom0:\\" + SERIAL_LINE[disc] + b";1\r\n")

        # planted parallel table (row = record index), for the region-sweep test
        for i in range(F.ENEMY_COUNT):
            f.seek(PLANT_TABLE_BASE + i * PLANT_TABLE_STRIDE + PLANT_TABLE_FIELD)
            f.write(bytes((encode_zones(zones_for(i)),)))

        for i in range(F.ENEMY_COUNT):
            rec = bytearray(F.ENEMY_STRIDE)
            r = cat[i]
            rec[0x04:0x0C] = bytes([0x64] * 8)            # element affinities
            rec[PLANT_OFF] = encode_zones(zones_for(i))   # <- the answer the scanner must find
            # +0x3A carries the real per-record value, not a flat 99 — eleven
            # retail records differ, and a fixture that flattens them hides any
            # retail-value check that wrongly compares the raw 17-byte run.
            struct.pack_into("<IH", rec, 0x36, r["hp"], F.enemy_unk3a(i))
            struct.pack_into("<HHHH", rec, 0x3C, r["str"], r["vit"], r["eatk"], r["edef"])
            rec[0x44], rec[0x45], rec[0x46] = r["dex"], r["eva"], r["agl"]
            struct.pack_into("<H", rec, 0x52, r["id"])
            # break sequence + a zone mask that covers exactly the zones it uses
            slots = F.encode_break_seq(break_for(i))
            for n, v in enumerate(slots):
                rec[F.BREAK_SEQ_OFF + n] = v
            rec[F.ENEMY_ZONE_MASK_OFF] = slots[0] | slots[1] | slots[2] | slots[3]
            f.seek(t["stats"] + i * F.ENEMY_STRIDE)
            f.write(rec)

            row = bytearray(F.REWARD_STRIDE)
            struct.pack_into("<IHH", row, 0x00, r["exp"], r["sp"], r["cp"])
            f.seek(t["rewards"] + i * F.REWARD_STRIDE)
            f.write(row)
    return path


# ---------------------------------------------------------------------------
CHECKS = []

def check(name, path=False):
    """Register a check. `path=True` hands it the ISO path instead of an open
    handle — for checks that need to write, or want a fixture of their own."""
    def deco(fn):
        CHECKS.append((name, fn, path))
        return fn
    return deco

def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, want {want!r}")


@check("disc identification")
def t_identify(iso, _tmp):
    ok, serial, disc, vol = X.check_version(iso)
    eq(ok, True, "recognized"); eq(serial, "SLUS-20892", "serial")
    eq(disc, 1, "disc number"); eq(vol, "XENOSAGA_II", "volume id")


@check("enemy table confirms at the known base")
def t_confirm(iso, _tmp):
    matched, checked = X._confirm_base(iso, F.enemy_catalog(), F.ENEMY_TABLE_OFF)
    eq(checked, F.ENEMY_COUNT, "records checked")
    # all 125, including the eleven whose undecoded +0x3A isn't 99: this compares
    # verified fields, not the raw 17-byte run
    eq(matched, F.ENEMY_COUNT, "records holding retail values")
    eq(X.disc_is_pristine(iso), True, "pristine-disc anchor")


@check("signature search locates the table from scratch")
def t_locate(iso, _tmp):
    hit = X.locate_enemy_table(iso)
    if not hit:
        raise AssertionError("locate_enemy_table found nothing")
    eq(hit["base"], F.ENEMY_TABLE_OFF, "located base")
    eq(hit["stride"], F.ENEMY_STRIDE, "stride")


@check("rebalance plan: maths, grouping, dummy exclusion")
def t_plan(iso, _tmp):
    cat = F.enemy_catalog()
    plan = X.plan_rebalance(iso, F.profile("faster"))
    by_index = {i: (name, group, edits) for i, name, group, edits in plan}

    perun = by_index[6]                                   # 22,400 HP -> major
    eq(perun[1], "major", "Perun group")
    eq(perun[2]["HP"], (22400, round(22400 * 0.70)), "Perun HP scaling")
    eq(perun[2]["EXP"], (30000, 45000), "Perun EXP scaling")

    soldier = by_index[65]                                # 110 HP -> regular
    eq(soldier[1], "regular", "U-TIC Soldier A group")
    eq(soldier[2]["HP"], (110, round(110 * 0.45)), "soldier HP scaling")

    for i, rec in cat.items():
        if F.is_dummy_record(rec):
            if i in by_index:
                raise AssertionError(f"dummy record {i} ({rec['name']}) was planned")
    dummies = [i for i, r in cat.items() if F.is_dummy_record(r)]
    if len(dummies) < 8:
        raise AssertionError(f"expected the known debug records, found {dummies}")

    # a field that is 0 on disc means "none" and must stay 0, never scale to 1
    for _i, _n, _g, edits in plan:
        for lbl, (old, new) in edits.items():
            if old == 0:
                raise AssertionError(f"{lbl}: scaled a zero field to {new}")
            eq(new <= F.ENEMY_FIELD_CAPS[lbl], True, f"{lbl} within cap")


@check("rebalance write round-trips on disc", path=True)
def t_apply(iso_path, _tmp):
    with X.Iso(iso_path, write=True) as iso:
        plan = X.plan_rebalance(iso, F.profile("faster"))
        recs, fields = X.apply_rebalance(iso, plan)
        eq(recs, len(plan), "records written")
        if fields < recs:
            raise AssertionError("wrote fewer fields than records")
    with X.Iso(iso_path) as iso:
        for i, _name, _group, edits in plan:
            cur = X.read_enemy(iso, i)
            for lbl, (_old, new) in edits.items():
                eq(cur[lbl], new, f"record {i} {lbl} after write")
        eq(X.disc_is_pristine(iso), False, "anchor detects the edited disc")
        # untouched fields must be byte-identical to the catalog
        cat = F.enemy_catalog()
        eq(X.read_enemy(iso, 6)["DEX"], cat[6]["dex"], "unscaled field preserved")


@check("a reward-only pass still trips the already-edited guard", path=True)
def t_pristine_rewards(_iso_path, tmp):
    """Regression: the guard used to watch one HP anchor, so a reward-only
    profile — which touches no stat byte at all — left it reading pristine and a
    second profile stacked its multipliers on top, silently."""
    fresh = os.path.join(tmp, "rewards-only.iso")
    build_fixture(fresh)
    try:
        with X.Iso(fresh, write=True) as iso:
            eq(X.disc_is_pristine(iso), True, "fresh fixture reads as pristine")
            plan = X.plan_rebalance(iso, F.profile("grindcut"))
            for _i, _n, _g, edits in plan:
                eq(set(edits) <= {"EXP", "SP", "CP"}, True, "grindcut touches rewards only")
            X.apply_rebalance(iso, plan)
            eq(X.disc_is_pristine(iso), False, "guard sees the reward-only edit")
    finally:
        os.remove(fresh)


@check("column survey flags the planted zone byte")
def t_columns(iso, _tmp):
    recs = X.read_records(iso)
    prof = {c["off"]: c for c in X.column_profile(recs)}
    if PLANT_OFF not in prof:
        raise AssertionError(f"+0x{PLANT_OFF:02X} is not in the undecoded ranges")
    eq(prof[PLANT_OFF]["packed2"], True, "planted column reads as packed zone symbols")
    eq(prof[PLANT_OFF]["distinct"], len(ZONE_STRINGS), "distinct planted values")
    # the mapped fields must not be offered as unknown columns
    for off in (0x36, 0x3C, 0x44, 0x52):
        if off in prof:
            raise AssertionError(f"+0x{off:02X} is decoded but listed as unknown")


@check("zone scan recovers the planted column and its encoding")
def t_zone_scan(iso, _tmp):
    recs = X.read_records(iso)
    truth = {i: zones_for(i) for i in range(0, F.ENEMY_COUNT, 3)}
    cands = X.zone_scan(recs, truth)
    best = cands[0]
    eq(best["off"], PLANT_OFF, "top candidate offset")
    eq(best["width"], 1, "top candidate width")
    eq(best["consistency"], 1.0, "consistency")
    eq(best["resolution"], 1.0, "resolution")

    mapping = X.zone_mapping(recs, truth, best["off"], best["width"])
    eq(len(mapping), len(ZONE_STRINGS), "one value per zone string")
    for v, zs in mapping.items():
        eq(len(zs), 1, f"value {v} maps to a single zone string")
        eq(v, encode_zones(zs[0]), f"value {v} decodes back to {zs[0]}")


@check("region sweep finds a parallel table when the record has none")
def t_region(iso, _tmp):
    truth = {i: zones_for(i) for i in range(0, F.ENEMY_COUNT, 3)}
    hits = X.scan_region_for_column(iso, truth, PLANT_TABLE_BASE - 0x100, 0x400,
                                    [PLANT_TABLE_STRIDE])
    want = PLANT_TABLE_BASE + PLANT_TABLE_FIELD
    if not any(h["base"] == want and h["resolution"] == 1.0 for h in hits):
        raise AssertionError(f"planted table at 0x{want:X} not among {hits[:4]}")


@check("ground-truth loader accepts JSON and CSV")
def t_truth(_iso, tmp):
    j = os.path.join(tmp, "zones.json")
    with open(j, "w") as f:
        f.write('{"Perun": "bb", "6": "BB", "U-TIC Soldier A": "C B", "Nope": "BB",'
                ' "Deion": "XY"}')
    truth, unmatched = X.load_zone_truth(j)
    eq(truth[6], "BB", "name and index both resolve to record 6")
    eq(truth[65], "CB", "spaces stripped, case normalized")
    eq(sorted(k for k, _ in unmatched), ["Deion", "Nope"], "rejects bad rows")

    c = os.path.join(tmp, "zones.csv")
    with open(c, "w") as f:
        f.write("# name, zones\nPerun, BB\nAiakos,CC\n\n")
    truth, unmatched = X.load_zone_truth(c)
    eq(truth, {6: "BB", 2: "CC"}, "csv parse")
    eq(unmatched, [], "no unmatched rows")


@check("break sequence round-trips through the record", path=True)
def t_break(iso_path, _tmp):
    """+0x54..+0x57 hold the break sequence one-hot, 0 = end; +0x4C is the
    hittable-zone mask. Verified on disc against 46 published sequences — this
    covers the codec and the write path."""
    for text, want in (("CBB", (4, 2, 2, 0)), ("BCBC", (2, 4, 2, 4)),
                       ("A", (1, 0, 0, 0)), ("", (0, 0, 0, 0)),
                       ("c-b-a-a", (4, 2, 1, 1))):
        got = F.encode_break_seq(text)
        eq(got, want, f"encode {text!r}")
        eq(F.decode_break_seq(got), text.upper().replace("-", ""), f"decode {text!r}")
    for bad in ("ABCDE", "X", "AB C D E"):
        try:
            F.encode_break_seq(bad)
            raise AssertionError(f"accepted a bad sequence: {bad!r}")
        except ValueError:
            pass
    eq(F.zone_mask_text(0b110), "BC", "mask 6 names its zones")
    eq(F.zone_mask_text(0), "", "mask 0 has no zones")

    with X.Iso(iso_path) as iso:
        for i in range(F.ENEMY_COUNT):
            eq(X.break_seq_of(X.read_enemy(iso, i)), break_for(i),
               f"record {i} reads back the fixture's sequence")
        # the fixture's mask covers exactly the zones its sequence uses
        rec = X.read_enemy(iso, 3)
        eq(F.zone_mask_text(rec["Zones"]), "BC", "record 3 zone mask")

    with X.Iso(iso_path, write=True) as iso:
        slots = F.encode_break_seq("AB")
        X.write_enemy(iso, 3, {f"Brk{n + 1}": v for n, v in enumerate(slots)})
    with X.Iso(iso_path) as iso:
        eq(X.break_seq_of(X.read_enemy(iso, 3)), "AB", "sequence after write")
        # a shorter sequence must clear the trailing slots, not leave a tail
        eq(X.read_enemy(iso, 3)["Brk3"], 0, "third slot cleared")


@check("disc 2 resolves its own table bases end to end", path=True)
def t_disc2(_iso_path, tmp):
    """Both retail discs carry the enemy tables, disc 2's 0x800 lower. A
    rebalance written only to disc 1 reverts at the disc swap, so the engine has
    to find disc 2's copy from the disc itself — no flag, no caller hint.

    This builds a synthetic disc 2 and drives the whole path: serial detection,
    base resolution, read, pristine check, and a write that lands in disc 2's
    table and nowhere near disc 1's offsets."""
    p = os.path.join(tmp, "synthetic-disc2.iso")
    build_fixture(p, disc=2)
    t1, t2 = F.enemy_tables(1), F.enemy_tables(2)
    eq(t2["stats"], t1["stats"] - 0x800, "disc 2 stat base sits 0x800 lower")

    cat = F.enemy_catalog()
    with X.Iso(p) as iso:
        eq(iso.disc, 2, "serial detected as disc 2")
        eq(iso.tables["stats"], t2["stats"], "resolved stat base")
        eq(X.read_enemy(iso, 6)["HP"], cat[6]["hp"], "reads Perun's HP off disc 2")
        eq(X.read_enemy_id(iso, 6), cat[6]["id"], "reads the record's enemy id")
        eq(X.disc_is_pristine(iso), True, "fresh disc 2 reads as pristine")
        # nothing should be readable at disc 1's base on a disc-2 image
        m, _c = X._confirm_base(iso, cat, t1["stats"])
        eq(m, 0, "disc 1's base holds nothing on a disc-2 image")

    with X.Iso(p, write=True) as iso:
        eq(X.write_enemy(iso, 6, {"HP": 4242}), 1, "one field written")
    with X.Iso(p) as iso:
        eq(X.read_enemy(iso, 6)["HP"], 4242, "write landed in disc 2's table")
        eq(X.disc_is_pristine(iso), False, "edit is detected on disc 2")


@check("a disc-1 patch replays onto disc 2", path=True)
def t_cross_disc(_iso_path, tmp):
    """The recommended way to keep both discs in step: rebalance one, export a
    patch, apply it to the other. The patch records values, not offsets, so it
    has to land at disc 2's bases — and the serial mismatch must be a warning,
    not a refusal, or the workflow is impossible."""
    d1 = build_fixture(os.path.join(tmp, "pair-d1.iso"), disc=1)
    d2 = build_fixture(os.path.join(tmp, "pair-d2.iso"), disc=2)

    with X.Iso(d1, write=True) as iso:
        X.apply_rebalance(iso, X.plan_rebalance(iso, F.profile("faster")))
    with X.Iso(d1) as iso:
        delta = X.diff_vanilla(iso)
    if not delta:
        raise AssertionError("rebalance left disc 1 matching retail")
    # the disc's own value is element 0 — element 1 is what retail held
    doc = X.make_patch({i: {k: have for k, (have, _wanted) in f.items()}
                        for i, f in delta.items()}, serial="SLUS-20892")

    with X.Iso(d2, write=True) as iso:
        recs, _fields = X.apply_patch(iso, X.parse_patch(doc))
    eq(recs, len(delta), "records applied to disc 2")

    with X.Iso(d1) as a, X.Iso(d2) as b:
        eq(a.disc, 1, "first image is disc 1")
        eq(b.disc, 2, "second image is disc 2")
        for i in range(F.ENEMY_COUNT):
            if X.read_enemy(a, i) != X.read_enemy(b, i):
                raise AssertionError(f"record {i} differs between the patched discs")


@check("web ISO editor mirrors the Python profiles")
def t_web_parity(_iso, _tmp):
    """Two editors that rebalance discs differently would be a bad bug, so the
    browser side must run the same numbers as the CLI.

    It used to hold its own copy of PROFILES/MAJOR_HP/CAPS, and this check parsed
    the JS literals to catch drift. It now reads them out of web/tables.json,
    generated from x2fields — so the check is that the generated file is current,
    and that iso.js really does consume it instead of re-declaring its own."""
    import json
    here = os.path.dirname(os.path.abspath(__file__))
    tables = os.path.join(here, "..", "web", "tables.json")
    iso_js = os.path.join(here, "..", "web", "iso.js")
    for p in (tables, iso_js):
        if not os.path.exists(p):
            raise AssertionError(f"{os.path.basename(p)} not found")

    with open(tables, encoding="utf-8") as f:
        web = json.load(f)
    eq(web.get("majorHpThreshold"), F.MAJOR_HP_THRESHOLD, "MAJOR_HP threshold")
    eq(web.get("fieldCaps"), F.ENEMY_FIELD_CAPS, "field caps")
    eq(sorted(web.get("profiles", {})), sorted(F.PROFILES), "profile keys")
    for key, prof in F.PROFILES.items():
        for field in ("label", "note"):
            eq(web["profiles"][key][field], prof[field], f"{key}.{field}")
        for group in ("regular", "major"):
            eq(web["profiles"][key][group], prof[group], f"{key}.{group} scaling")

    src = Path(iso_js).read_text(encoding="utf-8")
    eq("tables.json" in src, True, "iso.js fetches tables.json")
    for name in ("PROFILES", "MAJOR_HP", "CAPS"):
        if f"const {name}=" in src:
            raise AssertionError(f"iso.js re-declares {name} instead of reading "
                                 f"tables.json — the duplication is back")


def main():
    ap = argparse.ArgumentParser(description="ISO engine self-test (no game data needed)")
    ap.add_argument("--keep", metavar="DIR", help="build the fixture here and keep it")
    a = ap.parse_args()

    tmp = a.keep or tempfile.mkdtemp(prefix="x2selftest-")
    os.makedirs(tmp, exist_ok=True)
    iso_path = os.path.join(tmp, "synthetic-disc1.iso")
    print(f"building fixture: {iso_path}")
    build_fixture(iso_path)

    failed = 0
    for name, fn, wants_path in CHECKS:
        try:
            if wants_path:
                fn(iso_path, tmp)
            else:
                with X.Iso(iso_path) as iso:
                    fn(iso, tmp)
            print(f"  ✓ {name}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {name}: {e}")

    if not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
