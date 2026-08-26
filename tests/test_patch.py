"""
Tests for the new editing surface: enemy damage-affinity slots, retail
comparison, and the shareable patch-file format (which the web editor also
reads and writes, so its rules have to be exact).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fixtures as FX
import x2fields as F
import x2patch as X
import x2save as SV

EDITOR = os.path.normpath(os.path.join(FX.HERE, "..", "Editor"))


class PatchCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="x2patch-")
        # a disc that already matches retail, so any difference is ours
        retail = {i: {label: v[key] for label, key in F.ENEMY_CATALOG_KEY.items()
                      if key in v}
                  for i, v in F.enemy_catalog().items()}
        self.iso = FX.write_fake_disc(os.path.join(self.dir, "disc1.iso"),
                                      enemies=retail)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fresh(self, name):
        p = os.path.join(self.dir, name)
        shutil.copyfile(self.iso, p)
        return p

    def run_cli(self, *args, expect=0):
        r = subprocess.run([sys.executable, "x2patch.py", *args], cwd=EDITOR,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        return r.stdout


class TestAffinities(PatchCase):
    """+0x58, eight signed bytes, percent = byte * 5 (verified against 71 guide
    entries). Note these bytes run four past the nominal 0x5C record — an
    affinity block is the last four bytes of one record plus the first four of
    the next — so the write path has to address them absolutely."""

    def test_slots_sit_where_the_notes_say(self):
        offs = [off for (_l, off, _w, _k) in F.ENEMY_AFFINITY_FIELDS]
        self.assertEqual(offs, list(range(F.ENEMY_AFFINITY_OFF,
                                          F.ENEMY_AFFINITY_OFF + 8)))
        for _l, _o, w, _k in F.ENEMY_AFFINITY_FIELDS:
            self.assertEqual(w, 1)
        self.assertEqual([l for (l, *_r) in F.ENEMY_AFFINITY_FIELDS],
                         list(F.AFFINITY_ELEMENTS))

    def test_percent_codec_round_trips_including_negatives(self):
        for pct in (-200, -100, 0, 5, 100, 150, 250, 300, 400):
            self.assertEqual(F.affinity_pct(F.affinity_byte(pct)), pct, pct)
        self.assertEqual(F.affinity_byte(-200), 0xD8)      # -40 as a signed byte
        self.assertEqual(F.affinity_pct(0xD8), -200)
        self.assertEqual(F.affinity_byte(100), 20)
        # off-step input snaps to the nearest representable 5%
        self.assertEqual(F.affinity_pct(F.affinity_byte(102)), 100)

    def test_slots_are_readable_and_writable(self):
        p = self.fresh("aff.iso")
        first = F.AFFINITY_ELEMENTS[0]
        last = F.AFFINITY_ELEMENTS[-1]
        with X.Iso(p, write=True) as iso:
            self.assertEqual(X.write_enemy(iso, 4, {first: F.affinity_byte(0),
                                                    last: F.affinity_byte(250)}), 2)
        with X.Iso(p) as iso:
            rec = X.read_enemy(iso, 4)
        self.assertEqual(F.affinity_pct(rec[first]), 0)      # immune
        self.assertEqual(F.affinity_pct(rec[last]), 250)     # takes extra

    def test_a_negative_affinity_survives_the_round_trip(self):
        p = self.fresh("affneg.iso")
        el = F.AFFINITY_ELEMENTS[3]
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 5, {el: F.affinity_byte(-200)})
        with X.Iso(p) as iso:
            self.assertEqual(F.affinity_pct(X.read_enemy(iso, 5)[el]), -200)

    def test_writing_one_slot_leaves_the_stats_alone(self):
        p = self.fresh("aff2.iso")
        el = F.AFFINITY_ELEMENTS[2]
        with X.Iso(p) as iso:
            before = X.read_enemy(iso, 6)
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {el: F.affinity_byte(50)})
        with X.Iso(p) as iso:
            after = X.read_enemy(iso, 6)
        self.assertEqual(F.affinity_pct(after[el]), 50)
        for label in ("HP", "STR", "VIT", "EATK", "EDEF", "DEX", "EVA", "AGL",
                      "EXP", "SP", "CP"):
            self.assertEqual(after[label], before[label], label)

    def test_a_block_write_does_not_disturb_the_next_records_stats(self):
        """The straddle makes this worth pinning: enemy 6's affinity bytes 4..7
        physically sit inside record 7, so a careless implementation would
        corrupt record 7's stats."""
        p = self.fresh("affstraddle.iso")
        with X.Iso(p) as iso:
            before7 = X.read_enemy(iso, 7)
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {el: F.affinity_byte(45)
                                   for el in F.AFFINITY_ELEMENTS})
        with X.Iso(p) as iso:
            after7 = X.read_enemy(iso, 7)
            self.assertTrue(all(F.affinity_pct(X.read_enemy(iso, 6)[el]) == 45
                                for el in F.AFFINITY_ELEMENTS))
        for label in ("HP", "STR", "VIT", "EATK", "EDEF", "DEX", "EVA", "AGL"):
            self.assertEqual(after7[label], before7[label], label)

    def test_cli_notes_how_the_scale_works(self):
        out = self.run_cli("enemy-set", self.fresh("aff3.iso"), "6",
                           "--set", F.AFFINITY_ELEMENTS[0] + "=0")
        self.assertIn("5% steps", out)

    def test_cli_shows_them_only_when_asked(self):
        el = F.AFFINITY_ELEMENTS[0]
        self.assertNotIn(el, self.run_cli("enemies", self.iso, "--csv"))
        self.assertIn(el, self.run_cli("enemies", self.iso, "--csv", "--affinities"))


class TestUnits(PatchCase):
    """The player-unit table: 15 records before the enemy table, same layout."""

    def test_table_sits_directly_before_the_enemy_table(self):
        # the gap holds the name pool; the records themselves must not overlap
        end = F.UNIT_TABLES[1] + F.UNIT_COUNT * F.ENEMY_STRIDE
        self.assertLessEqual(end, F.ENEMY_TABLE_OFF)
        self.assertEqual(F.UNIT_TABLES[1] - F.UNIT_TABLES[2], 0x800,
                         "disc 2 carries the table the usual 0x800 lower")

    def test_names_resolve_through_the_pointer(self):
        with X.Iso(self.iso) as iso:
            self.assertEqual(X.unit_name(iso, 0), "chaos")
            self.assertEqual(X.unit_name(iso, 10), "E.S.Dinah")

    def test_round_trip_is_surgical(self):
        p = self.fresh("unit.iso")
        with X.Iso(p, write=True) as iso:
            before_other = X.read_unit(iso, 1)
            X.write_unit(iso, 0, {"HP": 999, "EP": 77})
        with X.Iso(p) as iso:
            u = X.read_unit(iso, 0)
            self.assertEqual((u["HP"], u["EP"]), (999, 77))
            self.assertEqual(X.read_unit(iso, 1), before_other,
                             "the neighbouring record must be untouched")
            # and the enemy table right after it must be untouched too
            self.assertEqual(X.diff_vanilla(iso), {})

    def test_affinities_read_and_write_including_the_last_record(self):
        # the block straddles into the next record, so unit 14's slots 4-7 live
        # PAST the table — the same overhang that once showed Dark Erde Kaiser's
        # last four affinities as blank
        p = self.fresh("unitaff.iso")
        last = F.UNIT_COUNT - 1
        with X.Iso(p) as iso:
            neighbour = X.read_unit(iso, 1)
        with X.Iso(p, write=True) as iso:
            self.assertEqual(X.unit_affinity_pcts(X.read_unit(iso, last))["Hit"], 100)
            X.write_unit(iso, last, {"Hit": F.affinity_byte(0)})     # immune
            X.write_unit(iso, 0, {"Hit": F.affinity_byte(-200)})     # absorbs
        with X.Iso(p) as iso:
            self.assertEqual(X.unit_affinity_pcts(X.read_unit(iso, last))["Hit"], 0)
            self.assertEqual(X.unit_affinity_pcts(X.read_unit(iso, 0))["Hit"], -200)
            # unit 0's Hit slot physically lives in unit 1's first bytes, so this
            # is the case where a careless write WOULD corrupt the neighbour
            after = X.read_unit(iso, 1)
            self.assertEqual({k: after[k] for k, _o, _w, _kk in F.UNIT_FIELDS},
                             {k: neighbour[k] for k, _o, _w, _kk in F.UNIT_FIELDS},
                             "writing unit 0's Hit disturbed unit 1's stats")
            self.assertEqual(X.diff_vanilla(iso), {}, "the enemy table is untouched")

    def test_record_tail_covers_the_overhang(self):
        reach = max(o + w for _l, o, w, _k in
                    F.UNIT_FIELDS + F.UNIT_AFFINITY_FIELDS)
        self.assertEqual(F.unit_record_tail(), reach - F.ENEMY_STRIDE)
        self.assertGreater(F.unit_record_tail(), 0,
                           "the affinity block does overhang — the tail cannot be 0")

    def test_cli_accepts_affinity_fields(self):
        p = self.fresh("unitaffcli.iso")
        self.run_cli("unit-set", p, "0", "--set", f"Ice={F.affinity_byte(50)}")
        with X.Iso(p) as iso:
            self.assertEqual(X.unit_affinity_pcts(X.read_unit(iso, 0))["Ice"], 50)
        self.assertIn("Beam", self.run_cli("units", p, "--affinities"))

    def test_cli_lists_and_writes(self):
        p = self.fresh("unitcli.iso")
        out = self.run_cli("units", p)
        self.assertIn("chaos", out)
        self.assertIn("E.S.Dinah", out)
        self.run_cli("unit-set", p, "0", "--set", "HP=555")
        with X.Iso(p) as iso:
            self.assertEqual(X.read_unit(iso, 0)["HP"], 555)

    def test_explain_diff_names_unit_fields(self):
        hp_off = next(o for lbl, o, _w, _k in F.UNIT_FIELDS if lbl == "HP")
        self.assertEqual(X._locate(F.UNIT_TABLES[1] + 2 * F.ENEMY_STRIDE + hp_off, 1),
                         ("unit stats", "unit 2 HP"))

    def test_unknown_unit_field_is_rejected(self):
        # SystemExit with a message exits 1 (argparse errors are the 2s)
        self.run_cli("unit-set", self.iso, "0", "--set", "Luck=7", expect=1)


class TestSkillTargeting(unittest.TestCase):
    """The field that turns a single-target skill into an AoE."""

    def test_named_values(self):
        for v, want in ((0x21, "One ally"), (0x22, "One enemy"), (0x24, "Self"),
                        (0x29, "All allies"), (0x2A, "All enemies")):
            self.assertEqual(F.skill_target_text(v), want)

    def test_unverified_values_are_never_given_a_clean_name(self):
        # Revert holds 0x31, whose high nibble differs from every verified value.
        # It must read as something obviously raw rather than be tidied into
        # "One ally", which is how a guess becomes a fact.
        self.assertIn("0x31", F.skill_target_text(0x31))
        self.assertEqual(F.skill_target_text(0xFF), "0xFF")

    def test_the_all_bit_is_what_widens_a_skill(self):
        for one, allv in ((0x21, 0x29), (0x22, 0x2A)):
            self.assertEqual(one | F.SKILL_TARGET_ALL, allv)
            self.assertTrue(F.skill_target_text(allv).startswith("All"))

    def test_the_field_sits_before_the_record(self):
        # a slice of a 32-byte read would index from the far END of the buffer
        # for a negative offset, silently returning the wrong byte
        self.assertEqual(F.SKILL_TARGET_OFF, -4)
        spec = next(f for f in F.SKILL_NUM_FIELDS if f[0] == "Target")
        self.assertEqual(spec[1], -4)

    def test_the_read_buffer_starts_before_the_first_block(self):
        # otherwise the first record of the first block addresses outside it
        for disc in F.SKILL_BLOCKS:
            first = min(b for _n, b, _c, _t in F.skill_blocks(disc))
            self.assertEqual(F.skill_base(disc), first - 4)
            self.assertLessEqual(first + F.SKILL_TARGET_OFF, F.skill_base(disc))

    def test_every_editable_skill_has_a_retail_target(self):
        cat = F.skill_catalog()
        for i in F.skill_editable_indices(1):
            with self.subTest(i):
                self.assertIsNotNone((cat[i].get("numeric") or {}).get("target"),
                                     f"skill {i} has no retail Target to compare against")


class TestDropItemNames(unittest.TestCase):
    """Drop ids are picked by name, so every id an enemy uses must resolve."""

    def test_every_retail_drop_id_names_an_item(self):
        # a bare id is meaningless without its category's base — the editor
        # knows the base, so the user should never have to
        cat = F.enemy_catalog()
        unresolved = []
        for i, r in cat.items():
            for c_key, i_key in (("dropcat", "dropitem"), ("rarecat", "rareitem")):
                c, item = r[c_key], r[i_key]
                if not c or not item:
                    continue
                if F.drop_item_name(c, item) is None:
                    unresolved.append((i, r["name"], c, item))
        self.assertEqual(unresolved, [],
                         "these drops would show as bare numbers in the picker")

    def test_the_categories_partition_one_table(self):
        # E.S. gear from base 0, consumables from base 40 — each category runs
        # until the next base starts, which is what bounds the dropdown
        self.assertEqual(F.DROP_CAT_BASE[F.DROP_CAT_ES], 0)
        self.assertEqual(F.DROP_CAT_BASE[F.DROP_CAT_CONSUMABLE], 40)
        self.assertEqual(F.drop_item_name(F.DROP_CAT_CONSUMABLE, 1), "Med Kit S")
        self.assertEqual(F.drop_item_name(F.DROP_CAT_ES, 1), "Auxiliary Armor A")
        # the same raw id means different items in the two categories, which is
        # exactly why the field cannot be shown as a plain number
        self.assertNotEqual(F.drop_item_name(1, 14), F.drop_item_name(2, 14))


class TestSkillNames(unittest.TestCase):
    """Renaming a skill in a packed string pool."""

    def test_budget_comes_from_the_retail_name_not_the_disc(self):
        # Reading the budget off the disc looks right and is wrong the moment
        # anyone renames: shortening moves the terminator, the next read reports
        # the SHORTER budget, and the name can never be restored to full length.
        self.assertEqual(F.skill_name_budget("Aura Blast"), 11)
        self.assertEqual(F.skill_name_budget("Flare"), 6)
        self.assertEqual(F.skill_name_budget(""), 1)

    def test_every_editable_skill_can_be_renamed(self):
        cat = F.skill_catalog()
        missing = [i for i in F.skill_editable_indices(1)
                   if not cat.get(i, {}).get("nameOff")]
        self.assertEqual(missing, [],
                         "these skills have no name offset, so they cannot be renamed")

    def test_name_lookup_is_blob_precise_not_pool_wide(self):
        # the read span is a bounding box over three scattered pools; using it
        # to identify bytes would claim the enemy tables as "skill text"
        cat = F.skill_catalog()
        off = cat[0]["nameOff"]
        self.assertEqual(F.skill_name_at(off), 0)
        self.assertEqual(F.skill_name_at(off + len("Medica")), 0)     # the NUL
        self.assertIsNone(F.skill_name_at(off + 32),
                          "a byte past the name blob is not that skill's name")
        lo, ln = F.skill_text_span(1)
        self.assertTrue(lo < F.ENEMY_TABLE_OFF < lo + ln,
                        "the span really does contain the enemy table…")
        self.assertIsNone(F.skill_name_at(F.ENEMY_TABLE_OFF),
                          "…and must not claim it as skill text")

    def test_the_read_span_covers_every_name(self):
        for disc in (1, 2):
            lo, ln = F.skill_text_span(disc)
            shift = 0 if disc == 1 else 0x800
            for i in F.skill_editable_indices(disc):
                off = F.skill_catalog()[i]["nameOff"] - shift
                with self.subTest(disc=disc, skill=i):
                    self.assertTrue(lo <= off < lo + ln)


class TestExplainDiff(unittest.TestCase):
    """Reading a third-party mod's bytes rather than trusting its description."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="x2diff-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _pair(self, base, edits):
        a = os.path.join(self.dir, "a.bin")
        b = os.path.join(self.dir, "b.bin")
        Path(a).write_bytes(base)
        mod = bytearray(base)
        for off, data in edits:
            mod[off:off + len(data)] = data
        Path(b).write_bytes(bytes(mod))
        return a, b

    def test_finds_runs_including_the_awkward_ones(self):
        # a flat byte loop over 4.6 GB is billions of iterations, so the scan is
        # hierarchical — which makes block boundaries the thing to get wrong
        for what, edits, want in (
            ("single byte",        [(500, b"Z")],                 [(500, 1)]),
            ("adjacent bytes",     [(500, b"ZZZZ")],              [(500, 4)]),
            ("two separate runs",  [(10, b"ZZ"), (9000, b"ZZZ")], [(10, 2), (9000, 3)]),
            ("across a 4K block",  [(4094, b"ZZZZZZ")],           [(4094, 6)]),
            ("at offset zero",     [(0, b"ZZZ")],                 [(0, 3)]),
            ("at the very end",    [(9997, b"ZZZ")],              [(9997, 3)]),
        ):
            with self.subTest(what):
                a, b = self._pair(b"A" * 10000, edits)
                self.assertEqual(X.diff_images(a, b, chunk=8192, block=4096), want)

    def test_identical_files_report_nothing(self):
        a, b = self._pair(b"A" * 10000, [])
        self.assertEqual(X.diff_images(a, b), [])

    def test_a_longer_file_reports_the_tail(self):
        a = os.path.join(self.dir, "a.bin"); Path(a).write_bytes(b"A" * 100)
        b = os.path.join(self.dir, "b.bin"); Path(b).write_bytes(b"A" * 100 + b"B" * 5)
        self.assertEqual(X.diff_images(a, b), [(100, 5)])

    def test_offsets_resolve_to_named_fields(self):
        t = F.enemy_tables(1)
        hp_off = next(o for lbl, o, _w, _k in F.ENEMY_FIELDS if lbl == "HP")
        cases = [
            (t["stats"] + 6 * F.ENEMY_STRIDE + hp_off, "enemy stats", "record 6 HP"),
            (t["stats"] + 6 * F.ENEMY_STRIDE + F.ENEMY_NOZONE_OFF,
             "enemy stats", "record 6 NoZone"),
            (t["rewards"] + 40 * F.REWARD_STRIDE, "enemy rewards", "record 40 EXP"),
            # anchor on the named block, not skill_base() — the lowest-addressed
            # block is whichever one happens to sit first, and that changed once
            # the dual techs were located
            (next(b for b in F.skill_blocks(1) if b[0] == "ether")[1] + 6,
             "skill blocks", "skill 0 EP"),
            (next(b for b in F.skill_blocks(1) if b[0] == "dual tech")[1] + 10,
             "skill blocks", "skill 200 Power"),
        ]
        for off, region, what in cases:
            with self.subTest(hex(off)):
                self.assertEqual(X._locate(off, 1), (region, what))

    def test_an_offset_outside_every_known_table_is_unmapped(self):
        # the honest answer to "can this editor reproduce that mod?" is no for
        # anything here, so it must never be silently bucketed as understood
        self.assertEqual(X._locate(0x1FF8000, 1), (None, None))


class TestBreakability(unittest.TestCase):
    """Breakability is not just "does the record hold a sequence".

    +0x51 bit 3 (the guide's "Hit zone: None") makes an enemy unbreakable
    whatever its sequence bytes say — 15 retail records carry a perfectly
    hittable BB that the game never reads.
    """

    def test_the_composite_rule(self):
        for nozone, seq, want in (
            (0x00, "BB", True),    # zones on, has a sequence
            (0x08, "BB", False),   # zone targeting off => the BB is inert
            (0x00, "",   False),   # no sequence
            (0x08, "",   False),   # both
            (0x0C, "CB", False),   # bit 3 set alongside other bits
            (0x04, "CB", True),    # other bits set, bit 3 clear
        ):
            with self.subTest(nozone=hex(nozone), seq=seq):
                self.assertEqual(F.is_breakable(nozone, seq), want)

    def test_shortening_skips_records_it_cannot_affect(self):
        seqs = {0: "BB", 1: "CBB", 2: "CC"}
        plain = F.plan_break_shortening(seqs, 1)
        self.assertEqual([i for i, _o, _n in plain], [0, 1, 2])
        # record 1 has zone targeting off: trimming it would write bytes the
        # game never reads, so it must drop out of the plan entirely
        gated = F.plan_break_shortening(seqs, 1, nozone={0: 0, 1: 0x08, 2: 0})
        self.assertEqual([i for i, _o, _n in gated], [0, 2])

    def test_enemy_type_decodes(self):
        self.assertEqual(F.enemy_type_text(0x62), "Mechanism")   # bits 0-1 = 2
        self.assertEqual(F.enemy_type_text(0x20), "Bio")
        self.assertEqual(F.enemy_type_text(0x21), "Gnosis")
        self.assertIn("type 3", F.enemy_type_text(0x03))         # never guessed

    def test_retail_unbreakable_set_is_bigger_than_the_sequence_bytes_suggest(self):
        cat = F.enemy_catalog()
        def seq(r):
            return F.decode_break_seq([r[f"brk{n + 1}"] for n in range(F.BREAK_SEQ_SLOTS)])
        no_seq = [i for i, r in cat.items() if not seq(r)]
        unbreakable = [i for i, r in cat.items() if not F.is_breakable(r["nozone"], seq(r))]
        self.assertEqual(len(no_seq), 16, "records with no sequence bytes")
        self.assertEqual(len(unbreakable), 36, "records the game will not let you break")
        self.assertTrue(set(no_seq) < set(unbreakable),
                        "the sequence-less records are a strict subset")


class TestRetailBaseline(unittest.TestCase):
    """The root cause: a field became writable without becoming comparable."""

    def test_every_writable_field_has_a_catalog_key(self):
        writable = {f[0] for f in (F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS
                                   + F.ZONE_FIELDS + F.STATUS_RES_FIELDS
                                   + F.REWARD_FIELDS + F.DROP_FIELDS)}
        missing = sorted(writable - set(F.ENEMY_CATALOG_KEY))
        self.assertEqual(missing, [],
                         "these fields can be edited but not compared against "
                         "retail, so the editor would call a modified disc clean")

    def test_every_catalog_record_carries_every_key(self):
        catalog = F.enemy_catalog()
        self.assertEqual(len(catalog), F.ENEMY_COUNT)
        keys = set(F.ENEMY_CATALOG_KEY.values())
        for i, rec in catalog.items():
            with self.subTest(i):
                self.assertEqual(sorted(keys - set(rec)), [],
                                 f"record {i} ({rec.get('name','?')}) is missing "
                                 f"retail values — rerun gen_enemy_catalog.py")


class TestRetailComparison(PatchCase):
    def test_a_retail_disc_reports_no_differences(self):
        with X.Iso(self.iso) as iso:
            self.assertEqual(X.diff_vanilla(iso), {})
        self.assertIn("matches the retail", self.run_cli("diff", self.iso))

    def test_a_single_edit_is_the_only_difference(self):
        p = self.fresh("one.iso")
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {"HP": 1111})
        with X.Iso(p) as iso:
            delta = X.diff_vanilla(iso)
        self.assertEqual(list(delta), [6])
        self.assertEqual(delta[6], {"HP": (1111, 22400)})

    def test_every_writable_block_is_compared(self):
        # The comparison used to cover only the eleven guide-verified numbers, so
        # a disc could have its bosses retuned and still report "matches retail".
        # One representative field from each block that used to be exempt.
        cases = {
            "affinity":   {F.AFFINITY_ELEMENTS[0]: 0},
            "break":      {"Brk2": 0},
            "zone mask":  {"Zones": 7},
            "resistance": {F.STATUS_RES_NAMES[0]: 99},
            "drop":       {"DropRate": 3},
        }
        for what, edit in cases.items():
            with self.subTest(what):
                p = self.fresh(f"diff-{what.split()[0]}.iso")
                with X.Iso(p, write=True) as iso:
                    X.write_enemy(iso, 6, edit)
                with X.Iso(p) as iso:
                    delta = X.diff_vanilla(iso)
                self.assertEqual(list(delta), [6], f"{what} edit went unreported")
                self.assertEqual(set(delta[6]), set(edit))

    def test_shortening_a_break_sequence_is_reported_and_restorable(self):
        # the whole point: this is the edit the editor makes in bulk
        p = self.fresh("brk.iso")
        with X.Iso(p) as iso:
            before = X.break_seq_of(X.read_enemy(iso, 6))
        self.assertGreater(len(before), 1, "fixture needs a multi-hit sequence")
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {f"Brk{len(before)}": 0})
        with X.Iso(p) as iso:
            self.assertEqual(X.break_seq_of(X.read_enemy(iso, 6)), before[:-1])
            self.assertIn(6, X.diff_vanilla(iso))
        self.run_cli("restore", p)
        with X.Iso(p) as iso:
            self.assertEqual(X.break_seq_of(X.read_enemy(iso, 6)), before)
            self.assertEqual(X.diff_vanilla(iso), {})

    def test_restore_puts_retail_values_back(self):
        p = self.fresh("restore.iso")
        self.run_cli("rebalance", p, "--profile", "faster")
        with X.Iso(p) as iso:
            self.assertTrue(X.diff_vanilla(iso))
        self.run_cli("restore", p)
        with X.Iso(p) as iso:
            self.assertEqual(X.diff_vanilla(iso), {})

    def test_a_retail_disc_passes_the_pristine_check(self):
        # the same question diff_vanilla() answers, via the fast path the
        # rebalance guard uses — they must agree
        with X.Iso(self.iso) as iso:
            self.assertTrue(X.disc_is_pristine(iso))
        p = self.fresh("dirty.iso")
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {"SP": 1})       # rewards only
        with X.Iso(p) as iso:
            self.assertFalse(X.disc_is_pristine(iso),
                             "a reward-only edit must trip the guard too")
            self.assertTrue(X.diff_vanilla(iso))

    def test_restore_can_target_named_records(self):
        p = self.fresh("restore2.iso")
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {"HP": 5})
            X.write_enemy(iso, 7, {"HP": 5})
        self.run_cli("restore", p, "--only", "6")
        with X.Iso(p) as iso:
            self.assertEqual(X.read_enemy(iso, 6)["HP"], 22400)
            self.assertEqual(X.read_enemy(iso, 7)["HP"], 5)

    def test_restore_dry_run_writes_nothing(self):
        p = self.fresh("restore3.iso")
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {"HP": 5})
        before = Path(p).read_bytes()
        out = self.run_cli("restore", p, "--dry-run")
        self.assertIn("dry run", out)
        self.assertEqual(Path(p).read_bytes(), before)


class TestPatchFiles(PatchCase):
    def test_export_then_apply_reproduces_the_disc(self):
        source = self.fresh("source.iso")
        self.run_cli("rebalance", source, "--profile", "faster")
        out = os.path.join(self.dir, "mod.json")
        self.run_cli("export-patch", source, "--out", out, "--note", "half HP")

        target = self.fresh("target.iso")
        self.run_cli("apply-patch", target, out)
        with X.Iso(source) as a, X.Iso(target) as b:
            for i in range(F.ENEMY_COUNT):
                self.assertEqual(X.read_enemy(a, i), X.read_enemy(b, i),
                                 f"record {i} differs after applying the patch")

    def test_exported_document_is_well_formed(self):
        p = self.fresh("exp.iso")
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {"HP": 999, "EXP": 1})
        out = os.path.join(self.dir, "small.json")
        self.run_cli("export-patch", p, "--out", out, "--note", "tiny")
        doc = json.loads(Path(out).read_text())
        self.assertEqual(doc["format"], X.PATCH_FORMAT)
        self.assertEqual(doc["version"], X.PATCH_VERSION)
        self.assertEqual(doc["note"], "tiny")
        self.assertEqual(doc["game"], "SLUS-20892")
        self.assertEqual(doc["edits"], {"6": {"HP": 999, "EXP": 1}})

    def test_apply_dry_run_writes_nothing(self):
        p = self.fresh("dry.iso")
        out = os.path.join(self.dir, "d.json")
        with open(out, "w") as f:
            json.dump(X.make_patch({6: {"HP": 3}}, note="n"), f)
        before = Path(p).read_bytes()
        self.assertIn("dry run", self.run_cli("apply-patch", p, out, "--dry-run"))
        self.assertEqual(Path(p).read_bytes(), before)

    def test_patches_can_carry_affinities(self):
        p = self.fresh("affpatch.iso")
        out = os.path.join(self.dir, "aff.json")
        with open(out, "w") as f:
            json.dump(X.make_patch({6: {F.AFFINITY_ELEMENTS[0]: F.affinity_byte(0),
                                        F.AFFINITY_ELEMENTS[1]: F.affinity_byte(250)}}), f)
        self.run_cli("apply-patch", p, out)
        with X.Iso(p) as iso:
            rec = X.read_enemy(iso, 6)
        self.assertEqual((F.affinity_pct(rec[F.AFFINITY_ELEMENTS[0]]),
                          F.affinity_pct(rec[F.AFFINITY_ELEMENTS[1]])), (0, 250))

    def test_round_trip_through_make_and_parse(self):
        edits = {6: {"HP": 1, "EXP": 2}, 100: {F.AFFINITY_ELEMENTS[3]: 3}}
        self.assertEqual(X.parse_patch(X.make_patch(edits)), edits)

    def test_rejects_malformed_patches(self):
        bad = [
            ({}, "not a"),
            ({"format": "something-else", "version": 1, "edits": {}}, "not a"),
            ({"format": X.PATCH_FORMAT, "version": 99, "edits": {"6": {"HP": 1}}},
             "not supported"),
            ({"format": X.PATCH_FORMAT, "version": 1, "edits": {}}, "no edits"),
            ({"format": X.PATCH_FORMAT, "version": 1, "edits": {"6": {"LUCK": 1}}},
             "unknown field"),
            ({"format": X.PATCH_FORMAT, "version": 1, "edits": {"9999": {"HP": 1}}},
             "outside"),
            ({"format": X.PATCH_FORMAT, "version": 1, "edits": {"-1": {"HP": 1}}},
             "outside"),
            ({"format": X.PATCH_FORMAT, "version": 1, "edits": {"six": {"HP": 1}}},
             "not a number"),
            ({"format": X.PATCH_FORMAT, "version": 1, "edits": {"6": {"HP": "lots"}}},
             "whole number"),
            ({"format": X.PATCH_FORMAT, "version": 1, "edits": {"6": {"HP": 1.5}}},
             "whole number"),
            ({"format": X.PATCH_FORMAT, "version": 1, "edits": {"6": {"HP": True}}},
             "whole number"),
            ({"format": X.PATCH_FORMAT, "version": 1, "edits": {"6": 5}}, "field map"),
        ]
        for doc, fragment in bad:
            with self.subTest(doc=doc):
                with self.assertRaises(ValueError) as cm:
                    X.parse_patch(doc)
                self.assertIn(fragment, str(cm.exception))

    def test_a_rejected_patch_writes_nothing(self):
        p = self.fresh("reject.iso")
        out = os.path.join(self.dir, "bad.json")
        with open(out, "w") as f:
            json.dump({"format": X.PATCH_FORMAT, "version": 1,
                       "edits": {"6": {"HP": 1}, "9999": {"HP": 2}}}, f)
        before = Path(p).read_bytes()
        self.run_cli("apply-patch", p, out, expect=1)
        self.assertEqual(Path(p).read_bytes(), before,
                         "a patch that fails validation must not half-apply")


class TestBattleCaptions(unittest.TestCase):
    """The `$zoom13;` caption pool: located by content, never by a constant.

    These run against a bare image rather than a fake disc on purpose — the
    locator's whole claim is that it needs no table, no base and no version
    check, so a test that handed it a well-formed disc would be testing less.
    """
    # deliberately awkward: a duplicated caption, one whose text also appears
    # unprefixed (the decoy a byte replace would eat), and one whose text is a
    # strict prefix of another's (the terminator is what tells them apart)
    PLAN = [(0x1000, "Miracle Star"), (0x2000, "Miracle Star"),
            (0x3000, "Annihilation"), (0x4000, "Medica"), (0x5000, "Medica 2")]
    DECOY = 0x6000

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="x2caption-")
        self.iso = os.path.join(self.dir, "captions.bin")
        with open(self.iso, "wb") as f:
            f.truncate(0x8000)
            for off, name in self.PLAN:
                f.seek(off)
                f.write(F.caption_needle(name))
            f.seek(self.DECOY)
            f.write(b"a line of prose naming Miracle Star without the prefix\x00")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_scan_finds_every_caption_and_nothing_else(self):
        with X.Iso(self.iso) as iso:
            got = X.scan_captions(iso)
        self.assertEqual(got, self.PLAN)

    def test_a_prefix_of_another_name_is_not_confused_for_it(self):
        with X.Iso(self.iso) as iso:
            texts = [t for _o, t in X.scan_captions(iso)]
        # "Medica" is a strict prefix of "Medica 2"; the NUL keeps them apart
        self.assertEqual(texts.count("Medica"), 1)
        self.assertEqual(texts.count("Medica 2"), 1)

    def test_spans_cover_the_blob_and_exclude_the_decoy(self):
        with X.Iso(self.iso) as iso:
            spans = X.caption_spans(iso)
        self.assertIn((0x1000, len(F.CAPTION_PREFIX) + len("Miracle Star") + 1),
                      spans)
        self.assertFalse(any(b <= self.DECOY < b + n for b, n in spans))

    def test_rewrite_is_in_place_padded_and_leaves_the_decoy_alone(self):
        with X.Iso(self.iso) as iso:
            before = iso.read(self.DECOY, 64)
            hits = [(o, t) for o, t in X.scan_captions(iso) if t == "Miracle Star"]
        with X.Iso(self.iso, write=True) as iso:
            self.assertEqual(X.rename_captions(iso, 34, "Flare", hits), 2)
        with X.Iso(self.iso) as iso:
            # padded to the retail budget, so no fragment of the old name shows
            self.assertEqual(iso.read(0x1000, 21),
                             F.CAPTION_PREFIX + b"Flare" + b"\x00" * 8)
            self.assertEqual(iso.read(self.DECOY, 64), before)

    def test_a_shortened_caption_can_be_restored_to_full_length(self):
        """The budget comes from the catalog, so shortening is not one-way."""
        with X.Iso(self.iso) as iso:
            hits = [(o, t) for o, t in X.scan_captions(iso) if t == "Miracle Star"]
        with X.Iso(self.iso, write=True) as iso:
            X.rename_captions(iso, 34, "Flare", hits)
        with X.Iso(self.iso) as iso:
            again = [(o, t) for o, t in X.scan_captions(iso) if t == "Flare"]
        with X.Iso(self.iso, write=True) as iso:
            X.rename_captions(iso, 34, "Miracle Star", again)
        with X.Iso(self.iso) as iso:
            self.assertEqual(iso.read(0x1000, 21), F.caption_needle("Miracle Star"))

    def test_an_oversized_name_is_refused_before_anything_is_written(self):
        with X.Iso(self.iso) as iso:
            hits = [(o, t) for o, t in X.scan_captions(iso) if t == "Medica"]
            before = iso.read(0x4000, 32)
        with X.Iso(self.iso, write=True) as iso:
            with self.assertRaises(SystemExit):
                X.rename_captions(iso, 0, "Grand Restoration", hits)
        with X.Iso(self.iso) as iso:
            self.assertEqual(iso.read(0x4000, 32), before)

    def test_the_caption_budget_is_computed_independently_of_the_name_budget(self):
        # equal for every active skill today, but they are separate pools and
        # the code must not assume "it fitted the name" means "it fits here"
        self.assertEqual(F.caption_budget("Miracle Star"), 13)
        self.assertEqual(X.caption_fits("Annihilation", "Angel's Rain"),
                         (True, 13, 13))
        self.assertEqual(X.caption_fits("Medica", "Medica 2")[0], False)


class TestSlotIdentity(unittest.TestCase):
    def test_icon_title_is_split_into_name_and_playtime(self):
        info = SV.parse_icon_sys(FX.icon_sys("XenosagaEPII-07[42:09]", nl=15))
        self.assertEqual(info["name"], "XenosagaEPII-07")
        self.assertEqual(info["playtime"], "42:09")

    def test_title_without_a_playtime_still_yields_a_name(self):
        info = SV.parse_icon_sys(FX.icon_sys("Just A Name", nl=0))
        self.assertEqual(info["name"], "Just A Name")
        self.assertEqual(info["playtime"], "")

    def test_non_icon_data_is_rejected(self):
        self.assertIsNone(SV.parse_icon_sys(b"\x00" * 964))
        self.assertIsNone(SV.parse_icon_sys(b"PS2D"))

    def test_thumbnail_is_trimmed_to_the_jpeg(self):
        gd = FX.gamedata()
        thumb = SV.thumbnail(gd)
        self.assertIsNotNone(thumb)
        self.assertTrue(thumb.startswith(b"\xff\xd8"))
        self.assertTrue(thumb.endswith(b"\xff\xd9"))
        self.assertLess(len(thumb), F.GD_THUMB_END - F.GD_THUMB_OFF)

    def test_no_thumbnail_when_the_region_is_not_a_jpeg(self):
        gd = bytearray(FX.gamedata())
        gd[F.GD_THUMB_OFF:F.GD_THUMB_OFF + 2] = b"\x00\x00"
        self.assertIsNone(SV.thumbnail(bytes(gd)))

    def test_slots_carry_identity_for_every_container(self):
        d = tempfile.mkdtemp(prefix="x2ident-")
        try:
            for name, blob in (("card.ps2", FX.x2_memcard(n_slots=2)),
                               ("a.psv", FX.psv()),
                               ("a.psu", FX.psu()),
                               ("a.sps", FX.sharkport()),
                               ("a.cbs", FX.cbs())):
                p = os.path.join(d, name)
                with open(p, "wb") as f:
                    f.write(blob)
                slots = SV.list_slots(p)
                self.assertTrue(slots, name)
                for s in slots:
                    with self.subTest(container=name, slot=s["slot"]):
                        self.assertTrue(s["name"].startswith("XenosagaEPII-"))
                        self.assertRegex(s["playtime"], r"^\d+:\d\d$")
                        self.assertEqual(s["label"], s["name"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
