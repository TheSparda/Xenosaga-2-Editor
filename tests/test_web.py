"""
Front-end tests without a browser.

Two things are checkable from here and both have bitten this kind of code before:
the Python glue the web editor runs inside Pyodide (it is real CPython, so it can
be executed directly against the same fixtures), and every DOM id the scripts
reach for actually existing in markup somebody emits.
"""
import json
import os
import re
import shutil
import tempfile
import unittest

import fixtures as FX
import x2fields as F

WEB = os.path.normpath(os.path.join(FX.HERE, "..", "web"))
EDITOR = os.path.normpath(os.path.join(FX.HERE, "..", "Editor"))


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f:
        return f.read()


def glue_source():
    """The Python the web editor hands to Pyodide, lifted out of app.js."""
    m = re.search(r"py\.runPython\(`(.*?)`\);", read("app.js"), re.S)
    assert m, "could not find the runPython block in app.js"
    return m.group(1)


class TestPyodideGlue(unittest.TestCase):
    """Run the in-browser adapters as plain CPython."""

    @classmethod
    def setUpClass(cls):
        cls.ns = {}
        exec(compile(glue_source(), "app.js:runPython", "exec"), cls.ns)
        cls.dir = tempfile.mkdtemp(prefix="x2web-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def put(self, name, blob):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(blob)
        return p

    def test_defines_the_adapters_the_front_end_calls(self):
        for fn in ("load_reference", "load_slots", "load_save", "apply_edits"):
            self.assertIn(fn, self.ns, f"app.js calls {fn}() but the glue lacks it")

    def test_load_reference_supplies_what_app_js_reads(self):
        ref = json.loads(self.ns["load_reference"]())
        # app.js takes its sheet columns, caps, gear labels and roster from here
        self.assertEqual(ref["caps"], F.CHAR_CAPS)
        self.assertEqual([tuple(c) for c in ref["sheetCols"]], list(F.SHEET_COLS))
        self.assertEqual(ref["esFields"],
                         [l for (l, _o, _w, _k) in F.ES_EQUIP_FIELDS])
        self.assertEqual(len(ref["esEquipList"]), 31)
        self.assertEqual(ref["esEquip"]["0"], "Auxiliary Armor A")
        self.assertEqual(ref["roster"]["0"], "chaos")

    def test_load_slots_on_a_card(self):
        p = self.put("card.ps2", FX.x2_memcard(n_slots=3))
        info = json.loads(self.ns["load_slots"](p))
        self.assertEqual(info["format"], "memcard")
        self.assertEqual(len(info["slots"]), 3)
        self.assertEqual(info["slots"][0]["label"], "XenosagaEPII-01")

    def test_load_slots_on_something_unrecognized(self):
        p = self.put("junk.bin", b"nope" * 200)
        info = json.loads(self.ns["load_slots"](p))
        self.assertEqual(info["format"], "")
        self.assertEqual(info["slots"], [])

    def test_load_save_returns_the_sheet_plus_a_thumbnail(self):
        p = self.put("one.psv", FX.psv())
        d = json.loads(self.ns["load_save"](p))
        self.assertNotIn("error", d)
        self.assertEqual(d["format"], "psv")
        self.assertEqual(d["gold"], 1234567)
        self.assertEqual(len(d["characters"]), F.CHAR_COUNT)
        self.assertTrue(d["thumb"], "the embedded screenshot was not passed through")
        import base64
        self.assertTrue(base64.b64decode(d["thumb"]).startswith(b"\xff\xd8"))

    def test_load_save_reports_errors_as_data(self):
        p = self.put("bad.psv", b"\x00VSP" + b"\x00" * 500)
        d = json.loads(self.ns["load_save"](p))
        self.assertIn("error", d, "a broken save must not raise into the browser")

    def test_apply_edits_writes_the_named_slot(self):
        p = self.put("edit.ps2", FX.x2_memcard(n_slots=2, gold=700))
        payload = json.dumps({"gold": 4242, "characters": {"2": {"Level": 77}}})
        self.ns["apply_edits"](p, payload, 1)
        self.assertEqual(json.loads(self.ns["load_save"](p, 0))["gold"], 700)
        after = json.loads(self.ns["load_save"](p, 1))
        self.assertEqual(after["gold"], 4242)
        self.assertEqual(after["characters"][2]["Level"], 77)

    def test_apply_edits_defaults_to_the_first_slot(self):
        p = self.put("edit2.psv", FX.psv())
        self.ns["apply_edits"](p, json.dumps({"gold": 5}))
        self.assertEqual(json.loads(self.ns["load_save"](p))["gold"], 5)


class TestDomReferences(unittest.TestCase):
    """Every #id the scripts query has to be emitted by somebody."""

    SCRIPTS = ("app.js", "iso.js", "ref.js")

    def setUp(self):
        self.markup = read("index.html") + "".join(read(s) for s in self.SCRIPTS)

    def test_queried_ids_exist(self):
        for script in self.SCRIPTS:
            src = read(script)
            ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"', src))
            ids |= set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', src))
            for name in sorted(ids):
                with self.subTest(script=script, id=name):
                    self.assertRegex(
                        self.markup, r'id=[\'"]' + re.escape(name) + r'[\'"]',
                        f"{script} queries #{name}, which nothing emits")

    def test_helpers_shared_across_scripts_are_exported(self):
        app = read("app.js")
        for helper in ("openReview", "openPicker", "openInfo", "toast"):
            with self.subTest(helper):
                self.assertTrue(re.search(r"window\." + helper + r"\s*=", app),
                                f"{helper} is used across files but app.js never "
                                f"assigns window.{helper}")

    def test_iso_editor_uses_the_helpers_it_expects(self):
        iso = read("iso.js")
        for helper in ("window.openReview", "window.openInfo"):
            self.assertIn(helper, iso)

    def test_engine_files_the_boot_loop_fetches_all_exist(self):
        m = re.search(r'for\(const f of \[(.*?)\]\)', read("app.js"), re.S)
        self.assertTrue(m, "could not find the engine file list in app.js")
        for name in re.findall(r'"([^"]+)"', m.group(1)):
            with self.subTest(name):
                self.assertTrue(os.path.exists(os.path.join(EDITOR, name)),
                                f"app.js loads Editor/{name}, which does not exist")

    def test_service_worker_precache_list_is_complete(self):
        m = re.search(r"const SHELL = \[(.*?)\];", read("sw.js"), re.S)
        self.assertTrue(m)
        urls = [u for u in re.findall(r'"([^"]+)"', m.group(1)) if u != "./"]
        for url in urls:
            with self.subTest(url):
                self.assertTrue(os.path.exists(os.path.normpath(os.path.join(WEB, url))),
                                f"the service worker precaches {url}, which is missing")
        # anything the app fetches at runtime has to be in there or offline breaks
        for needed in ("tables.json", "../Editor/x2mc.py", "../Editor/x2enemies.json"
                       .replace("x2enemies", "x2_enemies")):
            self.assertIn(needed, urls, f"{needed} is not precached")

    def test_versions_agree(self):
        page = re.search(r'id="appver">([^<]+)<', read("index.html")).group(1).strip()
        app = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', read("app.js")).group(1)
        sw = re.search(r'CACHE\s*=\s*"x2editor-v([^"]+)"', read("sw.js")).group(1)
        self.assertEqual({page, app, sw}, {page},
                         f"version strings disagree: html={page} app={app} sw={sw}")


if __name__ == "__main__":
    unittest.main()
