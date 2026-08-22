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
import argparse, os, struct, sys, shutil, datetime, re
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

    sp = sub.add_parser("dump-region", help="hex-dump a byte range")
    sp.add_argument("iso")
    sp.add_argument("--off", type=lambda x: int(x, 0), required=True)
    sp.add_argument("--len", type=lambda x: int(x, 0), default=256)
    sp.set_defaults(fn=cmd_dump_region)

    a = p.parse_args()
    rc = a.fn(a)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
