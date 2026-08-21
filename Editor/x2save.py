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


def extract_gamedata(path, fmt=None):
    """Return the raw 20,832-byte Xenosaga II save payload from a container.

    PSV is fully supported. The .max/.sps/.cbs containers wrap the same payload
    but their extraction is not wired up yet (see Xenosaga2_ISO_offsets.md)."""
    fmt = fmt or sniff_format(path)
    data = open(path, "rb").read()
    if fmt == "psv":
        for name, blob in parse_psv_files(data):
            if name not in ("icon.sys", "system.ico"):
                return blob                      # the save data file (folder-named)
        raise ValueError("no gamedata file inside PSV")
    raise NotImplementedError(
        f"gamedata extraction for {fmt!r} not implemented yet (PSV only for now)"
    )


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


def main():
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "../Saves"
    if os.path.isfile(root):                     # decode a single save
        d = decode_save(root)
        print(f"Gold: {d['gold']:,}")
        print(f"{'#':>2} {'character':<14} {'lvl':>3} {'HP':>6}  id")
        for i, c in enumerate(d["characters"]):
            if not c["active"]:
                continue
            print(f"{i:>2} {c['name']:<14} {c['Level']:>3} {c['HP']:>6}  "
                  f"0x{c['Character id']:04X}")
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
