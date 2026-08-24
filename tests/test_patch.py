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

    def test_affinities_are_not_compared(self):
        p = self.fresh("affdiff.iso")
        with X.Iso(p, write=True) as iso:
            X.write_enemy(iso, 6, {F.AFFINITY_ELEMENTS[0]: 0})
        with X.Iso(p) as iso:
            self.assertEqual(X.diff_vanilla(iso), {},
                             "affinities have no retail baseline to compare with")

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
