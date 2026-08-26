"""
Container-level tests: every supported save wrapper must decode to the same
20,832-byte payload, and every write must land the intended fields, preserve the
container's shape, and touch nothing else.
"""
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

import fixtures as FX
import x2fields as F
import x2mc as MC
import x2save as SV
import x2lzari as LZ


class TempFileCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="x2test-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def put(self, name, blob):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(blob)
        return p


class TestEcc(unittest.TestCase):
    """The ECC table is reconstructed from the algorithm's structure, so pin the
    properties that make it that algorithm rather than just its output."""

    def test_table_head_matches_published_values(self):
        self.assertEqual(MC._ECC_TBL[:16],
                         [0x00, 0x87, 0x96, 0x11, 0xA5, 0x22, 0x33, 0xB4,
                          0xB4, 0x33, 0x22, 0xA5, 0x11, 0x96, 0x87, 0x00])

    def test_table_is_xor_linear(self):
        for a in (0x01, 0x13, 0x5A, 0xF0):
            for b in (0x02, 0x24, 0x7E, 0x0F):
                self.assertEqual(MC._ECC_TBL[a ^ b],
                                 MC._ECC_TBL[a] ^ MC._ECC_TBL[b])

    def test_bit7_is_byte_parity(self):
        for v in range(256):
            self.assertEqual(bool(MC._ECC_TBL[v] & 0x80), bin(v).count("1") % 2 == 1)

    def test_shape_and_masks(self):
        code = MC.ecc_page(bytes(range(256)) * 2)
        self.assertEqual(len(code), 12)
        for i in range(0, 12, 3):
            self.assertEqual(code[i] & ~0x77, 0)
            self.assertEqual(code[i + 1] & ~0x7F, 0)
            self.assertEqual(code[i + 2] & ~0x7F, 0)

    def test_detects_a_single_bit_flip(self):
        page = bytes((i * 37) & 0xFF for i in range(512))
        flipped = bytearray(page)
        flipped[200] ^= 0x08
        self.assertNotEqual(MC.ecc_page(page), MC.ecc_page(bytes(flipped)))


class TestMemcard(TempFileCase):
    def test_reads_the_filesystem(self):
        card = MC.Ps2Card(FX.x2_memcard(n_slots=3))
        self.assertEqual(card.pagesize, 512)
        self.assertEqual(card.cluster_size, 1024)
        self.assertEqual(card.spare, 16)
        names = sorted(d.name for d, _f in card.walk())
        self.assertIn("BASLUS-20892Xeno201", names)
        self.assertIn("BASLUS-20892Xeno203", names)
        self.assertIn("BASLUS-21118OtherGame", names)

    def test_raw_and_ecc_images_read_identically(self):
        gd_ecc = SV.extract_gamedata(self.put("card.ps2", FX.x2_memcard(ecc=True)))
        gd_raw = SV.extract_gamedata(self.put("raw.ps2", FX.x2_memcard(ecc=False)))
        self.assertEqual(gd_ecc, gd_raw)
        self.assertEqual(len(gd_ecc), F.GAMEDATA_SIZE)

    def test_fixture_ecc_is_verified_by_the_reader(self):
        # The fixture writes ECC with the same code the reader checks, so this
        # asserts self-consistency, not correctness against hardware.
        self.assertEqual(MC.Ps2Card(FX.x2_memcard()).ecc_mode(), "verified")

    def test_blank_spare_is_reported_absent_not_mismatched(self):
        card = MC.Ps2Card(FX.memcard([("BASLUS-20892Xeno201", {"a": b"\x01" * 64})],
                                     ecc=True, spare_fill=b"\x00" * 4))
        # overwrite every stored code with zeros -> "the image carries no ECC"
        for p in range(64):
            o = card._page_off(p) + card.pagesize
            card.data[o:o + 12] = b"\x00" * 12
        self.assertEqual(card.ecc_mode(range(64)), "absent")

    def test_refuses_to_write_when_ecc_cannot_be_reproduced(self):
        blob = bytearray(FX.x2_memcard())
        card = MC.Ps2Card(blob)
        folder, ent = SV._card_slots(card)[0]
        page = card.file_spans(ent)[0][2]
        o = card._page_off(page) + card.pagesize
        card.data[o:o + 12] = b"\x5a" * 12               # plausible but wrong code
        with self.assertRaises(ValueError) as cm:
            card.write_file(ent, FX.gamedata(gold=1))
        self.assertIn("error-correcting", str(cm.exception))

    def test_slot_selection(self):
        p = self.put("card.ps2", FX.x2_memcard(n_slots=3, gold=1000))
        slots = SV.list_slots(p)
        self.assertEqual([s["folder"] for s in slots],
                         ["BASLUS-20892Xeno201", "BASLUS-20892Xeno202",
                          "BASLUS-20892Xeno203"])
        for i in range(3):
            self.assertEqual(SV.decode_save(p, slot=i)["gold"], 1000 + i)
        with self.assertRaises(IndexError):
            SV.extract_gamedata(p, slot=3)

    def test_write_hits_only_the_named_slot(self):
        p = self.put("card.ps2", FX.x2_memcard(n_slots=3, gold=1000))
        before = Path(p).read_bytes()
        SV.write_save(p, {"gold": 55555}, make_backup=False, slot=1)
        after = Path(p).read_bytes()
        self.assertEqual(len(before), len(after))
        self.assertEqual(SV.decode_save(p, slot=0)["gold"], 1000)
        self.assertEqual(SV.decode_save(p, slot=1)["gold"], 55555)
        self.assertEqual(SV.decode_save(p, slot=2)["gold"], 1002)
        # the gold field lives in one page; its ECC is the only collateral change
        changed = [i for i in range(len(before)) if before[i] != after[i]]
        self.assertTrue(changed)
        span = max(changed) - min(changed)
        self.assertLess(span, 600, f"write spread over {span} bytes; expected one page")

    def test_unformatted_image_is_rejected(self):
        with self.assertRaises(ValueError):
            MC.Ps2Card(b"\x00" * 4096)

    def test_card_without_an_x2_save_reports_no_slots(self):
        p = self.put("other.ps2", FX.memcard([("BASLUS-21118OtherGame",
                                              {"OtherGame": b"\x01" * 2048})]))
        self.assertEqual(SV.list_slots(p), [])
        with self.assertRaises(ValueError):
            SV.extract_gamedata(p)


class TestAllContainers(TempFileCase):
    """Format-independent guarantees. Each entry is (filename, builder)."""

    CASES = [
        ("save.psv", FX.psv),
        ("save.sps", FX.sharkport),
        ("save.cbs", FX.cbs),
        ("save.psu", FX.psu),
        ("card.ps2", lambda gd: FX.memcard([(FX.FOLDER, {
            "icon.sys": FX.icon_sys(), FX.FOLDER: gd, "system.ico": b"\x55" * 1800})])),
    ]

    def test_sniffed_format_is_stable(self):
        expected = {"save.psv": "psv", "save.sps": "sharkport", "save.cbs": "cbs",
                    "save.psu": "psu", "card.ps2": "memcard"}
        for name, build in self.CASES:
            with self.subTest(name):
                p = self.put(name, build(FX.gamedata()))
                self.assertEqual(SV.sniff_format(p), expected[name])

    def test_every_container_yields_the_same_payload(self):
        gd = FX.gamedata(gold=987654)
        for name, build in self.CASES:
            with self.subTest(name):
                p = self.put(name, build(gd))
                self.assertEqual(SV.extract_gamedata(p), gd)

    def test_decode_matches_the_payload_we_built(self):
        gd = FX.gamedata(gold=424242)
        for name, build in self.CASES:
            with self.subTest(name):
                d = SV.decode_save(self.put(name, build(gd)))
                self.assertEqual(d["gold"], 424242)
                active = [c for c in d["characters"] if c["active"]]
                self.assertEqual(len(active), 10)          # 7 on-foot + 3 E.S.
                self.assertEqual(d["characters"][0]["name"], "chaos")
                self.assertEqual(d["characters"][0]["Level"], 41)
                self.assertEqual(d["characters"][1]["HP"], 1450)
                self.assertTrue(d["characters"][10]["is_es"])
                self.assertEqual(d["characters"][10]["Slot 1"], 3)

    def test_round_trip_write_then_read(self):
        edits = {"gold": 7654321,
                 "characters": {2: {"Level": 99, "HP": 9999, "Str": 999}}}
        for name, build in self.CASES:
            with self.subTest(name):
                p = self.put(name, build(FX.gamedata()))
                SV.write_save(p, edits, make_backup=False)
                d = SV.decode_save(p)
                self.assertEqual(d["gold"], 7654321)
                self.assertEqual(d["characters"][2]["Level"], 99)
                self.assertEqual(d["characters"][2]["HP"], 9999)
                self.assertEqual(d["characters"][2]["Str"], 999)
                # untouched neighbours survived
                self.assertEqual(d["characters"][1]["HP"], 1450)
                self.assertEqual(d["characters"][3]["Level"], 39)

    def test_backup_is_written_once_and_keeps_the_original(self):
        for name, build in self.CASES:
            with self.subTest(name):
                original = build(FX.gamedata(gold=111))
                p = self.put(name, original)
                SV.write_save(p, {"gold": 222}, make_backup=True)
                self.assertEqual(Path(p + ".bak").read_bytes(), original)
                SV.write_save(p, {"gold": 333}, make_backup=True)
                self.assertEqual(Path(p + ".bak").read_bytes(), original)
                self.assertEqual(SV.decode_save(p)["gold"], 333)

    def test_container_size_is_preserved(self):
        # cbs is legitimately re-compressed; everything else is patched in place.
        for name, build in self.CASES:
            if name.endswith(".cbs"):
                continue
            with self.subTest(name):
                p = self.put(name, build(FX.gamedata()))
                before = os.path.getsize(p)
                SV.write_save(p, {"gold": 1}, make_backup=False)
                self.assertEqual(os.path.getsize(p), before)


class TestPayloadEdits(unittest.TestCase):
    def test_values_are_clamped_to_field_width(self):
        gd = SV.apply_edits(FX.gamedata(), {
            "gold": 2 ** 40,
            "characters": {0: {"Level": 5000, "HP": 200000, "Dex": -7}}})
        d = SV.decode_gamedata(gd)
        self.assertEqual(d["gold"], 0xFFFFFFFF)
        self.assertEqual(d["characters"][0]["Level"], 0xFF)
        self.assertEqual(d["characters"][0]["HP"], 0xFFFF)
        self.assertEqual(d["characters"][0]["Dex"], 0)

    def test_edits_are_surgical(self):
        """Only the targeted byte moves — plus the checksum, which must move."""
        gd = SV.fix_checksum(FX.gamedata())
        new = SV.apply_edits(gd, {"characters": {4: {"Level": 77}}})
        self.assertEqual(len(new), len(gd))
        ck = list(range(F.GD_CHECKSUM_OFF,
                        F.GD_CHECKSUM_OFF + F.GD_CHECKSUM_WIDTH))
        diff = [i for i in range(len(gd)) if gd[i] != new[i]]
        target = F.CHAR_TABLE_OFF + 4 * F.CHAR_STRIDE + 0x13
        self.assertEqual([i for i in diff if i not in ck], [target])

    def test_checksum_is_recomputed(self):
        gd = FX.gamedata(checksum=0x12345678)
        new = SV.apply_edits(gd, {"gold": 9})
        self.assertTrue(SV.CHECKSUM_KNOWN)
        self.assertNotEqual(struct.unpack_from("<Q", new, 8)[0], 0x12345678)
        self.assertTrue(SV.checksum_ok(new))

    def test_checksum_matches_the_game_on_retail_saves(self):
        """Untouched saves must already verify — they were written by the game,
        so a mismatch means our routine is wrong, not the save.

        Saves/ is gitignored (personal + copyrighted), so this is a local-only
        check and skips on CI rather than pretending to have run."""
        root = Path(__file__).resolve().parent.parent / "Saves"
        paths = [p for p in sorted(root.rglob("*.PSV"))
                 if "EDITED" not in p.name.upper()]   # pre-crack editor output
        if not paths:
            self.skipTest("no local retail saves to check against")
        for p in paths:
            self.assertTrue(SV.checksum_ok(SV.extract_gamedata(str(p))),
                            f"stored checksum disagrees with ours: {p.name}")

    def test_checksum_is_position_weighted(self):
        """Swapping two bytes must change it — the property a plain sum lacks,
        and the reason the plain-sum searches all came back empty."""
        gd = bytearray(SV.fix_checksum(FX.gamedata()))
        a, b = F.GD_GOLD_OFF, F.GD_GOLD_OFF + 1
        self.assertNotEqual(gd[a], gd[b])
        gd[a], gd[b] = gd[b], gd[a]
        self.assertFalse(SV.checksum_ok(bytes(gd)))

    def test_granting_the_secret_keys_touches_only_their_flags(self):
        gd = SV.fix_checksum(FX.gamedata())
        new = SV.apply_edits(gd, {"inventory": {
            "keyItems": {i: 1 for i in F.secret_key_ids()}}})
        self.assertTrue(SV.checksum_ok(new))
        held = SV.decode_gamedata(new)["inventory"]["keyItems"]
        self.assertTrue(all(held[i] == 1 for i in F.secret_key_ids()))
        ck = range(F.GD_CHECKSUM_OFF, F.GD_CHECKSUM_OFF + F.GD_CHECKSUM_WIDTH)
        body = [i for i in range(len(gd)) if gd[i] != new[i] and i not in ck]
        lo = F.INV_KEYITEM_OFF + 2 * F.SECRET_KEY_FIRST
        hi = lo + 2 * F.SECRET_KEY_COUNT
        self.assertTrue(all(lo <= i < hi for i in body), body[:8])

    def test_out_of_range_record_is_refused(self):
        with self.assertRaises(IndexError):
            SV.apply_edits(FX.gamedata(), {"characters": {F.CHAR_COUNT: {"Level": 1}}})

    def test_unknown_field_is_refused(self):
        with self.assertRaises(KeyError):
            SV.apply_edits(FX.gamedata(), {"characters": {0: {"Luck": 1}}})

    def test_wrong_payload_size_is_refused(self):
        with self.assertRaises(ValueError):
            SV.decode_gamedata(b"\x00" * 100)
        with self.assertRaises(ValueError):
            SV.apply_edits(b"\x00" * 100, {"gold": 1})


class TestPlaytime(unittest.TestCase):
    """The elapsed-time struct at 0x68 — the number the load screen shows."""

    def test_decodes_hours_and_minutes(self):
        gd = FX.gamedata(playtime=(30, 18))
        self.assertEqual(SV.decode_gamedata(gd)["playtime"]["text"], "30:18")

    def test_hours_past_a_day_roll_into_the_day_byte(self):
        # 27:36 is stored as day 2, hour 3 — the trap that makes a naive
        # hour-only read say 3:36
        gd = FX.gamedata(playtime=(27, 36))
        self.assertEqual(gd[F.GD_PLAYTIME_OFF + 4], 2)      # day
        self.assertEqual(gd[F.GD_PLAYTIME_OFF + 3], 3)      # hour
        self.assertEqual(SV.decode_gamedata(gd)["playtime"]["hours"], 27)

    def test_editing_it_keeps_the_seconds_the_save_had(self):
        gd = FX.gamedata(playtime=(1, 2))                   # fixture sets sec=42
        new = SV.apply_edits(gd, {"playtime": {"hours": 9, "minutes": 5}})
        self.assertEqual(SV.decode_gamedata(new)["playtime"]["text"], "9:05")
        self.assertEqual(new[F.GD_PLAYTIME_OFF + 1], 42)


class TestGrowthBlock(unittest.TestCase):
    """EXP, the two point pools and the learned-skill masks at 0x2274."""

    def test_points_decode_per_character(self):
        d = SV.decode_gamedata(FX.gamedata())
        self.assertEqual(d["characters"][0]["EXP"], 250000)
        self.assertEqual(d["characters"][0]["Skill Points"], 3150)
        self.assertEqual(d["characters"][0]["Class Points"], 900)
        self.assertEqual(d["characters"][1]["Skill Points"], 0)

    def test_learned_masks_decode_to_catalog_indices(self):
        d = SV.decode_gamedata(FX.gamedata())
        self.assertEqual(d["characters"][0]["ether"], [0, 2, 5])
        self.assertEqual(d["characters"][0]["skills"], [110, 141])

    def test_learning_and_forgetting_are_both_writable(self):
        gd = SV.apply_edits(FX.gamedata(), {"characters": {
            0: {"ether": [0, 2, 5, 56], "skills": []}}})
        c = SV.decode_gamedata(gd)["characters"][0]
        self.assertEqual(c["ether"], [0, 2, 5, 56])          # 56 = last ether bit
        self.assertEqual(c["skills"], [])

    def test_a_mask_edit_touches_only_that_characters_bytes(self):
        gd = FX.gamedata()
        new = SV.apply_edits(gd, {"characters": {2: {"ether": [1]}}})
        base = F.GROWTH_TABLE_OFF + 2 * F.GROWTH_STRIDE + F.ETHER_MASK_OFF
        diff = [i for i in range(len(gd))
                if gd[i] != new[i] and not (8 <= i < 16)]    # 8..15 = checksum
        self.assertEqual(diff, [base])

    def test_an_index_outside_the_mask_is_refused(self):
        with self.assertRaises(IndexError):
            SV.apply_edits(FX.gamedata(), {"characters": {0: {"ether": [57]}}})

    def test_the_masks_index_the_same_catalog_the_iso_tabs_edit(self):
        # the ether mask covers the ether numeric block, and the skill mask
        # starts exactly where the ISO passive table starts
        self.assertEqual(F.ETHER_MASK_TEXT0, 0)
        self.assertEqual(F.SKILL_MASK_TEXT0, F.PASSIVE_TEXT0)
        self.assertEqual(F.SKILL_MASK_COUNT, F.PASSIVE_COUNT)
        # the mask's permanent hole: catalog 25 is Burst Veil, the one ether
        # inside the run that the skill shop does not sell
        self.assertEqual(F.skill_catalog()[25]["name"], "Burst Veil")
        priced = {v["id"] for v in F.res_json("x2_costs.json").values()
                  if v["type"] == 2}
        self.assertNotIn(26, priced)                         # == catalog 25


class TestEquippedSkills(unittest.TestCase):
    def test_slots_decode_and_write(self):
        gd = SV.apply_edits(FX.gamedata(), {"characters": {0: {"equip": [29, 45, 0, 0]}}})
        self.assertEqual(SV.decode_gamedata(gd)["characters"][0]["equip"],
                         [29, 45, 0, 0])

    def test_a_short_list_clears_the_rest(self):
        gd = SV.apply_edits(FX.gamedata(), {"characters": {0: {"equip": [7]}}})
        self.assertEqual(SV.decode_gamedata(gd)["characters"][0]["equip"], [7, 0, 0, 0])

    def test_an_equipped_id_names_a_skill_through_the_cost_table(self):
        # id -> catalog index is the same mapping the ISO Costs tab uses
        names = F.skill_names()
        self.assertEqual(names[F.skill_cost_catalog_index(1, 29)], "STR+2")


class TestAffinities(unittest.TestCase):
    def test_retail_reads_a_flat_hundred_percent(self):
        c = SV.decode_gamedata(FX.gamedata())["characters"][0]
        self.assertEqual(set(c["affinity"].values()), {20})   # 20 * 5 == 100%
        self.assertEqual(F.affinity_pct(20), 100)

    def test_writing_one_element_leaves_the_other_seven(self):
        gd = SV.apply_edits(FX.gamedata(), {"characters": {
            0: {"affinity": {"Fire": F.affinity_byte(200)}}}})
        c = SV.decode_gamedata(gd)["characters"][0]
        self.assertEqual(F.affinity_pct(c["affinity"]["Fire"]), 200)
        self.assertEqual(F.affinity_pct(c["affinity"]["Ice"]), 100)


class TestInventories(unittest.TestCase):
    def test_all_three_decode(self):
        inv = SV.decode_gamedata(FX.gamedata())["inventory"]
        self.assertEqual(len(inv["consumables"]), F.INV_CONSUMABLE_COUNT)
        self.assertEqual(len(inv["esGear"]), F.INV_ES_GEAR_COUNT)
        self.assertEqual(len(inv["keyItems"]), F.INV_KEYITEM_COUNT)
        self.assertEqual(inv["consumables"][0], 12)
        self.assertEqual(inv["keyItems"][76], 1)

    def test_quantities_are_capped_at_the_stack_limit(self):
        gd = SV.apply_edits(FX.gamedata(), {"inventory": {"consumables": {3: 5000}}})
        self.assertEqual(SV.decode_gamedata(gd)["inventory"]["consumables"][3],
                         F.INV_QTY_MAX)

    def test_key_items_are_flags_not_counts(self):
        gd = SV.apply_edits(FX.gamedata(), {"inventory": {"keyItems": {5: 99}}})
        self.assertEqual(SV.decode_gamedata(gd)["inventory"]["keyItems"][5], 1)

    def test_a_slot_outside_the_array_is_refused(self):
        with self.assertRaises(IndexError):
            SV.apply_edits(FX.gamedata(), {"inventory": {
                "consumables": {F.INV_CONSUMABLE_COUNT: 1}}})

    def test_slots_name_themselves_through_the_disc_item_catalog(self):
        cat = F.item_catalog()
        self.assertEqual(cat[F.INV_CONSUMABLE_ITEM0]["name"], "Med Kit S")
        self.assertEqual(cat[F.INV_ES_GEAR_ITEM0]["name"], "Auxiliary Armor A")
        # the four consumable slots that read zero in every retail save are
        # exactly the catalog's placeholders
        placeholders = [i for i in range(F.INV_CONSUMABLE_COUNT)
                        if cat[F.INV_CONSUMABLE_ITEM0 + i]["placeholder"]]
        self.assertEqual(placeholders, [17, 18, 19, 35])
        # and the E.S. gear array's nine, which is the signature that located it
        self.assertEqual([i for i in range(F.INV_ES_GEAR_COUNT)
                          if cat[F.INV_ES_GEAR_ITEM0 + i]["placeholder"]],
                         [2, 3, 4, 7, 8, 9, 37, 38, 39])

    def test_es_accessory_slots_read_one_based(self):
        # stored 1 is catalog 0; a stored 0 means the slot is empty
        self.assertEqual(F.es_accessory_name(1), "Auxiliary Armor A")
        self.assertIsNone(F.es_accessory_name(0))
        self.assertIsNone(F.es_accessory_name(3))     # catalog 2 is a placeholder


class TestReadOnlyFields(unittest.TestCase):
    def test_the_name_pointer_and_unit_id_are_refused(self):
        for label in F.SAVE_READONLY_FIELDS:
            with self.assertRaises(KeyError):
                SV.apply_edits(FX.gamedata(), {"characters": {0: {label: 1}}})

    def test_they_still_decode(self):
        c = SV.decode_gamedata(FX.gamedata())["characters"][0]
        self.assertEqual(c["Name ptr"], 0x0564)
        self.assertEqual(c["Unit id"], 3)


class TestPsu(unittest.TestCase):
    def test_lists_its_files(self):
        names = [n for n, _o, _l in MC.psu_files(FX.psu())]
        self.assertEqual(names, ["icon.sys", FX.FOLDER, "system.ico"])

    def test_bodies_are_1024_aligned(self):
        for _n, off, _l in MC.psu_files(FX.psu()):
            self.assertEqual(off % MC.DIRENT_SIZE, 0)

    def test_a_psv_is_not_mistaken_for_a_psu(self):
        self.assertFalse(MC.looks_like_psu(FX.psv()[:MC.DIRENT_SIZE]))
        self.assertFalse(MC.looks_like_psu(FX.cbs()[:MC.DIRENT_SIZE]))
        self.assertFalse(MC.looks_like_psu(FX.sharkport()[:MC.DIRENT_SIZE]))
        self.assertFalse(MC.looks_like_psu(FX.x2_memcard()[:MC.DIRENT_SIZE]))


class TestScan(TempFileCase):
    def test_scan_reports_format_and_slot_count(self):
        self.put("a.psv", FX.psv())
        self.put("b.ps2", FX.x2_memcard(n_slots=4))
        self.put("noise.txt", b"not a save")
        found = {s["name"]: s for s in SV.scan_saves(self.dir)}
        self.assertEqual(found["a.psv"]["format"], "psv")
        self.assertEqual(found["a.psv"]["slots"], 1)
        self.assertEqual(found["b.ps2"]["format"], "memcard")
        self.assertEqual(found["b.ps2"]["slots"], 4)
        self.assertNotIn("noise.txt", found)


if __name__ == "__main__":
    unittest.main()


class TestLzariCodec(unittest.TestCase):
    """LZARI is bit-exact or it is nothing — a stream that decodes for a while
    and then diverges is the normal failure mode, so these check whole payloads
    rather than prefixes."""

    def test_round_trip_over_awkward_payloads(self):
        cases = {
            "empty": b"",
            "one byte": b"A",
            "long run": b"\x00" * 5000,
            "repeating text": b"the quick brown fox " * 300,
            "incompressible": bytes((i * 7919 + i // 251) % 256 for i in range(4000)),
            "match at the very end": b"x" * 100 + b"abcabcabc",
            "window sized": bytes(range(256)) * 16,
        }
        for name, payload in cases.items():
            with self.subTest(name):
                self.assertEqual(LZ.decompress(LZ.compress(payload), len(payload)),
                                 payload)

    def test_overlapping_match_round_trips(self):
        """A run encodes as a match whose length exceeds its distance, which the
        decoder resolves byte by byte as it writes. Comparing against a ring
        buffer instead of the emitted output gets this wrong."""
        payload = b"ab" + b"c" * 200 + b"ababababababab"
        self.assertEqual(LZ.decompress(LZ.compress(payload), len(payload)), payload)

    def test_a_real_gamedata_payload_round_trips(self):
        gd = FX.gamedata()
        self.assertEqual(LZ.decompress(LZ.compress(gd), len(gd)), gd)


class TestMaxContainer(TempFileCase):
    def test_reads_gamedata(self):
        p = self.put("s.max", FX.max_save())
        self.assertEqual(SV.extract_gamedata(p), FX.gamedata())
        self.assertEqual(SV.sniff_format(p), "max")

    def test_entries_are_found_despite_irregular_padding(self):
        """Real .max files pad entries by 2 bytes in one place and 12 in another,
        so the reader scans for headers instead of assuming an alignment. The
        fixture uses a third, different padding to keep that honest."""
        p = self.put("pad.max", FX.max_save())
        _gd, off = SV._max_gamedata(Path(p).read_bytes())
        self.assertGreater(off, 0)
        self.assertEqual(SV.extract_gamedata(p), FX.gamedata())

    def test_write_preserves_the_other_files_and_the_header(self):
        p = self.put("w.max", FX.max_save())
        original = Path(p).read_bytes()
        SV.write_save(p, {"gold": 42}, make_backup=False)
        edited = Path(p).read_bytes()
        self.assertEqual(SV.decode_gamedata(SV.extract_gamedata(p))["gold"], 42)
        # name fields, file count and decompressed length all survive
        self.assertEqual(original[0x10:0x50], edited[0x10:0x50])
        self.assertEqual(struct.unpack_from("<I", original, 0x54),
                         struct.unpack_from("<I", edited, 0x54))
        self.assertEqual(struct.unpack_from("<I", original, 0x58),
                         struct.unpack_from("<I", edited, 0x58))
        # the size field has to track the re-compressed body
        self.assertEqual(SV.MAX_HDR + struct.unpack_from("<I", edited, 0x50)[0],
                         len(edited))
        # only the gamedata entry changed inside the payload
        before, _c, _d = SV._max_payload(original)
        after, _c2, _d2 = SV._max_payload(edited)
        self.assertEqual(len(before), len(after))
        _gd, off = SV._max_gamedata(edited)
        self.assertEqual(before[:off], after[:off])
        end = off + F.GAMEDATA_SIZE
        self.assertEqual(before[end:], after[end:])

    def test_unidentified_checksum_is_preserved(self):
        """The 0x0C field matches no CRC-32 variant or sum we tried, and mymc
        writes 0 there, so it is evidently unenforced — we leave it alone rather
        than invent a value."""
        p = self.put("c.max", FX.max_save(checksum=0xCAFEBABE))
        SV.write_save(p, {"gold": 7}, make_backup=False)
        self.assertEqual(struct.unpack_from("<I", Path(p).read_bytes(), 0x0C)[0],
                         0xCAFEBABE)
