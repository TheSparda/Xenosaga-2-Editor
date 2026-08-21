#!/usr/bin/env python3
"""
Xenosaga Episode II PS2 save reader (stdlib only).

Counterpart to Suikoden 3's s3save.py. This does NOT touch the ISO and does NOT
require one. It identifies and (eventually) decodes the save containers we have
local samples of. Container sniffing + inventory works today; per-container
payload extraction and the gamedata field decode are the RESEARCH half.

Container formats seen in Saves/ (magic verified 2026-08-21):
  format   ext          magic / signature                 notes
  ----------------------------------------------------------------------------
  memcard  .ps2/.mcd    "Sony PS2 Memory Card Format"      8 MB PS2MFS image
  psu      .psu         (EMS export; dir entries)          in-place
  psv      .psv         b"\\x00VSP"                          PS3-exported PS2 save (signed)
  max      .max         b"Ps2PowerSave"                     AR Max / MAX Drive
  sharkport .sps/.xps   u32 len + b"SharkPortSave"          SharkPort / X-Port
  codebreaker .cbs      b"CFU\\x00"                           CodeBreaker (RC4 + zlib)

Save-folder prefixes on the memory card:
  BASLUS-20892  (USA, disc 1 serial — the save id used across the game)
  BESCES-82034  (PAL; one local .max sample is a European save)
"""
import struct, os, glob, re
import x2fields as F

# --- container magics --------------------------------------------------------
MC_MAGIC   = b"Sony PS2 Memory Card Format"
PSV_MAGIC  = b"\x00VSP"
MAX_MAGIC  = b"Ps2PowerSave"
SPS_MAGIC  = b"SharkPortSave"
CBS_MAGIC  = b"CFU\x00"

USA_PREFIX = "BASLUS-20892"     # Xenosaga II (USA) save id
PAL_PREFIX = "BESCES-82034"     # Xenosaga II (PAL) save id

_DF_DIR = 0x0020                # PS2 dirent mode bit: entry is a directory

# CodeBreaker (.cbs) RC4 keystream table (public-domain, from mymc / S3 editor).
_CBS_RC4 = bytes([
    0x5f,0x1f,0x85,0x6f,0x31,0xaa,0x3b,0x18,0x21,0xb9,0xce,0x1c,0x07,0x4c,0x9c,0xb4,
    0x81,0xb8,0xef,0x98,0x59,0xae,0xf9,0x26,0xe3,0x80,0xa3,0x29,0x2d,0x73,0x51,0x62,
    0x7c,0x64,0x46,0xf4,0x34,0x1a,0xf6,0xe1,0xba,0x3a,0x0d,0x82,0x79,0x0a,0x5c,0x16,
    0x71,0x49,0x8e,0xac,0x8c,0x9f,0x35,0x19,0x45,0x94,0x3f,0x56,0x0c,0x91,0x00,0x0b,
    0xd7,0xb0,0xdd,0x39,0x66,0xa1,0x76,0x52,0x13,0x57,0xf3,0xbb,0x4e,0xe5,0xdc,0xf0,
    0x65,0x84,0xb2,0xd6,0xdf,0x15,0x3c,0x63,0x1d,0x89,0x14,0xbd,0xd2,0x36,0xfe,0xb1,
    0xca,0x8b,0xa4,0xc6,0x9e,0x67,0x47,0x37,0x42,0x6d,0x6a,0x03,0x92,0x70,0x05,0x7d,
    0x96,0x2f,0x40,0x90,0xc4,0xf1,0x3e,0x3d,0x01,0xf7,0x68,0x1e,0xc3,0xfc,0x72,0xb5,
    0x54,0xcf,0xe7,0x41,0xe4,0x4d,0x83,0x55,0x12,0x22,0x09,0x78,0xfa,0xde,0xa7,0x06,
    0x08,0x23,0xbf,0x0f,0xcc,0xc1,0x97,0x61,0xc5,0x4a,0xe6,0xa0,0x11,0xc2,0xea,0x74,
    0x02,0x87,0xd5,0xd1,0x9d,0xb7,0x7e,0x38,0x60,0x53,0x95,0x8d,0x25,0x77,0x10,0x5e,
    0x9b,0x7f,0xd8,0x6e,0xda,0xa2,0x2e,0x20,0x4f,0xcd,0x8f,0xcb,0xbe,0x5a,0xe0,0xed,
    0x2c,0x9a,0xd4,0xe2,0xaf,0xd0,0xa9,0xe8,0xad,0x7a,0xbc,0xa8,0xf2,0xee,0xeb,0xf5,
    0xa6,0x99,0x28,0x24,0x6c,0x2b,0x75,0x5d,0xf8,0xd3,0x86,0x17,0xfb,0xc0,0x7b,0xb3,
    0x58,0xdb,0xc7,0x4b,0xff,0x04,0x50,0xe9,0x88,0x69,0xc9,0x2a,0xab,0xfd,0x5b,0x1b,
    0x8a,0xd9,0xec,0x27,0x44,0x0e,0x33,0xc8,0x6b,0x93,0x32,0x48,0xb6,0x30,0x43,0xa5])

def _cbs_rc4(data):
    s = bytearray(_CBS_RC4); t = bytearray(data); j = 0
    for ii in range(len(t)):
        i = (ii + 1) % 256; j = (j + s[i]) % 256; s[i], s[j] = s[j], s[i]
        t[ii] ^= s[(s[i] + s[j]) % 256]
    return bytes(t)

def _load_cbs(b):
    """Return {filename: bytes} for a CodeBreaker (.cbs) save (RC4 + zlib)."""
    import zlib
    hlen = struct.unpack_from("<L", b, 8)[0]
    dlen, flen = struct.unpack_from("<LL", b, 12)
    body = zlib.decompressobj().decompress(_cbs_rc4(b[hlen:hlen + flen]), dlen)
    fs = {}
    while body:
        h = struct.unpack_from("<8s8sLHHLL32s", body, 0); sz = h[2]
        fs[h[7].split(b"\x00")[0].decode("latin1")] = body[64:64 + sz]
        body = body[64 + sz:]
    return fs

def _load_sharkport(b):
    """Return {filename: bytes} for a SharkPort/X-Port (.sps/.xps) save (uncompressed)."""
    import io
    f = io.BytesIO(b); f.read(17); f.read(4)              # magic + savetype
    for _ in range(3):                                    # title / datestamp / comment
        n = struct.unpack("<L", f.read(4))[0]; f.read(n)
    f.read(4)                                             # flen
    hlen, dn, dl, dm, cr, mo = struct.unpack("<H64sL8xH2x8s8s", f.read(98)); f.read(hlen - 98)
    dl -= 2; fs = {}
    for _ in range(dl):
        hlen, name, flen, mode, cr, mo = struct.unpack("<H64sL8xH2x8s8s", f.read(98)); f.read(hlen - 98)
        key = name.split(b"\x00")[0].decode("latin1")
        fs[key] = f.read(flen)
    return fs

def _pick_gamedata(files):
    """Given {name: bytes}, return the Xenosaga II gamedata payload (by size)."""
    for name, data in files.items():
        if len(data) == F.GAMEDATA_SIZE:
            return data
    return None


def sniff_format(path):
    """Return a format id string for a save file, or None if unrecognized.
    Cheap: reads only the first 64 bytes."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return None
    if head.startswith(MC_MAGIC):
        return "memcard"
    if head.startswith(PSV_MAGIC):
        return "psv"
    if head.startswith(MAX_MAGIC):
        return "max"
    if head.startswith(CBS_MAGIC):
        return "cbs"
    if head[4:4 + len(SPS_MAGIC)] == SPS_MAGIC:   # u32 length prefix, then magic
        return "sharkport"
    if path.lower().endswith(".psu"):
        return "psu"
    return None


def sniff_region(path):
    """Best-effort region tag ('USA' / 'PAL' / None) from an embedded save id."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    if USA_PREFIX.encode() in head:
        return "USA"
    if PAL_PREFIX.encode() in head:
        return "PAL"
    return None


# --- PSV container (PS3 export) ---------------------------------------------
# Layout: 0x84-byte signed header, then a McFsEntry-style table (mode u32 @name-4:
# 0x8427=dir / 0x8497=file; size u32 @name-8), then file bodies concatenated in
# entry order. Body starts at filesize - sum(file sizes). Verified on 20 slots.
_ENTRY_RE = re.compile(rb"(icon\.sys|system\.ico|B[A-Z]S[A-Z]{3}-[0-9]{5}[A-Za-z0-9]+)\x00")

def parse_psv_files(data):
    """Return an ordered list of (name, bytes) for the files inside a PSV."""
    ents = []
    for m in _ENTRY_RE.finditer(data):
        o = m.start()
        if o < 8:
            continue
        mode = struct.unpack_from("<I", data, o - 4)[0]
        size = struct.unpack_from("<I", data, o - 8)[0]
        if mode == 0x8497:                      # file (not the 0x8427 dir)
            ents.append((m.group(1).decode(), size))
    seen, ordered = set(), []
    for n, s in ents:                            # names appear in dir listing + own entry
        if (n, s) in seen:
            continue
        seen.add((n, s)); ordered.append((n, s))
    off = len(data) - sum(s for _, s in ordered)
    out = []
    for n, s in ordered:
        out.append((n, data[off:off + s])); off += s
    return out


def psv_gamedata_span(data):
    """Return (offset, length) of the save data file within a PSV image."""
    off = len(data) - sum(len(b) for _, b in parse_psv_files(data))
    for name, blob in parse_psv_files(data):
        if name not in ("icon.sys", "system.ico"):
            return off, len(blob)
        off += len(blob)
    raise ValueError("no gamedata file inside PSV")


def extract_gamedata(path, fmt=None):
    """Return the raw 20,832-byte Xenosaga II save payload from a container.

    Supports psv / sharkport (.sps/.xps) / cbs. The .max (Ps2PowerSave) container
    uses LZARI compression and isn't decoded yet."""
    fmt = fmt or sniff_format(path)
    data = open(path, "rb").read()
    if fmt == "psv":
        o, n = psv_gamedata_span(data)
        return data[o:o + n]
    if fmt == "sharkport":
        gd = _pick_gamedata(_load_sharkport(data))
    elif fmt == "cbs":
        gd = _pick_gamedata(_load_cbs(data))
    elif fmt == "max":
        raise NotImplementedError("`.max` (Ps2PowerSave/LZARI) not decoded yet")
    else:
        raise NotImplementedError(f"gamedata extraction for {fmt!r} not implemented")
    if gd is None:
        raise ValueError(f"no {F.GAMEDATA_SIZE}-byte gamedata found in {fmt} container")
    return gd


def decode_gamedata(gd):
    """Decode a raw gamedata payload into structured fields."""
    if len(gd) != F.GAMEDATA_SIZE:
        raise ValueError(f"unexpected gamedata size {len(gd)} (want {F.GAMEDATA_SIZE})")
    gold = struct.unpack_from("<I", gd, F.GD_GOLD_OFF)[0]
    chars = []
    for i in range(F.CHAR_COUNT):
        base = F.CHAR_TABLE_OFF + i * F.CHAR_STRIDE
        rec = {}
        for label, off, width, _kind in F.CHAR_FIELDS:
            rec[label] = int.from_bytes(gd[base + off:base + off + width], "little")
        rec["name"] = F.ROSTER.get(i, f"rec{i}")
        rec["active"] = rec["Character id"] != 0 and rec["Level"] > 0
        chars.append(rec)
    return {"gold": gold, "characters": chars}


def decode_save(path, fmt=None):
    """Open a save container and return its decoded fields (read-only)."""
    return decode_gamedata(extract_gamedata(path, fmt))


# --- WRITE path --------------------------------------------------------------
# NOTE: the gamedata header carries a 4-byte value @0x08 that is NOT yet
# reverse-engineered (it resists every standard CRC/sum/hash we tried, so it's
# a custom routine). We do NOT know if the game validates it on load. fix_checksum()
# is the single hook to plug the algorithm into once it's cracked; until then the
# field is preserved as-is and writes are gated behind an explicit round-trip check.
CHECKSUM_KNOWN = False

def fix_checksum(gd):
    """Return gd with its integrity field(s) recomputed. Currently a pass-through
    (algorithm unknown). See Xenosaga2_ISO_offsets.md."""
    return gd

_FIELD_MAX = {1: 0xFF, 2: 0xFFFF, 4: 0xFFFFFFFF}

def _field_spec(label):
    for lb, off, width, kind in F.CHAR_FIELDS:
        if lb == label:
            return off, width
    raise KeyError(label)

def apply_edits(gd, edits):
    """Apply edits to a 20,832-byte gamedata blob and return a new blob.

    edits = {"gold": int, "characters": {rec_index: {field_label: value, ...}}}
    Values are clamped to their field width. Only known fields are writable."""
    if len(gd) != F.GAMEDATA_SIZE:
        raise ValueError(f"bad gamedata size {len(gd)}")
    b = bytearray(gd)
    if "gold" in edits and edits["gold"] is not None:
        v = max(0, min(int(edits["gold"]), _FIELD_MAX[4]))
        struct.pack_into("<I", b, F.GD_GOLD_OFF, v)
    for idx, fields in edits.get("characters", {}).items():
        if not (0 <= idx < F.CHAR_COUNT):
            raise IndexError(f"character index {idx} out of range")
        base = F.CHAR_TABLE_OFF + idx * F.CHAR_STRIDE
        for label, value in fields.items():
            off, width = _field_spec(label)
            v = max(0, min(int(value), _FIELD_MAX[width]))
            b[base + off:base + off + width] = v.to_bytes(width, "little")
    return fix_checksum(bytes(b))

def _sharkport_gd_span(data):
    """Return (offset, length) of the gamedata file inside a SharkPort save."""
    import io
    f = io.BytesIO(data); f.read(17); f.read(4)
    for _ in range(3):
        n = struct.unpack("<L", f.read(4))[0]; f.read(n)
    f.read(4)
    hlen, dn, dl, dm, cr, mo = struct.unpack("<H64sL8xH2x8s8s", f.read(98)); f.read(hlen - 98)
    for _ in range(dl - 2):
        hlen, name, flen, mode, cr, mo = struct.unpack("<H64sL8xH2x8s8s", f.read(98)); f.read(hlen - 98)
        off = f.tell()
        if flen == F.GAMEDATA_SIZE:
            return off, flen
        f.read(flen)
    raise ValueError("gamedata not found in SharkPort save")

def _splice_gamedata(container, fmt, new_gd):
    """Return a new container image with its gamedata replaced (length preserved).

    Mirrors the Suikoden-3 editor: psv/sharkport are patched in place (their container
    checksums/signatures aren't gamedata-dependent for PC tools); cbs is decompressed,
    patched, and re-compressed (RC4+zlib)."""
    if len(new_gd) != F.GAMEDATA_SIZE:
        raise ValueError("gamedata length changed; refusing to write")
    if fmt == "psv":
        o, n = psv_gamedata_span(container)
        return container[:o] + new_gd + container[o + n:]
    if fmt == "sharkport":
        o, n = _sharkport_gd_span(container)
        return container[:o] + new_gd + container[o + n:]
    if fmt == "cbs":
        import zlib
        hlen = struct.unpack_from("<L", container, 8)[0]
        dlen = struct.unpack_from("<L", container, 12)[0]
        body = bytearray(zlib.decompressobj().decompress(_cbs_rc4(container[hlen:]), dlen))
        pos = 0
        while pos < len(body):
            h = struct.unpack_from("<8s8sLHHLL32s", bytes(body), pos); sz = h[2]
            if sz == F.GAMEDATA_SIZE:
                body[pos + 64:pos + 64 + sz] = new_gd
                newcomp = _cbs_rc4(zlib.compress(bytes(body), 9))
                newb = bytearray(container[:hlen]) + newcomp
                struct.pack_into("<L", newb, 16, len(newb))   # flen = total file size
                return bytes(newb)
            pos += 64 + sz
        raise ValueError("gamedata not found in CodeBreaker save")
    raise NotImplementedError(f"writing {fmt!r} containers not implemented yet")

def write_save(path, edits, make_backup=True, fmt=None):
    """Apply edits to a save container in place. Backs up to <path>.bak first,
    then round-trip verifies the write. Returns the decoded post-edit state.

    Raises before touching the file if the edit would change the payload size or
    if the round-trip check fails (the original is restored from backup)."""
    fmt = fmt or sniff_format(path)
    container = open(path, "rb").read()
    gd = extract_gamedata(path, fmt)
    new_gd = apply_edits(gd, edits)
    if len(new_gd) != len(gd):
        raise ValueError("edited gamedata size mismatch; aborting")
    new_container = _splice_gamedata(container, fmt, new_gd)

    if make_backup:
        bak = path + ".bak"
        if not os.path.exists(bak):
            with open(bak, "wb") as f:
                f.write(container)
    with open(path, "wb") as f:
        f.write(new_container)

    # round-trip: re-read and confirm the intended fields landed
    check = decode_save(path, fmt)
    if "gold" in edits and edits["gold"] is not None:
        want = max(0, min(int(edits["gold"]), _FIELD_MAX[4]))
        if check["gold"] != want:
            raise IOError("round-trip verify failed (gold); file left as written")
    for idx, fields in edits.get("characters", {}).items():
        for label, value in fields.items():
            _, width = _field_spec(label)
            want = max(0, min(int(value), _FIELD_MAX[width]))
            if check["characters"][idx][label] != want:
                raise IOError(f"round-trip verify failed (rec{idx}.{label})")
    return check


# --- inventory (works today) -------------------------------------------------
def scan_saves(root):
    """Walk `root` and return a list of dicts describing every recognized save."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            p = os.path.join(dirpath, name)
            fmt = sniff_format(p)
            if not fmt and not name.upper().endswith(".PSV"):
                continue
            if not fmt:
                fmt = "psv"
            out.append({
                "path": p,
                "name": name,
                "format": fmt,
                "region": sniff_region(p),
                "size": os.path.getsize(p),
            })
    return out


def _print_decode(d):
    print(f"Gold: {d['gold']:,}")
    print(f"{'#':>2} {'character':<14} {'lvl':>3} {'HP':>6}  id")
    for i, c in enumerate(d["characters"]):
        if not c["active"]:
            continue
        print(f"{i:>2} {c['name']:<14} {c['Level']:>3} {c['HP']:>6}  0x{c['Character id']:04X}")

def main():
    import sys, argparse
    if len(sys.argv) > 1 and sys.argv[1] == "set":
        ap = argparse.ArgumentParser(prog="x2save.py set")
        ap.add_argument("file")
        ap.add_argument("--gold", type=int)
        ap.add_argument("--char", type=int, help="record index (0=chaos, ...)")
        ap.add_argument("--level", type=int)
        ap.add_argument("--hp", type=int)
        ap.add_argument("--no-backup", action="store_true")
        a = ap.parse_args(sys.argv[2:])
        edits = {"characters": {}}
        if a.gold is not None:
            edits["gold"] = a.gold
        if a.char is not None:
            cf = {}
            if a.level is not None: cf["Level"] = a.level
            if a.hp is not None:    cf["HP"] = a.hp; cf["Current HP"] = a.hp  # base + live
            edits["characters"][a.char] = cf
        if not CHECKSUM_KNOWN:
            print("! note: save checksum not yet cracked — the game *may* reject the "
                  "edited save. A .bak is kept. Test one in your emulator.\n")
        d = write_save(a.file, edits, make_backup=not a.no_backup)
        print("written + round-trip verified:\n")
        _print_decode(d)
        return
    root = sys.argv[1] if len(sys.argv) > 1 else "../Saves"
    if os.path.isfile(root):                     # decode a single save
        _print_decode(decode_save(root))
        return
    saves = scan_saves(root)
    if not saves:
        print(f"No recognized saves under {root!r}")
        return
    print(f"{'format':<10} {'region':<6} {'size':>8}  name")
    print("-" * 72)
    for s in saves:
        print(f"{s['format']:<10} {str(s['region'] or '?'):<6} {s['size']:>8}  {s['name']}")
    print(f"\n{len(saves)} save(s). Payload decode pending — see Xenosaga2_ISO_offsets.md")


if __name__ == "__main__":
    main()
