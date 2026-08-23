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

# Fields within one 0x108 character record. The record is [char id u16] followed
# by the game's in-RAM stat struct, so offsets map 1:1 onto the CodeBreaker stat
# addresses (Shion base EE 0x61B592): HP, EP, Str, Vit, Ether-Atk, Ether-Def are
# u16 (game caps: HP 9999, EP/Dex/Eva/Agl 99, Str..Edef 999); Dex/Eva/Agl are u8.
# Names cross-checked against the pnach + almarsguides code lists. Level verified
# by tracking one character across 20 saves (7 -> 54). Past +0x22 = tech arrays.
CHAR_FIELDS = [
    ("Character id", 0x00, 2, "char"),
    ("HP",           0x02, 2, "num"),   # max HP (u16, cap 9999)
    ("EP",           0x06, 2, "num"),   # Ether Points (cap 99)
    ("Str",          0x08, 2, "num"),   # Strength     (cap 999)
    ("Vit",          0x0A, 2, "num"),   # Vitality
    ("Eatk",         0x0C, 2, "num"),   # Ether Attack
    ("Edef",         0x0E, 2, "num"),   # Ether Defense
    ("Dex",          0x10, 1, "num"),   # Dexterity    (cap 99)
    ("Eva",          0x11, 1, "num"),   # Evasion
    ("Agl",          0x12, 1, "num"),   # Agility (tentative; ~constant in samples)
    ("Level",        0x13, 1, "num"),
    ("Current HP",   0x5C, 2, "num"),   # live HP (==base at low lvl, +gear bonus later)
]

# +0x23..0x32 are constant per-character config (0x14 x8, 0x64 x8 — affinities/base tech),
# +0x33.. is a growing list of learned tech/skill ids. Not exposed as editable yet.

# E.S. (mech) equipment slots — EXPERIMENTAL. These four u16s in the record vary
# across saves for E.S. units only (weapon/frame/armor/anima ids), so they're
# editable, but the slot->kind mapping and the id->name catalog are NOT confirmed
# yet (that needs the ISO item tables + a ground-truth save). Raw numeric ids.
ES_EQUIP_FIELDS = [
    ("Gear 1", 0x86, 2, "num"),
    ("Gear 2", 0x88, 2, "num"),
    ("Gear 3", 0x8A, 2, "num"),
    ("Gear 4", 0x90, 2, "num"),
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

# ---------------------------------------------------------------------------
# INVENTORY reference (item id -> name), from the disc-1 pnach. The in-RAM tables
# are: consumables @ EE 0x61C800 (u16 quantity per id, cap 99), key items @ EE
# 0x61CC00 (u16 have-flag per id). These id->name maps are authoritative; the
# SAVE-side offsets of the inventory are NOT confirmed yet — the local samples hold
# almost no consumables and there's no known-inventory reference to verify a
# candidate, so decoding inventory from a save is deferred (see offsets notes).
# ---------------------------------------------------------------------------
INV_CONSUMABLE_RAM = 0x61C800    # u16 quantity per consumable id (cap 99)
INV_KEYITEM_RAM    = 0x61CC00    # u16 have-flag per key-item id

# Catalog JSONs are {id: {"name":..., "desc":...}} — names from the pnach, in-game
# descriptions extracted from the disc-1 data region (~ISO 0x200CE00).
def consumable_catalog():
    return {int(k): v for k, v in res_json("x2_consumables.json").items()}

def keyitem_catalog():
    return {int(k): v for k, v in res_json("x2_keyitems.json").items()}

def consumable_names():
    """{int id: name} for the 36 consumable items."""
    return {i: v["name"] for i, v in consumable_catalog().items()}

def keyitem_names():
    """{int id: name} for the 107 key items."""
    return {i: v["name"] for i, v in keyitem_catalog().items()}

def es_equip_catalog():
    """{int id: {name, desc}} for the E.S. accessory/circuit table (from ISO
    ~0x200C5D4). Covers ids 0-30 — id base (Auxiliary Armor A = 0) CONFIRMED by
    cross-checking all 36 saves: every in-range E.S. gear value maps to a sensible
    accessory. Higher gear ids (34-37 seen = weapon/frame items) aren't mapped yet."""
    return {int(k): v for k, v in res_json("x2_es_equip.json").items()}

# ---------------------------------------------------------------------------
# ISO ENEMY table (VERIFIED — disc 1). 97 enemy stat records at raw offset
# 0x2000000, stride 0x5C, followed by the name table at 0x2002342. HP verified
# aligned to names (Perun 860 ... Margulis 32000 ... Patriarch 192000).
# ---------------------------------------------------------------------------
ENEMY_TABLE_OFF = 0x2000000    # disc-1 raw byte offset of record 0
ENEMY_STRIDE    = 0x5C          # 92 bytes/record
ENEMY_COUNT     = 97
ENEMY_FIELDS = [
    ("HP",   0x36, 4, "num"),   # verified (Perun 860 .. Patriarch 192000)
    ("Atk",  0x3E, 2, "num"),   # stat, 1-999, +0.77 HP-correlation
    ("Def",  0x42, 2, "num"),   # stat, 1-999
    ("Cash", 0x4E, 2, "num"),   # battle cash reward
    ("EXP",  0x50, 2, "num"),   # battle EXP reward (+0.80 HP-correlation)
]
# +0x00: 4 param bytes; +0x04: 8x element affinity (0x64=100%); +0x36 u32 HP;
# +0x3A: 99 (const, level cap?). Atk/Def/Cash/EXP inferred by range+HP-correlation.

def enemy_catalog():
    """{int id: {name, hp, atk, def, cash, exp}} for the 97 enemies (from the ISO)."""
    return {int(k): v for k, v in res_json("x2_enemies.json").items()}

def enemy_names():
    """{int id: name} for the 97 enemies (Perun..Patriarch)."""
    return {i: v["name"] for i, v in enemy_catalog().items()}

# --- ISO schema stubs (still to be reverse-engineered) ---------------------
TECH_FIELDS = []      # Tech / Ether effect table (names @ISO ~0x2009B58)
GEAR_FIELDS = []      # Weapon / armor / accessory table
SHOP_FIELDS = []      # Shop stock / price tables
