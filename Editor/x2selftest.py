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
            rec[0x04:0x0C] = bytes([0x64] * 8)            # constant block, not affinities
            # affinities live at +0x58 and straddle the record boundary; a flat
            # 100% in both halves keeps every enemy self-consistent
            rec[0x00:0x04] = bytes([F.affinity_byte(100)] * 4)
            rec[0x58:0x5C] = bytes([F.affinity_byte(100)] * 4)
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

        # the verified ether-skill numeric block (32-byte records; EP at +0x06,
        # element at +0x08, power at +0x0A, 1-based string id at +0x16)
        # both verified skill blocks (ether + doubles), each at its own base
        for _bn, skb, count, text0 in F.skill_blocks(disc):
            for k in range(count):
                i = text0 + k
                rec = bytearray(F.SKILL_STRIDE)
                rec[0], rec[1], rec[2], rec[3] = 100, 85, 1, 2
                rec[0x06] = (i % 30) + 2                       # EP
                struct.pack_into("<H", rec, 0x08, 1 << (i % 5))
                struct.pack_into("<H", rec, 0x0A, 5 * (i + 1)) # power
                struct.pack_into("<H", rec, 0x16, k + 1)       # pool index
                f.seek(skb + k * F.SKILL_STRIDE)
                f.write(rec)

        # The LAST record's affinity (+0x58) and status-resistance (+0x6C)
        # blocks run past the table, into the gap before the name table — the
        # retail disc really does store them there, so a faithful fixture must
        # too. Getting this wrong is what made the web editor show Dark Erde
        # Kaiser's Ice/Pierce/Slash/Hit and every resistance as blank.
        tail = bytearray(F.enemy_record_tail())
        for k in range(4):                       # affinity elements 4..7
            tail[k] = F.affinity_byte(100)
        for _n, off, _w, _k in F.STATUS_RES_FIELDS:
            tail[off - F.ENEMY_STRIDE] = 50      # a distinct, checkable value
        f.seek(t["stats"] + F.ENEMY_COUNT * F.ENEMY_STRIDE)
        f.write(bytes(tail))
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

    perun = by_index[6]                     # Heaven's Ruins -> superboss
    eq(perun[1], "superboss", "Perun group")
    eq("HP" in perun[2], False, "faster leaves super-boss HP alone")
    eq(perun[2]["EXP"], (30000, 45000), "Perun EXP scaling")

    margulis = by_index[94]                 # 1,200 HP prologue boss -> boss
    eq(margulis[1], "boss", "Margulis group")
    eq(margulis[2]["HP"], (1200, round(1200 * 0.70)), "Margulis HP scaling")

    arvakv = by_index[13]                   # 22,000 HP Desert spawn -> random
    eq(arvakv[1], "random", "Arvakv group")
    eq(arvakv[2]["HP"], (22000, round(22000 * 0.45)), "Arvakv HP scaling")

    soldier = by_index[65]                  # 110 HP -> random
    eq(soldier[1], "random", "U-TIC Soldier A group")
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


@check("item drops decode and round-trip", path=True)
def t_drops(iso_path, _tmp):
    """The rest of the 0x10 rewards row: rate, category and a 1-BASED item id per
    slot. Both categories now resolve through the disc's unified item table, each
    from its own base — see t_item_names for the id-space checks."""
    eq(F.drop_label(F.DROP_CAT_NONE, 0, 0), "nothing", "empty slot")
    name = F.drop_item_name(F.DROP_CAT_CONSUMABLE, 1)
    eq(F.drop_label(F.DROP_CAT_CONSUMABLE, 1, 100), f"{name} 100%", "consumable named")
    eq(F.drop_label(F.DROP_CAT_ES, 14, 20), "Anti-Beam Armor 20%", "E.S. gear named")
    # an id past the end of the table still must not invent anything
    eq(F.drop_item_name(F.DROP_CAT_ES, 9999), None, "out-of-range id names nothing")

    # this check shares the fixture with the rebalance checks, so compare against
    # what the row held a moment ago rather than against retail values
    with X.Iso(iso_path) as iso:
        before = X.read_enemy(iso, 9)
    with X.Iso(iso_path, write=True) as iso:
        n = X.write_enemy(iso, 9, {"DropRate": 55, "RareRate": 5,
                                   "DropCat": F.DROP_CAT_CONSUMABLE, "RareCat": F.DROP_CAT_ES,
                                   "DropItem": 1, "RareItem": 14})
        eq(n, 6, "six drop fields written")
    with X.Iso(iso_path) as iso:
        rec = X.read_enemy(iso, 9)
        common, rare = X.drops_of(rec)
        eq(common, f"{name} 55%", "common drop reads back")
        eq(rare, "Anti-Beam Armor 5%", f"rare drop reads back, got {rare!r}")
        # the drop bytes share a row with EXP/SP/CP — those must be untouched
        for lbl2 in ("EXP", "SP", "CP"):
            eq(rec[lbl2], before[lbl2], f"{lbl2} untouched by a drop write")


@check("bulk break shortening: plan, floor, and idempotence")
def t_shorten(_iso, _tmp):
    """Trims from the END so the opening zone stays right, never empties a
    sequence (empty means 'cannot be broken' — a harder fight, not a faster
    one), and never resurrects one that is already empty."""
    eq(F.shorten_break_seq("CBAA", 1), "CBA", "4 -> 3")
    eq(F.shorten_break_seq("CBAA", 2), "CB", "4 -> 2 in one go")
    eq(F.shorten_break_seq("CBB", 1), "CB", "3 -> 2")
    eq(F.shorten_break_seq("CB", 1), "C", "2 -> 1")
    eq(F.shorten_break_seq("C", 1), "C", "1 stays 1 — never emptied")
    eq(F.shorten_break_seq("", 1), "", "unbreakable stays unbreakable")
    eq(F.shorten_break_seq("CBAA", 99), "C", "over-shortening floors at 1")

    plan = F.plan_break_shortening({0: "BB", 1: "CBAA", 2: "", 3: "C"}, 1)
    eq(plan, [(0, "BB", "B"), (1, "CBAA", "CBA")], "plan lists only real changes")
    # applying then re-planning converges
    after = {i: (dict((a, c) for a, _b, c in plan).get(i, s))
             for i, s in {0: "BB", 1: "CBAA", 2: "", 3: "C"}.items()}
    eq(F.plan_break_shortening(after, 1), [(1, "CBA", "CB")], "second pass keeps going")
    eq(F.plan_break_shortening({0: "B", 1: "", 2: "C"}, 1), [], "at the floor: no-op")

    # The floor is a balance choice, not a correctness one, so it can be lowered
    # deliberately — but only deliberately. Turning it off is what lets a
    # sequence be emptied, which REMOVES the break rather than shortening it.
    none = F.BREAK_FLOOR_NONE
    eq(F.shorten_break_seq("C", 1, none), "", "shield off: 1 -> unbreakable")
    eq(F.shorten_break_seq("CB", 2, none), "", "shield off: 2 -> unbreakable")
    eq(F.shorten_break_seq("CBAA", 99, none), "", "shield off: over-shortening empties")
    eq(F.shorten_break_seq("", 1, none), "", "shield off still never resurrects an empty one")
    eq(F.plan_break_shortening({0: "C", 1: ""}, 1, none), [(0, "C", "")],
       "shield off: the 1-hit is now in the plan, the empty one still isn't")

    # and on the real retail distribution, the default must never empty anything
    cat = F.enemy_catalog()
    seqs = {i: F.decode_break_seq([r[f"brk{n + 1}"] for n in range(F.BREAK_SEQ_SLOTS)])
            for i, r in cat.items()}
    for steps in (1, 2, 3):
        emptied = [i for i, _o, n in F.plan_break_shortening(seqs, steps) if not n]
        eq(emptied, [], f"retail data, -{steps}: nothing is emptied with the shield on")
    # with it off, -2 would silently make most of the bestiary unbreakable —
    # which is exactly why the default is on
    off2 = [i for i, _o, n in F.plan_break_shortening(seqs, 2, none) if not n]
    eq(len(off2) > 50, True,
       f"shield off at -2 empties {len(off2)} sequences — that is exactly what it guards")


@check("enemy table JSON round-trips and validates", path=True)
def t_table_json(_shared, tmp):
    """Full-table JSON for bulk editing. Import is strict on purpose: it writes
    to a disc image, so anything unexpected is an error, not a skipped row."""
    import json as _json
    # own fixture: this check writes, and later checks read the shared one
    iso_path = build_fixture(os.path.join(tmp, "tablejson.iso"), disc=1)
    with X.Iso(iso_path) as iso:
        doc = X.enemy_json(iso)
    eq(doc["count"], F.ENEMY_COUNT, "every enemy exported")
    eq(len(doc["enemies"]), F.ENEMY_COUNT, "row count")
    row = doc["enemies"][6]
    eq(row["index"], 6, "rows carry their index")
    for key in ("affinity", "resist", "drop", "rare", "break", "HP"):
        eq(key in row, True, f"row exposes {key}")

    # a clean re-import of an untouched export changes nothing
    with X.Iso(iso_path) as iso:
        parsed = X.parse_enemy_json(_json.loads(_json.dumps(doc)))
        changed = [i for i, f in parsed.items()
                   for k, v in f.items() if X.read_enemy(iso, i).get(k) != v]
    eq(changed, [], "round-trip is a no-op against the disc it came from")

    # edits survive the write in the units they were written in
    row["HP"] = 4242
    row["break"] = "CB"
    row["affinity"]["Fire"] = -200
    row["resist"]["Slow"] = 99
    edits = X.parse_enemy_json(doc)
    with X.Iso(iso_path, write=True) as iso:
        X.write_enemy(iso, 6, edits[6])
    with X.Iso(iso_path) as iso:
        rec = X.read_enemy(iso, 6)
        eq(rec["HP"], 4242, "HP")
        eq(X.break_seq_of(rec), "CB", "break sequence")
        eq(F.affinity_pct(rec["Fire"]), -200, "negative affinity in percent")
        eq(rec["Slow"], 99, "status resistance")

    def rejects(mutate, what):
        bad = _json.loads(_json.dumps(doc))
        mutate(bad)
        try:
            X.parse_enemy_json(bad)
            raise AssertionError(f"accepted {what}")
        except ValueError:
            pass
    rejects(lambda d: d.update(format="nope"), "a wrong format tag")
    rejects(lambda d: d.update(version=99), "a future version")
    rejects(lambda d: d["enemies"][0].update(index=9999), "an out-of-range index")
    rejects(lambda d: d["enemies"][0].update(HP=-1), "a negative value")
    rejects(lambda d: d["enemies"][0].update(HP="lots"), "a non-numeric value")
    rejects(lambda d: d["enemies"][0].update(**{"break": "XY"}), "a bad zone letter")
    rejects(lambda d: d["enemies"][0]["affinity"].update(Fire=103), "an off-step affinity")
    rejects(lambda d: d["enemies"][0]["affinity"].update(Flame=100), "an unknown element")
    rejects(lambda d: d["enemies"][0]["resist"].update(Nope=1), "an unknown status")


@check("the LAST record's overhanging fields are reachable", path=True)
def t_tail(_shared, tmp):
    """The affinity and resistance blocks end past the nominal record, so the
    final record's fields live beyond count*stride. Anything that slices the
    table into a fixed buffer must add enemy_record_tail() bytes — the web
    editor didn't, and showed Dark Erde Kaiser's Ice/Pierce/Slash/Hit and every
    resistance as blank-and-modified."""
    reach = max(off + w for (_l, off, w, _k) in
                (F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS + F.ZONE_FIELDS
                 + F.STATUS_RES_FIELDS))
    eq(F.enemy_record_tail(), max(0, reach - F.ENEMY_STRIDE), "tail matches the fields")
    eq(F.enemy_record_tail() > 0, True, "some field really does overhang")

    # its own fixture: this check writes to the last record, and the affinity
    # check that runs later expects the shared one untouched
    iso_path = build_fixture(os.path.join(tmp, "tail.iso"), disc=1)
    last = F.ENEMY_COUNT - 1
    with X.Iso(iso_path) as iso:
        rec = X.read_enemy(iso, last)
        for el in F.AFFINITY_ELEMENTS:
            eq(F.affinity_pct(rec[el]), 100, f"last record {el} readable")
        for n in F.STATUS_RES_NAMES:
            eq(rec[n], 50, f"last record {n} readable")
        # read_records must hand back the overhang too, or scanners truncate it
        recs = X.read_records(iso)
        eq(len(recs[last]), F.ENEMY_STRIDE + F.enemy_record_tail(), "sliced length")
        for _n, off, _w, _k in F.STATUS_RES_FIELDS:
            eq(recs[last][off], 50, "resistance survives the record slice")

    # and a write to the last record must land where the read came from
    with X.Iso(iso_path, write=True) as iso:
        X.write_enemy(iso, last, {F.AFFINITY_ELEMENTS[7]: F.affinity_byte(-100),
                                  "Junk": 77})
    with X.Iso(iso_path) as iso:
        rec = X.read_enemy(iso, last)
        eq(F.affinity_pct(rec[F.AFFINITY_ELEMENTS[7]]), -100, "last-record affinity write")
        eq(rec["Junk"], 77, "last-record resistance write")


@check("status resistances read and write at their own offsets", path=True)
def t_resist(iso_path, _tmp):
    """+0x6C, one u8 percent per status — which is 0x10 bytes INTO record i+1,
    the same shifted framing the affinity block showed. So a write for enemy i
    must not disturb enemy i+1's stats, and must not be confused with enemy
    i+1's own resistances."""
    for name, off, w, _k in F.STATUS_RES_FIELDS:
        eq(w, 1, f"{name} is a single byte")
        eq(off >= F.STATUS_RES_OFF, True, f"{name} sits in the block")
    eq(len(F.STATUS_RES_NAMES), 8, "eight named statuses")
    # the block starts past the nominal record, like the affinity block
    eq(F.STATUS_RES_OFF > F.ENEMY_STRIDE, True, "block is past the record end")

    with X.Iso(iso_path) as iso:
        before8 = X.read_enemy(iso, 8)
    with X.Iso(iso_path, write=True) as iso:
        n = X.write_enemy(iso, 7, {"Slow": 55, "Blind": 5, "Junk": 100})
        eq(n, 3, "three resistances written")
    with X.Iso(iso_path) as iso:
        rec7 = X.read_enemy(iso, 7)
        eq((rec7["Slow"], rec7["Blind"], rec7["Junk"]), (55, 5, 100), "written back")
        after8 = X.read_enemy(iso, 8)
        for lbl in ("HP", "STR", "VIT", "EATK", "EDEF", "DEX", "EVA", "AGL"):
            eq(after8[lbl], before8[lbl], f"record 8 {lbl} untouched")
        # enemy 8's own resistances live further on and must be independent
        for name in F.STATUS_RES_NAMES:
            eq(after8[name], before8[name], f"record 8 {name} untouched")


@check("drops name both item categories from the unified table")
def t_item_names(_iso, _tmp):
    """One table on disc holds E.S. gear and consumables; each drop category
    indexes it from its own base with a 1-based id. The 予備 placeholder slots
    occupy id space — skipping them is what made the E.S. ids look unmappable."""
    items = F.item_catalog()
    eq(len(items) > 100, True, "catalog loaded")
    ph = [i for i, v in items.items() if v.get("placeholder")]
    eq(len(ph) > 0, True, "placeholders are kept, not dropped")
    eq(F.item_name(ph[0]), None, "a placeholder refuses to name itself")
    # the anchors the guide pinned down
    eq(F.drop_item_name(F.DROP_CAT_ES, 1), "Auxiliary Armor A", "E.S. id 1")
    eq(F.drop_item_name(F.DROP_CAT_ES, 6), "EF Circuit A", "E.S. id 6 (past 3 spares)")
    eq(F.drop_item_name(F.DROP_CAT_ES, 22), "G Blind Guard", "E.S. id 22")
    eq(F.drop_item_name(F.DROP_CAT_CONSUMABLE, 1), "Med Kit S", "consumable id 1")
    eq(F.drop_item_name(F.DROP_CAT_CONSUMABLE, 24), "Skill Upgrade C", "consumable id 24")
    eq(F.drop_item_name(F.DROP_CAT_NONE, 5), None, "category 0 names nothing")
    lbl = F.drop_label(F.DROP_CAT_ES, 6, 25)
    eq(lbl, "EF Circuit A 25%", f"label reads {lbl!r}")


@check("damage affinities: codec, straddle, isolation", path=True)
def t_affinity(iso_path, _tmp):
    """+0x58, eight signed bytes, percent = byte*5 (71/71 against the guide).

    The block runs four bytes past the nominal 0x5C record, so enemy i's last
    four affinity bytes physically live inside record i+1. That is the part worth
    pinning: a write must land on the right enemy and must not disturb the next
    record's stats."""
    for pct in (-200, -100, 0, 5, 100, 250, 400):
        eq(F.affinity_pct(F.affinity_byte(pct)), pct, f"round-trip {pct}%")
    eq(F.affinity_byte(-200), 0xD8, "-200% is a signed -40")
    eq(F.affinity_pct(0xD8), -200, "0xD8 decodes negative")
    eq(F.ENEMY_AFFINITY_OFF + F.ENEMY_AFFINITY_COUNT > F.ENEMY_STRIDE, True,
       "the block really does straddle the record boundary")

    els = list(F.AFFINITY_ELEMENTS)
    with X.Iso(iso_path) as iso:
        for i in (0, 6, F.ENEMY_COUNT - 1):
            rec = X.read_enemy(iso, i)
            for el in els:
                eq(F.affinity_pct(rec[el]), 100, f"record {i} {el} starts at 100%")

    with X.Iso(iso_path) as iso:
        before7 = X.read_enemy(iso, 7)
    with X.Iso(iso_path, write=True) as iso:
        X.write_enemy(iso, 6, {els[0]: F.affinity_byte(0),      # immune
                               els[3]: F.affinity_byte(-200),   # absorbs double
                               els[7]: F.affinity_byte(250)})
    with X.Iso(iso_path) as iso:
        rec6 = X.read_enemy(iso, 6)
        eq(F.affinity_pct(rec6[els[0]]), 0, "immune written")
        eq(F.affinity_pct(rec6[els[3]]), -200, "negative written")
        eq(F.affinity_pct(rec6[els[7]]), 250, "weakness written")
        # element 7 lives inside record 7's bytes — record 7's stats must survive
        after7 = X.read_enemy(iso, 7)
        for lbl in ("HP", "STR", "VIT", "EATK", "EDEF", "DEX", "EVA", "AGL"):
            eq(after7[lbl], before7[lbl], f"record 7 {lbl} untouched")
        # ...and record 7's own affinities must not have been shifted either
        for el in els:
            eq(F.affinity_pct(after7[el]), 100, f"record 7 {el} untouched")


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


@check("ether-skill numeric table reads, writes and stays in its lane", path=True)
def t_skills(iso_path, tmp):
    """32-byte records at a per-disc base; EP/Element/Power/EffPct/EffMask are
    the exposed fields. A write to skill i must not touch skill i±1, and the
    disc-2 fixture must resolve its own base."""
    with X.Iso(iso_path) as iso:
        sk = X.read_skill(iso, 0)
        eq(sk["EP"], 2, "fixture EP reads back")
        eq(sk["Power"], 5, "fixture power reads back")
    with X.Iso(iso_path) as iso:
        before1 = X.read_skill(iso, 1)
    with X.Iso(iso_path, write=True) as iso:
        eq(X.write_skill(iso, 0, {"Power": 250, "EP": 50, "Element": 0x10}), 3,
           "three fields written")
        # 57/58 are placeholders between the blocks — not addressable
        for gap in (57, 58, 88):
            try:
                X.write_skill(iso, gap, {"EP": 1})
                raise AssertionError(f"wrote to unbacked skill index {gap}")
            except SystemExit:
                pass
    with X.Iso(iso_path) as iso:
        sk = X.read_skill(iso, 0)
        eq((sk["Power"], sk["EP"], sk["Element"]), (250, 50, 0x10), "write round-trips")
        eq(X.read_skill(iso, 1), before1, "neighbour untouched")

    # the doubles block shares this layout and is editable too
    with X.Iso(iso_path, write=True) as iso:
        eq(X.write_skill(iso, 59, {"Power": 777}), 1, "doubles block is writable")
    with X.Iso(iso_path) as iso:
        eq(X.read_skill(iso, 59)["Power"], 777, "doubles write round-trips")
        eq(X.read_skill(iso, 56)["Power"], 5 * 57, "ether block untouched by it")

    d2 = build_fixture(os.path.join(tmp, "skill-d2.iso"), disc=2)
    with X.Iso(d2) as iso:
        eq(iso.disc, 2, "fixture is disc 2")
        eq(X.read_skill(iso, 3)["Power"], 20, "disc 2 resolves its own skill base")
        eq(X.read_skill(iso, 59)["Power"], 5 * 60, "disc 2 doubles base too")
    # and the disc sync primitive carries the skill table across
    with X.Iso(iso_path) as src, X.Iso(d2, write=True) as dst:
        eq(X.sync_skills(src, dst) > 0, True, "skill sync copied")
    with X.Iso(d2) as iso:
        eq(X.read_skill(iso, 0)["Power"], 250, "ether edit arrived on disc 2")
        eq(X.read_skill(iso, 59)["Power"], 777, "doubles edit arrived on disc 2")
    with X.Iso(iso_path) as src, X.Iso(d2, write=True) as dst:
        eq(X.sync_skills(src, dst), 0, "second sync is a no-op")


@check("sync_discs mirrors every verified field onto the other disc", path=True)
def t_sync(_iso_path, tmp):
    """The one primitive keeping the discs in step: copy every verified field,
    each disc reading and writing at its own bases. Covers the fields most likely
    to be forgotten — the affinity block (which straddles the record boundary)
    and the break sequence — not just HP."""
    d1 = build_fixture(os.path.join(tmp, "sync-d1.iso"), disc=1)
    d2 = build_fixture(os.path.join(tmp, "sync-d2.iso"), disc=2)
    el = F.AFFINITY_ELEMENTS[3]

    with X.Iso(d1, write=True) as iso:
        X.apply_rebalance(iso, X.plan_rebalance(iso, F.profile("faster")))
        X.write_enemy(iso, 6, {el: F.affinity_byte(-200)})
        slots = F.encode_break_seq("CB")
        X.write_enemy(iso, 6, {f"Brk{n + 1}": v for n, v in enumerate(slots)})

    with X.Iso(d1) as src, X.Iso(d2, write=True) as dst:
        eq(src.disc, 1, "source is disc 1"); eq(dst.disc, 2, "target is disc 2")
        recs, fields = X.sync_discs(src, dst)
    if recs < 100:
        raise AssertionError(f"expected a broad sync, only touched {recs} records")

    with X.Iso(d1) as a, X.Iso(d2) as b:
        for i in range(F.ENEMY_COUNT):
            eq(X.read_enemy(a, i), X.read_enemy(b, i), f"record {i} after sync")
        rb = X.read_enemy(b, 6)
        eq(F.affinity_pct(rb[el]), -200, "negative affinity crossed over")
        eq(X.break_seq_of(rb), "CB", "break sequence crossed over")

    # idempotent: a second pass has nothing left to do
    with X.Iso(d1) as src, X.Iso(d2, write=True) as dst:
        eq(X.sync_discs(src, dst), (0, 0), "second sync is a no-op")


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

    It used to hold its own copy of PROFILES/CAPS/thresholds, and this check parsed
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
    eq(web.get("fieldCaps"), F.ENEMY_FIELD_CAPS, "field caps")
    eq(sorted(web.get("profiles", {})), sorted(F.PROFILES), "profile keys")
    for key, prof in F.PROFILES.items():
        for field in ("label", "note"):
            eq(web["profiles"][key][field], prof[field], f"{key}.{field}")
        for group in F.ENCOUNTER_CLASSES:
            eq(web["profiles"][key][group], prof[group], f"{key}.{group} scaling")

    # the audited encounter classes, so both front-ends group records identically
    enc = web.get("encounter", {})
    eq(enc.get("classes"), list(F.ENCOUNTER_CLASSES), "encounter classes")
    eq(enc.get("labels"), F.ENCOUNTER_LABELS, "encounter labels")
    # the "?" explainer on both tabs renders this, so it has to be the same text
    eq(enc.get("notes"), json.loads(json.dumps(F.ENCOUNTER_NOTES)), "encounter notes")
    eq({int(k): v for k, v in enc.get("byIndex", {}).items()},
       {i: c for i, c in F.encounter_classes().items() if c in ("boss", "superboss")},
       "encounter class per record")

    src = Path(iso_js).read_text(encoding="utf-8")
    eq("tables.json" in src, True, "iso.js fetches tables.json")
    for name in ("PROFILES", "CAPS", "ECLASS_BY_IDX"):
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
