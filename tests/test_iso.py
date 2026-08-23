"""
Disc-side tests: identification, and enemy stat/reward read-write against a
stand-in image that carries the real table offsets but none of the game.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import fixtures as FX
import x2fields as F
import x2patch as X

EDITOR = os.path.normpath(os.path.join(FX.HERE, "..", "Editor"))


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
        before = open(p, "rb").read()
        with X.Iso(p, write=True) as iso:
            n = X.write_enemy(iso, 6, {"HP": 11200, "EXP": 45000})
        self.assertEqual(n, 2)
        with X.Iso(p) as iso:
            rec = X.read_enemy(iso, 6)
        self.assertEqual(rec["HP"], 11200)
        self.assertEqual(rec["EXP"], 45000)
        self.assertEqual(rec["STR"], 85)                # untouched
        after = open(p, "rb").read()
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


class TestCli(IsoCase):
    def run_cli(self, *args, expect=0):
        r = subprocess.run([sys.executable, "x2patch.py", *args], cwd=EDITOR,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        return r.stdout

    def test_verify(self):
        out = self.run_cli("verify", self.iso)
        self.assertIn("[OK]", out)
        self.assertIn("SLUS-20892", out)

    def test_enemy_get(self):
        out = self.run_cli("enemy", self.iso, "6")
        self.assertIn("22,400", out)
        self.assertIn("30,000", out)

    def test_enemies_csv_has_a_row_per_record(self):
        out = self.run_cli("enemies", self.iso, "--csv").strip().splitlines()
        self.assertEqual(len(out), F.ENEMY_COUNT + 1)
        header = out[0].split(",")
        self.assertEqual(header[:3], ["idx", "name", "id"])
        for f in F.ENEMY_FIELDS + F.REWARD_FIELDS:
            self.assertIn(f[0], header)

    def test_enemy_set(self):
        p = self.fresh("cli-set.iso")
        out = self.run_cli("enemy-set", p, "6", "--set", "HP=1234", "--set", "SP=9")
        self.assertIn("wrote 2 field(s)", out)
        with X.Iso(p) as iso:
            rec = X.read_enemy(iso, 6)
        self.assertEqual(rec["HP"], 1234)
        self.assertEqual(rec["SP"], 9)

    def test_enemy_set_rejects_an_unknown_field(self):
        self.run_cli("enemy-set", self.fresh("cli-bad.iso"), "6", "--set", "LUCK=1",
                     expect=1)

    def test_rebalance_dry_run_writes_nothing(self):
        p = self.fresh("cli-dry.iso")
        before = open(p, "rb").read()
        out = self.run_cli("rebalance", p, "--hp", "50", "--dry-run")
        self.assertIn("dry run", out)
        self.assertEqual(open(p, "rb").read(), before)

    def test_rebalance_halves_hp_and_skips_bosses_by_default(self):
        p = self.fresh("cli-reb.iso")
        with X.Iso(p) as iso:
            before = {i: X.read_enemy(iso, i)["HP"] for i in range(F.ENEMY_COUNT)}
        self.run_cli("rebalance", p, "--hp", "50")
        with X.Iso(p) as iso:
            after = {i: X.read_enemy(iso, i)["HP"] for i in range(F.ENEMY_COUNT)}
        # the fixture numbers ids sequentially from 500, so BOSS_ID_MIN splits it
        boss_from = F.BOSS_ID_MIN - 500
        for i in range(F.ENEMY_COUNT):
            with self.subTest(record=i):
                if i < boss_from:
                    self.assertEqual(after[i], max(1, round(before[i] / 2)))
                else:
                    self.assertEqual(after[i], before[i], "boss was rescaled")

    def test_rebalance_bosses_flag_includes_them(self):
        p = self.fresh("cli-reb2.iso")
        last = F.ENEMY_COUNT - 1
        with X.Iso(p) as iso:
            before = X.read_enemy(iso, last)["HP"]
        self.run_cli("rebalance", p, "--hp", "50", "--bosses")
        with X.Iso(p) as iso:
            self.assertEqual(X.read_enemy(iso, last)["HP"], max(1, round(before / 2)))

    def test_rebalance_scales_rewards(self):
        p = self.fresh("cli-reb3.iso")
        with X.Iso(p) as iso:
            before = X.read_enemy(iso, 0)
        self.run_cli("rebalance", p, "--rewards", "200")
        with X.Iso(p) as iso:
            after = X.read_enemy(iso, 0)
        self.assertEqual(after["EXP"], before["EXP"] * 2)
        self.assertEqual(after["HP"], before["HP"])       # HP left at 100%

    def test_rebalance_at_100_percent_is_a_no_op(self):
        p = self.fresh("cli-noop.iso")
        before = open(p, "rb").read()
        out = self.run_cli("rebalance", p)
        self.assertIn("nothing to change", out)
        self.assertEqual(open(p, "rb").read(), before)


if __name__ == "__main__":
    unittest.main()
