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
import struct, os, glob

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


# --- RESEARCH stubs: extract the inner `gamedata` payload from each container --
def extract_gamedata(path, fmt=None):
    """Return the raw Xenosaga II save payload from a container.

    Not implemented yet — the payload offset/length and (for .cbs) the RC4+zlib
    decode need reverse-engineering against the local samples first. See
    Editor/Xenosaga2_ISO_offsets.md for the research plan."""
    raise NotImplementedError(
        "gamedata extraction not reverse-engineered yet; see Xenosaga2_ISO_offsets.md"
    )


def decode_save(gamedata):
    """Decode a raw gamedata payload into structured fields (characters, party,
    inventory, gold, playtime, ...). Not implemented — field offsets unknown."""
    raise NotImplementedError("gamedata field map not reverse-engineered yet")


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
