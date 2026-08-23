"""
Local web-app tests: the HTTP surface writes to the user's saves and disc image,
so the loopback-Host guard, the save/slot listing, and the write endpoints all
need to behave.
"""
import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

import fixtures as FX
import x2editor as E
import x2patch as X
import x2save as SV


class AppCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="x2app-")
        os.makedirs(os.path.join(self.root, "Saves"))
        self.card = os.path.join(self.root, "Saves", "Mcd001.ps2")
        with open(self.card, "wb") as f:
            f.write(FX.x2_memcard(n_slots=2, gold=4000))
        self.psv = os.path.join(self.root, "Saves", "slot.psv")
        with open(self.psv, "wb") as f:
            f.write(FX.psv())
        self.iso = FX.write_fake_disc(os.path.join(self.root, "disc1.iso"))

        self._old_root, self._old_iso = E.SCAN_ROOT, E.ISO_PATH
        E.SCAN_ROOT, E.ISO_PATH = self.root, None
        E.invalidate()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), E.Handler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)
        E.SCAN_ROOT, E.ISO_PATH = self._old_root, self._old_iso
        E.invalidate()
        shutil.rmtree(self.root, ignore_errors=True)

    def request(self, method, path, host=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
        if body is not None:
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=body, headers=headers)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()


class TestHostGuard(AppCase):
    def test_loopback_names_are_allowed(self):
        for host in (f"127.0.0.1:{self.port}", "127.0.0.1",
                     f"localhost:{self.port}", "localhost", "[::1]:8748"):
            with self.subTest(host):
                status, _ = self.request("GET", "/", host=host)
                self.assertEqual(status, 200)

    def test_a_rebound_dns_name_is_rejected(self):
        for host in ("evil.example.com", "attacker.test:8748", ""):
            with self.subTest(host):
                status, _ = self.request("GET", "/", host=host)
                self.assertEqual(status, 403)

    def test_writes_are_rejected_too(self):
        before = open(self.psv, "rb").read()
        status, _ = self.request("POST", "/api/write", host="evil.example.com",
                                 body={"i": 0, "edits": {"gold": 1}})
        self.assertEqual(status, 403)
        self.assertEqual(open(self.psv, "rb").read(), before)

    def test_enemy_writes_are_rejected_too(self):
        before = open(self.iso, "rb").read()
        status, _ = self.request("POST", "/api/enemy_write", host="evil.example.com",
                                 body={"i": 0, "edits": {"HP": 1}})
        self.assertEqual(status, 403)
        self.assertEqual(open(self.iso, "rb").read(), before)


class TestSaveApi(AppCase):
    def index_of(self, predicate):
        for i, s in enumerate(E.decodable_saves()):
            if predicate(s):
                return i
        self.fail("no matching save")

    def test_page_lists_one_entry_per_slot(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        page = body.decode()
        self.assertIn("BASLUS-20892Xeno201", page)
        self.assertIn("BASLUS-20892Xeno202", page)
        self.assertIn("slot.psv", page)

    def test_each_card_slot_decodes_separately(self):
        saves = E.decodable_saves()
        card = [s for s in saves if s["format"] == "memcard"]
        self.assertEqual(len(card), 2)
        for s in card:
            i = saves.index(s)
            status, body = self.request("GET", f"/api/save?i={i}")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["gold"], 4000 + s["slot"])

    def test_write_edits_the_selected_slot_only(self):
        i = self.index_of(lambda s: s["format"] == "memcard" and s["slot"] == 1)
        status, body = self.request("POST", "/api/write",
                                    body={"i": i, "edits": {"gold": 98765,
                                          "characters": {"3": {"Level": 88}}}})
        self.assertEqual(status, 200)
        res = json.loads(body)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["count"], 2)
        self.assertEqual(SV.decode_save(self.card, slot=1)["gold"], 98765)
        self.assertEqual(SV.decode_save(self.card, slot=1)["characters"][3]["Level"], 88)
        self.assertEqual(SV.decode_save(self.card, slot=0)["gold"], 4000)

    def test_write_reports_failure_as_json(self):
        status, body = self.request("POST", "/api/write",
                                    body={"i": 999, "edits": {"gold": 1}})
        self.assertEqual(status, 200)
        res = json.loads(body)
        self.assertFalse(res["ok"])
        self.assertTrue(res["error"])

    def test_unknown_route_is_404(self):
        status, _ = self.request("GET", "/api/nope")
        self.assertEqual(status, 404)


class TestEnemyApi(AppCase):
    def test_reads_a_record(self):
        status, body = self.request("GET", "/api/enemy?i=6")
        self.assertEqual(status, 200)
        self.assertIn("HP", json.loads(body))

    def test_write_lands_in_the_image(self):
        status, body = self.request("POST", "/api/enemy_write",
                                    body={"i": 6, "edits": {"HP": 4321}})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"], body)
        with X.Iso(self.iso) as iso:
            self.assertEqual(X.read_enemy(iso, 6)["HP"], 4321)

    def test_backup_is_optional_and_honoured(self):
        self.request("POST", "/api/enemy_write",
                     body={"i": 1, "edits": {"HP": 7}, "backup": False})
        self.assertFalse(os.path.exists(self.iso + ".bak"))
        self.request("POST", "/api/enemy_write",
                     body={"i": 1, "edits": {"HP": 8}, "backup": True})
        self.assertTrue(os.path.exists(self.iso + ".bak"))


class TestDiscovery(AppCase):
    def test_explicit_iso_path_is_used(self):
        moved = os.path.join(self.root, "elsewhere.iso")
        shutil.move(self.iso, moved)
        E.invalidate()
        self.assertEqual(E.disc1_iso(), moved)      # still found by the walk
        outside = tempfile.mkdtemp(prefix="x2out-")
        try:
            far = os.path.join(outside, "far.iso")
            shutil.move(moved, far)
            E.invalidate()
            self.assertIsNone(E.disc1_iso(), "an ISO outside the tree was picked up")
            E.ISO_PATH = far
            E.invalidate()
            self.assertEqual(E.disc1_iso(), far,
                             "the command-line ISO path was ignored")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_scans_are_memoized_and_invalidated(self):
        first = E.all_isos()
        self.assertIs(E.all_isos(), first, "scan was not memoized")
        E.invalidate()
        self.assertIsNot(E.all_isos(), first, "invalidate() did not clear the cache")


if __name__ == "__main__":
    unittest.main()
