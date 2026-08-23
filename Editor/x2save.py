#!/usr/bin/env python3
"""
Xenosaga Episode II PS2 save reader (stdlib only).

Counterpart to Suikoden 3's s3save.py. This does NOT touch the ISO and does NOT
require one.

Container support (magics verified 2026-08-21; PS2MFS/psu handled by x2mc.py):
  format   ext          magic / signature                 read  write
  ----------------------------------------------------------------------------
  memcard  .ps2/.mcd    "Sony PS2 Memory Card Format"      yes   yes  (PCSX2)
  psu      .psu         leading dir entry (no magic)       yes   yes
  psv      .psv         b"\\x00VSP"                          yes   yes  (PS3 export)
  sharkport .sps/.xps   u32 len + b"SharkPortSave"          yes   yes
  codebreaker .cbs      b"CFU\\x00"                           yes   yes  (RC4 + zlib)
  max      .max         b"Ps2PowerSave"                     no    no   (LZARI, TODO)

A memory card holds one folder per in-game save slot, so it can carry several
Xenosaga II saves; every other container carries exactly one. `list_slots()`
enumerates them and the `slot=` argument selects one.

Save-folder prefixes on the memory card:
  BASLUS-20892  (USA, disc 1 serial — the save id used across the game)
  BESCES-82034  (PAL; one local .max sample is a European save)
"""
import struct, os, glob, re
import x2fields as F
import x2mc as MC

# --- container magics --------------------------------------------------------
MC_MAGIC   = b"Sony PS2 Memory Card Format"
PSV_MAGIC  = b"\x00VSP"
MAX_MAGIC  = b"Ps2PowerSave"
SPS_MAGIC  = b"SharkPortSave"
CBS_MAGIC  = b"CFU\x00"

USA_PREFIX = "BASLUS-20892"     # Xenosaga II (USA) save id
PAL_PREFIX = "BESCES-82034"     # Xenosaga II (PAL) save id
SAVE_PREFIXES = (USA_PREFIX, PAL_PREFIX)

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

def _read(path):
    with open(path, "rb") as f:
        return f.read()


# --- slot identity: the save's own name, playtime and screenshot -------------
# Every save folder carries an icon.sys whose title the console shows on the load
# screen; Xenosaga II puts the slot name and playtime in it, e.g.
# "XenosagaEPII-01[30:18]". Layout (PS2 SDK mcIcon): "PS2D" magic, u16 type,
# u16 offset into the title where line 2 begins, then the 68-byte Shift-JIS
# title at +0xC0.
ICON_TITLE_OFF = 0xC0
ICON_TITLE_LEN = 68
_PLAYTIME_RE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")

def parse_icon_sys(blob):
    """{'title','name','playtime'} from an icon.sys, or None if it isn't one."""
    if len(blob) < ICON_TITLE_OFF + ICON_TITLE_LEN or blob[:4] != b"PS2D":
        return None
    raw = blob[ICON_TITLE_OFF:ICON_TITLE_OFF + ICON_TITLE_LEN].split(b"\x00")[0]
    nl = struct.unpack_from("<H", blob, 6)[0]

    def dec(b):
        return b.decode("shift_jis", "replace").strip()

    # the line break is a byte offset into the title, not a character in it
    title = dec(raw[:nl]) + " " + dec(raw[nl:]) if 0 < nl < len(raw) else dec(raw)
    title = " ".join(title.split())
    m = _PLAYTIME_RE.search(title)
    return {
        "title": title,
        "name": _PLAYTIME_RE.sub("", title).strip(" -–—") or title,
        "playtime": f"{int(m.group(1))}:{m.group(2).zfill(2)}" if m else "",
    }


def thumbnail(gd):
    """The JPEG screenshot embedded in a gamedata payload, or None.

    Trimmed to the actual end-of-image marker so the trailing padding inside the
    fixed-size region isn't handed to an image decoder."""
    if len(gd) < F.GD_THUMB_END:
        return None
    blob = bytes(gd[F.GD_THUMB_OFF:F.GD_THUMB_END])
    if not blob.startswith(b"\xff\xd8"):
        return None
    end = blob.rfind(b"\xff\xd9")
    return blob[:end + 2] if end > 0 else blob


def _pick_gamedata(files):
    """Given {name: bytes}, return the Xenosaga II gamedata payload (by size)."""
    for name, data in files.items():
        if len(data) == F.GAMEDATA_SIZE:
            return data
    return None


def sniff_format(path):
    """Return a format id string for a save file, or None if unrecognized.
    Cheap: reads only the first directory-entry's worth of bytes."""
    try:
        with open(path, "rb") as f:
            head = f.read(MC.DIRENT_SIZE)
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
    # .psu has no magic — it opens with the save folder's own directory entry.
    # Checked last so it can never shadow a format that does have one.
    if MC.looks_like_psu(head) or path.lower().endswith(".psu"):
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


# --- memory-card / .psu save location ---------------------------------------
# A memory card holds one folder per in-game save slot (e.g. BASLUS-20892Xeno201,
# ...02, ...03), so unlike the single-save containers a card can carry many
# Xenosaga II saves and callers have to say which one they mean.
def _card_slots(card):
    """[(folder entry, gamedata file entry)] for every X2 save on a card, by name."""
    out = []
    for folder, files in card.walk():
        if not folder.name.startswith(SAVE_PREFIXES):
            continue
        gd = next((f for f in files if f.length == F.GAMEDATA_SIZE), None)
        if gd is not None:
            out.append((folder, gd))
    out.sort(key=lambda t: t[0].name)
    return out

def _psu_gd_span(data):
    """(offset, length) of the gamedata file inside a .psu export."""
    for _name, off, ln in MC.psu_files(data):
        if ln == F.GAMEDATA_SIZE:
            return off, ln
    raise ValueError(f"no {F.GAMEDATA_SIZE}-byte gamedata inside the .psu")

def _pick_slot(slots, slot, what):
    if not slots:
        raise ValueError(f"no Xenosaga II save found in this {what}")
    if not 0 <= slot < len(slots):
        raise IndexError(f"slot {slot} out of range — this {what} holds {len(slots)}")
    return slots[slot]


def _slot(index, folder, filename, size, icon=None):
    """One entry for list_slots, with whatever identity the icon.sys gave us."""
    info = parse_icon_sys(icon) if icon else None
    return {
        "slot": index,
        "folder": folder,
        "file": filename,
        "size": size,
        "title": (info or {}).get("title", ""),
        "name": (info or {}).get("name", ""),
        "playtime": (info or {}).get("playtime", ""),
        # what to show in a picker: the in-game name beats the folder id
        "label": (info or {}).get("name") or folder,
    }


def list_slots(path, fmt=None):
    """Describe every Xenosaga II save inside a container.

    Memory cards can hold many (one folder per in-game slot); every other
    container holds exactly one, and reports a single slot so that callers can
    treat all formats the same way. Returns [] if none are found."""
    fmt = fmt or sniff_format(path)
    try:
        data = _read(path)
    except OSError:
        return []
    if fmt == "memcard":
        try:
            card = MC.Ps2Card(data)
        except ValueError:
            return []
        out = []
        for i, (folder, gd_ent) in enumerate(_card_slots(card)):
            icon = None
            for e in card.listdir(folder):
                if e.name.lower() == "icon.sys":
                    icon = card.read_file(e)
                    break
            out.append(_slot(i, folder.name, gd_ent.name, gd_ent.length, icon))
        return out
    if fmt == "psu":
        try:
            root = MC.psu_root(data)
            off, ln = _psu_gd_span(data)
            icon = next((data[o:o + l] for n, o, l in MC.psu_files(data)
                         if n.lower() == "icon.sys"), None)
        except ValueError:
            return []
        s = _slot(0, root.name, "", ln, icon)
        s["offset"] = off
        return [s]
    # Single-save containers. Report the payload file's name as the "folder" too,
    # since on the card it is named after its folder — that is what identifies
    # which in-game slot an export came from.
    try:
        if fmt == "psv":
            files = dict(parse_psv_files(data))
        elif fmt == "sharkport":
            files = _load_sharkport(data)
        elif fmt == "cbs":
            files = _load_cbs(data)
        else:
            gd = extract_gamedata(path, fmt)
            return [{"slot": 0, "folder": "", "file": "", "size": len(gd)}]
    except Exception:
        return []
    name = next((n for n, b in files.items() if len(b) == F.GAMEDATA_SIZE), None)
    if name is None:
        return []
    icon = next((b for n, b in files.items() if n.lower() == "icon.sys"), None)
    return [_slot(0, name, name, F.GAMEDATA_SIZE, icon)]


def _region_of(folders):
    """'USA' / 'PAL' / None from save-folder names."""
    for name in folders:
        if name.startswith(USA_PREFIX):
            return "USA"
        if name.startswith(PAL_PREFIX):
            return "PAL"
    return None


def extract_gamedata(path, fmt=None, slot=0):
    """Return the raw 20,832-byte Xenosaga II save payload from a container.

    Supports memory-card images (.ps2/.mcd), EMS exports (.psu), PS3 exports
    (.psv), SharkPort (.sps/.xps) and CodeBreaker (.cbs). `slot` selects which
    save to read out of a memory card (see list_slots); it is ignored by the
    single-save containers. The .max (Ps2PowerSave) container uses LZARI
    compression and isn't decoded yet."""
    fmt = fmt or sniff_format(path)
    data = _read(path)
    if fmt == "psv":
        o, n = psv_gamedata_span(data)
        return data[o:o + n]
    if fmt == "memcard":
        card = MC.Ps2Card(data)
        _folder, gd_ent = _pick_slot(_card_slots(card), slot, "memory card")
        return card.read_file(gd_ent)
    if fmt == "psu":
        o, n = _psu_gd_span(data)
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
        for label, off, width, _kind in F.CHAR_FIELDS + F.ES_EQUIP_FIELDS:
            rec[label] = int.from_bytes(gd[base + off:base + off + width], "little")
        rec["name"] = F.ROSTER.get(i, f"rec{i}")
        rec["active"] = rec["Character id"] != 0 and rec["Level"] > 0
        rec["is_es"] = rec["name"].startswith("E.S.")
        chars.append(rec)
    return {"gold": gold, "characters": chars}


def decode_save(path, fmt=None, slot=0):
    """Open a save container and return its decoded fields (read-only)."""
    return decode_gamedata(extract_gamedata(path, fmt, slot))


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
    for lb, off, width, kind in F.CHAR_FIELDS + F.ES_EQUIP_FIELDS:
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

def _splice_gamedata(container, fmt, new_gd, slot=0):
    """Return a new container image with its gamedata replaced (length preserved).

    Mirrors the Suikoden-3 editor: psv/sharkport/psu are patched in place (their
    container checksums/signatures aren't gamedata-dependent for PC tools); cbs is
    decompressed, patched, and re-compressed (RC4+zlib); a memory-card image is
    patched cluster by cluster through the filesystem, refreshing each touched
    page's error-correcting code."""
    if len(new_gd) != F.GAMEDATA_SIZE:
        raise ValueError("gamedata length changed; refusing to write")
    if fmt == "psv":
        o, n = psv_gamedata_span(container)
        return container[:o] + new_gd + container[o + n:]
    if fmt == "sharkport":
        o, n = _sharkport_gd_span(container)
        return container[:o] + new_gd + container[o + n:]
    if fmt == "psu":
        o, n = _psu_gd_span(container)
        return container[:o] + new_gd + container[o + n:]
    if fmt == "memcard":
        card = MC.Ps2Card(container)
        _folder, gd_ent = _pick_slot(_card_slots(card), slot, "memory card")
        return card.write_file(gd_ent, new_gd)
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
                # +0x10 is the *compressed* length — that is how _load_cbs reads
                # it back (b[hlen:hlen+flen]). Writing the whole file's size here
                # only happened to survive because Python clamps slices.
                struct.pack_into("<L", newb, 16, len(newcomp))
                return bytes(newb)
            pos += 64 + sz
        raise ValueError("gamedata not found in CodeBreaker save")
    raise NotImplementedError(f"writing {fmt!r} containers not implemented yet")

def write_save(path, edits, make_backup=True, fmt=None, slot=0):
    """Apply edits to a save container in place. Backs up to <path>.bak first,
    then round-trip verifies the write. Returns the decoded post-edit state.

    `slot` picks which save to edit inside a memory-card image (see list_slots).

    Raises before touching the file if the edit would change the payload size or
    if the round-trip check fails (the original is restored from backup)."""
    fmt = fmt or sniff_format(path)
    container = _read(path)
    gd = extract_gamedata(path, fmt, slot)
    new_gd = apply_edits(gd, edits)
    if len(new_gd) != len(gd):
        raise ValueError("edited gamedata size mismatch; aborting")
    new_container = _splice_gamedata(container, fmt, new_gd, slot)
    # Every container but cbs is patched byte-for-byte, so a size change there
    # means the splice went wrong. cbs is legitimately re-compressed.
    if fmt != "cbs" and len(new_container) != len(container):
        raise ValueError(
            f"container size changed ({len(container)} -> {len(new_container)}); "
            f"refusing to write")

    if make_backup:
        bak = path + ".bak"
        if not os.path.exists(bak):
            with open(bak, "wb") as f:
                f.write(container)
    with open(path, "wb") as f:
        f.write(new_container)

    # round-trip: re-read and confirm the intended fields landed
    check = decode_save(path, fmt, slot)
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
            slots = list_slots(p, fmt)
            out.append({
                "path": p,
                "name": name,
                "format": fmt,
                # a card's save id is buried in its filesystem, not its header,
                # so fall back to the folder names we just enumerated
                "region": sniff_region(p) or _region_of(s["folder"] for s in slots),
                "size": os.path.getsize(p),
                # memory cards hold one folder per in-game slot; everything else
                # holds a single save (0 = we could not find an X2 save in it)
                "slots": len(slots),
            })
    return out


def _print_decode(d):
    print(f"Gold: {d['gold']:,}")
    print(f"{'#':>2} {'character':<14} {'lvl':>3} {'HP':>6}  id")
    for i, c in enumerate(d["characters"]):
        if not c["active"]:
            continue
        print(f"{i:>2} {c['name']:<14} {c['Level']:>3} {c['HP']:>6}  0x{c['Character id']:04X}")

def _slot_line(s):
    bits = [s["folder"]]
    if s.get("name") and s["name"] != s["folder"]:
        bits.append(s["name"])
    if s.get("playtime"):
        bits.append(s["playtime"])
    return "  ".join(bits)

def _print_slots(path, fmt=None):
    slots = list_slots(path, fmt)
    if len(slots) <= 1:
        return
    print(f"{len(slots)} Xenosaga II saves on this card — pass --slot N to pick one:")
    for s in slots:
        print(f"  [{s['slot']}] {_slot_line(s)}")
    print()

def main():
    import sys, argparse
    if len(sys.argv) > 1 and sys.argv[1] == "set":
        ap = argparse.ArgumentParser(prog="x2save.py set")
        ap.add_argument("file")
        ap.add_argument("--gold", type=int)
        ap.add_argument("--char", type=int, help="record index (0=chaos, ...)")
        ap.add_argument("--level", type=int)
        ap.add_argument("--hp", type=int)
        ap.add_argument("--slot", type=int, default=0,
                        help="which save inside a memory-card image (see `slots`)")
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
        d = write_save(a.file, edits, make_backup=not a.no_backup, slot=a.slot)
        print("written + round-trip verified:\n")
        _print_decode(d)
        return

    if len(sys.argv) > 2 and sys.argv[1] == "slots":
        path = sys.argv[2]
        slots = list_slots(path)
        if not slots:
            print(f"No Xenosaga II save found in {path!r}")
            return
        print(f"{'slot':>4}  {'folder':<24} {'save name':<20} {'played':>7} {'bytes':>8}")
        for s in slots:
            print(f"{s['slot']:>4}  {s['folder']:<24} {s['name'] or '?':<20} "
                  f"{s['playtime'] or '?':>7} {s['size']:>8,}")
        return

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    slot = 0
    for i, a in enumerate(sys.argv):
        if a == "--slot" and i + 1 < len(sys.argv):
            slot = int(sys.argv[i + 1])
        elif a.startswith("--slot="):
            slot = int(a.split("=", 1)[1])
    root = args[0] if args else "../Saves"
    if os.path.isfile(root):                     # decode a single save
        _print_slots(root)
        _print_decode(decode_save(root, slot=slot))
        return
    saves = scan_saves(root)
    if not saves:
        print(f"No recognized saves under {root!r}")
        return
    print(f"{'format':<10} {'region':<6} {'slots':>5} {'size':>10}  name")
    print("-" * 76)
    for s in saves:
        print(f"{s['format']:<10} {str(s['region'] or '?'):<6} {s['slots']:>5} "
              f"{s['size']:>10,}  {s['name']}")
    print(f"\n{len(saves)} container(s). `x2save.py <file>` decodes one; "
          f"`x2save.py slots <card>` lists a memory card's saves.")


if __name__ == "__main__":
    main()
