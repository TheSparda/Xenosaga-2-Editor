"""
Guards for the embedded HardType preset (web/hardtype.json).

The committed JSON is derived data — 528 byte-records per variant that the web
editor stages into a 4.6 GB disc image on one click — so the invariants worth
asserting are: every record lands wholly inside a table the editor maps, the
hex decodes, the counts the UI reports match the records, and (only on a
machine that has the gitignored mod PPFs) the committed file is not stale.
"""
import json
import os
import unittest

import fixtures as FX
import x2fields as F

HARDTYPE = os.path.normpath(os.path.join(FX.HERE, "..", "web", "hardtype.json"))
MOD_DIR = os.path.normpath(os.path.join(FX.HERE, "..", "Hard Mode Mod",
                                        "XS2HTv3.91945"))


def extents():
    """Mirrors gen_hardtype.extents() / the web bufferMap, text span last."""
    et = F.ENEMY_TABLES[1]
    return {
        "stats": (et["stats"], 125 * 0x5C),
        "rewards": (et["rewards"], 125 * F.REWARD_STRIDE),
        "skills": (F.skill_base(1), F.skill_span(1)),
        "units": (F.UNIT_TABLES[1], F.UNIT_COUNT * 92),
        "costs": (F.skill_cost_base(1), F.skill_cost_span(1)),
        "text": F.skill_text_span(1),
    }


class TestHardtypeJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(HARDTYPE) as f:
            cls.doc = json.load(f)

    def test_shape(self):
        self.assertEqual(self.doc["layout"], 1)
        self.assertEqual(set(self.doc["variants"]), {"normal", "hard"})
        for v in self.doc["variants"].values():
            self.assertTrue(v["label"])
            self.assertGreater(v["recordCount"], 0)

    def test_every_record_lands_in_a_mapped_table(self):
        ext = extents()
        for key, v in self.doc["variants"].items():
            self.assertEqual(len(v["records"]), v["recordCount"])
            total = 0
            for off, hexdata in v["records"]:
                data = bytes.fromhex(hexdata)
                self.assertGreater(len(data), 0, f"{key}: empty record at {off:#x}")
                total += len(data)
                inside = any(off >= a and off + len(data) <= a + s
                             for a, s in ext.values())
                self.assertTrue(inside,
                                f"{key}: record at {off:#x} (+{len(data)}) is "
                                f"outside every mapped table")
            self.assertEqual(total, v["byteCount"],
                             f"{key}: byteCount disagrees with the records")

    def test_variants_actually_differ(self):
        n = self.doc["variants"]["normal"]["records"]
        h = self.doc["variants"]["hard"]["records"]
        self.assertNotEqual(n, h, "normal and hard stage identical bytes")

    @unittest.skipUnless(os.path.isdir(MOD_DIR),
                         "mod PPFs not present (gitignored)")
    def test_committed_file_is_fresh(self):
        import gen_hardtype
        with open(HARDTYPE) as f:
            committed = f.read()
        self.assertEqual(committed, gen_hardtype.render(),
                         "web/hardtype.json is stale — run Editor/gen_hardtype.py")


class TestPassiveTableGeometry(unittest.TestCase):
    """The passive table is addressed through the TEXT buffer, not one of its
    own, so the invariant that actually matters is containment: if it ever
    slipped outside the span a front-end holds, every passive write would land
    somewhere unrelated in a 4.6 GB image."""

    def test_sits_inside_the_skill_text_span(self):
        for disc in (1, 2):
            lo, length = F.skill_text_span(disc)
            base = F.passive_base(disc)
            end = base + (F.PASSIVE_COUNT + F.PASSIVE_TAIL_COUNT) * F.PASSIVE_STRIDE
            self.assertGreaterEqual(base, lo, f"disc {disc}: passive table starts "
                                              f"before the text span")
            self.assertLessEqual(end, lo + length, f"disc {disc}: passive table "
                                                   f"runs past the text span")

    def test_disc2_keeps_the_documented_shift(self):
        self.assertEqual(F.passive_base(1) - F.passive_base(2), 0x800)

    def test_fields_fit_the_record(self):
        for label, off, width, _kind in F.PASSIVE_FIELDS:
            self.assertLessEqual(off + width, F.PASSIVE_STRIDE,
                                 f"PASSIVE_FIELDS.{label} runs past the record")
        self.assertLess(F.PASSIVE_KIND_OFF, F.PASSIVE_STRIDE)

    def test_exposed_indices_exist_in_the_skill_catalog(self):
        cat = F.skill_catalog()
        for i in F.passive_indices():
            entry = cat.get(i) if i in cat else cat.get(str(i))
            self.assertIsNotNone(entry, f"passive index {i} has no catalog entry")
            self.assertIsNotNone(entry.get("nameOff"),
                                 f"passive index {i} has no name to rename")

    def test_record_offsets_are_bounded(self):
        self.assertIsNone(F.passive_record_off(1, F.PASSIVE_TEXT0 - 1))
        self.assertIsNone(F.passive_record_off(1, F.PASSIVE_TEXT0 + F.PASSIVE_COUNT))
        self.assertEqual(F.passive_record_off(1, F.PASSIVE_TEXT0), F.passive_base(1))


class TestGearTable(unittest.TestCase):
    """The E.S. accessory tail. Its risk is the id map: a wrong record->catalog
    id would show the user one accessory's name over another's effect bytes,
    which is worse than showing nothing."""

    def test_starts_where_the_passive_records_end(self):
        for disc in (1, 2):
            self.assertEqual(F.gear_base(disc),
                             F.passive_base(disc) + F.PASSIVE_COUNT * F.PASSIVE_STRIDE)

    def test_sits_inside_the_skill_text_span(self):
        for disc in (1, 2):
            lo, length = F.skill_text_span(disc)
            end = F.gear_base(disc) + F.GEAR_COUNT * F.PASSIVE_STRIDE
            self.assertGreaterEqual(F.gear_base(disc), lo)
            self.assertLessEqual(end, lo + length)

    def test_es_id_map_is_dense_and_ordered(self):
        ids = [F.GEAR_ES_ID[k] for k in range(F.GEAR_COUNT)]
        named = [i for i in ids if i is not None]
        self.assertEqual(named, sorted(named), "catalog ids must ascend with the records")
        self.assertEqual(named, list(range(len(named))), "ids must be dense from 0")
        self.assertEqual(len(F.GEAR_ES_ID), F.GEAR_COUNT)

    def test_every_named_slot_exists_in_the_shipped_catalog(self):
        path = os.path.normpath(os.path.join(FX.HERE, "..", "Editor", "x2_es_equip.json"))
        with open(path) as f:
            es = json.load(f)
        for k in F.gear_indices():
            self.assertIn(str(F.GEAR_ES_ID[k]), es,
                          f"gear record {k} maps to a catalog id that does not exist")
        self.assertEqual(len(F.gear_indices()), len(es),
                         "every catalog entry should have exactly one record")

    def test_record_offsets_are_bounded(self):
        self.assertIsNone(F.gear_record_off(1, -1))
        self.assertIsNone(F.gear_record_off(1, F.GEAR_COUNT))
        self.assertEqual(F.gear_record_off(1, 0), F.gear_base(1))

    def test_does_not_overlap_the_passive_records(self):
        p_end = F.passive_base(1) + F.PASSIVE_COUNT * F.PASSIVE_STRIDE
        self.assertGreaterEqual(F.gear_record_off(1, 0), p_end)


class TestSkillCostTable(unittest.TestCase):
    """The skill-cost table is the one region whose disc-2 base is NOT -0x800.
    Its risks are that shift assumption and the (type, id) -> catalog mapping,
    which decides which skill's NAME sits over a cost the user then edits."""

    def test_disc2_is_its_own_base_not_a_shift(self):
        d1, d2 = F.skill_cost_base(1), F.skill_cost_base(2)
        self.assertNotEqual(d1 - d2, 0x800,
                            "disc 2's cost base is a distinct base, not the usual shift")
        self.assertEqual(d2 - d1, 0xB1800)

    def test_span_matches_the_record_count(self):
        for disc in (1, 2):
            self.assertEqual(F.skill_cost_span(disc),
                             F.SKILL_COST_COUNT * F.SKILL_COST_STRIDE)

    def test_fields_fit_the_record(self):
        for label, off, width, _kind in F.SKILL_COST_FIELDS:
            self.assertLessEqual(off + width, F.SKILL_COST_STRIDE,
                                 f"SKILL_COST_FIELDS.{label} runs past the record")
        for off in (F.SKILL_COST_TYPE_OFF, F.SKILL_COST_ID_OFF, F.SKILL_COST_SLOT_OFF):
            self.assertLess(off, F.SKILL_COST_STRIDE)

    def test_record_offsets_are_bounded(self):
        self.assertIsNone(F.skill_cost_record_off(1, -1))
        self.assertIsNone(F.skill_cost_record_off(1, F.SKILL_COST_COUNT))
        self.assertEqual(F.skill_cost_record_off(1, 0), F.skill_cost_base(1))

    def test_mapping_covers_both_id_spaces(self):
        # ether ids are catalog+1; the auto/equip band shares one space at +109
        self.assertEqual(F.skill_cost_catalog_index(2, 1), 0)
        self.assertEqual(F.skill_cost_catalog_index(0, 1), 110)
        self.assertEqual(F.skill_cost_catalog_index(1, 62), 171)
        # auto and equip must agree, since they share the id space
        self.assertEqual(F.skill_cost_catalog_index(0, 30),
                         F.skill_cost_catalog_index(1, 30))

    def test_mapping_refuses_to_guess(self):
        """An unknown type must return None, not a plausible index — the caller
        uses this to put a skill NAME on an editable cost."""
        self.assertIsNone(F.skill_cost_catalog_index(3, 1))
        self.assertIsNone(F.skill_cost_catalog_index(2, 0))
        self.assertIsNone(F.skill_cost_catalog_index(0, 0))

    def test_named_skills_exist_in_the_catalog(self):
        cat = F.skill_catalog()
        for type_, hi in ((2, 51), (0, 28), (1, 62)):
            for id_ in range(1, hi + 1):
                ci = F.skill_cost_catalog_index(type_, id_)
                self.assertIn(ci, cat,
                              f"type {type_} id {id_} maps outside the catalog")


if __name__ == "__main__":
    unittest.main()
