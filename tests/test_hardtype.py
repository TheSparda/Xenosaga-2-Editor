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
    et = F.ENEMY_TABLES[1]
    return {
        "stats": (et["stats"], 125 * 0x5C),
        "rewards": (et["rewards"], 125 * F.REWARD_STRIDE),
        "skills": (F.skill_base(1), F.skill_span(1)),
        "units": (F.UNIT_TABLES[1], F.UNIT_COUNT * 92),
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
        self.assertEqual(open(HARDTYPE).read(), gen_hardtype.render(),
                         "web/hardtype.json is stale — run Editor/gen_hardtype.py")


if __name__ == "__main__":
    unittest.main()
