"""
Named field schemas + verified constants for Xenosaga Episode II (USA).

Counterpart to Suikoden 3's s3fields.py. At this stage the character / item /
battle tables have NOT been reverse-engineered yet — this module holds the
*confirmed* disc-level facts (serials, volume id) plus empty, clearly-labelled
schema stubs that the ISO/save engines will fill in as tables are located.

Each field, once known, is: (label, offset_within_record, width_bytes, kind)
  kind: "item" = resolve via item id list, "skill" = skill/tech id,
        "num" = plain number, "char" = character id.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))

def res_bytes(name):
    """Read a bundled data file as bytes. Reads from disk beside the sources
    normally; inside the single-file .pyz build (where open() can't reach archive
    members) it falls back to pkgutil, which reads straight out of the zip."""
    p = os.path.join(HERE, name)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    import pkgutil
    data = pkgutil.get_data(__name__, name)
    if data is None:
        raise FileNotFoundError(name)
    return data

def res_text(name, encoding="utf-8"):
    """Read a bundled data file as text (works from sources and from the .pyz)."""
    return res_bytes(name).decode(encoding)

def res_json(name):
    """Load a bundled *.json reference table (works from sources and from the .pyz)."""
    return json.loads(res_text(name))

# ---------------------------------------------------------------------------
# VERIFIED disc facts (extracted directly from the retail ISOs, 2026-08-21).
#   Serial lives in SYSTEM.CNF as  BOOT2 = cdrom0:\SLUS_xxx.xx;1
#   Volume id lives in the ISO9660 Primary Volume Descriptor (LBA 16, +40).
# ---------------------------------------------------------------------------
GAME_NAME = "Xenosaga Episode II - Jenseits von Gut und Boese"
VOLUME_ID = "XENOSAGA_II"

# serial -> disc number. Xenosaga II (USA) ships on two discs.
SERIALS = {
    "SLUS-20892": 1,   # Disc 1  (BOOT2 SLUS_208.92, VER 1.00)
    "SLUS-21133": 2,   # Disc 2  (BOOT2 SLUS_211.33)
}

def disc_of(serial):
    """Return 1 or 2 for a known X2 serial, else None."""
    return SERIALS.get(serial)

# ---------------------------------------------------------------------------
# SCHEMA STUBS — populated as reverse-engineering progresses. Each is an empty
# list today; the shape matches s3fields.py so the editor UI can iterate over
# them without special-casing "not yet known".
# ---------------------------------------------------------------------------

# Character records (per-character stats / starting gear / techs).
CHAR_FIELDS = []           # list[(label, off, width, kind)]

# Tech / Ether (spell) effect table.
TECH_FIELDS = []

# Weapon / armor / accessory gear table.
GEAR_FIELDS = []

# Enemy stat table.
ENEMY_FIELDS = []

# Shop stock / price tables.
SHOP_FIELDS = []

# --- rank/grade helpers get defined here once the games scales are known. ---
