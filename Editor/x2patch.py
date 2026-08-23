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
import argparse, json, os, struct, sys, shutil, datetime, re
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
# ENEMY read/write (disc 1, VERIFIED). Stat records at F.ENEMY_TABLE_OFF and a
# parallel rewards table at F.REWARD_TABLE_OFF — both indexed by record number.
# ---------------------------------------------------------------------------
def _enemy_tables(i):
    return ((F.ENEMY_TABLE_OFF + i * F.ENEMY_STRIDE,
             F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS),
            (F.REWARD_TABLE_OFF + i * F.REWARD_STRIDE, F.REWARD_FIELDS))

def read_enemy(iso, i):
    out = {}
    for base, fields in _enemy_tables(i):
        for (lbl, off, w, _k) in fields:
            out[lbl] = int.from_bytes(iso.read(base + off, w), "little")
    return out

def read_enemy_id(iso, i):
    """The record's own enemy id (+0x52). Read from the disc rather than taken
    from the shipped catalog so a partly-modified disc still classifies right."""
    off = F.ENEMY_TABLE_OFF + i * F.ENEMY_STRIDE + F.ENEMY_ID_OFF
    return int.from_bytes(iso.read(off, 2), "little")

def is_boss(enemy_id):
    return enemy_id >= F.BOSS_ID_MIN


# ---------------------------------------------------------------------------
# Vanilla comparison + shareable patch files.
#
# x2_enemies.json holds the verified retail values, so a disc can be diffed
# against it without a pristine copy to compare with — which also means edits can
# be exported as a small text file others can apply to their own disc.
# ---------------------------------------------------------------------------
PATCH_FORMAT = "x2-enemy-patch"
PATCH_VERSION = 1

def vanilla_enemy(i, catalog=None):
    """The retail values for record `i`, keyed by field label (affinities are not
    in the catalog, so they are absent)."""
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
    known = {f[0] for f in F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS + F.REWARD_FIELDS}
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
    for base, fields in _enemy_tables(i):
        for (lbl, off, w, _k) in fields:
            if lbl in edits and edits[lbl] is not None:
                v = max(0, min(int(edits[lbl]), (1 << (8 * w)) - 1))
                iso.write(base + off, v.to_bytes(w, "little"))
                n += 1
    return n


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

def _enemy_field_names():
    """Every writable field, including the unverified affinity slots."""
    return _summary_cols() + _affinity_cols()

AFFINITY_WARNING = (
    f"note: Aff1..Aff{F.ENEMY_AFFINITY_COUNT} are damage-affinity percentages "
    f"({F.ENEMY_AFFINITY_NORMAL} = normal), but which element each slot is has "
    f"NOT been confirmed. Treat them as an experiment.")

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
    print("  affinities " + " ".join(f"{rec[c]}" for c in _affinity_cols()))
    print(f"  {AFFINITY_WARNING}")

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
    if not edits:
        raise SystemExit("nothing to do — pass at least one --set FIELD=VALUE")
    if any(k in _affinity_cols() for k in edits):
        print(AFFINITY_WARNING)
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

def cmd_enemy_rebalance(a):
    """Scale HP and/or rewards across the whole bestiary — the disc-wide fix for
    the game's HP bloat. Bosses (enemy id >= 561) are skipped unless --bosses."""
    with Iso(a.iso) as iso:
        require_version(iso)
        base = {i: read_enemy(iso, i) for i in range(F.ENEMY_COUNT)}
        ids = {i: read_enemy_id(iso, i) for i in range(F.ENEMY_COUNT)}
    plan = {}
    for i, rec in base.items():
        if not a.bosses and is_boss(ids[i]):
            continue
        edits = {}
        if a.hp != 100:
            edits["HP"] = max(1, round(rec["HP"] * a.hp / 100))
        if a.rewards != 100:
            for k in ("EXP", "SP", "CP"):
                edits[k] = round(rec[k] * a.rewards / 100)
        if edits:
            plan[i] = edits
    if not plan:
        print("nothing to change (both scales are 100%)")
        return
    print(f"{len(plan)} record(s) to change  (HP {a.hp}% · rewards {a.rewards}%"
          f"{' · incl. bosses' if a.bosses else ''})")
    if a.dry_run:
        names = F.enemy_names()
        for i, e in list(plan.items())[:a.show]:
            deltas = ", ".join(f"{k} {base[i][k]:,}->{v:,}" for k, v in e.items())
            print(f"  {i:03d} {names.get(i,'?'):<22} {deltas}")
        if len(plan) > a.show:
            print(f"  … {len(plan) - a.show} more (raise --show to see them)")
        print("dry run — nothing written. Re-run without --dry-run to apply.")
        return
    if a.backup:
        print(f"backup -> {backup(a.iso)}")
    n = 0
    with Iso(a.iso, write=True) as iso:
        for i, e in plan.items():
            n += write_enemy(iso, i, e)
    print(f"wrote {n} field(s) across {len(plan)} record(s)")

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
    print("(affinity slots are not exported here — the catalog has no retail "
          "baseline for them. The web editor exports them from the disc it opened.)")

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
    sp.add_argument("--backup", action="store_true", help="copy the ISO to .bak first")
    sp.set_defaults(fn=cmd_enemy_set)

    sp = sub.add_parser("rebalance", help="scale HP / rewards across all enemies")
    sp.add_argument("iso")
    sp.add_argument("--hp", type=float, default=100, help="HP percent (50 = halve)")
    sp.add_argument("--rewards", type=float, default=100, help="EXP/SP/CP percent")
    sp.add_argument("--bosses", action="store_true", help="include bosses (enemy id 561+)")
    sp.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    sp.add_argument("--show", type=int, default=20, help="rows to print in a dry run")
    sp.add_argument("--backup", action="store_true", help="copy the ISO to .bak first")
    sp.set_defaults(fn=cmd_enemy_rebalance)

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
    sp.set_defaults(fn=cmd_apply_patch)

    sp = sub.add_parser("restore", help="put the retail enemy values back")
    sp.add_argument("iso")
    sp.add_argument("--only", action="append", metavar="IDX[,IDX...]",
                    help="restore just these records (default: all)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--show", type=int, default=20)
    sp.add_argument("--backup", action="store_true")
    sp.set_defaults(fn=cmd_restore)

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
