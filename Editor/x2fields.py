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

# ===========================================================================
# SAVE gamedata layout (VERIFIED 2026-08-21 against 20 PSV save slots).
#
# The on-card save file (named after its folder, e.g. "BASLUS-20892Xeno201")
# is a fixed 20,832-byte blob:
#   0x0000  header: +0x08 u32 checksum ("muY+"-style), +0x10 u8 misc counter
#   0x0174..0x0D44   embedded JPEG thumbnail (per-save screenshot)
#   0x0D44..0x1174   ~1 KB high-entropy block (2nd image / packed state) [TODO]
#   0x00D0  u32  GOLD (verified: rises/falls with earning+spending)
#   0x1174  character table: CHAR_COUNT records x CHAR_STRIDE bytes
# ===========================================================================
GAMEDATA_SIZE = 20832

GD_GOLD_OFF = 0xD0                 # u32 gold / money

CHAR_TABLE_OFF = 0x1174
CHAR_STRIDE    = 0x108             # 264 bytes/record (matches pnach EE stride)
CHAR_COUNT     = 15               # rec0-6 on-foot, 7-9 reserved, 10-14 E.S. units

# Fields within one 0x108 character record. Level/HP/id verified by tracking a
# single character across 20 save points (level 7 -> 54); the five u16 stats
# grow in lockstep with level. Stat *names* (STR/VIT/...) are not pinned yet, so
# they're numbered. Everything past +0x22 is tech-level arrays (0x14 x8, 0x64 x8).
CHAR_FIELDS = [
    ("Character id", 0x00, 2, "char"),
    ("HP",           0x02, 2, "num"),
    ("Stat 1",       0x06, 2, "num"),
    ("Stat 2",       0x08, 2, "num"),
    ("Stat 3",       0x0A, 2, "num"),
    ("Stat 4",       0x0C, 2, "num"),
    ("Stat 5",       0x0E, 2, "num"),
    ("Level",        0x13, 1, "num"),
]

# rec index -> character. Inferred from the pnach EE-RAM order (Chaos lowest
# address, stride 0x40), which matches the save record order 1:1 (7 on-foot
# filled, 3 empty, then the high-HP E.S. mech slots). Keyed by record index.
ROSTER = {
    0: "chaos", 1: "KOS-MOS", 2: "Shion", 3: "Jin", 4: "Ziggy",
    5: "MOMO", 6: "Jr.", 7: "(reserved)", 8: "(reserved)", 9: "(reserved)",
    10: "E.S. Dinah", 11: "E.S. Zebulun", 12: "E.S. Asher",
    13: "E.S. (slot 14)", 14: "E.S. (slot 15)",
}

# --- ISO schema stubs (still to be reverse-engineered) ---------------------
TECH_FIELDS = []      # Tech / Ether effect table
GEAR_FIELDS = []      # Weapon / armor / accessory table
ENEMY_FIELDS = []     # Enemy stat table
SHOP_FIELDS = []      # Shop stock / price tables
