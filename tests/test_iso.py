"""
Disc-side tests: identification, and enemy stat/reward read-write against a
stand-in image that carries the real table offsets but none of the game.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import fixtures as FX
import x2fields as F
import x2patch as X


class IsoCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="x2iso-")
        cls.iso = FX.write_fake_disc(os.path.join(cls.dir, "disc1.iso"), enemies={
            6: {"HP": 22400, "STR": 85, "VIT": 20, "EATK": 70, "EDEF": 45,
                "DEX": 70, "EVA": 72, "AGL": 12, "EXP": 30000, "SP": 1200, "CP": 0},
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def fresh(self, name="work.iso"):
        p = os.path.join(self.dir, name)
        shutil.copyfile(self.iso, p)
        return p


class TestIdentification(IsoCase):
    def test_recognizes_the_disc(self):
        with X.Iso(self.iso) as iso:
            ok, serial, disc, vol = X.check_version(iso)
        self.assertTrue(ok)
        self.assertEqual(serial, "SLUS-20892")
        self.assertEqual(disc, 1)
        self.assertEqual(vol, F.VOLUME_ID)

    def test_rejects_a_foreign_image(self):
        p = os.path.join(self.dir, "other.iso")
        with open(p, "wb") as f:
            f.truncate(0x20000)
            f.seek(0x8000 + 40)
            f.write(b"SOMETHING_ELSE".ljust(32))
        with X.Iso(p) as iso:
            ok, _serial, _disc, _vol = X.check_version(iso)
            self.assertFalse(ok)
            with self.assertRaises(SystemExit):
                X.require_version(iso)

    def test_find_spans_chunk_boundaries(self):
        # the needle sits far past the first 1 MiB chunk of the streaming search
        with X.Iso(self.iso) as iso:
            names = iso.find(b"XENOSAGA_II")
            self.assertEqual(names, 0x8000 + 40)
            deep = iso.find(b"\x80\x57\x00\x00", start=F.ENEMY_TABLE_OFF)  # HP 22400
            self.assertEqual(deep, F.ENEMY_TABLE_OFF + 6 * F.ENEMY_STRIDE + 0x36)


class TestEnemyTables(IsoCase):
    def test_reads_both_tables_for_one_record(self):
        with X.Iso(self.iso) as iso:
            rec = X.read_enemy(iso, 6)
        self.assertEqual(rec["HP"], 22400)
        self.assertEqual(rec["STR"], 85)
        self.assertEqual(rec["EVA"], 72)
        self.assertEqual(rec["EXP"], 30000)
        self.assertEqual(rec["SP"], 1200)
        self.assertEqual(rec["CP"], 0)

    def test_write_lands_and_is_surgical(self):
        p = self.fresh("surgical.iso")
        before = Path(p).read_bytes()
        with X.Iso(p, write=True) as iso:
            n = X.write_enemy(iso, 6, {"HP": 11200, "EXP": 45000})
        self.assertEqual(n, 2)
        with X.Iso(p) as iso:
            rec = X.read_enemy(iso, 6)
        self.assertEqual(rec["HP"], 11200)
        self.assertEqual(rec["EXP"], 45000)
        self.assertEqual(rec["STR"], 85)                # untouched
        after = Path(p).read_bytes()
        self.assertEqual(len(before), len(after))
        changed = {i for i in range(len(before)) if before[i] != after[i]}
        hp = F.ENEMY_TABLE_OFF + 6 * F.ENEMY_STRIDE + 0x36
        exp = F.REWARD_TABLE_OFF + 6 * F.REWARD_STRIDE
        allowed = set(range(hp, hp + 4)) | set(range(exp, exp + 4))
        # a subset, not an exact match: high bytes that were already 0 stay 0
        self.assertTrue(changed)
        self.assertEqual(changed - allowed, set(),
                         "write touched bytes outside the two target fields")

    def test_neighbouring_records_are_untouched(self):
        p = self.fresh("neighbour.iso")
        with X.Iso(p) as iso:
            five, seven = X.read_enemy(iso, 5), X.read_enemy(iso, 7)
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {f[0]: 1 for f in F.ENEMY_FIELDS + F.REWARD_FIELDS})
        with X.Iso(p) as iso:
            self.assertEqual(X.read_enemy(iso, 5), five)
            self.assertEqual(X.read_enemy(iso, 7), seven)

    def test_values_are_clamped_to_field_width(self):
        p = self.fresh("clamp.iso")
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 3, {"HP": 10 ** 12, "AGL": 5000, "SP": -4})
        with X.Iso(p) as iso:
            rec = X.read_enemy(iso, 3)
        self.assertEqual(rec["HP"], 0xFFFFFFFF)
        self.assertEqual(rec["AGL"], 0xFF)
        self.assertEqual(rec["SP"], 0)

    def test_none_values_are_skipped(self):
        p = self.fresh("skip.iso")
        with X.Iso(p) as iso:
            hp = X.read_enemy(iso, 9)["HP"]
        with X.Iso(p, write=True) as iso:
            self.assertEqual(X.write_enemy(iso, 9, {"HP": None, "STR": 42}), 1)
        with X.Iso(p) as iso:
            self.assertEqual(X.read_enemy(iso, 9)["HP"], hp)
            self.assertEqual(X.read_enemy(iso, 9)["STR"], 42)

    def test_read_only_handle_refuses_to_write(self):
        with X.Iso(self.iso) as iso:
            with self.assertRaises(IOError):
                X.write_enemy(iso, 0, {"HP": 1})

    def test_every_record_is_addressable(self):
        with X.Iso(self.iso) as iso:
            for i in (0, 1, F.ENEMY_COUNT // 2, F.ENEMY_COUNT - 1):
                self.assertIn("HP", X.read_enemy(iso, i))
        # the last record must fit inside the image
        end = F.ENEMY_TABLE_OFF + F.ENEMY_COUNT * F.ENEMY_STRIDE
        self.assertLessEqual(end, os.path.getsize(self.iso))


class TestEngineEntryPoints(IsoCase):
    """What the removed command line used to cover, called directly.

    The CLI these went through is gone — `web/` is the only front end now — but
    the engine underneath it still backs the catalog generators and the browser's
    JS port, so the behaviour is still worth pinning. Argument parsing, help text
    and exit codes are deliberately NOT reproduced: those were the CLI's own.
    """

    def test_a_known_disc_identifies_itself(self):
        with X.Iso(self.iso) as iso:
            ok, serial, disc, vol = X.check_version(iso)
        self.assertTrue(ok)
        self.assertEqual(serial, "SLUS-20892")
        self.assertEqual(disc, 1)
        self.assertEqual(vol, F.VOLUME_ID)

    def test_one_record_reads_back_the_values_it_was_built_with(self):
        with X.Iso(self.iso) as iso:
            rec = X.read_enemy(iso, 6)
        self.assertEqual(rec["HP"], 22400)
        self.assertEqual(rec["EXP"], 30000)

    def test_every_record_carries_every_verified_field(self):
        want = {f[0] for f in F.ENEMY_FIELDS + F.REWARD_FIELDS}
        with X.Iso(self.iso) as iso:
            rows = [X.read_enemy(iso, i) for i in range(F.ENEMY_COUNT)]
        self.assertEqual(len(rows), F.ENEMY_COUNT)
        for i, rec in enumerate(rows):
            self.assertTrue(want <= set(rec), f"record {i} is missing {want - set(rec)}")

    def test_writing_two_fields_writes_exactly_those_two(self):
        p = self.fresh("eng-set.iso")
        with X.Iso(p, write=True) as iso:
            n = X.write_enemy(iso, 6, {"HP": 1234, "SP": 9})
        self.assertEqual(n, 2)
        with X.Iso(p) as iso:
            rec = X.read_enemy(iso, 6)
        self.assertEqual(rec["HP"], 1234)
        self.assertEqual(rec["SP"], 9)

    def test_planning_a_rebalance_writes_nothing(self):
        p = self.fresh("eng-dry.iso")
        before = Path(p).read_bytes()
        with X.Iso(p) as iso:
            plan = X.plan_rebalance(iso, F.PROFILES["faster"])
        self.assertTrue(plan)
        self.assertEqual(Path(p).read_bytes(), before)

    def test_every_profile_names_both_enemy_groups(self):
        self.assertTrue(F.PROFILES)
        for key, prof in F.PROFILES.items():
            self.assertTrue(set(prof) & {"regular", "major"}, key)

    def test_an_already_edited_disc_is_not_pristine(self):
        # this fixture's filler values do not match retail, so the guard that
        # stops a second scaling pass compounding the first should fire
        with X.Iso(self.iso) as iso:
            self.assertFalse(X.disc_is_pristine(iso))

    def test_applying_a_plan_scales_and_caps(self):
        p = self.fresh("eng-apply.iso")
        with X.Iso(p) as iso:
            before = X.read_enemy(iso, 0)
            plan = X.plan_rebalance(iso, F.PROFILES["grindcut"])
        wanted = dict(next((e for i, _n, _g, e in plan if i == 0), {}))
        with X.Iso(p, write=True) as iso:
            records, fields = X.apply_rebalance(iso, plan)
        self.assertEqual(records, len(plan))
        self.assertGreater(fields, 0)
        with X.Iso(p) as iso:
            after = X.read_enemy(iso, 0)
        for lbl, (old, new) in wanted.items():
            self.assertEqual(before[lbl], old, lbl)
            self.assertEqual(after[lbl], new, lbl)
            self.assertLessEqual(after[lbl], F.ENEMY_FIELD_CAPS.get(lbl, 1 << 32), lbl)


if __name__ == "__main__":
    unittest.main()
