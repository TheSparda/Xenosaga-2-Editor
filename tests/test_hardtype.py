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


if __name__ == "__main__":
    unittest.main()
