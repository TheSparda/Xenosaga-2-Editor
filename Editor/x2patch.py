#!/usr/bin/env python3
"""
Xenosaga Episode II (USA) ISO patcher / research tool.

Counterpart to Suikoden 3's s3patch.py. Xenosaga II ships on TWO discs:
  Disc 1  SLUS-20892   (BOOT2 SLUS_208.92, VER 1.00)
  Disc 2  SLUS-21133   (BOOT2 SLUS_211.33)

Status: the VERIFIED half (serial/volume detection, raw read/search/backup) works
today. The editable data tables (characters, techs/ether, gear, enemies, shops) are
NOT reverse-engineered yet — that's the RESEARCH half. Nothing is written blind.

Usage examples:
  python3 x2patch.py verify     "ISO/....(Disc 1).iso"
  python3 x2patch.py info       "ISO/....(Disc 1).iso"
  python3 x2patch.py find-bytes "ISO/..." --hex "58 65 6E 6F"
  python3 x2patch.py dump-region "ISO/..." --off 0x8000 --len 256
"""
import argparse, json, os, struct, sys, shutil, datetime, re, collections
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_verify(a):
    with Iso(a.iso) as iso:
        ok, serial, disc, vol = check_version(iso)
        tag = "OK" if ok else "UNRECOGNIZED"
        print(f"[{tag}] serial={serial} disc={disc} volume={vol!r} size={iso.size:,} bytes")
        return 0 if ok else 2

def cmd_info(a):
    with Iso(a.iso) as iso:
        ok, serial, disc, vol = check_version(iso)
        print(f"path    : {a.iso}")
        print(f"size    : {iso.size:,} bytes")
        print(f"serial  : {serial}")
        print(f"disc    : {disc}")
        print(f"volume  : {vol!r}")
        print(f"known X2: {ok}")

def cmd_find_bytes(a):
    needle = bytes(int(x, 16) for x in a.hex.split())
    with Iso(a.iso) as iso:
        off = iso.find(needle, start=a.start)
        print("not found" if off < 0 else f"found at 0x{off:X} ({off})")

def cmd_list(a):
    with Iso(a.iso) as iso:
        print(f"{'name':<18} {'lba':>8} {'size':>12}  dir?")
        for name, lba, size, isdir in iso.list_files():
            print(f"  {name:<16} {lba:>8} {size:>12}  {'D' if isdir else ''}")

def cmd_extract(a):
    with Iso(a.iso) as iso:
        blob = iso.extract_file(a.name)
        if blob is None:
            raise SystemExit(f"{a.name!r} not found in ISO root")
        out = a.out or a.name
        with open(out, "wb") as f:
            f.write(blob)
        print(f"extracted {a.name} -> {out} ({len(blob):,} bytes, magic {blob[:4].hex()})")

def cmd_strings(a):
    """Dump printable ASCII runs (offset: text) from a byte range — the game's
    item/skill/text tables live uncompressed in the disc-1 data region ~0x200CE00."""
    import re as _re
    with Iso(a.iso) as iso:
        data = iso.read(a.off, a.len)
    for m in _re.finditer(rb"[\x20-\x7e]{%d,}" % a.min, data):
        print(f"{a.off + m.start():08X}: {m.group().decode()}")

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

def cmd_enemy_list(a):
    """Print the whole bestiary straight from the disc (optionally as CSV)."""
    names = F.enemy_names()
    cols = _summary_cols() + (_affinity_cols() if a.affinities else [])
    with Iso(a.iso) as iso:
        require_version(iso)
        rows = [(i, names.get(i, "?"), read_enemy_id(iso, i), read_enemy(iso, i))
                for i in range(F.ENEMY_COUNT)]
    if a.csv:
        print(",".join(["idx", "name", "id"] + cols))
        for i, name, eid, rec in rows:
            print(",".join([str(i), '"' + name.replace('"', '""') + '"', str(eid)]
                           + [str(rec[c]) for c in cols]))
        return
    print(f"{'idx':>3}  {'name':<24} {'id':>4} " + " ".join(f"{c:>8}" for c in cols))
    for i, name, eid, rec in rows:
        print(f"{i:>3}  {name:<24} {eid:>4} " + " ".join(f"{rec[c]:>8}" for c in cols))

def cmd_enemy_get(a):
    with Iso(a.iso) as iso:
        require_version(iso)
        rec = read_enemy(iso, a.index)
        eid = read_enemy_id(iso, a.index)
    name = F.enemy_names().get(a.index, "?")
    print(f"{a.index:03d} · {name}   (enemy id {eid}"
          f"{', boss' if is_boss(eid) else ''})")
    for c in _summary_cols():
        van = vanilla_enemy(a.index).get(c)
        mark = "" if van is None or van == rec[c] else f"   (retail {van:,})"
        print(f"  {c:<5} {rec[c]:>10,}{mark}")
    common, rare = drops_of(rec)
    print(f"  {'drop':<5} {common:>10}")
    print(f"  {'rare':<5} {rare:>10}")
    seq = break_seq_of(rec)
    print(f"  {'zones':<5} {F.zone_mask_text(rec['Zones']) or '(none)':>10}"
          f"   (which heights this enemy can be hit at)")
    print(f"  {'break':<5} {seq or '(cannot)':>10}"
          f"   (hit these zones in order to Break it)")
    pcts = affinity_pcts(rec)
    print("  status resistance: " +
          "  ".join(f"{n} {rec[n]}%" for n in F.STATUS_RES_NAMES))
    print("  damage taken:")
    for name in F.AFFINITY_ELEMENTS:
        v = pcts[name]
        tag = "" if v == F.ENEMY_AFFINITY_NORMAL else (
            "  absorbs" if v < 0 else "  immune" if v == 0 else
            "  resists" if v < F.ENEMY_AFFINITY_NORMAL else "  WEAK")
        print(f"    {name:<8} {v:>5}%{tag}")
    print(f"  {AFFINITY_NOTE}")

def cmd_enemy_set(a):
    """Write named fields of one enemy record: --set HP=5000 --set EXP=1200."""
    edits = {}
    for pair in a.set or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects FIELD=VALUE, got {pair!r}")
        k, v = pair.split("=", 1)
        k = k.strip()
        known = {n.upper(): n for n in _enemy_field_names()}
        if k.upper() not in known:
            raise SystemExit(f"unknown field {k!r}; known: "
                             f"{', '.join(_enemy_field_names())}")
        edits[known[k.upper()]] = int(v, 0)
    if a.break_seq is not None:
        # friendlier than four --set Brk1=4 pairs, and it validates the sequence
        try:
            slots = F.encode_break_seq(a.break_seq)
        except ValueError as e:
            raise SystemExit(f"--break: {e}")
        edits.update({f"Brk{n + 1}": v for n, v in enumerate(slots)})
    if not edits:
        raise SystemExit("nothing to do — pass at least one --set FIELD=VALUE")
    if any(k in _affinity_cols() for k in edits):
        print(AFFINITY_NOTE)
    with Iso(a.iso) as iso:
        require_version(iso)
        before = read_enemy(iso, a.index)
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    with Iso(a.iso, write=True) as iso:
        n = write_enemy(iso, a.index, edits)
    with Iso(a.iso) as iso:
        after = read_enemy(iso, a.index)
    name = F.enemy_names().get(a.index, "?")
    print(f"wrote {n} field(s) to {a.index:03d} · {name}")
    for k in edits:
        print(f"  {k:<5} {before[k]:>10,} -> {after[k]:>10,}")
        if after[k] != max(0, min(edits[k], 0xFFFFFFFF)):
            print(f"  ! {k} did not read back as requested (clamped to field width?)")
    if a.also:
        _sync_to(a.iso, a.also)

def cmd_diff(a):
    """Show every enemy field that differs from the verified retail values."""
    names = F.enemy_names()
    with Iso(a.iso) as iso:
        require_version(iso)
        delta = diff_vanilla(iso)
    if not delta:
        print("disc matches the retail enemy tables exactly")
        return
    total = sum(len(v) for v in delta.values())
    print(f"{len(delta)} record(s), {total} field(s) differ from retail:")
    for i, fields in sorted(delta.items()):
        print(f"  {i:03d} {names.get(i, '?'):<22} " +
              ", ".join(f"{k} {have:,}<-{want:,}" for k, (have, want) in fields.items()))

def cmd_export_patch(a):
    """Write the disc's deviations from retail as a shareable patch file."""
    with Iso(a.iso) as iso:
        serial, _disc = require_version(iso)
        delta = diff_vanilla(iso)
    if not delta:
        raise SystemExit("disc matches retail — nothing to export")
    edits = {i: {k: have for k, (have, _want) in fields.items()}
             for i, fields in delta.items()}
    doc = make_patch(edits, note=a.note or "", serial=serial)
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {a.out} — {len(edits)} record(s), "
          f"{sum(len(v) for v in edits.values())} field(s)")

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

def cmd_apply_ppf(a):
    """Apply a PPF's mapped records to a disc; report the unreachable rest."""
    recs = parse_ppf(a.patch)
    with Iso(a.iso) as iso:
        require_version(iso)
        disc = iso.disc
    spans = _mapped_spans(disc)
    inside = lambda off, n: any(b <= off and off + n <= b + ln for b, ln in spans)
    doable = [(o, d) for o, d in recs if inside(o, len(d))]
    rest = [(o, d) for o, d in recs if not inside(o, len(d))]
    # Battle captions have no table, so nothing above can claim them — but they
    # are locatable by content, and a record that lands inside a caption THIS
    # image's own scan just found is not a write at an unconfirmed offset. That
    # is the whole difference, and it is why the scan has to happen here rather
    # than a list of known caption offsets being hardcoded.
    caps = []
    if rest and a.captions:
        with Iso(a.iso) as iso:
            cspans = caption_spans(iso)
        in_cap = lambda off, n: any(b <= off and off + n <= b + ln for b, ln in cspans)
        caps = [(o, d) for o, d in rest if in_cap(o, len(d))]
        rest = [(o, d) for o, d in rest if not in_cap(o, len(d))]
        doable += caps
    print(f"{len(recs)} record(s) in the patch; {len(doable)} land in mapped "
          f"tables ({sum(len(d) for _o, d in doable)} bytes)"
          + (f", {len(caps)} of them inside battle captions located by scanning "
             f"this image" if caps else "")
          + (f"; {len(rest)} do NOT — they fall outside every located table, so "
             f"writing them would mean writing at unconfirmed offsets. Use a PPF "
             f"tool on a pristine image if you need those too." if rest else ""))
    if a.dry_run:
        print("(dry run — nothing written)")
        return 0
    if not doable:
        raise SystemExit("nothing this editor can apply")
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    with Iso(a.iso, write=True) as iso:
        for off, data in doable:
            iso.write(off, data)
    print(f"✓ wrote {len(doable)} record(s)")
    if a.also:
        _sync_to(a.iso, a.also)
    return 0

def cmd_xdelta_make(a):
    n = make_xdelta(a.pristine, a.iso, a.out)
    print(f"wrote {a.out}  ({n:,} bytes)")
    print(f"apply with: xdelta3 -d -s <pristine ISO> {a.out} out.iso")
    return 0

def cmd_xdelta_apply(a):
    n = apply_xdelta(a.pristine, a.patch, a.out)
    print(f"wrote {a.out}  ({n:,} bytes)")
    return 0

def cmd_apply_patch(a):
    with open(a.patch) as f:
        doc = json.load(f)
    edits = parse_patch(doc)
    names = F.enemy_names()
    with Iso(a.iso) as iso:
        serial, _disc = require_version(iso)
        before = {i: read_enemy(iso, i) for i in edits}
    if doc.get("game") and doc["game"] != serial:
        print(f"! patch says {doc['game']}, this disc is {serial} — continuing anyway")
    if doc.get("note"):
        print(f"note: {doc['note']}")
    print(f"{len(edits)} record(s), {sum(len(v) for v in edits.values())} field(s):")
    for i, fields in sorted(edits.items())[:a.show]:
        print(f"  {i:03d} {names.get(i, '?'):<22} " +
              ", ".join(f"{k} {before[i][k]:,}->{v:,}" for k, v in fields.items()))
    if len(edits) > a.show:
        print(f"  … {len(edits) - a.show} more")
    if a.dry_run:
        print("dry run — nothing written.")
        return
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    with Iso(a.iso, write=True) as iso:
        recs, fields = apply_patch(iso, edits)
    print(f"applied {fields} field(s) across {recs} record(s)")
    if a.also:
        _sync_to(a.iso, a.also)

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

def cmd_shorten_breaks(a):
    """Drop every break sequence by N hits — the combo loop's tax, cut directly."""
    names = F.enemy_names()
    with Iso(a.iso) as iso:
        require_version(iso)
        recs = {i: read_enemy(iso, i) for i in range(F.ENEMY_COUNT)}
    seqs = {i: break_seq_of(r) for i, r in recs.items()}
    # zone targeting off => the sequence bytes are inert, so skip those records
    nozone = {i: r["NoZone"] for i, r in recs.items()}
    floor = F.BREAK_FLOOR_NONE if a.allow_unbreakable else F.BREAK_MIN_LEN
    plan = F.plan_break_shortening(seqs, a.steps, floor, nozone)
    if not plan:
        print("nothing to shorten — every sequence is already at the minimum")
        return 0
    print(f"shortening by {a.steps} hit(s): {len(plan)} of {F.ENEMY_COUNT} records\n")
    emptied = [i for i, _o, n in plan if not n]
    if emptied:
        print(f"  ⚠ {len(emptied)} enemy(s) lose their break sequence entirely and become "
              f"UNBREAKABLE — that makes those fights longer, not shorter\n")
    by_len = collections.Counter(len(o) for _i, o, _n in plan)
    print("  affected by original length: " +
          ", ".join(f"{n}-hit x{c}" for n, c in sorted(by_len.items(), reverse=True)))
    print()
    for i, old, new in plan[:a.show]:
        print(f"  {i:3d} {names.get(i, '?'):<24} {old:<5} → {new}")
    if len(plan) > a.show:
        print(f"  … {len(plan) - a.show} more (use --show)")
    if a.dry_run:
        print("\n(dry run — nothing written)")
        return 0
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    n = 0
    with Iso(a.iso, write=True) as iso:
        for i, _old, new in plan:
            slots = F.encode_break_seq(new)
            n += write_enemy(iso, i, {f"Brk{k + 1}": v for k, v in enumerate(slots)})
    print(f"\n✓ wrote {n} field(s) across {len(plan)} record(s)")
    if a.also:
        _sync_to(a.iso, a.also)
    return 0

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

def cmd_explain_diff(a):
    """Say what a modified disc changes, and whether this editor could do it."""
    with Iso(a.pristine) as iso:
        serial, disc = require_version(iso)
    print(f"comparing against {serial} (disc {disc})…")
    runs = diff_images(a.pristine, a.iso)
    if not runs:
        print("identical — nothing changed")
        return 0
    total = sum(n for _o, n in runs)
    print(f"{len(runs):,} changed byte run(s), {total:,} byte(s) total\n")
    buckets = collections.OrderedDict()
    for off, n in runs:
        region, what = _locate(off, disc)
        buckets.setdefault(region or "unmapped", []).append((off, n, what))
    for region, items in buckets.items():
        nb = sum(n for _o, n, _w in items)
        print(f"  {region:<15} {len(items):>5} run(s)  {nb:>8,} byte(s)"
              + ("   <-- this editor cannot reach it" if region == "unmapped" else ""))
    print()
    editable = sum(len(v) for k, v in buckets.items() if k != "unmapped")
    print(f"reproducible with this editor: {editable} of {len(runs)} run(s)")
    if "unmapped" in buckets:
        print("\nunmapped runs (a table we have not decoded, or code):")
        for off, n, _w in buckets["unmapped"][:a.show]:
            print(f"  0x{off:09X}  {n:,} byte(s)")
        if len(buckets["unmapped"]) > a.show:
            print(f"  … {len(buckets['unmapped']) - a.show} more (use --show)")
    if a.verbose:
        for region, items in buckets.items():
            if region == "unmapped":
                continue
            print(f"\n{region}:")
            for off, n, what in items[:a.show]:
                print(f"  0x{off:09X}  {n:>4}B  {what or ''}")
            if len(items) > a.show:
                print(f"  … {len(items) - a.show} more")
    return 0

def cmd_export_json(a):
    """Dump the whole enemy table as readable JSON for bulk editing."""
    with Iso(a.iso) as iso:
        require_version(iso)
        doc = enemy_json(iso, note=a.note or "")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {a.out} — {doc['count']} enemies")
    return 0

def cmd_import_json(a):
    """Validate a table document and write the values it differs on."""
    with open(a.json, encoding="utf-8") as f:
        doc = json.load(f)
    try:
        edits = parse_enemy_json(doc)
    except ValueError as e:
        raise SystemExit(f"rejected: {e}")
    names = F.enemy_names()
    with Iso(a.iso) as iso:
        require_version(iso)
        delta = {}
        for i, fields in sorted(edits.items()):
            cur = read_enemy(iso, i)
            d = {k: (cur[k], v) for k, v in fields.items() if cur.get(k) != v}
            if d:
                delta[i] = d
    if not delta:
        print("every value already matches the disc — nothing to write")
        return 0
    total = sum(len(v) for v in delta.values())
    print(f"{len(delta)} record(s), {total} field(s) differ:")
    for i, d in sorted(delta.items())[:a.show]:
        body = "  ".join(f"{k} {o}→{n}" for k, (o, n) in sorted(d.items()))
        print(f"  {i:3d} {names.get(i, '?'):<24} {body}")
    if len(delta) > a.show:
        print(f"  … {len(delta) - a.show} more (use --show)")
    if a.dry_run:
        print("\n(dry run — nothing written)")
        return 0
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    with Iso(a.iso, write=True) as iso:
        n = sum(write_enemy(iso, i, {k: v[1] for k, v in d.items()})
                for i, d in delta.items())
    print(f"wrote {n} field(s) across {len(delta)} record(s)")
    if a.also:
        _sync_to(a.iso, a.also)
    return 0

def cmd_sync(a):
    """Copy the enemy tables from one disc onto the other."""
    with Iso(a.src) as src, Iso(a.dst) as dst:
        require_version(src); require_version(dst)
        if src.disc == dst.disc:
            raise SystemExit(f"both images are disc {src.disc} — "
                             f"pass one of each")
        print(f"source : disc {src.disc} ({a.src})")
        print(f"target : disc {dst.disc} ({a.dst})")
        labels = [f[0] for f in (F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS +
                                 F.ZONE_FIELDS + F.FLAG_FIELDS + F.STATUS_RES_FIELDS +
                                 F.REWARD_FIELDS + F.DROP_FIELDS)]
        diff = []
        for i in range(F.ENEMY_COUNT):
            w, h = read_enemy(src, i), read_enemy(dst, i)
            d = {k: (h.get(k), w.get(k)) for k in labels if w.get(k) != h.get(k)}
            if d:
                diff.append((i, d))
    names = F.enemy_names()
    if not diff:
        print("the two discs already hold identical enemy tables — nothing to do")
        return 0
    print(f"\n{len(diff)} record(s) differ:")
    for i, d in diff[:a.show]:
        body = "  ".join(f"{k} {h}→{w}" for k, (h, w) in sorted(d.items()))
        print(f"  {i:3d} {names.get(i, '?'):<24} {body}")
    if len(diff) > a.show:
        print(f"  … {len(diff) - a.show} more (use --show)")
    if a.dry_run:
        print("\n(dry run — nothing written)")
        return 0
    if a.backup:
        print(f"backup -> {backup(a.dst)}")
    _sync_to(a.src, a.dst)
    return 0

def cmd_restore(a):
    """Put the retail values back — for the whole bestiary or named records."""
    only = None
    if a.only:
        only = {int(x, 0) for part in a.only for x in part.split(",") if x.strip()}
    names = F.enemy_names()
    with Iso(a.iso) as iso:
        require_version(iso)
        delta = diff_vanilla(iso)
    if only is not None:
        delta = {i: v for i, v in delta.items() if i in only}
    if not delta:
        print("nothing to restore — those records already match retail")
        return
    print(f"restoring {len(delta)} record(s), {sum(len(v) for v in delta.values())} field(s)")
    for i, fields in sorted(delta.items())[:a.show]:
        print(f"  {i:03d} {names.get(i, '?'):<22} " +
              ", ".join(f"{k} {have:,}->{want:,}" for k, (have, want) in fields.items()))
    if a.dry_run:
        print("dry run — nothing written.")
        return
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    n = 0
    with Iso(a.iso, write=True) as iso:
        for i, fields in delta.items():
            n += write_enemy(iso, i, {k: want for k, (_have, want) in fields.items()})
    print(f"restored {n} field(s)")
    if a.also:
        _sync_to(a.iso, a.also)
def cmd_rebalance(a):
    """Apply a battle-pacing profile to every enemy record (combo-loop tuning)."""
    prof = dict(F.profile(a.profile))
    for grp in ("regular", "major"):            # per-field CLI overrides
        scales = dict(prof.get(grp, {}))
        if a.rewards is not None:               # shorthand for --exp/--sp/--cp
            scales.update({"EXP": a.rewards, "SP": a.rewards, "CP": a.rewards})
        for lbl in ("HP", "STR", "VIT", "EATK", "EDEF", "DEX", "EVA", "AGL", "EXP", "SP", "CP"):
            v = getattr(a, lbl.lower(), None)
            if v is not None:                   # an explicit field wins over --rewards
                scales[lbl] = v
        prof[grp] = scales
    with Iso(a.iso, write=not a.dry_run) as iso:
        ok, serial, disc, _vol = check_version(iso)
        if not ok:
            raise SystemExit(f"need a Xenosaga II (USA) disc; this is {serial}")
        print(f"disc    : {serial} (disc {disc}), tables at 0x{iso.tables['stats']:X}")
        if disc == 1:
            print("note    : disc 2 carries the same tables — run the same profile on it "
                  "too, or enemies revert to retail at the disc swap.")
        if not disc_is_pristine(iso):
            print("! the enemy tables no longer match their verified retail values — this "
                  "disc was already edited; scaling again compounds the previous pass.")
            if not (a.force or a.dry_run):
                raise SystemExit("refusing to compound — pass --force if that's intended")
        plan = plan_rebalance(iso, prof, threshold=a.threshold, include_dummy=a.include_dummy)
        print(f"profile: {a.profile} — {F.profile(a.profile)['label']}")
        print(f"{F.profile(a.profile)['note']}")
        print(f"records affected: {len(plan)} of {F.ENEMY_COUNT} "
              f"(major = catalog HP >= {a.threshold or F.MAJOR_HP_THRESHOLD:,})\n")
        show = plan if a.verbose else plan[:12]
        for i, name, group, edits in show:
            body = "  ".join(f"{k} {v[0]:,}→{v[1]:,}" for k, v in edits.items())
            print(f"  {i:3d} {name:<24} [{group:7}] {body}")
        if len(show) < len(plan):
            print(f"  … {len(plan) - len(show)} more (use --verbose)")
        if a.dry_run:
            print("\n(dry run — nothing written)")
            return 0
        if a.backup:
            print(f"backup: {backup(a.iso)}")
        recs, fields = apply_rebalance(iso, plan)
        print(f"\n✓ wrote {fields} field(s) across {recs} record(s)")
    if a.also:
        _sync_to(a.iso, a.also)
    return 0

def cmd_verify_tables(a):
    """Confirm the enemy table at this disc's known base, or hunt for it."""
    with Iso(a.iso) as iso:
        ok, serial, disc, vol = check_version(iso)
        print(f"disc    : {serial} (disc {disc}) volume={vol!r}")
        if not ok:
            print("✗ not a recognized Xenosaga II image")
            return 2
        cat = F.enemy_catalog()
        known = iso.tables["stats"]
        matched, checked = _confirm_base(iso, cat, known)
        print(f"known base 0x{known:X} (disc {disc}): "
              f"{matched}/{checked} catalog records hold retail values")
        if matched >= 8:
            print("✓ enemy stat table present at the known offset — edits apply here")
            if matched != checked:
                print(f"  ! {checked - matched} record(s) already differ from retail — "
                      f"this disc has been edited (see `diff`)")
            return 0
        print("… not at the known offset; signature-scanning the whole image "
              "(one pass, a few minutes on a 4.6 GB disc)")
        hit = locate_enemy_table(iso)
        if not hit:
            print("✗ no enemy stat table on this disc — nothing here to rebalance.")
            return 1
        print(f"✓ FOUND at base 0x{hit['base']:X} (stride 0x{hit['stride']:X}, "
              f"anchor rec {hit['anchor']}, {hit['matched']}/{hit['checked']} records match)")
        if hit["base"] != known:
            print(f"  → not the base recorded for disc {disc} (0x{known:X}). Update "
                  f"F.ENEMY_TABLES[{disc}] before the editor patches this image.")
        return 0

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

def cmd_skill_rename(a):
    """Rename one skill, in place, within its existing byte budget.

    Battle captions are rewritten too unless --no-captions is given. That costs
    one scan pass per image, and it is the default because the alternative is a
    rename that is visibly half-applied: the menu says "Flare" and the attack
    still flashes "Miracle Star".
    """
    with Iso(a.iso) as iso:
        require_version(iso)
        cur = read_skill_text(iso, a.index)
    if not cur:
        raise SystemExit(f"skill {a.index} has no editable name")
    if a.name is None:
        renamed = "" if cur["name"] == cur["retail"] else f"  [retail: {cur['retail']!r}]"
        print(f"skill {a.index}: {cur['name']!r}  "
              f"(up to {cur['budget'] - 1} characters){renamed}")
        if cur["meta"]:
            print(f"  {cur['meta']}")
        if a.captions:
            with Iso(a.iso) as iso:
                owned, _amb = caption_owners(iso, indices=[a.index])
            hits = owned.get(a.index, [])
            print(f"  {len(hits)} battle caption(s) at " +
                  (", ".join(f"0x{o:X}" for o, _t in hits) if hits else "(none)"))
        return 0
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    # locate before writing — see find_captions() for why the order matters
    here = find_captions(a.iso, [a.index]) if a.captions else None
    there = (find_captions(a.also, [a.index])
             if a.captions and a.also else None)
    with Iso(a.iso, write=True) as iso:
        write_skill_name(iso, a.index, a.name)
    print(f"skill {a.index}: {cur['name']!r} -> {a.name!r}")
    if here:
        apply_captions(a.iso, {a.index: a.name}, here)
    if a.also:
        with Iso(a.also, write=True) as other:
            require_version(other)
            write_skill_name(other, a.index, a.name)
        print(f"  also written to {a.also}")
        if there:
            apply_captions(a.also, {a.index: a.name}, there)
    return 0


def cmd_captions(a):
    """Locate battle captions by content and report who owns them.

    Read-only on purpose: this is the census and the audit. Writing lives in
    skill-rename, where there is a new name to write.
    """
    with Iso(a.iso) as iso:
        require_version(iso)
        print(f"scanning {os.path.basename(a.iso)} (one pass over the image)…")
        caps = scan_captions(iso)
        idx = [a.index] if a.index is not None else None
        owned, ambiguous = caption_owners(iso, caps, indices=idx)
        names = {i: (read_skill_text(iso, i) or {}) for i in owned}
    print(f"{len(caps)} battle caption(s) on this image")
    if a.census:
        counts = collections.Counter(t for _o, t in caps)
        print(f"{len(counts)} distinct caption text(s); most duplicated:")
        for text, n in counts.most_common(a.show):
            print(f"  {n:>3}x  {text!r}")
    if a.index is not None and not owned:
        print(f"skill {a.index} has no battle caption on this image")
    for i, hits in sorted(owned.items()):
        cat = F.skill_catalog().get(i) or {}
        cur = names.get(i, {}).get("name") or cat.get("name") or "?"
        retail = cat.get("name") or ""
        tag = "" if cur == retail else f"  [retail: {retail!r}]"
        print(f"skill {i:>3} {cur!r}: {len(hits)} caption(s), "
              f"{F.caption_budget(retail) - 1} char budget{tag}")
        if a.verbose or a.index is not None:
            for off, text in hits:
                print(f"    0x{off:X}  {text!r}")
    for text, who in sorted(ambiguous.items()):
        print(f"! caption {text!r} is claimed by skills {who} — attributed to none")
    if a.grep:
        # The recovery path for an orphaned caption. Attribution is by content,
        # so a caption stops being attributable if its skill was renamed with
        # --no-captions and then renamed again: the disc still says "Flare"
        # while the name pool has moved on to "Fire", and nothing links them.
        # Searching the census by text finds it anyway.
        q = a.grep.lower()
        hits = [(o, t) for o, t in caps if q in t.lower()]
        print(f"{len(hits)} caption(s) matching {a.grep!r}:")
        for off, text in hits[:a.show]:
            print(f"    0x{off:X}  {text!r}")
    return 0

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

def cmd_units(a):
    """List the player-unit table (characters + E.S.) from the disc."""
    with Iso(a.iso) as iso:
        require_version(iso)
        print(f"{'idx':>3} {'name':<12} {'id':>4}  " +
              " ".join(f"{lbl:>5}" for lbl, _o, _w, _k in F.UNIT_FIELDS))
        for i in range(F.UNIT_COUNT):
            u = read_unit(iso, i)
            nm = unit_name(iso, i)
            print(f"{i:3} {nm:<12} {u['id']:>4}  " +
                  " ".join(f"{u[lbl]:>5}" for lbl, _o, _w, _k in F.UNIT_FIELDS))
        if a.affinities:
            print("\n" + " " * 21 + " ".join(f"{n:>7}" for n in F.AFFINITY_ELEMENTS))
            for i in range(F.UNIT_COUNT):
                pct = unit_affinity_pcts(read_unit(iso, i))
                print(f"{i:3} {unit_name(iso, i):<17} " +
                      " ".join(f"{pct[n]:>6}%" for n in F.AFFINITY_ELEMENTS))
            print(f"\n({AFFINITY_NOTE})")
            print("Retail leaves every unit flat at 100% on all eight, so nothing "
                  "cross-checks\nthat the game reads this block for player characters "
                  "— see the notes.")
    return 0

def cmd_unit_set(a):
    """Write fields of one unit record (e.g. --set HP=999 --set EP=50)."""
    edits = {}
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        allowed = [f[0] for f in F.UNIT_FIELDS + F.UNIT_AFFINITY_FIELDS]
        if k not in allowed:
            raise SystemExit(f"unknown unit field {k!r} — one of "
                             + ", ".join(allowed))
        edits[k] = int(v, 0)
    if not edits:
        raise SystemExit("nothing to write — pass --set FIELD=VALUE")
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    with Iso(a.iso, write=True) as iso:
        require_version(iso)
        nm = unit_name(iso, a.index)
        n = write_unit(iso, a.index, edits)
        after = read_unit(iso, a.index)
    print(f"wrote {n} field(s) to unit {a.index} ({nm}): "
          + ", ".join(f"{k}={after[k]}" for k in edits))
    if a.also:
        _sync_to(a.iso, a.also)
    return 0

def cmd_skills(a):
    """List the skill/tech catalog extracted from the disc."""
    cat = F.skill_catalog()
    rows = sorted(cat.items())
    if a.grep:
        needle = a.grep.lower()
        rows = [(o, v) for o, v in rows
                if needle in v["name"].lower() or needle in v["desc"].lower()
                or needle in " ".join(v["tags"]).lower()]
    if a.csv:
        print("offset,name,target,tags,ep,desc")
        for o, v in rows:
            desc = v["desc"].replace('"', "'")
            print(f'0x{o:X},"{v["name"]}","{v["target"]}",'
                  f'"{"|".join(v["tags"])}",{v["ep"] if v["ep"] is not None else ""},"{desc}"')
        return 0
    print(f"{len(rows)} skill(s)"
          + (f" matching {a.grep!r}" if a.grep else "") + "\n")
    for i, v in rows:
        if v.get("placeholder"):
            continue
        num = v.get("numeric") or {}
        ep = f"EP {num['ep']}" if num.get("ep") else (f"EP {v['ep']}" if v["ep"] else "")
        pw = f"pow {num['power']}" if "power" in num else ""
        el = F.skill_element_text(num["element"]) if num.get("element") else ""
        tags = "/".join(v["tags"])
        print(f"  {i:3d} {v['name']:<22} {v['target']:<28} {tags:<18} "
              f"{ep:<6} {pw:<8} {el}")
        if a.verbose and v["desc"]:
            print(f"      {v['desc']}")
    return 0

def cmd_skill_set(a):
    """Write numeric fields of one ether skill: --set Power=50 --set EP=2."""
    edits = {}
    known = {f[0].upper(): f[0] for f in F.SKILL_NUM_FIELDS}
    for pair in a.set or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects FIELD=VALUE, got {pair!r}")
        k, v = pair.split("=", 1)
        if k.strip().upper() not in known:
            raise SystemExit(f"unknown field {k!r}; known: "
                             f"{', '.join(f[0] for f in F.SKILL_NUM_FIELDS)}")
        edits[known[k.strip().upper()]] = int(v, 0)
    if not edits:
        raise SystemExit("nothing to do — pass at least one --set FIELD=VALUE")
    name = F.skill_names().get(a.index, "?")
    with Iso(a.iso) as iso:
        require_version(iso)
        before = read_skill(iso, a.index)
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    with Iso(a.iso, write=True) as iso:
        n = write_skill(iso, a.index, edits)
    with Iso(a.iso) as iso:
        after = read_skill(iso, a.index)
    print(f"wrote {n} field(s) to skill {a.index:3d} · {name}")
    for k in edits:
        print(f"  {k:<8} {before[k]:>6} -> {after[k]:>6}")
    if a.also:
        with Iso(a.iso) as src, Iso(a.also, write=True) as dst:
            require_version(src); require_version(dst)
            if src.disc == dst.disc:
                raise SystemExit("--also: both images are the same disc")
            n2 = sync_skills(src, dst)
        print(f"synced skill table onto the other disc"
              + ("" if n2 else " (already identical)"))
    return 0

def cmd_enemy_columns(a):
    """Survey the 65 undecoded bytes of the stat record — the break/zone hunt."""
    with Iso(a.iso) as iso:
        recs = read_records(iso, base=a.base)
    names = F.enemy_names()
    prof = column_profile(recs)
    print(f"{'off':>5} {'distinct':>8} {'min':>4} {'max':>5}  flags      top values")
    for c in prof:
        if a.interesting and (c["distinct"] < 2 or c["distinct"] > a.max_distinct):
            continue
        flags = "".join(k[0].upper() if c[k] else "·" for k in ("packed2", "nibble", "mask3"))
        top = " ".join(f"{v}×{n}" for v, n in c["top"])
        print(f"+0x{c['off']:02X} {c['distinct']:>8} {c['min']:>4} {c['max']:>5}  {flags:<10} {top}")
    print("\nflags: P=values decode as packed 2-bit zone symbols (1=A,2=B,3=C, 0=pad), "
          "N=every nibble <=3, M=all values <=7 (3-bit mask)")
    if a.show:
        i = a.show
        print(f"\nrecord {i} ({names.get(i, '?')}) raw:")
        rec = recs[i]
        for p in range(0, len(rec), 16):
            row = rec[p:p + 16]
            print(f"  +0x{p:02X}  " + " ".join(f"{b:02X}" for b in row))
    return 0

def cmd_find_zones(a):
    """Score the undecoded columns against ground-truth weak-zone strings."""
    truth, unmatched = load_zone_truth(a.truth)
    print(f"ground truth: {len(truth)} enemies matched to records"
          + (f", {len(unmatched)} unmatched" if unmatched else ""))
    for key, why in unmatched[:10]:
        print(f"  ? {key}: {why}")
    if len(truth) < 8:
        print("! fewer than 8 known enemies — the scan will produce coincidences.\n"
              "  Aim for 30+, including several that share a zone string.")
    groups = len({z for z in truth.values()})
    print(f"distinct zone strings: {groups}\n")
    with Iso(a.iso) as iso:
        recs = read_records(iso, base=a.base)
        cands = zone_scan(recs, truth)
        print(f"{'field':>10} {'consist':>8} {'resolv':>7} {'distinct':>8}")
        for c in [c for c in cands if c["distinct"] > 1][:a.top]:
            print(f"  +0x{c['off']:02X} u{c['width'] * 8:<3} {c['consistency']:>8.3f} "
                  f"{c['resolution']:>7.3f} {c['distinct']:>8}")
        best = cands[0] if cands else None
        if best and best["consistency"] == 1.0 and best["resolution"] > 0.5:
            print(f"\n✓ candidate: +0x{best['off']:02X} (u{best['width'] * 8}) — value ↔ zone map:")
            for v, zs in sorted(zone_mapping(recs, truth, best["off"], best["width"]).items()):
                print(f"    {v:>5} (0x{v:02X}, {v:08b}b) → {', '.join(zs)}")
            print("\n  If one value maps to exactly one zone string throughout, that's the "
                  "field. Record it in x2fields.ZONE_FIELDS and the offsets notes.")
        else:
            print("\n… no column in the record explains the zone strings.")
            if a.region:
                s, l = a.region
                strides = a.strides or [4, 8, 0x10, 0x14, 0x18, 0x20, 0x2C, 0x40, 0x5C]
                print(f"  sweeping 0x{s:X}..0x{s + l:X} for a parallel table "
                      f"(strides {', '.join(hex(x) for x in strides)})…")
                hits = scan_region_for_column(iso, truth, s, l, strides)
                for h in hits[:a.top]:
                    print(f"    base 0x{h['base']:X} stride 0x{h['stride']:X} "
                          f"resolution {h['resolution']:.3f} distinct {h['distinct']}")
                if not hits:
                    print("    nothing — widen --region or the stride set.")
            else:
                print("  Next: re-run with --region START,LEN to sweep for a separate "
                      "parallel table (the rewards table is laid out that way).")
    return 0

def cmd_dump_region(a):
    with Iso(a.iso) as iso:
        data = iso.read(a.off, a.len)
        for i in range(0, len(data), 16):
            row = data[i:i + 16]
            hexs = " ".join(f"{b:02X}" for b in row)
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
            print(f"{a.off + i:08X}  {hexs:<47}  {text}")


def main():
    p = argparse.ArgumentParser(description="Xenosaga II (USA) ISO tool")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("verify", help="check the disc is a known X2 image")
    sp.add_argument("iso"); sp.set_defaults(fn=cmd_verify)

    sp = sub.add_parser("info", help="print serial / disc / volume id")
    sp.add_argument("iso"); sp.set_defaults(fn=cmd_info)

    sp = sub.add_parser("find-bytes", help="locate a hex byte string in the image")
    sp.add_argument("iso"); sp.add_argument("--hex", required=True)
    sp.add_argument("--start", type=lambda x: int(x, 0), default=0)
    sp.set_defaults(fn=cmd_find_bytes)

    sp = sub.add_parser("list", help="list the ISO's root-directory files")
    sp.add_argument("iso"); sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("extract", help="extract a root file (e.g. the boot ELF)")
    sp.add_argument("iso"); sp.add_argument("--name", required=True)
    sp.add_argument("--out"); sp.set_defaults(fn=cmd_extract)

    sp = sub.add_parser("strings", help="dump ASCII strings (offset: text) from a range")
    sp.add_argument("iso")
    sp.add_argument("--off", type=lambda x: int(x, 0), required=True)
    sp.add_argument("--len", type=lambda x: int(x, 0), default=0x2000)
    sp.add_argument("--min", type=int, default=2, help="min run length")
    sp.set_defaults(fn=cmd_strings)

    sp = sub.add_parser("enemies", help="list every enemy's stats + rewards from the disc")
    sp.add_argument("iso"); sp.add_argument("--csv", action="store_true")
    sp.add_argument("--affinities", action="store_true",
                    help="also show the 8 unverified affinity slots")
    sp.set_defaults(fn=cmd_enemy_list)

    sp = sub.add_parser("enemy", help="show one enemy record")
    sp.add_argument("iso"); sp.add_argument("index", type=lambda x: int(x, 0))
    sp.set_defaults(fn=cmd_enemy_get)

    sp = sub.add_parser("enemy-set", help="write fields of one enemy (e.g. --set HP=5000)")
    sp.add_argument("iso"); sp.add_argument("index", type=lambda x: int(x, 0))
    sp.add_argument("--set", action="append", metavar="FIELD=VALUE")
    sp.add_argument("--break", dest="break_seq", metavar="SEQ",
                    help="set the Break sequence, e.g. CB or C-B-B (max "
                         "4 hits, zones A/B/C; empty string = cannot be broken)")
    sp.add_argument("--backup", action="store_true", help="copy the ISO to .bak first")
    sp.add_argument("--also", metavar="OTHER_ISO",
                    help="after editing, copy the result onto the other disc so both stay in step")
    sp.set_defaults(fn=cmd_enemy_set)

    sp = sub.add_parser("diff", help="show enemy fields that differ from retail")
    sp.add_argument("iso"); sp.set_defaults(fn=cmd_diff)

    sp = sub.add_parser("export-patch",
                        help="save the disc's deviations from retail as a patch file")
    sp.add_argument("iso"); sp.add_argument("--out", required=True)
    sp.add_argument("--note", help="short description stored in the patch")
    sp.set_defaults(fn=cmd_export_patch)

    sp = sub.add_parser("apply-patch", help="apply a patch file to a disc")
    sp.add_argument("iso"); sp.add_argument("patch")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--show", type=int, default=20)
    sp.add_argument("--backup", action="store_true")
    sp.add_argument("--also", metavar="OTHER_ISO",
                    help="after editing, copy the result onto the other disc so both stay in step")
    sp.set_defaults(fn=cmd_apply_patch)

    sp = sub.add_parser("explain-diff",
                        help="say what a modified disc changes, and whether this "
                             "editor could reproduce it")
    sp.add_argument("iso", help="the MODIFIED image")
    sp.add_argument("--pristine", required=True)
    sp.add_argument("--show", type=int, default=20)
    sp.add_argument("--verbose", action="store_true", help="name every field touched")
    sp.set_defaults(fn=cmd_explain_diff)

    sp = sub.add_parser("apply-ppf",
                        help="apply a PPF3.0 patch's mapped records (mod import)")
    sp.add_argument("iso"); sp.add_argument("patch")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--backup", action="store_true")
    sp.add_argument("--also", metavar="OTHER_ISO")
    sp.add_argument("--no-captions", dest="captions", action="store_false",
                    help="don't scan for battle captions; records that land in "
                         "one are then reported as unreachable")
    sp.set_defaults(fn=cmd_apply_ppf, captions=True)

    sp = sub.add_parser("xdelta-make",
                        help="create an xdelta patch (pristine ISO -> edited ISO)")
    sp.add_argument("iso", help="the EDITED image")
    sp.add_argument("--pristine", required=True, help="an unmodified image of the same disc")
    sp.add_argument("--out", required=True)
    sp.set_defaults(fn=cmd_xdelta_make)

    sp = sub.add_parser("xdelta-apply",
                        help="apply an xdelta patch (pristine ISO + patch -> new ISO)")
    sp.add_argument("patch")
    sp.add_argument("--pristine", required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(fn=cmd_xdelta_apply)

    sp = sub.add_parser("shorten-breaks",
                        help="drop every enemy's break sequence by N hits")
    sp.add_argument("iso")
    sp.add_argument("--steps", type=int, default=1,
                    help="hits to remove (default 1)")
    sp.add_argument("--allow-unbreakable", action="store_true",
                    help="let a sequence be emptied. By default the last hit is kept, "
                         "because an empty sequence removes the break instead of "
                         "shortening it and makes the fight longer")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--show", type=int, default=15)
    sp.add_argument("--backup", action="store_true")
    sp.add_argument("--also", metavar="OTHER_ISO")
    sp.set_defaults(fn=cmd_shorten_breaks)

    sp = sub.add_parser("export-json", help="dump the whole enemy table as readable JSON")
    sp.add_argument("iso"); sp.add_argument("--out", required=True)
    sp.add_argument("--note", default="")
    sp.set_defaults(fn=cmd_export_json)

    sp = sub.add_parser("import-json", help="validate and apply an edited enemy table")
    sp.add_argument("iso"); sp.add_argument("json")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--show", type=int, default=20)
    sp.add_argument("--backup", action="store_true")
    sp.add_argument("--also", metavar="OTHER_ISO",
                    help="after importing, copy the result onto the other disc")
    sp.set_defaults(fn=cmd_import_json)

    sp = sub.add_parser("units", help="list the player-unit table (characters + E.S.)")
    sp.add_argument("iso")
    sp.add_argument("--affinities", action="store_true",
                    help="also show each unit's eight damage affinities")
    sp.set_defaults(fn=cmd_units)

    sp = sub.add_parser("unit-set", help="write fields of one player unit")
    sp.add_argument("iso"); sp.add_argument("index", type=int)
    sp.add_argument("--set", action="append", metavar="FIELD=VALUE")
    sp.add_argument("--backup", action="store_true")
    sp.add_argument("--also", metavar="OTHER_ISO",
                    help="after editing, copy the tables onto the other disc")
    sp.set_defaults(fn=cmd_unit_set)

    sp = sub.add_parser("skill-rename",
                        help="rename a skill in place (omit --name to inspect)")
    sp.add_argument("iso"); sp.add_argument("index", type=int)
    sp.add_argument("--name")
    sp.add_argument("--backup", action="store_true")
    sp.add_argument("--also", metavar="OTHER_ISO")
    sp.add_argument("--no-captions", dest="captions", action="store_false",
                    help="skip the battle captions: the rename then shows in "
                         "menus but not in battle, and renaming again will no "
                         "longer find those captions (see `captions --grep`)")
    sp.set_defaults(fn=cmd_skill_rename, captions=True)

    sp = sub.add_parser("captions",
                        help="locate battle captions ($zoom13;) by content")
    sp.add_argument("iso")
    sp.add_argument("--index", type=int, help="only this skill")
    sp.add_argument("--census", action="store_true",
                    help="also summarise every caption on the disc")
    sp.add_argument("--show", type=int, default=15,
                    help="how many rows --census prints (default 15)")
    sp.add_argument("--verbose", action="store_true", help="list every offset")
    sp.add_argument("--grep", metavar="TEXT",
                    help="find captions by text, whoever owns them")
    sp.set_defaults(fn=cmd_captions)

    sp = sub.add_parser("skills", help="list the skill/tech catalog read off the disc")
    sp.add_argument("--grep", help="filter by name, tag or description text")
    sp.add_argument("--csv", action="store_true")
    sp.add_argument("--verbose", action="store_true", help="also print descriptions")
    sp.set_defaults(fn=cmd_skills)

    sp = sub.add_parser("skill-set", help="write numeric fields of one ether skill")
    sp.add_argument("iso"); sp.add_argument("index", type=lambda x: int(x, 0))
    sp.add_argument("--set", action="append", metavar="FIELD=VALUE")
    sp.add_argument("--also", metavar="OTHER_ISO",
                    help="after editing, copy the skill table onto the other disc")
    sp.add_argument("--backup", action="store_true")
    sp.set_defaults(fn=cmd_skill_set)

    sp = sub.add_parser("sync", help="copy the enemy tables from one disc onto the other")
    sp.add_argument("src"); sp.add_argument("dst")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--show", type=int, default=20)
    sp.add_argument("--backup", action="store_true")
    sp.set_defaults(fn=cmd_sync)

    sp = sub.add_parser("restore", help="put the retail enemy values back")
    sp.add_argument("iso")
    sp.add_argument("--only", action="append", metavar="IDX[,IDX...]",
                    help="restore just these records (default: all)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--show", type=int, default=20)
    sp.add_argument("--backup", action="store_true")
    sp.add_argument("--also", metavar="OTHER_ISO",
                    help="after editing, copy the result onto the other disc so both stay in step")
    sp.set_defaults(fn=cmd_restore)

    sp = sub.add_parser("rebalance", help="apply a battle-pacing profile to every enemy")
    sp.add_argument("iso")
    sp.add_argument("--profile", default="faster", choices=sorted(F.PROFILES),
                    help="; ".join(f"{k}: {v['label']}" for k, v in F.PROFILES.items()))
    sp.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    sp.add_argument("--verbose", action="store_true", help="list every affected record")
    sp.add_argument("--backup", action="store_true", help="copy the ISO to .bak first")
    sp.add_argument("--force", action="store_true", help="allow scaling an already-edited disc")
    sp.add_argument("--include-dummy", action="store_true", help="also scale debug/unused records")
    sp.add_argument("--threshold", type=int, help=f"HP at/above which a record counts as "
                    f"'major' (default {F.MAJOR_HP_THRESHOLD:,})")
    sp.add_argument("--rewards", type=int, metavar="PCT",
                    help="override EXP, SP and CP together (percent)")
    for lbl in ("HP", "STR", "VIT", "EATK", "EDEF", "DEX", "EVA", "AGL", "EXP", "SP", "CP"):
        sp.add_argument(f"--{lbl.lower()}", type=int, metavar="PCT",
                        help=f"override {lbl} scaling for both groups (percent)")
    sp.add_argument("--also", metavar="OTHER_ISO",
                    help="after editing, copy the result onto the other disc so both stay in step")
    sp.set_defaults(fn=cmd_rebalance)

    sp = sub.add_parser("verify-tables", help="confirm/locate the enemy table on a disc")
    sp.add_argument("iso"); sp.set_defaults(fn=cmd_verify_tables)

    sp = sub.add_parser("enemy-columns", help="profile the undecoded bytes of the stat record")
    sp.add_argument("iso")
    sp.add_argument("--base", type=lambda x: int(x, 0), help="override the table base")
    sp.add_argument("--interesting", action="store_true", help="hide constant/high-entropy columns")
    sp.add_argument("--max-distinct", type=int, default=32)
    sp.add_argument("--show", type=int, metavar="IDX", help="also hex-dump one record")
    sp.set_defaults(fn=cmd_enemy_columns)

    sp = sub.add_parser("find-zones", help="hunt the weak-zone/break field with ground truth")
    sp.add_argument("iso")
    sp.add_argument("--truth", required=True, help="JSON or CSV of enemy name -> zone string")
    sp.add_argument("--base", type=lambda x: int(x, 0), help="override the table base")
    sp.add_argument("--top", type=int, default=12)
    sp.add_argument("--region", type=lambda s: tuple(int(x, 0) for x in s.split(",")),
                    metavar="START,LEN", help="also sweep this range for a parallel table")
    sp.add_argument("--strides", type=lambda s: [int(x, 0) for x in s.split(",")],
                    help="strides to try in the region sweep")
    sp.set_defaults(fn=cmd_find_zones)

    sp = sub.add_parser("dump-region", help="hex-dump a byte range")
    sp.add_argument("iso")
    sp.add_argument("--off", type=lambda x: int(x, 0), required=True)
    sp.add_argument("--len", type=lambda x: int(x, 0), default=256)
    sp.set_defaults(fn=cmd_dump_region)

    a = p.parse_args()
    try:
        rc = a.fn(a)
    except (ValueError, OSError) as e:
        # bad patch file, unreadable image, etc. — a message beats a traceback
        raise SystemExit(f"error: {e}")
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
