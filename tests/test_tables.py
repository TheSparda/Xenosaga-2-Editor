"""
Schema guards. Every number in x2fields.py is a byte offset that gets written
into somebody's save or 4.6 GB disc image, so the cheap structural invariants
are worth asserting: nothing overlaps, nothing runs past its record, the shipped
catalogs are the size the notes claim, and the generated copy the web ISO editor
reads has not drifted from the Python it came from.
"""
import json
import os
import subprocess
import sys
import unittest

import fixtures as FX
import x2fields as F

EDITOR = os.path.normpath(os.path.join(FX.HERE, "..", "Editor"))
WEB_TABLES = os.path.normpath(os.path.join(FX.HERE, "..", "web", "tables.json"))


def spans(fields):
    return [(off, off + width, label) for (label, off, width, *_r) in fields]


class TestFieldGeometry(unittest.TestCase):
    def assert_no_overlap(self, fields, stride, what):
        seen = {}
        for start, end, label in spans(fields):
            self.assertLessEqual(end, stride,
                                 f"{what}.{label} runs past the {stride}-byte record")
            self.assertGreaterEqual(start, 0)
            for b in range(start, end):
                self.assertNotIn(b, seen,
                                 f"{what}.{label} overlaps {seen.get(b)} at +{b:#x}")
                seen[b] = label

    def test_widths_are_writable(self):
        for fields in (F.CHAR_FIELDS, F.ES_EQUIP_FIELDS, F.ENEMY_FIELDS,
                       F.REWARD_FIELDS):
            for label, _off, width, _kind in fields:
                self.assertIn(width, (1, 2, 4), f"{label} has odd width {width}")

    def test_character_record(self):
        self.assert_no_overlap(F.CHAR_FIELDS + F.ES_EQUIP_FIELDS, F.CHAR_STRIDE, "char")

    def test_enemy_record(self):
        self.assert_no_overlap(F.ENEMY_FIELDS, F.ENEMY_STRIDE, "enemy")
        self.assertLess(F.ENEMY_ID_OFF + 2, F.ENEMY_STRIDE)
        for _s, e, label in spans(F.ENEMY_FIELDS):
            self.assertLessEqual(e, F.ENEMY_ID_OFF,
                                 f"enemy.{label} collides with the id field")

    def test_reward_row(self):
        self.assert_no_overlap(F.REWARD_FIELDS, F.REWARD_STRIDE, "reward")

    def test_character_table_fits_the_payload(self):
        end = F.CHAR_TABLE_OFF + F.CHAR_COUNT * F.CHAR_STRIDE
        self.assertLessEqual(end, F.GAMEDATA_SIZE)
        self.assertLess(F.GD_GOLD_OFF + 4, F.CHAR_TABLE_OFF)

    def test_caps_cover_every_editable_stat(self):
        editable = {l for (l, _o, _w, k) in F.CHAR_FIELDS if k == "num"}
        self.assertEqual(editable - set(F.CHAR_CAPS), set(),
                         "a stat has no cap, so 'max stats' would skip it")
        self.assertEqual(set(F.CHAR_CAPS) - editable, set(),
                         "a cap names a field that is not editable")
        for label, cap in F.CHAR_CAPS.items():
            width = next(w for (l, _o, w, _k) in F.CHAR_FIELDS if l == label)
            self.assertLessEqual(cap, (1 << (8 * width)) - 1,
                                 f"{label}'s cap does not fit its field")

    def test_sheet_columns_name_real_fields(self):
        labels = {l for (l, _o, _w, _k) in F.CHAR_FIELDS}
        for _header, key in F.SHEET_COLS:
            self.assertIn(key, labels)

    def test_roster_covers_every_record(self):
        self.assertEqual(set(F.ROSTER), set(range(F.CHAR_COUNT)))


class TestCatalogs(unittest.TestCase):
    def test_sizes_match_the_notes(self):
        self.assertEqual(len(F.consumable_catalog()), 36)
        self.assertEqual(len(F.keyitem_catalog()), 107)
        self.assertEqual(len(F.es_equip_catalog()), 31)
        self.assertEqual(len(F.enemy_catalog()), F.ENEMY_COUNT)

    def test_every_entry_has_a_name(self):
        for name, cat in (("consumables", F.consumable_catalog()),
                          ("key items", F.keyitem_catalog()),
                          ("E.S. gear", F.es_equip_catalog())):
            for i, v in cat.items():
                self.assertTrue(v.get("name"), f"{name} id {i} has no name")

    def test_es_gear_ids_start_at_zero_and_are_dense(self):
        self.assertEqual(sorted(F.es_equip_catalog()), list(range(31)))

    def test_bestiary_is_indexed_by_record(self):
        cat = F.enemy_catalog()
        self.assertEqual(sorted(cat), list(range(F.ENEMY_COUNT)))
        for i, v in cat.items():
            for key in ("name", "id", "hp", "str", "vit", "eatk", "edef",
                        "dex", "eva", "agl", "exp", "sp", "cp"):
                self.assertIn(key, v, f"record {i} is missing {key}")
            self.assertGreater(v["hp"], 0, f"record {i} has no HP")
            self.assertLessEqual(v["hp"], 0xFFFFFFFF)
            for key in ("dex", "eva", "agl"):
                self.assertLessEqual(v[key], 0xFF, f"record {i}.{key} exceeds a byte")

    def test_verified_anchors(self):
        # the anchors the table derivation was pinned on (see the notes)
        cat = F.enemy_catalog()
        self.assertEqual(cat[6]["name"], "Perun")
        self.assertEqual(cat[6]["hp"], 22400)
        self.assertEqual(cat[6]["exp"], 30000)
        self.assertEqual(cat[6]["sp"], 1200)
        self.assertEqual(cat[F.ENEMY_COUNT - 1]["hp"], 192000)   # Dark Erde Kaiser

    def test_boss_id_band_actually_splits_the_bestiary(self):
        ids = [v["id"] for v in F.enemy_catalog().values()]
        bosses = [i for i in ids if i >= F.BOSS_ID_MIN]
        self.assertTrue(0 < len(bosses) < len(ids),
                        "BOSS_ID_MIN classifies every record the same way")


class TestEncounterClass(unittest.TestCase):
    """The audited random / boss / super-boss split (x2fields ENCOUNTER CLASS).

    The tables are hand-curated from the game's boss listings, so what these
    checks defend is that they still describe the records they were written
    against — a catalog regeneration that shifted an index would otherwise
    silently re-point "Patriarch" at whatever moved into slot 110."""

    def test_every_entry_matches_the_catalog_record_it_names(self):
        cat = F.enemy_catalog()
        for table, cls in ((F.BOSS_RECORDS, "boss"),
                           (F.SUPERBOSS_RECORDS, "superboss")):
            for i, (eid, name, where) in table.items():
                rec = cat.get(i)
                self.assertIsNotNone(rec, f"{cls} index {i} is not in the catalog")
                self.assertEqual(rec["id"], eid, f"{cls} {i} ({name}) enemy id")
                self.assertEqual(rec["name"], name, f"{cls} {i} name")
                self.assertTrue(where, f"{cls} {i} ({name}) has no source note")
                self.assertFalse(F.is_dummy_record(rec),
                                 f"{cls} {i} ({name}) is a debug record")

    def test_the_three_classes_are_disjoint_and_cover_everything(self):
        self.assertEqual(set(F.BOSS_RECORDS) & set(F.SUPERBOSS_RECORDS), set())
        classes = F.encounter_classes()
        self.assertEqual(sorted(classes), list(range(F.ENEMY_COUNT)))
        self.assertLessEqual(set(classes.values()),
                             set(F.ENCOUNTER_CLASSES) | {"dummy"})
        for cls in F.ENCOUNTER_CLASSES:
            self.assertIn(cls, F.ENCOUNTER_LABELS)
            self.assertGreater(list(classes.values()).count(cls), 0,
                               f"no record is classified {cls}")

    def test_known_records_are_classified_the_way_the_guides_describe_them(self):
        # the cases the old HP>=20,000 heuristic got wrong, and one it got right
        cases = {
            94: "boss",        # Margulis, prologue — 1,200 HP
            111: "boss",       # Albedo in the Space-Time Anomaly — final boss
            103: "boss",       # Orgulla, Ormus Stronghold — 18,000 HP
            110: "boss",       # Patriarch — 21,600 HP
            13: "random",      # Arvakv — 22,000 HP Desert spawn, not a boss
            10: "superboss",   # Zwerg Kape, Heaven's Ruins — 4,160 HP
            124: "superboss",  # Dark Erde Kaiser, Space Coliseum
            65: "random",      # U-TIC Soldier A
            119: "dummy",      # BOS026
        }
        classes = F.encounter_classes()
        for i, want in cases.items():
            self.assertEqual(classes[i], want, f"record {i}")

    def test_profiles_cover_every_class(self):
        for key, prof in F.PROFILES.items():
            for cls in F.ENCOUNTER_CLASSES:
                self.assertIn(cls, prof, f"profile {key} has no {cls} row")
                for lbl, pct in prof[cls].items():
                    self.assertIn(lbl, F.ENEMY_FIELD_CAPS, f"{key}.{cls}.{lbl}")
                    self.assertGreater(pct, 0, f"{key}.{cls}.{lbl}")


class TestGeneratedWebTables(unittest.TestCase):
    def test_committed_file_matches_x2fields(self):
        with open(WEB_TABLES) as f:
            committed = json.load(f)
        self.assertEqual(committed, json.loads(json.dumps(F.web_tables())),
                         "web/tables.json is stale — run "
                         "python3 Editor/gen_web_tables.py")

    def test_generator_check_mode_agrees(self):
        r = subprocess.run([sys.executable, "gen_web_tables.py", "--check"],
                           cwd=EDITOR, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_generator_is_deterministic(self):
        sys.path.insert(0, EDITOR)
        import gen_web_tables as G
        self.assertEqual(G.render(), G.render())

    def test_web_iso_editor_has_no_second_copy_of_the_offsets(self):
        """The whole point of tables.json: iso.js must not hardcode offsets."""
        with open(os.path.join(FX.HERE, "..", "web", "iso.js")) as f:
            src = f.read()
        for value in (hex(F.ENEMY_TABLE_OFF), hex(F.REWARD_TABLE_OFF),
                      hex(F.ENEMY_NAMES_OFF)):
            self.assertNotIn(value.upper().replace("0X", "0x"), src,
                             f"{value} is hardcoded in iso.js again")
        self.assertIn("tables.json", src)




class TestRecordTail(unittest.TestCase):
    """The affinity and status-resistance blocks end past the nominal record, so
    the LAST record's fields live beyond count*stride. A front-end that slices a
    fixed buffer must add the tail — the web editor didn't, and rendered Dark
    Erde Kaiser's Ice/Pierce/Slash/Hit and every resistance as blank."""

    def test_tail_covers_every_overhanging_field(self):
        reach = max(off + w for (_l, off, w, _k) in
                    (F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS + F.ZONE_FIELDS
                     + F.STATUS_RES_FIELDS))
        self.assertEqual(F.enemy_record_tail(), max(0, reach - F.ENEMY_STRIDE))
        self.assertGreater(F.enemy_record_tail(), 0,
                           "a field really does overhang; a zero tail means the "
                           "field table changed and this guard went stale")

    def test_generated_tables_expose_it(self):
        with open(WEB_TABLES) as f:
            t = json.load(f)
        self.assertEqual(t["enemy"]["recordTail"], F.enemy_record_tail())

    def test_iso_js_reads_the_tail(self):
        with open(os.path.join(os.path.dirname(WEB_TABLES), "iso.js")) as f:
            src = f.read()
        self.assertIn("recordTail", src,
                      "iso.js must consume enemy.recordTail")
        self.assertRegex(
            src, r"COUNT\s*\*\s*STRIDE\s*\+\s*TAIL",
            "iso.js slices the stat table without adding the tail, so the last "
            "record's overhanging fields read past the buffer")


class TestSkillSpan(unittest.TestCase):
    """The web editor reads ONE span covering both skill blocks, because the two
    blocks are disjoint. If that span ever stops covering them, the panel would
    address the gap and write into whatever lives there."""

    def test_span_covers_every_block_on_both_discs(self):
        for disc in F.SKILL_BLOCKS:
            base, span = F.skill_base(disc), F.skill_span(disc)
            for name, b, count, _t0 in F.skill_blocks(disc):
                with self.subTest(disc=disc, block=name):
                    self.assertGreaterEqual(b, base)
                    self.assertLessEqual(b + count * F.SKILL_STRIDE, base + span)

    def test_span_is_the_same_on_both_discs(self):
        # one buffer size serves either disc only while this holds
        self.assertEqual(F.skill_span(1), F.skill_span(2))

    def test_every_editable_skill_has_retail_numerics(self):
        # the panel shows "differs from retail" per skill; without numerics in
        # the catalog it would silently report a match on a modified disc
        cat = F.skill_catalog()
        for i in F.skill_editable_indices(1):
            with self.subTest(i):
                self.assertIn(i, cat, f"skill {i} is editable but not in the catalog")
                self.assertTrue(cat[i].get("numeric"),
                                f"skill {i} has no retail numerics")


if __name__ == "__main__":
    unittest.main()
