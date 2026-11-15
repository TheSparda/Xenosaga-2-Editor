#!/usr/bin/env python3
"""
Xenosaga Episode II (USA) ISO engine — a library, not a program.

Xenosaga II ships on TWO discs:
  Disc 1  SLUS-20892   (BOOT2 SLUS_208.92, VER 1.00)
  Disc 2  SLUS-21133   (BOOT2 SLUS_211.33)

The disc container plus the readers and writers for every table the editor
understands: enemies, player units, skills, passives, E.S. gear, purchase costs,
battle captions and the secret-key gates, with the multi-disc sync and the
retail baselines that make "compare to retail" answerable.

There is no command line here. The user-facing editor is `web/` — this module
backs the `gen_*.py` catalog generators that produce the JSON and
`web/tables.json` it loads, plus `x2selftest.py` and the test suite. The ISO
editing the browser does is a JavaScript port of these same offsets, kept in
step by `gen_web_tables.py --check` in CI.
"""
import os, struct, shutil, re
import x2fields as F

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# ISO container
# ---------------------------------------------------------------------------
class Iso:
    """Thin random-access wrapper over a disc image. Raw byte offsets only; the
    game tables (once found) live in a flat region, matching the S3 approach."""
    def __init__(self, path, write=False):
        self.path = path
        self.mode = "r+b" if write else "rb"
        self.f = open(path, self.mode)
        self.f.seek(0, os.SEEK_END)
        self.size = self.f.tell()
        self.f.seek(0)
        self._disc = None

    @property
    def disc(self):
        """Which disc this image is (1 or 2), from SYSTEM.CNF. Detected once and
        cached, and it decides every enemy-table offset below — disc 2 carries
        the same tables 0x800 lower. Unrecognized images fall back to disc 1,
        which is what the CLI's require_version() has already rejected by the
        time any write happens."""
        if self._disc is None:
            self._disc = F.disc_of(detect_serial(self)) or 1
        return self._disc

    @property
    def tables(self):
        """This disc's enemy table bases: {'stats','names','rewards'}."""
        return F.enemy_tables(self.disc)

    def read(self, off, n):
        self.f.seek(off)
        return self.f.read(n)

    def write(self, off, data):
        if "r+" not in self.mode:
            raise IOError("ISO opened read-only")
        self.f.seek(off)
        self.f.write(data)

    def find(self, needle, start=0, end=None, chunk=1 << 20):
        """Locate a byte string. Returns first offset or -1. Streams in chunks so
        it works on multi-GB discs without loading them into memory."""
        end = self.size if end is None else min(end, self.size)
        overlap = len(needle) - 1
        pos = start
        self.f.seek(pos)
        carry = b""
        while pos < end:
            buf = carry + self.f.read(min(chunk, end - pos))
            i = buf.find(needle)
            if i != -1:
                return pos - len(carry) + i
            carry = buf[-overlap:] if overlap else b""
            pos += chunk
        return -1

    def find_multi(self, needles, start=0, end=None, chunk=1 << 22, stop_after=None):
        """Locate several byte strings in ONE pass over the image (a 4.6 GB disc
        costs ~a minute per pass, so multi-anchor searches must share one).
        Yields (offset, needle) in chunk order. `stop_after` ends the scan once
        that many hits have been yielded — callers that confirm a table early
        (disc 1's tables sit ~33 MB in) should use it."""
        end = self.size if end is None else min(end, self.size)
        overlap = max(len(n) for n in needles) - 1
        pos, carry, hits = start, b"", 0
        self.f.seek(pos)
        while pos < end:
            buf = carry + self.f.read(min(chunk, end - pos))
            origin = pos - len(carry)
            found = []
            for n in needles:
                i = buf.find(n)
                while i != -1:
                    # a match lying entirely inside the carried-over tail was
                    # already reported for the previous chunk — don't re-yield it
                    if i + len(n) > len(carry):
                        found.append((origin + i, n))
                    i = buf.find(n, i + 1)
            for off, n in sorted(found):
                yield off, n
                hits += 1
                if stop_after and hits >= stop_after:
                    return
            carry = buf[-overlap:] if overlap else b""
            pos += chunk

    def close(self):
        self.f.close()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    # --- ISO9660 directory walk (root only; enough to reach the boot ELF/overlays)
    _SECTOR = 2048

    def _read_lba(self, lba, n=1):
        return self.read(lba * self._SECTOR, n * self._SECTOR)

    def list_files(self):
        """Yield (name, lba, size, is_dir) for the ISO's root directory."""
        pvd = self._read_lba(16)
        root = pvd[156:156 + 34]
        root_lba = struct.unpack_from("<I", root, 2)[0]
        root_size = struct.unpack_from("<I", root, 10)[0]
        data = self._read_lba(root_lba, (root_size + self._SECTOR - 1) // self._SECTOR)
        i = 0
        while i < len(data):
            rlen = data[i]
            if rlen == 0:
                i = (i // self._SECTOR + 1) * self._SECTOR
                if i >= len(data):
                    break
                continue
            ext_lba = struct.unpack_from("<I", data, i + 2)[0]
            ext_size = struct.unpack_from("<I", data, i + 10)[0]
            flags = data[i + 25]
            namelen = data[i + 32]
            name = data[i + 33:i + 33 + namelen].decode("latin1").split(";")[0]
            if name not in ("\x00", "\x01"):          # skip . and ..
                yield name, ext_lba, ext_size, bool(flags & 2)
            i += rlen

    def extract_file(self, name):
        """Return the bytes of a root-level file (e.g. 'SLUS_208.92'), or None."""
        for fn, lba, size, isdir in self.list_files():
            if not isdir and fn.upper() == name.upper():
                return self.read(lba * self._SECTOR, size)
        return None


# ---------------------------------------------------------------------------
# VERIFIED: identify the disc from its own filesystem (no hardcoded LBAs).
# ---------------------------------------------------------------------------
_SERIAL_RE = re.compile(rb"SLUS_(\d{3})\.(\d{2})")

def detect_serial(iso, scan=4 << 20):
    """Read the PS2 serial out of SYSTEM.CNF's BOOT2 line. Returns e.g. 'SLUS-20892'
    (normalized: SLUS_208.92 -> SLUS-20892), or None if not found in the first `scan`
    bytes (SYSTEM.CNF lives near the start of every retail PS2 disc)."""
    m = _SERIAL_RE.search(iso.read(0, min(scan, iso.size)))
    if not m:
        return None
    return "SLUS-" + m.group(1).decode() + m.group(2).decode()

def detect_volume_id(iso):
    """ISO9660 Primary Volume Descriptor volume identifier (LBA 16, +40, 32 bytes)."""
    return iso.read(16 * 2048 + 40, 32).decode("latin1").strip()

def check_version(iso):
    """Return (ok, serial, disc, volume_id). ok=True iff this is a known X2 disc."""
    serial = detect_serial(iso)
    vol = detect_volume_id(iso)
    disc = F.disc_of(serial) if serial else None
    ok = disc is not None and vol == F.VOLUME_ID
    return ok, serial, disc, vol

def require_version(iso):
    ok, serial, disc, vol = check_version(iso)
    if not ok:
        raise SystemExit(
            f"Not a recognized Xenosaga II (USA) disc "
            f"(serial={serial!r}, volume={vol!r}). Expected one of {list(F.SERIALS)}."
        )
    return serial, disc


# ---------------------------------------------------------------------------
# Backup helper (mirrors s3patch: one .bak before the first destructive write).
# ---------------------------------------------------------------------------
def backup(path):
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


# ---------------------------------------------------------------------------
# ENEMY read/write (VERIFIED, both discs). Stat records and a parallel rewards
# table, both indexed by record number. The bases come from the disc itself
# (iso.tables) because disc 2 holds the same tables 0x800 lower.
# ---------------------------------------------------------------------------
def _enemy_tables(iso, i):
    t = iso.tables
    return ((t["stats"] + i * F.ENEMY_STRIDE,
             F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS + F.ZONE_FIELDS +
             F.FLAG_FIELDS + F.STATUS_RES_FIELDS),
            (t["rewards"] + i * F.REWARD_STRIDE, F.REWARD_FIELDS + F.DROP_FIELDS))

def read_enemy(iso, i):
    out = {}
    for base, fields in _enemy_tables(iso, i):
        for (lbl, off, w, _k) in fields:
            out[lbl] = int.from_bytes(iso.read(base + off, w), "little")
    return out

def read_enemy_id(iso, i):
    """The record's own enemy id (+0x52). Read from the disc rather than taken
    from the shipped catalog so a partly-modified disc still classifies right."""
    off = iso.tables["stats"] + i * F.ENEMY_STRIDE + F.ENEMY_ID_OFF
    return int.from_bytes(iso.read(off, 2), "little")

def is_boss(enemy_id):
    return enemy_id >= F.BOSS_ID_MIN


# ---------------------------------------------------------------------------
# Vanilla comparison + shareable patch files.
#
# x2_enemies.json holds the verified retail values, so a disc can be diffed
# against it without a pristine copy to compare with — which also means edits can
# be exported as a small text file others can apply to their own disc.
#
# The catalog has to cover every writable field for that to mean anything. When
# it held only the eleven guide-verified numbers, this reported "matches retail"
# on a disc whose break sequences, zones, affinities, resistances and drops had
# all been rewritten. Editor/gen_enemy_catalog.py fills the rest from the discs.
# ---------------------------------------------------------------------------
PATCH_FORMAT = "x2-enemy-patch"
PATCH_VERSION = 1

def vanilla_enemy(i, catalog=None):
    """The retail values for record `i`, keyed by field label — every writable
    field, including break sequences, zones, affinities, resistances and drops."""
    cat = (catalog if catalog is not None else F.enemy_catalog()).get(i, {})
    return {label: cat[key] for label, key in F.ENEMY_CATALOG_KEY.items()
            if key in cat}

def diff_vanilla(iso):
    """{record: {field: (disc value, retail value)}} for everything that differs."""
    cat = F.enemy_catalog()
    out = {}
    for i in range(F.ENEMY_COUNT):
        want = vanilla_enemy(i, cat)
        if not want:
            continue
        have = read_enemy(iso, i)
        delta = {k: (have[k], v) for k, v in want.items() if have.get(k) != v}
        if delta:
            out[i] = delta
    return out

def make_patch(edits, note="", serial=None):
    """Wrap {record: {field: value}} as a shareable patch document."""
    return {
        "format": PATCH_FORMAT,
        "version": PATCH_VERSION,
        "game": serial or sorted(F.SERIALS)[0],
        "note": note,
        "edits": {str(i): dict(fields) for i, fields in sorted(edits.items())},
    }

def parse_patch(doc):
    """Validate a patch document and return {record: {field: value}}.

    Deliberately strict: this writes to a disc image, so an unknown field name,
    an out-of-range record, or a non-integer value is an error rather than
    something to skip quietly."""
    if not isinstance(doc, dict) or doc.get("format") != PATCH_FORMAT:
        raise ValueError(f"not a {PATCH_FORMAT} file")
    version = doc.get("version")
    if version != PATCH_VERSION:
        raise ValueError(f"patch version {version!r} is not supported "
                         f"(this build reads version {PATCH_VERSION})")
    known = {f[0] for f in F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS +
             F.ZONE_FIELDS + F.FLAG_FIELDS + F.STATUS_RES_FIELDS +
             F.REWARD_FIELDS + F.DROP_FIELDS}
    out = {}
    for key, fields in (doc.get("edits") or {}).items():
        try:
            i = int(key)
        except (TypeError, ValueError):
            raise ValueError(f"record key {key!r} is not a number")
        if not 0 <= i < F.ENEMY_COUNT:
            raise ValueError(f"record {i} is outside 0..{F.ENEMY_COUNT - 1}")
        if not isinstance(fields, dict):
            raise ValueError(f"record {i}: expected a field map")
        clean = {}
        for label, value in fields.items():
            if label not in known:
                raise ValueError(f"record {i}: unknown field {label!r}")
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"record {i}.{label}: expected a whole number, "
                                 f"got {value!r}")
            clean[label] = value
        if clean:
            out[i] = clean
    if not out:
        raise ValueError("patch contains no edits")
    return out

def apply_patch(iso, edits):
    """Write a parsed patch. Returns (records touched, fields written)."""
    n = 0
    for i, fields in sorted(edits.items()):
        n += write_enemy(iso, i, fields)
    return len(edits), n

def write_enemy(iso, i, edits):
    """Write edited fields for enemy record `i` (stats and/or rewards).
    edits = {field_label: value}, clamped to field width. Returns fields written."""
    n = 0
    for base, fields in _enemy_tables(iso, i):
        for (lbl, off, w, _k) in fields:
            if lbl in edits and edits[lbl] is not None:
                v = max(0, min(int(edits[lbl]), (1 << (8 * w)) - 1))
                iso.write(base + off, v.to_bytes(w, "little"))
                n += 1
    return n

def read_enemy_record(iso, i, base=None):
    """Raw 0x5C stat record bytes for index `i` (research helper — includes the
    65 bytes we haven't decoded yet)."""
    base = iso.tables["stats"] if base is None else base
    return iso.read(base + i * F.ENEMY_STRIDE, F.ENEMY_STRIDE)


# ---------------------------------------------------------------------------
# REBALANCE — battle-pacing profiles over the verified tables (F.PROFILES).
#
# Ep. II's combo loop (stock -> break -> boost) is code we haven't located, but
# what makes it feel like a tax is tuning we CAN write: HP sets how many stocked
# chains a kill costs, VIT/EDEF set whether off-loop attacks matter, AGL sets how
# often enemies interrupt a setup, SP/CP gate the skill system. Profiles scale
# those verified fields; nothing is written blind and nothing is written here at
# all — plan_rebalance() returns a plan the caller reviews, then applies.
# ---------------------------------------------------------------------------
def disc_is_pristine(iso, base=None):
    """True if the enemy tables still hold their verified retail values — stats
    *and* rewards. False means the disc was already edited, so scaling again
    would compound.

    Both tables have to be checked: a reward-only profile leaves every stat byte
    untouched, so a stats-only anchor would wave the second pass straight
    through and silently stack the multipliers.

    Related but not the same as diff_vanilla(): this is a fast yes/no that can be
    pointed at an arbitrary `base` (so the disc-2 hunt can reuse it), while
    diff_vanilla() reports field-by-field what changed at the known base."""
    cat = F.enemy_catalog()
    matched, checked = _confirm_base(iso, cat, base or iso.tables["stats"])
    if not checked or matched != checked:
        return False
    blob = iso.read(iso.tables["rewards"], F.ENEMY_COUNT * F.REWARD_STRIDE)
    for i, rec in cat.items():
        p = i * F.REWARD_STRIDE
        if struct.unpack_from("<IHH", blob, p) != (rec["exp"], rec["sp"], rec["cp"]):
            return False
    return True

def _scale(value, pct, cap, floor):
    return max(floor, min(int(round(value * pct / 100.0)), cap))

def plan_rebalance(iso, prof, threshold=None, include_dummy=False):
    """Compute the per-record edits a profile implies. Returns
    [(index, name, group, {field: (old, new)}), ...] — read-only, writes nothing.

    Grouping ("regular" vs "major") is decided on the *catalog* HP, not the
    disc's current HP, so re-planning against an already-patched disc still
    classifies each record the same way."""
    threshold = F.MAJOR_HP_THRESHOLD if threshold is None else threshold
    cat = F.enemy_catalog()
    plan = []
    for i in range(F.ENEMY_COUNT):
        rec = cat.get(i, {})
        if not include_dummy and rec and F.is_dummy_record(rec):
            continue
        cur = read_enemy(iso, i)
        group = "major" if rec.get("hp", cur.get("HP", 0)) >= threshold else "regular"
        scales = prof.get(group, {})
        edits = {}
        for lbl, pct in scales.items():
            if pct == 100 or lbl not in cur:
                continue
            cap = F.ENEMY_FIELD_CAPS.get(lbl, 0xFFFFFFFF)
            old = cur[lbl]
            if old == 0:                      # 0 means "none" (no CP, no SP) — leave it
                continue
            new = _scale(old, pct, cap, 1)
            if new != old:
                edits[lbl] = (old, new)
        if edits:
            plan.append((i, rec.get("name", str(i)), group, edits))
    return plan

def apply_rebalance(iso, plan):
    """Write a plan from plan_rebalance(). Returns (records, fields) written."""
    fields = 0
    for i, _name, _group, edits in plan:
        fields += write_enemy(iso, i, {k: v[1] for k, v in edits.items()})
    return len(plan), fields


# ---------------------------------------------------------------------------
# TABLE VERIFICATION — find the enemy stat table on *any* disc by signature.
#
# Both discs carry the tables (disc 2's copy is 0x800 lower, byte-identical), so
# a rebalance has to be written to both or it stops at the disc swap.
#
# Two different questions live here, and they need two different comparisons:
#
#   "where is the table?"    -> enemy_signature(), the contiguous 17-byte run at
#                               record+0x36. Being one long run is the point: it
#                               makes a cheap, specific needle for a 4.6 GB scan.
#   "does it still hold the
#    retail values?"         -> _confirm_base(), which compares only the VERIFIED
#                               fields (F.ENEMY_FIELDS).
#
# The distinction is load-bearing. The 17-byte run spans +0x3A, a halfword this
# project has never decoded, and it is NOT the constant it looks like: 114 of the
# 125 records hold 99 there, but eleven real enemies (Kfuga Lily, E2 Hauser,
# Yacud Cannon, Stole Marine, the four Cera records, Executus Arma and the two
# U-TIC Soldiers) hold 0, 1, 2 or 10 — on both discs. Using the run to answer the
# second question therefore reports 114/125 on a *pristine* retail disc, which
# read as "this disc was already edited" and made `rebalance` refuse to run on
# genuine retail media. Comparing the verified fields gives a true 125/125.
#
# Keeping 0x0063 in the needle is still correct — every anchor record holds it,
# and the extra two bytes buy specificity — it just must not be mistaken for a
# retail-value check.
# ---------------------------------------------------------------------------
def enemy_signature(rec):
    """The 17-byte +0x36..+0x46 run implied by a catalog record — the search
    needle. Assumes 0x0063 at +0x3A, which holds for every anchor record but not
    for all 125; use _confirm_base() to ask whether values are retail."""
    return (struct.pack("<IH", rec["hp"], 0x0063) +
            struct.pack("<HHHH", rec["str"], rec["vit"], rec["eatk"], rec["edef"]) +
            bytes((rec["dex"], rec["eva"], rec["agl"])))

# distinctive HP values make cheap anchors — few false hits across 4.6 GB
ANCHOR_RECORDS = (6, 124, 109, 117, 116)        # Perun, Dark Erde Kaiser, Proto Omega, Baal Zebul, Mikumari

def locate_enemy_table(iso, anchors=ANCHOR_RECORDS, confirm=8, region=None):
    """Signature-search the disc for the enemy stat table. Returns
    {base, stride, matched, anchor, checked} or None.

    One pass, all anchors at once. Each hit implies a table base; a base is
    accepted once `confirm` further catalog records also match there, which
    rules out a lone coincidental byte run."""
    cat = F.enemy_catalog()
    needles = {}
    for i in anchors:
        if i in cat:
            needles.setdefault(enemy_signature(cat[i]), i)
    start, end = region or (0, None)
    for off, needle in iso.find_multi(list(needles), start=start, end=end):
        i = needles[needle]
        base = off - 0x36 - i * F.ENEMY_STRIDE
        if base < 0:
            continue
        matched, checked = _confirm_base(iso, cat, base)
        if matched >= confirm:
            return {"base": base, "stride": F.ENEMY_STRIDE, "matched": matched,
                    "checked": checked, "anchor": i}
    return None

def _confirm_base(iso, cat, base):
    """How many catalog records hold their retail values at a candidate base
    (reads the whole table once). Returns (matched, checked).

    Compares the VERIFIED fields only — never the raw +0x36..+0x46 run, which
    includes the undecoded +0x3A halfword that eleven real records don't set to
    99. See the section comment above."""
    span = F.ENEMY_COUNT * F.ENEMY_STRIDE
    if base + span > iso.size:
        return 0, 0
    blob = iso.read(base, span)
    matched = checked = 0
    for i, rec in cat.items():
        if "hp" not in rec:
            continue
        checked += 1
        p = i * F.ENEMY_STRIDE
        if all(int.from_bytes(blob[p + off:p + off + w], "little")
               == rec[F.ENEMY_CATALOG_KEY[lbl]]
               for (lbl, off, w, _k) in F.ENEMY_FIELDS):
            matched += 1
    return matched, checked


# ---------------------------------------------------------------------------
# BREAK / WEAK-ZONE HUNT (tier 1) — the combo system's own data.
#
# Every enemy has a fixed weak-zone sequence (BB, CB, CC, BCBB...) that you must
# reproduce with the zone buttons to Break it. Strategy guides publish that
# sequence per enemy, which is exactly the kind of ground truth that solved the
# stat table — and the 0x5C record still has 65 undecoded bytes, so the field is
# very likely already inside data we read.
#
# The trick is that we don't need to guess the encoding. If a byte column IS the
# zone field then enemies sharing a zone string must share its value, so scoring
# a column by how well its value partition agrees with the zone partition finds
# it whatever the bit layout turns out to be:
#   consistency = P(same value | same zone string)   — must be 1.0 for the field
#   resolution  = P(diff value | diff zone string)   — <1.0 means lossy (a mask
#                                                      or a length, not the seq)
# ---------------------------------------------------------------------------
def read_records(iso, base=None, count=None, stride=None, tail=None):
    """All raw stat records as a list of bytes. Defaults to THIS disc's base —
    hardcoding disc 1's would read 22 records off on a disc-2 image.

    Each returned record is `stride + tail` bytes: the affinity and resistance
    blocks overhang the record, so a plain stride-sized slice truncates them and
    leaves the LAST record short. `tail=0` gives the old exact-stride behaviour
    for callers that only want the nominal record."""
    base = iso.tables["stats"] if base is None else base
    count = F.ENEMY_COUNT if count is None else count
    stride = F.ENEMY_STRIDE if stride is None else stride
    tail = F.enemy_record_tail() if tail is None else tail
    blob = iso.read(base, count * stride + tail)
    return [blob[i * stride:i * stride + stride + tail] for i in range(count)]

def _packed2_ok(v):
    """Value decodes as up to four 2-bit zone symbols (1=A,2=B,3=C) with 0
    padding — the natural way to store a 1-4 symbol sequence in one byte."""
    syms = [(v >> s) & 3 for s in (0, 2, 4, 6)]
    while syms and syms[-1] == 0:
        syms.pop()
    return bool(syms) and all(s in (1, 2, 3) for s in syms)

def column_profile(records, offsets=None):
    """Profile each undecoded byte column across every record. Returns a list of
    {off, distinct, min, max, top, packed2, nibble, mask3} sorted by offset —
    the survey to run before there's any ground truth to score against."""
    offsets = F.enemy_unmapped_offsets() if offsets is None else offsets
    out = []
    for off in offsets:
        vals = [r[off] for r in records]
        hist = {}
        for v in vals:
            hist[v] = hist.get(v, 0) + 1
        uniq = sorted(hist)
        out.append({
            "off": off,
            "distinct": len(uniq),
            "min": uniq[0], "max": uniq[-1],
            "top": sorted(hist.items(), key=lambda kv: -kv[1])[:4],
            "packed2": all(v == 0 or _packed2_ok(v) for v in uniq),
            "nibble": all((v & 0xF) <= 3 and (v >> 4) <= 3 for v in uniq),
            "mask3": uniq[-1] <= 7,
        })
    return out

def _partition_scores(values, truth):
    """(consistency, resolution) of a value sequence against {index: zone str}."""
    items = [(i, z) for i, z in truth.items() if i < len(values)]
    same = same_ok = diff = diff_ok = 0
    for a in range(len(items)):
        ia, za = items[a]
        for b in range(a + 1, len(items)):
            ib, zb = items[b]
            equal = values[ia] == values[ib]
            if za == zb:
                same += 1
                same_ok += equal
            else:
                diff += 1
                diff_ok += not equal
    return (same_ok / same if same else 1.0,
            diff_ok / diff if diff else 0.0)

def zone_scan(records, truth, offsets=None, pairs=True):
    """Score every undecoded column (and every u16 pair) against ground-truth
    zone strings. Returns candidates sorted best-first; a real hit is
    consistency 1.0 with high resolution."""
    offsets = F.enemy_unmapped_offsets() if offsets is None else offsets
    cands = []
    for off in offsets:
        vals = [r[off] for r in records]
        c, r = _partition_scores(vals, truth)
        cands.append({"off": off, "width": 1, "consistency": c, "resolution": r,
                      "distinct": len(set(vals))})
    if pairs:
        oset = set(offsets)
        for off in offsets:
            if off + 1 not in oset or off + 1 >= len(records[0]):
                continue
            # a pair whose other half never varies is just an alias of the
            # informative byte — it would tie with it and crowd the ranking
            if len({r[off] for r in records}) == 1 or len({r[off + 1] for r in records}) == 1:
                continue
            vals = [r[off] | (r[off + 1] << 8) for r in records]
            c, r = _partition_scores(vals, truth)
            cands.append({"off": off, "width": 2, "consistency": c, "resolution": r,
                          "distinct": len(set(vals))})
    # ties go to the narrower, earlier field — the simplest explanation of the data
    cands.sort(key=lambda d: (-d["consistency"], -d["resolution"], d["width"], d["off"]))
    return cands

def zone_mapping(records, truth, off, width=1):
    """Tabulate value <-> zone string for a candidate column, so a perfect hit
    can be read off as an encoding. Returns {value: sorted[zone strings]}."""
    out = {}
    for i, z in sorted(truth.items()):
        if i >= len(records):
            continue
        r = records[i]
        v = r[off] | (r[off + 1] << 8) if width == 2 else r[off]
        out.setdefault(v, set()).add(z)
    return {v: sorted(s) for v, s in out.items()}

def scan_region_for_column(iso, truth, start, length, strides, min_resolution=0.5,
                           count=None):
    """Fallback for when the zone data is NOT in the stat record: sweep a disc
    region for any parallel-indexed table (row = enemy record index) with a
    column that partitions exactly like the zone strings. Returns candidates
    [{base, stride, consistency, resolution, distinct}].

    Cost is bounded by early exit — most offsets die on the first mismatched
    pair — but this is still the slow path; run it only after the in-record
    scan comes up empty."""
    count = F.ENEMY_COUNT if count is None else count
    idx = sorted(i for i in truth if i < count)
    groups = {}
    for i in idx:
        groups.setdefault(truth[i], []).append(i)
    same = [g for g in groups.values() if len(g) > 1]
    if not same:
        raise ValueError("ground truth has no two enemies sharing a zone string — "
                         "consistency can't be tested; add more entries")
    out = []
    for stride in strides:
        need = length + (count - 1) * stride + 1
        data = iso.read(start, min(need, iso.size - start))
        limit = len(data) - (count - 1) * stride
        for b in range(max(0, limit)):
            ok = True
            for g in same:                      # consistency, early-exit
                v = data[b + g[0] * stride]
                for i in g[1:]:
                    if data[b + i * stride] != v:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            vals = [data[b + i * stride] for i in range(count)]
            c, r = _partition_scores(vals, truth)
            if r >= min_resolution:
                out.append({"base": start + b, "stride": stride, "consistency": c,
                            "resolution": r, "distinct": len(set(vals))})
    out.sort(key=lambda d: (-d["resolution"], d["base"]))
    return out

def load_zone_truth(path, catalog=None):
    """Read ground-truth weak zones. Accepts JSON ({"Perun": "BB", "6": "BB"})
    or CSV/TSV/text lines `name, zones`. Names resolve against the verified
    enemy catalog, case- and space-insensitively. Returns (truth, unmatched)
    where truth is {record index: "BB"}."""
    catalog = F.enemy_catalog() if catalog is None else catalog
    by_name = {}
    for i, rec in catalog.items():
        by_name.setdefault(str(rec.get("name", "")).strip().lower(), i)
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    raw = {}
    stripped = text.strip()
    if stripped.startswith("{"):
        import json as _json
        raw = _json.loads(stripped)
    else:
        for line in stripped.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = [p.strip() for p in (line.split("\t") if "\t" in line else line.split(","))]
            if len(parts) >= 2 and parts[1]:
                raw[parts[0]] = parts[1]
    truth, unmatched = {}, []
    for key, zones in raw.items():
        zones = str(zones).strip().upper().replace(" ", "")
        if not zones or any(c not in "ABC" for c in zones):
            unmatched.append((key, "not a zone string"))
            continue
        key = str(key).strip()
        if key.isdigit() and int(key) in catalog:
            truth[int(key)] = zones
        elif key.lower() in by_name:
            truth[by_name[key.lower()]] = zones
        else:
            unmatched.append((key, "no such enemy"))
    return truth, unmatched


def _summary_cols():
    """The columns worth showing in a table — verified fields only."""
    return [f[0] for f in F.ENEMY_FIELDS + F.REWARD_FIELDS]

def _affinity_cols():
    return [f[0] for f in F.ENEMY_AFFINITY_FIELDS]

def _zone_cols():
    return [f[0] for f in F.ZONE_FIELDS]

def _res_cols():
    return [f[0] for f in F.STATUS_RES_FIELDS]

def _drop_cols():
    return [f[0] for f in F.DROP_FIELDS]

def drops_of(rec):
    """('Med Kit S 100%', 'Med Kit L 10%') — common and rare, ready to print."""
    return (F.drop_label(rec["DropCat"], rec["DropItem"], rec["DropRate"]),
            F.drop_label(rec["RareCat"], rec["RareItem"], rec["RareRate"]))

def _enemy_field_names():
    """Every writable field, including the unverified affinity slots."""
    return (_summary_cols() + _affinity_cols() + _zone_cols() + _res_cols()
            + _drop_cols())

def break_seq_of(rec):
    """The record's break sequence as text ('CBB'), or '' if it can't be broken."""
    return F.decode_break_seq([rec[f"Brk{n + 1}"] for n in range(F.BREAK_SEQ_SLOTS)])

AFFINITY_NOTE = (
    f"affinities are percentages ({F.ENEMY_AFFINITY_NORMAL} = normal damage, "
    f"below resists, above takes extra, 0 is immune, negative absorbs); stored as "
    f"a signed byte x{F.ENEMY_AFFINITY_SCALE}, so they move in "
    f"{F.ENEMY_AFFINITY_SCALE}% steps.")

def affinity_pcts(rec):
    """{element: percent} for one record."""
    return {name: F.affinity_pct(rec[name]) for name in F.AFFINITY_ELEMENTS}


# ---------------------------------------------------------------------------
# xdelta / VCDIFF patches.
#
# A patch file (above) is the better format for sharing a rebalance: it names
# fields, it is readable, and it is validated on import. An xdelta patch is the
# blunt instrument for the cases that one cannot express — any byte anywhere,
# including regions this tool does not decode.
#
# The CLI shells out to xdelta3 because it has both files and can diff them. The
# web editor cannot (it never has a pristine copy, and diffing 4.6 GB in a tab is
# not sensible), so it synthesizes the same format directly from the edits it has
# already staged — see web/vcdiff.js. Both produce standard VCDIFF that any
# decoder applies.
# ---------------------------------------------------------------------------
XDELTA_MISSING = ("xdelta3 is not installed. Install it (brew install xdelta, "
                  "apt install xdelta3) or export from the web editor, which "
                  "builds the same format without it.")

def xdelta_available():
    return shutil.which("xdelta3") is not None

def _xdelta(args, what):
    import subprocess
    if not xdelta_available():
        raise SystemExit(XDELTA_MISSING)
    r = subprocess.run(["xdelta3", *args], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stderr.strip() or f"xdelta3 {what} failed")

def make_xdelta(pristine, edited, out):
    """Diff a pristine image against an edited one. Returns the patch size."""
    _xdelta(["-e", "-f", "-s", pristine, edited, out], "encode")
    return os.path.getsize(out)

def apply_xdelta(pristine, patch, out):
    """Reproduce an edited image from a pristine one plus a patch."""
    _xdelta(["-d", "-f", "-s", pristine, patch, out], "decode")
    return os.path.getsize(out)

# ---------------------------------------------------------------------------
# PPF3.0 import — the format difficulty mods for this game actually ship in.
# Same policy as everywhere else: only bytes that land in a mapped table are
# written; everything else is reported, not silently applied. For a complete,
# unreviewed application use a PPF tool or xdelta on a pristine image.
# ---------------------------------------------------------------------------
def parse_ppf(path):
    """[(offset, bytes), ...] from a PPF3.0 file, honouring blockcheck/undo."""
    b = open(path, "rb").read()
    if b[:5] != b"PPF30":
        raise SystemExit("not a PPF3.0 patch")
    blockcheck, undo = b[57], b[58]
    p = 60 + (1024 if blockcheck else 0)
    recs = []
    while p + 9 <= len(b):
        off = int.from_bytes(b[p:p + 8], "little"); p += 8
        n = b[p]; p += 1
        recs.append((off, b[p:p + n])); p += n
        if undo:
            p += n
    return recs

def _mapped_spans(disc):
    """[(base, length), ...] of every region this editor understands.

    The skill text span is LAST and is deliberately the widest: it is a bounding
    box, not a table, so a record may only be attributed to it once every
    precise table has declined. It carries a patch's skill renames and rewritten
    descriptions, and — because the passive/equip table sits inside it — the
    passive effect records too. Kept in step with the web editor's bufferMap()
    and Editor/gen_hardtype.py; all three must agree or a patch stages
    differently depending on which front end you used.
    """
    t = F.enemy_tables(disc)
    return [
        (F.unit_tables(disc), F.UNIT_COUNT * F.ENEMY_STRIDE),
        (t["stats"], F.ENEMY_COUNT * F.ENEMY_STRIDE + F.enemy_record_tail()),
        (t["rewards"], F.ENEMY_COUNT * F.REWARD_STRIDE),
        (F.skill_base(disc), F.skill_span(disc)),
        (F.skill_cost_base(disc), F.skill_cost_span(disc)),
        F.skill_text_span(disc),
    ]


# ---------------------------------------------------------------------------
# MULTI-DISC — keeping the two discs' enemy tables in step.
#
# Both retail discs carry the same enemy data at different bases, so any edit has
# to be made twice or the game reverts to retail values at the disc swap. Rather
# than duplicate every command's logic, there is one primitive: copy every
# verified field from one disc to the other. `--also` on the write commands runs
# the edit on the first disc and then syncs, which means the two discs cannot end
# up with different values however the edit was produced.
# ---------------------------------------------------------------------------
def sync_discs(src, dst):
    """Copy every verified enemy field from `src` to `dst`.

    Returns (records touched, fields written). Reads and writes through the
    normal field path, so each disc uses its own bases and the affinity block's
    straddle is handled the same way it is everywhere else."""
    labels = [f[0] for f in (F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS +
                             F.ZONE_FIELDS + F.FLAG_FIELDS + F.STATUS_RES_FIELDS +
                                 F.REWARD_FIELDS + F.DROP_FIELDS)]
    recs = fields = 0
    for i in range(F.ENEMY_COUNT):
        want = read_enemy(src, i)
        have = read_enemy(dst, i)
        delta = {k: want[k] for k in labels if want.get(k) != have.get(k)}
        if delta:
            fields += write_enemy(dst, i, delta)
            recs += 1
    return recs, fields

def _sync_to(primary_path, other_path):
    """Mirror primary_path's enemy tables onto other_path, with a short report."""
    with Iso(primary_path) as src, Iso(other_path, write=True) as dst:
        require_version(src); require_version(dst)
        if src.disc == dst.disc:
            raise SystemExit(f"--also: both images are disc {src.disc} — "
                             f"pass the other disc")
        n = dst.disc
        recs, fields = sync_discs(src, dst)
    with Iso(primary_path) as src, Iso(other_path, write=True) as dst:
        sk = sync_skills(src, dst)
        urecs, _uf = sync_units(src, dst)
    if recs or sk or urecs:
        print(f"synced onto disc {n}: {fields} field(s) across {recs} record(s)"
              + (" + the skill tables" if sk else "")
              + (f" + {urecs} unit record(s)" if urecs else ""))
    else:
        print(f"synced onto disc {n}: already identical")
    return recs, fields

# ---------------------------------------------------------------------------
# FULL-TABLE JSON — for bulk editing in a text editor or a spreadsheet.
#
# Patch files carry only the deltas, as raw bytes, which is right for sharing a
# mod but painful to author. This is the whole bestiary in readable units:
# affinities as signed percentages, the break sequence as letters, drops with the
# item name alongside the id. Import is DELIBERATELY STRICT — it writes to a disc
# image, so an unknown field, an out-of-range value or a bad break sequence is an
# error, never something to skip quietly.
TABLE_FORMAT, TABLE_VERSION = "x2-enemy-table", 1

def enemy_json(iso, note=""):
    """The whole enemy table as a readable document."""
    names = F.enemy_names()
    rows = []
    for i in range(F.ENEMY_COUNT):
        rec = read_enemy(iso, i)
        row = {"index": i, "name": names.get(i, "?")}
        for lbl, _o, _w, _k in F.ENEMY_FIELDS + F.REWARD_FIELDS:
            row[lbl] = rec[lbl]
        row["break"] = break_seq_of(rec)
        row["zones"] = F.zone_mask_text(rec["Zones"])
        row["affinity"] = {e: F.affinity_pct(rec[e]) for e in F.AFFINITY_ELEMENTS}
        row["resist"] = {n: rec[n] for n in F.STATUS_RES_NAMES}
        row["drop"] = {"rate": rec["DropRate"], "category": rec["DropCat"],
                       "item": rec["DropItem"],
                       "_name": F.drop_item_name(rec["DropCat"], rec["DropItem"])}
        row["rare"] = {"rate": rec["RareRate"], "category": rec["RareCat"],
                       "item": rec["RareItem"],
                       "_name": F.drop_item_name(rec["RareCat"], rec["RareItem"])}
        rows.append(row)
    return {"format": TABLE_FORMAT, "version": TABLE_VERSION,
            "game": detect_serial(iso), "note": note,
            "count": F.ENEMY_COUNT,
            "_help": "Edit values in place. 'break' is zone letters (A/B/C, max "
                     f"{F.BREAK_SEQ_SLOTS}, empty = cannot be broken). Affinities are "
                     "percentages in 5% steps; negative absorbs. '_name' fields are "
                     "read-only hints and are ignored on import.",
            "enemies": rows}

def parse_enemy_json(doc):
    """Validate a table document -> {record index: {field label: raw value}}.

    Raises ValueError with a row-specific message on anything unexpected."""
    if not isinstance(doc, dict) or doc.get("format") != TABLE_FORMAT:
        raise ValueError(f"not a {TABLE_FORMAT} file")
    if doc.get("version") != TABLE_VERSION:
        raise ValueError(f"table version {doc.get('version')!r} is not supported "
                         f"(this build reads version {TABLE_VERSION})")
    rows = doc.get("enemies")
    if not isinstance(rows, list) or not rows:
        raise ValueError("no 'enemies' array")

    plain = {f[0]: f for f in F.ENEMY_FIELDS + F.REWARD_FIELDS}
    caps = {f[0]: (1 << (8 * f[2])) - 1 for f in
            (F.ENEMY_FIELDS + F.REWARD_FIELDS + F.ENEMY_AFFINITY_FIELDS
             + F.ZONE_FIELDS + F.FLAG_FIELDS + F.STATUS_RES_FIELDS + F.DROP_FIELDS)}
    out = {}
    for n, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"row {n}: expected an object")
        i = row.get("index")
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < F.ENEMY_COUNT:
            raise ValueError(f"row {n}: 'index' must be 0..{F.ENEMY_COUNT - 1}, "
                             f"got {i!r}")
        where = f"enemy {i} ({row.get('name', '?')})"
        edits = {}

        def num(label, value, limit):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{where}: {label} must be a whole number, got {value!r}")
            if not 0 <= value <= limit:
                raise ValueError(f"{where}: {label} must be 0..{limit}, got {value}")
            return value

        for lbl in plain:
            if lbl in row:
                edits[lbl] = num(lbl, row[lbl], caps[lbl])

        if "break" in row:
            try:
                slots = F.encode_break_seq(row["break"])
            except ValueError as e:
                raise ValueError(f"{where}: break — {e}")
            edits.update({f"Brk{k + 1}": v for k, v in enumerate(slots)})

        aff = row.get("affinity")
        if aff is not None:
            if not isinstance(aff, dict):
                raise ValueError(f"{where}: 'affinity' must be an object")
            for el, pct in aff.items():
                if el not in F.AFFINITY_ELEMENTS:
                    raise ValueError(f"{where}: unknown element {el!r}; expected one "
                                     f"of {', '.join(F.AFFINITY_ELEMENTS)}")
                if isinstance(pct, bool) or not isinstance(pct, int):
                    raise ValueError(f"{where}: affinity {el} must be a whole "
                                     f"number of percent, got {pct!r}")
                if not F.AFFINITY_PCT_MIN <= pct <= F.AFFINITY_PCT_MAX:
                    raise ValueError(f"{where}: affinity {el} must be "
                                     f"{F.AFFINITY_PCT_MIN}..{F.AFFINITY_PCT_MAX}%, got {pct}")
                if pct % F.ENEMY_AFFINITY_SCALE:
                    raise ValueError(f"{where}: affinity {el} must be a multiple of "
                                     f"{F.ENEMY_AFFINITY_SCALE}% (stored as a byte "
                                     f"x{F.ENEMY_AFFINITY_SCALE}), got {pct}")
                edits[el] = F.affinity_byte(pct)

        res = row.get("resist")
        if res is not None:
            if not isinstance(res, dict):
                raise ValueError(f"{where}: 'resist' must be an object")
            for st, v in res.items():
                if st not in F.STATUS_RES_NAMES:
                    raise ValueError(f"{where}: unknown status {st!r}; expected one "
                                     f"of {', '.join(F.STATUS_RES_NAMES)}")
                edits[st] = num(f"resist {st}", v, 0xFF)

        for key, pre in (("drop", "Drop"), ("rare", "Rare")):
            d = row.get(key)
            if d is None:
                continue
            if not isinstance(d, dict):
                raise ValueError(f"{where}: {key!r} must be an object")
            for k, lbl in (("rate", pre + "Rate"), ("category", pre + "Cat"),
                           ("item", pre + "Item")):
                if k in d:
                    edits[lbl] = num(f"{key}.{k}", d[k], 0xFF)
        if edits:
            out[i] = edits
    if not out:
        raise ValueError("document contains no editable values")
    return out


# ---------------------------------------------------------------------------
# "What does this mod actually change?"
#
# The honest way to answer whether this editor can reproduce someone else's mod
# is to read their bytes, not their description. Given a pristine image and a
# modified one, this walks every differing byte run and says which known table it
# lands in — down to the record and field where we have one — and, more usefully,
# what lands OUTSIDE everything we understand.
#
# A run in "unmapped" is the interesting part: it is either a table we have not
# reverse-engineered, or code. Either way it is the honest answer to "can we edit
# everything it does": no, not that part, and here is where it is.
# ---------------------------------------------------------------------------
def _regions(disc):
    t = F.enemy_tables(disc)
    kb = F.skill_base(disc)
    # Ordered most-precise first, because the last entry's extent is a GUESS: the
    # name table's end is not known, so 0x4000 is a generous window that provably
    # overlaps real tables — it swallowed the dual-tech block until this was
    # ordered. A guessed region must never claim a byte a known one can explain.
    return [
        ("unit stats",    F.unit_tables(disc),
                          F.UNIT_COUNT * F.ENEMY_STRIDE + F.unit_record_tail(), "unit"),
        ("enemy stats",   t["stats"],   F.ENEMY_COUNT * F.ENEMY_STRIDE
                                        + F.enemy_record_tail(), "stat"),
        ("enemy rewards", t["rewards"], F.ENEMY_COUNT * F.REWARD_STRIDE, "reward"),
        ("skill blocks",  kb,           F.skill_span(disc), "skill"),
        ("skill costs",   F.skill_cost_base(disc), F.skill_cost_span(disc), "cost"),
        ("enemy names",   t["names"],   0x4000, None),
    ]

def _locate(off, disc):
    """('enemy stats', 'record 6 HP') for a byte offset, or (None, None)."""
    # Editable name text first, and matched against the individual name blobs
    # rather than the pool's bounding box — that box spans megabytes and would
    # otherwise claim every unmapped byte between the three pools.
    i = F.skill_name_at(off, disc)
    if i is not None:
        e = F.skill_catalog()[i]
        return "skill text", f"skill {i} name ({e.get('name')})"
    for name, base, size, kind in _regions(disc):
        if not base <= off < base + size:
            continue
        if kind == "unit":
            i, r = divmod(off - base, F.ENEMY_STRIDE)
            for lbl, fo, w, _k in F.UNIT_FIELDS + F.UNIT_AFFINITY_FIELDS:
                if fo <= r < fo + w:
                    return name, f"unit {i} {lbl}"
            return name, f"unit {i} +0x{r:02X} (undecoded)"
        if kind == "stat":
            i, r = divmod(off - base, F.ENEMY_STRIDE)
            for lbl, fo, w, _k in (F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS
                                   + F.ZONE_FIELDS + F.FLAG_FIELDS
                                   + F.STATUS_RES_FIELDS):
                if fo <= r < fo + w:
                    return name, f"record {i} {lbl}"
            return name, f"record {i} +0x{r:02X} (undecoded)"
        if kind == "reward":
            i, r = divmod(off - base, F.REWARD_STRIDE)
            for lbl, fo, w, _k in F.REWARD_FIELDS + F.DROP_FIELDS:
                if fo <= r < fo + w:
                    return name, f"record {i} {lbl}"
            return name, f"record {i} +0x{r:02X} (undecoded)"
        if kind == "skill":
            for _n, b, count, t0 in F.skill_blocks(disc):
                if b <= off < b + count * F.SKILL_STRIDE:
                    i, r = divmod(off - b, F.SKILL_STRIDE)
                    for lbl, fo, w, _k in F.SKILL_NUM_FIELDS:
                        if fo <= r < fo + w:
                            return name, f"skill {t0 + i} {lbl}"
                    return name, f"skill {t0 + i} +0x{r:02X} (undecoded)"
            return None, None          # a gap between blocks is not ours to claim
        if kind == "cost":
            i, r = divmod(off - base, F.SKILL_COST_STRIDE)
            if r == F.SKILL_COST_SLOT_OFF:
                key = F.SECRET_SKILL_GATES.get(i)
                return name, (f"record {i} Secret Key gate (key {key})" if key
                              else f"record {i} Slot")
            for lbl, fo, w, _k in F.SKILL_COST_FIELDS:
                if fo <= r < fo + w:
                    return name, f"record {i} {lbl}"
            if r == F.SKILL_COST_TYPE_OFF: return name, f"record {i} Type"
            if r == F.SKILL_COST_ID_OFF:   return name, f"record {i} Id"
            return name, f"record {i} +0x{r:02X} (undecoded)"
        return name, None
    return None, None

def diff_images(pristine, modded, chunk=1 << 22, block=4096):
    """[(offset, length), ...] byte runs that differ. Streams both files.

    Hierarchical on purpose: a flat Python byte loop over a 4.6 GB pair is
    billions of iterations. Compare whole chunks first, then 4 KB blocks within a
    differing chunk, and only fall to per-byte scanning inside a block that
    actually differs. A real mod touches a few hundred bytes, so almost
    everything is settled by the two `!=` comparisons on bytes objects, which run
    in C.
    """
    runs = []
    pending = None            # (start, end) of a run still being extended

    def add(start, end):
        nonlocal pending
        if pending and pending[1] == start:
            pending = (pending[0], end)
        else:
            if pending:
                runs.append((pending[0], pending[1] - pending[0]))
            pending = (start, end)

    with open(pristine, "rb") as a, open(modded, "rb") as b:
        pos = 0
        while True:
            x, y = a.read(chunk), b.read(chunk)
            if not x and not y:
                break
            n = max(len(x), len(y))
            x = x.ljust(n, b"\0")
            y = y.ljust(n, b"\0")
            if x != y:
                for bs in range(0, n, block):
                    bx, by = x[bs:bs + block], y[bs:bs + block]
                    if bx == by:
                        continue
                    start = None
                    for k in range(len(bx)):
                        if bx[k] != by[k]:
                            if start is None:
                                start = k
                        elif start is not None:
                            add(pos + bs + start, pos + bs + k)
                            start = None
                    if start is not None:
                        add(pos + bs + start, pos + bs + len(bx))
            pos += n
    if pending:
        runs.append((pending[0], pending[1] - pending[0]))
    return runs


def _skill_off(iso, i):
    off = F.skill_record_off(iso.disc, i)
    if off is None:
        lo = F.skill_editable_indices(iso.disc)
        raise SystemExit(
            f"skill {i} has no verified numeric record. Editable indices are "
            f"{lo[0]}..{lo[56]} and {lo[57]}..{lo[-1]} — the tech and "
            f"combination blocks use a different record layout (see the notes).")
    return off

# ---------------------------------------------------------------------------
# PLAYER UNITS — the 15-record table before the enemy table (x2fields.UNIT_*).
# Same 0x5C record structure; only the verified fields are addressable.
# ---------------------------------------------------------------------------
def unit_base(iso, i):
    return F.unit_tables(iso.disc) + i * F.ENEMY_STRIDE

def read_unit(iso, i):
    base = unit_base(iso, i)
    out = {lbl: int.from_bytes(iso.read(base + o, w), "little")
           for (lbl, o, w, _k) in F.UNIT_FIELDS + F.UNIT_AFFINITY_FIELDS}
    out["id"] = int.from_bytes(iso.read(base + F.UNIT_ID_OFF, 2), "little")
    return out

def unit_affinity_pcts(rec):
    """{element: percent} for one unit, same signed-byte x5 scale as enemies."""
    return {n: F.affinity_pct(rec[n]) for n in F.AFFINITY_ELEMENTS}

def unit_name(iso, i):
    ptr = int.from_bytes(iso.read(unit_base(iso, i) + F.UNIT_NAME_PTR_OFF, 2),
                         "little")
    raw = iso.read(F.UNIT_NAME_BASE[iso.disc] + ptr, 24)
    return raw.split(b"\x00", 1)[0].decode("euc-jp", errors="replace")

def write_unit(iso, i, edits):
    base = unit_base(iso, i)
    n = 0
    for (lbl, o, w, _k) in F.UNIT_FIELDS + F.UNIT_AFFINITY_FIELDS:
        if lbl in edits and edits[lbl] is not None:
            v = max(0, min(int(edits[lbl]), (1 << (8 * w)) - 1))
            iso.write(base + o, v.to_bytes(w, "little"))
            n += 1
    return n

def sync_units(src, dst):
    """Copy the unit table between discs, through the field path."""
    recs = fields = 0
    for i in range(F.UNIT_COUNT):
        want, have = read_unit(src, i), read_unit(dst, i)
        delta = {k: want[k] for k, _o, _w, _kk in
                 F.UNIT_FIELDS + F.UNIT_AFFINITY_FIELDS if want[k] != have[k]}
        if delta:
            fields += write_unit(dst, i, delta)
            recs += 1
    return recs, fields

# ---------------------------------------------------------------------------
# Skill name text — see x2fields' SKILL NAME TEXT block for why only the blob
# at nameOff is rewritten and not every occurrence on the disc.
# ---------------------------------------------------------------------------
def _name_off(i):
    e = F.skill_catalog().get(i) or {}
    return e.get("nameOff")

def read_skill_text(iso, i):
    """{'name', 'retail', 'budget', 'meta'} for the skill's text blob, or None.

    `budget` and the description's position both come from the RETAIL name in
    the catalog, not from the disc — see x2fields.skill_name_budget().
    """
    entry = F.skill_catalog().get(i) or {}
    off = entry.get("nameOff")
    if not off:
        return None
    retail = entry.get("name") or ""
    budget = F.skill_name_budget(retail)
    raw = iso.read(off, budget + 0x100)
    name = raw[:budget].split(b"\x00", 1)[0].decode("latin1")
    # Only the ether/double and dual pools are NAME \0 META \0. The single-tech
    # and special names live in a flat menu-string list, where the bytes after
    # the terminator are simply the NEXT name — reading them as a description
    # prints "MICRO MISSILE" as MINIGUN's description.
    has_meta = bool(entry.get("desc") or entry.get("target"))
    meta = raw[budget:].split(b"\x00", 1)[0].decode("latin1") if has_meta else ""
    return {"name": name, "retail": retail, "budget": budget,
            "meta": meta, "off": off}

def write_skill_name(iso, i, new):
    """Rewrite a skill's name in place. Raises if it will not fit."""
    cur = read_skill_text(iso, i)
    if not cur:
        raise SystemExit(f"skill {i} has no name offset in the catalog")
    data = new.encode("latin1", errors="replace") + b"\x00"
    if len(data) > cur["budget"]:
        raise SystemExit(
            f"{new!r} needs {len(data)} bytes but the retail name "
            f"{cur['retail']!r} only allotted {cur['budget']} — a packed pool "
            f"cannot grow, so the replacement must be at most "
            f"{cur['budget'] - 1} characters")
    # pad to the full budget so a shorter name leaves no trailing fragment of
    # the previous one lying between the terminator and the description
    iso.write(cur["off"], data.ljust(cur["budget"], b"\x00"))
    return cur

# ---------------------------------------------------------------------------
# BATTLE CAPTIONS — the label a skill flashes on screen when it fires, stored
# per battle script as `$zoom13;<name>` and duplicated. See x2fields' BATTLE
# CAPTIONS block for why these are safe to write despite having no table.
#
# Everything here is content-addressed, and that is the point. Nothing below
# holds, caches or writes a constant offset: a caption is only ever written at an
# offset a scan of THIS image just returned. That is what separates it from the
# "writing at unconfirmed offsets" the notes forbid, and it means the code works
# unchanged on an image whose files have moved.
# ---------------------------------------------------------------------------
CAPTION_MAX = 64            # longest caption text read back; retail's are < 20

def scan_captions(iso, region=None, max_len=CAPTION_MAX):
    """[(offset, text), ...] for EVERY `$zoom13;` caption on the image.

    One pass for the whole disc, whatever the caller wanted out of it. Scanning
    per skill is the obvious shape and the wrong one: a pass over a 4.6 GB image
    costs about a minute, and putting 190 name needles in that pass costs 190
    buffer searches per chunk. Harvesting the single prefix and matching the text
    afterwards is one needle and one pass, and it yields the disc's whole caption
    census for free.

    `offset` points at the PREFIX, so a rewrite lands at offset + len(prefix).
    """
    start, end = region or (0, None)
    # Materialise the offsets before reading any of them. find_multi streams
    # through the same file handle this loop would seek, so reading mid-iteration
    # derails the scan and silently loses hits.
    offs = [off for off, _n in
            iso.find_multi([F.CAPTION_PREFIX], start=start, end=end)]
    n = len(F.CAPTION_PREFIX)
    return [(off, iso.read(off + n, max_len).split(b"\x00", 1)[0].decode("latin1"))
            for off in offs]

def caption_owners(iso, captions=None, indices=None):
    """Attribute captions to skills. Returns (owned, ambiguous).

    `owned` is {skill index: [(offset, text), ...]}; `ambiguous` is
    {text: [index, ...]} for captions more than one skill could claim.

    A caption is matched against BOTH the skill's retail catalog name and the
    name currently in the disc's name pool. Both are needed and neither is
    sufficient on its own:

      - retail alone stops finding a skill's captions the moment they have been
        rewritten once, so a second rename would silently do nothing;
      - current alone misses the half-applied disc a patch import leaves behind,
        where the name pool already says "Flare" while seven captions still say
        "Miracle Star".

    A name two skills both claim is attributed to neither and reported through
    `ambiguous` — the alternative is renaming somebody else's caption.
    """
    caps = scan_captions(iso) if captions is None else captions
    want = set(indices) if indices is not None else None
    claims = {}                                  # text -> {index, ...}
    for i, e in F.skill_catalog().items():
        if want is not None and i not in want:
            continue
        retail = e.get("name") or ""
        if not retail:
            continue
        names = {retail}
        cur = read_skill_text(iso, i)
        if cur and cur["name"]:
            names.add(cur["name"])
        for nm in names:
            claims.setdefault(nm, set()).add(i)
    owned, ambiguous = {}, {}
    for off, text in caps:
        who = claims.get(text)
        if not who:
            continue
        if len(who) > 1:
            ambiguous[text] = sorted(who)
            continue
        owned.setdefault(next(iter(who)), []).append((off, text))
    return owned, ambiguous

def caption_spans(iso, captions=None):
    """[(start, length), ...] of every caption blob on the image, prefix included.

    These are content-confirmed extents, not table offsets: the span exists
    because a scan of THIS image just found the string sitting there. That is
    what lets a patch importer write inside one without breaking the rule that
    nothing is written at an offset nothing has confirmed.
    """
    caps = scan_captions(iso) if captions is None else captions
    n = len(F.CAPTION_PREFIX)
    return [(off, n + len(text) + 1) for off, text in caps]

def caption_fits(retail, new):
    """(ok, needed, budget) for writing `new` into a caption of `retail`."""
    budget = F.caption_budget(retail)
    needed = len(new.encode("latin1", errors="replace")) + 1
    return needed <= budget, needed, budget

def write_caption(iso, off, retail, new):
    """Rewrite ONE caption in place, at an offset a scan just returned.

    `off` points at the prefix. The budget comes from `retail` — the caption's
    original text — not from whatever is on the disc now.
    """
    ok, needed, budget = caption_fits(retail, new)
    if not ok:
        raise SystemExit(
            f"caption {new!r} needs {needed} bytes but {retail!r} only allotted "
            f"{budget} — the caption pool is packed and cannot grow")
    data = new.encode("latin1", errors="replace") + b"\x00"
    # pad the full budget for the same reason the name pool does: a shorter
    # replacement must not leave a fragment of the previous caption behind
    iso.write(off + len(F.CAPTION_PREFIX), data.ljust(budget, b"\x00"))

def rename_captions(iso, i, new, hits=None):
    """Write `new` into every located caption of skill `i`. Returns how many.

    Raises before writing anything if the name does not fit, so a skill's copies
    can never end up half-rewritten.
    """
    retail = (F.skill_catalog().get(i) or {}).get("name") or ""
    if hits is None:
        owned, _amb = caption_owners(iso, indices=[i])
        hits = owned.get(i, [])
    if not hits:
        return 0
    ok, needed, budget = caption_fits(retail, new)
    if not ok:
        raise SystemExit(
            f"skill {i}: {new!r} needs {needed} caption bytes but the retail "
            f"caption {retail!r} only allotted {budget}")
    for off, _text in hits:
        write_caption(iso, off, retail, new)
    return len(hits)

def find_captions(path, indices, note=True):
    """(owned, ambiguous) for one image on disc, opened read-only.

    Separate from the write step, and the separation is load-bearing. A caption
    is attributed by the text it currently holds, so this has to run BEFORE the
    name pool is rewritten: once "Flare" has been overwritten with "Fire", the
    disc holds no string tying the seven captions still reading "Flare" to that
    skill, and a second rename finds nothing. Locating first is what makes
    renaming repeatable.
    """
    if note:
        print(f"  scanning {os.path.basename(path)} for battle captions "
              f"(one pass over the image)…")
    with Iso(path) as iso:
        return caption_owners(iso, indices=list(indices))

def apply_captions(path, plan, located):
    """Write {index: new name} into the captions `located` found. (skills, copies).

    Reports per skill rather than in aggregate, including the skills whose name
    fitted the name pool but will not fit the caption — the two budgets are
    independent, so "renamed" does not imply "caption renamed".
    """
    owned, ambiguous = located
    skills = copies = 0
    with Iso(path, write=True) as iso:
        for i, new in sorted(plan.items()):
            hits = owned.get(i, [])
            retail = (F.skill_catalog().get(i) or {}).get("name") or ""
            if not hits:
                print(f"  skill {i}: no battle caption on this image "
                      f"(not every skill has one)")
                continue
            ok, needed, budget = caption_fits(retail, new)
            if not ok:
                print(f"  ! skill {i}: {len(hits)} caption(s) left unchanged — "
                      f"{new!r} needs {needed} bytes, the caption budget is "
                      f"{budget}. The name pool allowed it; the caption pool is "
                      f"a separate, shorter space.")
                continue
            copies += rename_captions(iso, i, new, hits)
            skills += 1
            print(f"  skill {i}: {len(hits)} battle caption(s) -> {new!r}")
    for text, who in sorted(ambiguous.items()):
        print(f"  ! caption {text!r} is claimed by skills {who} — left alone")
    return skills, copies


def read_skill_at(iso, off):
    """Named numeric fields of the skill record based at an absolute offset.

    Reads each field at base+offset rather than slicing a single 32-byte buffer:
    Target is at -0x04, and a negative index into a bytes object silently reads
    from the far end instead of failing.
    """
    return {lbl: int.from_bytes(iso.read(off + o, w), "little")
            for (lbl, o, w, _k) in F.SKILL_NUM_FIELDS}

def read_skill(iso, i):
    """Named numeric fields of the skill at TEXT INDEX `i`."""
    return read_skill_at(iso, _skill_off(iso, i))

def write_skill(iso, i, edits):
    """Write named numeric fields of the skill at TEXT INDEX `i`."""
    base = _skill_off(iso, i)
    n = 0
    for (lbl, off, w, _k) in F.SKILL_NUM_FIELDS:
        if lbl in edits and edits[lbl] is not None:
            v = max(0, min(int(edits[lbl]), (1 << (8 * w)) - 1))
            iso.write(base + off, v.to_bytes(w, "little"))
            n += 1
    return n

# ---------------------------------------------------------------------------
# Passive / gear / cost record readers.
#
# All three are read-only here on purpose: the web editor writes them, and what
# the CLI needs is a way to lift the RETAIL values off a pristine disc so the
# editors have a baseline to compare against. Without one, "Compare to retail"
# can only ever answer for the enemy, unit and skill tables, and quietly says
# nothing about the other three.
# ---------------------------------------------------------------------------
def read_passive(iso, i):
    """Named fields of the passive/equip record at catalog index `i`."""
    base = F.passive_record_off(iso.disc, i)
    out = {lbl: int.from_bytes(iso.read(base + o, w), "little")
           for (lbl, o, w, _k) in F.PASSIVE_FIELDS}
    out["Kind"] = iso.read(base + F.PASSIVE_KIND_OFF, 1)[0]
    return out

def read_gear(iso, k):
    """Named fields of the E.S. accessory effect record at table index `k`.

    Same 12-byte layout as a passive — the gear records are that table's tail —
    so it reads through the same field list rather than a copy of it.
    """
    base = F.gear_record_off(iso.disc, k)
    out = {lbl: int.from_bytes(iso.read(base + o, w), "little")
           for (lbl, o, w, _k) in F.PASSIVE_FIELDS}
    out["Kind"] = iso.read(base + F.PASSIVE_KIND_OFF, 1)[0]
    return out

def read_cost(iso, k):
    """Named fields of the skill purchase-cost record at table index `k`.

    Type/Id/Slot come back alongside Cost because the ids are data: the HardType
    mod re-prices four ethers by SWAPPING id bytes, so a Cost-only baseline
    reports "unchanged" on records it demonstrably rewrote.
    """
    base = F.skill_cost_record_off(iso.disc, k)
    out = {lbl: int.from_bytes(iso.read(base + o, w), "little")
           for (lbl, o, w, _k) in F.SKILL_COST_FIELDS}
    for lbl, off in (("Type", F.SKILL_COST_TYPE_OFF), ("Id", F.SKILL_COST_ID_OFF),
                     ("Slot", F.SKILL_COST_SLOT_OFF)):
        out[lbl] = iso.read(base + off, 1)[0]
    return out

# ---------------------------------------------------------------------------
# SECRET-SKILL GATING — the cost record's `slot` byte is the Secret Key index.
#
# Retail: 31 records carry slot 1..31, one per Secret Key, and the game shows
# those skills as "???" until you hold the key. Zeroing the byte should drop the
# requirement and leave the skill an ordinary purchase. This is the only edit in
# this file inferred from a field's MEANING rather than watched being read, so
# every entry point says so out loud instead of burying it in a docstring.
# ---------------------------------------------------------------------------
def read_secret_key_gates(iso):
    """[{key, record, skill, cost, gated}] for all 31 secret skills, key order."""
    names = F.skill_names()
    out = []
    for rec, key in sorted(F.SECRET_SKILL_GATES.items(), key=lambda kv: kv[1]):
        c = read_cost(iso, rec)
        ci = F.skill_cost_catalog_index(c["Type"], c["Id"])
        out.append({"key": key, "record": rec, "cost": c["Cost"],
                    "skill": names.get(ci, f"skill {ci}"),
                    "gated": c["Slot"] != 0})
    return out


def write_secret_key_gates(iso, required):
    """Require (True) or drop (False) the Secret Key on all 31 secret skills.

    Returns the number of records actually changed. Restoring is exact because
    the retail slot values are recorded in F.SECRET_SKILL_GATES, so this works
    on a disc that has already been patched."""
    changed = 0
    for rec, key in F.SECRET_SKILL_GATES.items():
        off = F.skill_cost_record_off(iso.disc, rec) + F.SKILL_COST_SLOT_OFF
        want = key if required else 0
        if iso.read(off, 1)[0] != want:
            iso.write(off, bytes([want]))
            changed += 1
    return changed


def sync_skills(src, dst):
    """Copy every verified skill block from one disc to the other."""
    moved = 0
    sblocks = {b[0]: b for b in F.skill_blocks(src.disc)}
    for name, dbase, count, _t0 in F.skill_blocks(dst.disc):
        _n, sbase, scount, _s0 = sblocks[name]
        span = min(count, scount) * F.SKILL_STRIDE
        blob = src.read(sbase, span)
        if dst.read(dbase, span) != blob:
            dst.write(dbase, blob)
            moved += min(count, scount)
    return moved


