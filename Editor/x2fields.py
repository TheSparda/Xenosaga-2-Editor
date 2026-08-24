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

# The per-save screenshot the game embeds, shown on the load screen. Useful for
# telling one slot from another in the editor.
GD_THUMB_OFF = 0x174
GD_THUMB_END = 0xD44

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

# In-game caps, from the almarsguides CodeBreaker lists cross-checked with the
# pnach. Used for the "max stats" convenience button and for input validation, so
# both front-ends have to agree — keep this the only copy.
CHAR_CAPS = {
    "Level": 99, "HP": 9999, "Current HP": 9999, "EP": 99,
    "Str": 999, "Vit": 999, "Eatk": 999, "Edef": 999,
    "Dex": 99, "Eva": 99, "Agl": 99,
}

# Columns the character sheet shows, in display order: (header, field label).
SHEET_COLS = [("Lvl", "Level"), ("HP", "HP"), ("Cur HP", "Current HP"), ("EP", "EP"),
              ("Str", "Str"), ("Vit", "Vit"), ("EAtk", "Eatk"), ("EDef", "Edef"),
              ("Dex", "Dex"), ("Eva", "Eva"), ("Agl", "Agl")]

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

# ---------------------------------------------------------------------------
# SKILL / TECH catalog (VERIFIED — extracted from disc 1, 2026-08-23).
#
# The game stores each skill as a NAME string immediately followed by a
# DESCRIPTION string, in one run at ISO 0x2009B58..0x20108D4. The description's
# first line is structured metadata, e.g.
#
#     "All enemies/Long/P/Pierce/Fire\nScorching rain of bullets."
#      target      range type element
#
# and healing/ether skills carry their cost inline as "(EP 4)". So targeting,
# physical-vs-ether, damage type, element and EP cost all come straight off the
# disc — no guide needed. 174 skills, extracted with zero unparsed entries.
#
# Text is ASCII with occasional EUC-JP glyphs (0xA1DF is the multiplication sign,
# used in "All allies (Medica x 2)"), which is why a naive ASCII-only scan
# truncates 25 of them.
#
# This is the CATALOG only. The numeric table behind it — raw power, cast time,
# accuracy — has not been located yet; that still needs ground truth to anchor.
# ---------------------------------------------------------------------------
# SKILL NUMERIC TABLE (VERIFIED 2026-08-24) — 32-byte records, one per skill.
#
# The ether-skill records sit at ISO 0x2007CA0 (disc 1) / 0x20074A0 (disc 2,
# the usual -0x800), 57 records covering skill text indices 0..56
# (Medica .. Erde Kaiser Fury). Fields within a record:
#
#   +0x00 u8   accuracy-like (100 on every ether skill; 90/50 pairs in the
#              neighbouring tech blocks)                      [unverified name]
#   +0x03 u8   category: 1 attack / 2 heal / 4 support / 0 self-misc
#   +0x06 u8   EP COST — matches the "(EP n)" in the skill's own description
#              on 56/56, the anchor that framed the whole table
#   +0x08 u16  ELEMENT bitmask — Aura 0x02, Thunder 0x04, Fire 0x08, Ice 0x10,
#              exactly the affinity element bit order (verified on the four
#              elemental Blasts; Beam=0x01 inferred, not observed)
#   +0x0A u16  POWER — Medica 5, Medica 2 10, Medica All 5, all four Blasts 20,
#              Erde Kaiser Fury 250 (family-consistency verified; no guide
#              publishes ether power numbers to check against)
#   +0x12 u16  effect chance (100 on every effect-bearing skill seen)
#   +0x13 u8   effect kind (1 inflict / 2 block / 3 add-buff / 4 damage-cut)
#   +0x14 u16  effect bitmask (Flame Veil 0x08, Ice Veil 0x10 — element bits)
#   +0x16 u16  STRING ID, 1-based text index — 57/57 exact. This is the field
#              that killed every earlier search: records name themselves, so
#              no order assumption was ever needed.
#   +0x1C u16  animation/VFX id                              [unverified name]
#
# HOW IT WAS FOUND, for next time: two earlier scans failed because the text
# catalog had silently dropped 予備 placeholder entries AND every skill whose
# description has no newline (all the passive equip skills) — compacting the
# indices the scan searched with. With the true index space rebuilt
# (placeholders kept), a strided scan on the 56 in-description EP costs hit
# 56/56 at stride 32 immediately. Same failure shape as the E.S. item ids:
# placeholders occupy index space.
#
# The surrounding region (0x20065A0..0x2009B58 on disc 1) is MORE blocks of the
# same 32-byte record: seven runs of string-ids 1..7 (per-character tech
# blocks), a run of 1..16 (E.S. techs), doubles/enemy-skill blocks after the
# ether block. Their name pools live elsewhere (0x200FC10 'Ice Brand',
# 0x20107F0 'MINIGUN'...), so they are mapped but not yet name-verified — only
# the 57-record ether block is exposed as editable.
# Two blocks share this record layout, both verified (see notes):
#   ether   0x2007CA0, 57 records, skill text indices  0..56
#   doubles 0x2008400, 29 records, skill text indices 59..87
# Disc 2 is the usual -0x800. Records are addressed by TEXT INDEX (what
# `x2patch.py skills` prints), not by position within a block.
SKILL_BLOCKS = {
    1: (("ether", 0x2007CA0, 57, 0), ("double", 0x2008400, 29, 59)),
    2: (("ether", 0x20074A0, 57, 0), ("double", 0x2007C00, 29, 59)),
}
SKILL_STRIDE = 32
SKILL_NUM_FIELDS = [               # exposed, editable
    ("EP",      0x06, 1, "num"),
    ("Element", 0x08, 2, "num"),
    ("Power",   0x0A, 2, "num"),
    ("EffPct",  0x12, 2, "num"),
    ("EffMask", 0x14, 2, "num"),
]
SKILL_ELEMENT_BITS = {"Beam": 0x01, "Aura": 0x02, "Thunder": 0x04,
                      "Fire": 0x08, "Ice": 0x10}

def skill_blocks(disc):
    try:
        return SKILL_BLOCKS[disc]
    except KeyError:
        raise KeyError(f"no skill blocks known for disc {disc!r}") from None

def skill_editable_indices(disc=1):
    """Every skill text index backed by a verified numeric record."""
    out = []
    for _n, _b, count, text0 in skill_blocks(disc):
        out.extend(range(text0, text0 + count))
    return out

def skill_record_off(disc, text_index):
    """Absolute ISO offset of a skill's 32-byte record, or None if that skill
    has no verified numeric record (the tech/combo blocks use a DIFFERENT
    layout — see the notes — so they are deliberately not addressable here)."""
    for _name, base, count, text0 in skill_blocks(disc):
        if text0 <= text_index < text0 + count:
            return base + (text_index - text0) * SKILL_STRIDE
    return None

def skill_base(disc):
    """Lowest address of the disc's skill blocks — the start of the one span a
    front-end needs to read to cover both."""
    return min(b for (_n, b, _c, _t) in skill_blocks(disc))

def skill_span(disc=1):
    """Bytes from the first skill block's base through the end of the last.

    The two blocks are not adjacent (a 64-byte gap sits between them), and both
    move by the same -0x800 on disc 2, so this length is the same either way —
    which is what lets one buffer serve both discs.
    """
    blocks = skill_blocks(disc)
    return max(b + c * SKILL_STRIDE for (_n, b, c, _t) in blocks) - skill_base(disc)

def skill_element_text(mask):
    """0x08 -> 'Fire'; 0 -> '-'; unknown bits shown raw."""
    names = [n for n, b in SKILL_ELEMENT_BITS.items() if mask & b]
    rest = mask & ~sum(SKILL_ELEMENT_BITS.values())
    if rest:
        names.append(f"0x{rest:X}")
    return "+".join(names) if names else "-"

def skill_catalog():
    """{index: {name, target, tags, ep, desc, placeholder[, numeric]}} —
    the 174-entry skill text table (indices 0..173; numerics on 0..56)."""
    return {int(k): v for k, v in res_json("x2_skills.json").items()}

def skill_names():
    """{index: name}."""
    return {k: v["name"] for k, v in skill_catalog().items()}

def es_equip_catalog():
    """{int id: {name, desc}} for the E.S. accessory/circuit table (from ISO
    ~0x200C5D4). Covers ids 0-30 — id base (Auxiliary Armor A = 0) CONFIRMED by
    cross-checking all 36 saves: every in-range E.S. gear value maps to a sensible
    accessory. Higher gear ids (34-37 seen = weapon/frame items) aren't mapped yet."""
    return {int(k): v for k, v in res_json("x2_es_equip.json").items()}

# ---------------------------------------------------------------------------
# ISO ENEMY tables (VERIFIED — both discs). 125 stat records + parallel name
# table + parallel rewards table. Verified against a strategy guide +
# xenoserieswiki: 74/76 guide enemies matched on an 8-field signature with
# exactly one hit each (anchors: Perun rec 6 HP 22,400; Proto Omega 999,999;
# Dark Erde Kaiser 192,000). See Xenosaga2_ISO_offsets.md for the derivation.
#
# BOTH DISCS CARRY THE TABLES (verified 2026-08-23). Disc 2's copy sits exactly
# 0x800 lower, and the 125 x 0x5C stat records are byte-for-byte identical
# between the two images — same for the rewards rows and the name blob. That is
# a correctness fact, not a convenience one: a rebalance written to disc 1 alone
# silently reverts to retail at the disc swap, so the player would meet retuned
# enemies for half the game and stock ones for the rest. Patch both discs, with
# the same values.
# ---------------------------------------------------------------------------
ENEMY_STRIDE     = 0x5C        # stat record stride, index 0..124
ENEMY_COUNT      = 125
ENEMY_TABLES = {
    #        stat records  name blob   rewards rows
    1: {"stats": 0x1FFF5F0, "names": 0x2002310, "rewards": 0x201094C},
    2: {"stats": 0x1FFEDF0, "names": 0x2001B10, "rewards": 0x201014C},
}
# Disc-1 aliases. Every offset cited in the notes and the tests is a disc-1 one,
# so the historical names stay meaningful rather than becoming dict lookups.
ENEMY_TABLE_OFF  = ENEMY_TABLES[1]["stats"]
ENEMY_NAMES_OFF  = ENEMY_TABLES[1]["names"]

def enemy_tables(disc):
    """Table bases for `disc` (1 or 2). Raises for anything else — guessing a
    base is how you write enemy stats over the middle of a movie."""
    try:
        return ENEMY_TABLES[disc]
    except KeyError:
        raise KeyError(f"no enemy tables known for disc {disc!r} "
                       f"(known: {sorted(ENEMY_TABLES)})") from None
ENEMY_FIELDS = [               # (label, offset, width, kind) — stat record
    ("HP",   0x36, 4, "num"),  # Perun 22,400 .. Proto Omega 999,999
    ("STR",  0x3C, 2, "num"),  # physical attack (POW in guides)
    ("VIT",  0x3E, 2, "num"),  # physical defense (ARM in guides)
    ("EATK", 0x40, 2, "num"),  # ether attack
    ("EDEF", 0x42, 2, "num"),  # ether defense
    ("DEX",  0x44, 1, "num"),  # accuracy
    ("EVA",  0x45, 1, "num"),  # evasion
    ("AGL",  0x46, 1, "num"),  # agility / turn speed
]
ENEMY_ID_OFF = 0x52           # u16 enemy id: 501+ field, BOSS_ID_MIN+ boss, 701+ E.S.

# +0x3A: an undecoded u16 sitting between HP and STR, and NOT the constant it
# looks like. 114 of the 125 records hold 99; the eleven below hold 0, 1, 2 or 10
# — identical on both discs, so it is real data and not disc-specific.
#
# It is recorded here for two reasons. The table-search needle
# (x2patch.enemy_signature) assumes 99, which is safe only because every anchor
# record happens to hold 99. And a "does this disc still hold retail values?"
# comparison that includes this halfword reports 114/125 on pristine retail media
# — which is exactly the bug that made `rebalance` refuse to run on real discs.
# The synthetic test fixtures reproduce this distribution so that regression
# stays caught.
ENEMY_UNK3A_OFF = 0x3A
ENEMY_UNK3A_DEFAULT = 99
ENEMY_UNK3A_EXCEPTIONS = {          # record index -> value (verified, both discs)
    37: 0,     # Kfuga Lily
    38: 2,     # E2 Hauser
    43: 1,     # Yacud Cannon
    50: 0,     # Stole Marine
    52: 1,     # Cera 7 F
    53: 1,     # Cera 6 F
    54: 1,     # Executus Arma
    55: 1,     # Cera 7 S
    56: 1,     # Cera 6 S
    65: 10,    # U-TIC Soldier A
    66: 10,    # U-TIC Soldier B
}

def enemy_unk3a(i):
    """The +0x3A halfword a retail record holds at index `i`."""
    return ENEMY_UNK3A_EXCEPTIONS.get(i, ENEMY_UNK3A_DEFAULT)

# ---------------------------------------------------------------------------
# BREAK / ZONE fields (VERIFIED 2026-08-23) — the combo system's own data.
#
# Ep. II's zones are the three attack heights: A (above 3 m, O), B (1-3 m,
# square), C (below 1 m, triangle). Two separate fields, both one-hot on the same
# three bits:
#
#   +0x4C  u8   HITTABLE-ZONE MASK — which of the three zones this enemy has at
#               all. bit0 = A, bit1 = B, bit2 = C. Every one of the 125 records
#               holds a value <= 7.
#   +0x54  u8[4] BREAK SEQUENCE — the zones you must hit, in order, to Break the
#               enemy. One slot per hit, 0 = end of sequence, never a gap before
#               a non-zero. So "CBB" is (4, 2, 2, 0).
#
# Derivation: `enemy data.rtf` publishes a "Hit zone" set and a "Break" sequence
# per enemy. Mapping its 75 entries onto records BY EXACT 8-FIELD STAT SIGNATURE
# (not by name — the guide's names are per-encounter, and name matching
# mis-assigned ~20% of rows and buried the field) gave 72 unique, conflict-free
# assignments. Against that truth the zone scan returned:
#   +0x4C  consistency 1.000, resolution 1.000 over 51 rows  -> the mask
#   +0x54  consistency 1.000 (u16 resolution 0.944)          -> the sequence
# and decoding +0x54..+0x57 reproduces all 46 published sequences EXACTLY, with
# every slot value in {0, 1, 2, 4} and no gaps. Independent cross-check: no
# record's sequence uses a zone missing from its own +0x4C mask (0/125
# violations). Identical on both discs.
#
# 16 of the 125 records have an empty sequence — those are the "Cannot" break
# entries in the guide (mechanisms and scripted fights).
# ---------------------------------------------------------------------------
# STATUS RESISTANCES (VERIFIED 2026-08-23) — one u8 percentage per status.
#
# Enemy i's block sits at `base + i*ENEMY_STRIDE + 0x6C`, which is 0x10 bytes
# INTO RECORD i+1. That is the same shifted framing the affinity block showed
# (+0x58, running past the record end): the game's real record boundary is not
# where our stat base puts it. Empirically, per enemy i:
#
#     stats        base + i*0x5C + 0x36..     (125/125 against the catalog)
#     affinities   base + i*0x5C + 0x58       (71/71 against the guide)
#     resistances  base + i*0x5C + 0x6C       (479/486 against the guide)
#
# Byte-to-status mapping, scored against every published guide row:
#
#     +0  Slow   50/51      +6  EthPD  70/71
#     +2  Blind  70/71      +7  EthDD  70/71
#     +3  Heavy  70/71      +9  ResDw  50/51
#     +4  Weak   70/71     +10  Junk   29/29
#
# 479/486 overall (98.6%), byte-identical on both discs.
#
# Bytes +1, +5 and +8 are NOT identified. +5 carries real per-enemy data (15
# distinct values); +1 and +8 are almost always 0 with a handful of exceptions.
# The guide's remaining two columns, Lost and Curse, do not map to any byte here
# — Lost peaks at 38% agreement and Curse only "matches" because it is nearly
# always 0, which any zero byte satisfies. So they are left out rather than
# assigned on a coin-flip. Bytes +11 onward are 0 across all 125 records.
STATUS_RES_OFF = 0x6C
STATUS_RES_SLOTS = 11
STATUS_RES_FIELDS = [
    ("Slow",  STATUS_RES_OFF + 0,  1, "num"),
    ("Blind", STATUS_RES_OFF + 2,  1, "num"),
    ("Heavy", STATUS_RES_OFF + 3,  1, "num"),
    ("Weak",  STATUS_RES_OFF + 4,  1, "num"),
    ("EthPD", STATUS_RES_OFF + 6,  1, "num"),
    ("EthDD", STATUS_RES_OFF + 7,  1, "num"),
    ("ResDw", STATUS_RES_OFF + 9,  1, "num"),
    ("Junk",  STATUS_RES_OFF + 10, 1, "num"),
]
STATUS_RES_NAMES = [f[0] for f in STATUS_RES_FIELDS]

ZONE_BITS = {"A": 0x01, "B": 0x02, "C": 0x04}
ZONE_SYMBOLS = {0x01: "A", 0x02: "B", 0x04: "C"}
ENEMY_ZONE_MASK_OFF = 0x4C
BREAK_SEQ_OFF = 0x54
BREAK_SEQ_SLOTS = 4
# exposed as ordinary editable fields so the generic read/write path covers them
# ---------------------------------------------------------------------------
# BATTLE FLAGS (VERIFIED 2026-08-24) — two bytes at +0x50/+0x51.
#
# Found by partition scan against a strategy guide's per-enemy property columns,
# with the guide's enemy TYPE used as a positive control (if the scan could not
# recover a property that certainly exists, a null result would have meant "not
# in this record" rather than "no such field").
#
#   +0x50 bits 0-1  enemy type: 0 = Bio, 1 = Gnosis, 2 = Mechanism
#                   57/57 exact against the guide's "Enemy type" column.
#   +0x51 bit 3     zone targeting off — 57/57 exact against the guide's
#                   "Hit zone: None" column, on both discs.
#
# The second one closes a real gap. Breakability is NOT just "does the record
# hold a break sequence": 15 enemies carry a perfectly hittable `BB` whose bytes
# are inert because this bit is set. So:
#
#     unbreakable  ==  zone targeting off  OR  no break sequence
#
# That composite rule reproduces the guide's "Break: Cannot" column 57/57 on both
# discs, and puts the unbreakable set at 36 of 125 records rather than the 16 you
# get from the sequence bytes alone.
#
# Deliberately named for what was verified. The guide column this matches is
# "Hit zone", so the flag is recorded as zone targeting rather than as a
# break flag, even though breakability is what it decides — the same caution
# the +0x04 affinity retraction earned. The other bits of both bytes are NOT
# identified: nothing else reached 100% against any guide column (counter-boost,
# air and down effects all topped out well below), so they stay unexposed.
ENEMY_TYPE_OFF, ENEMY_TYPE_MASK = 0x50, 0x03
ENEMY_TYPE_NAMES = {0: "Bio", 1: "Gnosis", 2: "Mechanism"}
ENEMY_NOZONE_OFF, ENEMY_NOZONE_BIT = 0x51, 0x08

def enemy_type_text(byte):
    """+0x50 -> 'Mechanism'. Unknown codes are shown raw rather than guessed."""
    v = byte & ENEMY_TYPE_MASK
    return ENEMY_TYPE_NAMES.get(v, f"type {v}")

def zone_targeting_off(nozone_byte):
    """True when +0x51 bit 3 is set — the guide's 'Hit zone: None'."""
    return bool(nozone_byte & ENEMY_NOZONE_BIT)

def is_breakable(nozone_byte, seq):
    """The composite rule, verified 57/57 against the guide on both discs."""
    return not zone_targeting_off(nozone_byte) and bool(seq)

FLAG_FIELDS = [
    ("Type",   ENEMY_TYPE_OFF,   1, "num"),
    ("NoZone", ENEMY_NOZONE_OFF, 1, "num"),
]

ZONE_FIELDS = ([("Zones", ENEMY_ZONE_MASK_OFF, 1, "num")] +
               [(f"Brk{n + 1}", BREAK_SEQ_OFF + n, 1, "num")
                for n in range(BREAK_SEQ_SLOTS)])

def decode_break_seq(slots):
    """(4, 2, 2, 0) -> 'CBB'. Unknown/0 slots end the sequence."""
    out = []
    for v in slots:
        sym = ZONE_SYMBOLS.get(v)
        if sym is None:
            break
        out.append(sym)
    return "".join(out)

def encode_break_seq(text):
    """'CBB' or 'c-b-b' -> (4, 2, 2, 0). Raises ValueError on a bad sequence."""
    syms = [c for c in str(text).upper() if not c.isspace() and c != "-"]
    if len(syms) > BREAK_SEQ_SLOTS:
        raise ValueError(f"a break sequence is at most {BREAK_SEQ_SLOTS} hits")
    bad = [c for c in syms if c not in ZONE_BITS]
    if bad:
        raise ValueError(f"not a zone letter: {''.join(bad)} (use A, B or C)")
    vals = [ZONE_BITS[c] for c in syms]
    return tuple(vals + [0] * (BREAK_SEQ_SLOTS - len(vals)))

# ---------------------------------------------------------------------------
# BULK BREAK SHORTENING
#
# The break sequence is the combo loop's actual gate: a 4-hit boss costs four
# correct zone hits per break, every break, all fight. Dropping every sequence by
# one hit (4->3, 3->2, 2->1) cuts that tax across the board without touching a
# single stat. `steps` applies the drop repeatedly.
#
# A 1-hit sequence is left alone rather than emptied — an empty sequence means
# "cannot be broken", which is a different thing entirely and would make a fight
# harder, not faster. Enemies that already can't be broken stay that way.
# The floor exists because emptying a sequence does not speed a fight up — it
# removes the break entirely. 16 retail enemies ship with no sequence, and 15 of
# them still carry a live zone mask, so "no sequence" is not "no weak zones": it
# means there is no break to reach, and the fight gets longer. Keeping the floor
# at 1 is therefore the safe default, but it is a balance choice rather than a
# correctness one, so callers can lower it deliberately.
BREAK_MIN_LEN = 1
BREAK_FLOOR_NONE = 0

def shorten_break_seq(seq, steps=1, floor=BREAK_MIN_LEN):
    """'CBAA' -> 'CBA' (steps=1) -> 'CB' (steps=2). Trims from the END, so the
    opening zone a player already knows stays correct. Never touches a sequence
    that is already empty. With the default floor a sequence is never emptied;
    pass floor=BREAK_FLOOR_NONE to allow that (it makes the enemy unbreakable)."""
    if not seq:
        return seq
    n = max(max(0, int(floor)), len(seq) - max(0, int(steps)))
    return seq[:n]

def plan_break_shortening(sequences, steps=1, floor=BREAK_MIN_LEN, nozone=None):
    """[(index, old, new), ...] for every record the shortening would change.

    `sequences` is {record index: sequence text}. Records already at the floor,
    or unbreakable, are omitted — so the caller can show exactly what it touches
    before writing anything.

    `nozone` is {record index: +0x51 byte}. When given, records whose zone
    targeting is off are skipped too: they cannot be broken whatever their
    sequence bytes say, so trimming those bytes changes nothing the game reads.
    15 retail records are in exactly that state."""
    plan = []
    for i in sorted(sequences):
        if nozone is not None and zone_targeting_off(nozone.get(i, 0)):
            continue
        old = sequences[i] or ""
        new = shorten_break_seq(old, steps, floor)
        if new != old:
            plan.append((i, old, new))
    return plan

def zone_mask_text(mask):
    """5 -> 'AC' — which zones the enemy has."""
    return "".join(s for v, s in sorted(ZONE_SYMBOLS.items()) if mask & v)

# ---------------------------------------------------------------------------
# DAMAGE AFFINITIES (VERIFIED 2026-08-23) — eight per-element damage multipliers.
#
#   +0x58, eight SIGNED bytes, percent = byte * 5.
#
# Element order is the strategy guide's column order, confirmed by the match:
#   Beam, Aura, Thunder, Fire, Ice, Pierce, Slash, Hit
#
# 100% is normal, below resists, above takes extra, 0% is immune, and NEGATIVE
# absorbs (Svarozic at -200% on Fire heals for double). Values seen on disc run
# -200..+300, all multiples of 5, which is exactly what a byte*5 encoding buys.
# Verified against 71 guide entries with complete damage rows: **71/71 exact**.
# Byte-identical on both discs.
#
# NOTE ON THE STRADDLE. The eight bytes for enemy `i` sit at
# `base + i*0x5C + 0x58`, which runs four bytes past the nominal 0x5C record --
# so they occupy the last four bytes of record `i` and the first four of record
# `i+1`. That is verified, not assumed: record `i`'s `+0x00..0x03` equals enemy
# `i-1`'s affinity elements 4..7 for all 124 pairs. The last record's block lands
# in the 52-byte gap before the name table, so there is room. Practically this
# needs no special handling -- the read/write path computes `base + off`, so
# offsets 0x58..0x5F address the right bytes -- but it does mean `+0x00..0x03`
# was never "unknown", and a scanner that slices 0x5C per record cannot see the
# whole block.
#
# THE PREVIOUS DEFINITION WAS WRONG. Up to v1.4.0 this project exposed eight
# "affinity" slots at +0x04 as an opt-in experiment. Those bytes are not
# affinities: they read 0x64 (100) in 124 of the 125 records and carry ASCII in
# the one exception, so they never varied per enemy and editing them changed
# nothing. The guide's damage rows vary heavily (70 of 72 mapped entries have a
# non-100 value), which is what exposed the mismatch. +0x04..+0x0B is left
# documented-but-unexposed below.
AFFINITY_ELEMENTS = ("Beam", "Aura", "Thunder", "Fire", "Ice",
                     "Pierce", "Slash", "Hit")
ENEMY_AFFINITY_OFF = 0x58
ENEMY_AFFINITY_COUNT = 8
ENEMY_AFFINITY_SCALE = 5            # stored byte * 5 == percent
ENEMY_AFFINITY_NORMAL = 100         # percent, i.e. a stored byte of 20
ENEMY_AFFINITY_FIELDS = [(name, ENEMY_AFFINITY_OFF + i, 1, "num")
                         for i, name in enumerate(AFFINITY_ELEMENTS)]

# +0x04..+0x0B: constant 0x64 x8 in 124/125 records (the exception holds ASCII).
# Whatever it is, it is not per-enemy tuning. Recorded so nobody re-derives it.
ENEMY_CONST64_OFF = 0x04
ENEMY_CONST64_COUNT = 8

def affinity_pct(byte):
    """Stored byte -> percent. Signed: 0xD8 -> -40 -> -200%."""
    return (byte - 256 if byte > 127 else byte) * ENEMY_AFFINITY_SCALE

def affinity_byte(pct):
    """Percent -> stored byte. Rounds to the nearest representable step (5%)."""
    step = int(round(pct / float(ENEMY_AFFINITY_SCALE)))
    step = max(-128, min(step, 127))
    return step & 0xFF

AFFINITY_PCT_MIN = affinity_pct(0x80)   # -640
AFFINITY_PCT_MAX = affinity_pct(0x7F)   # +635

# Field label -> key in x2_enemies.json, so a disc can be diffed against the
# verified vanilla values (and restored to them).
#
# This has to cover every writable field. It used to hold only the eleven numbers
# that could be checked against a printed guide, which meant "Compare to retail"
# quietly had nothing to say about break sequences, zones, affinities, status
# resistances or drops — it reported a match on a disc whose bosses had been
# retuned. The rest are populated by Editor/gen_enemy_catalog.py straight from
# the discs, cross-checked between the two pressings; see that file for what the
# retail claim actually rests on.
ENEMY_CATALOG_KEY = {
    "HP": "hp", "STR": "str", "VIT": "vit", "EATK": "eatk", "EDEF": "edef",
    "DEX": "dex", "EVA": "eva", "AGL": "agl", "EXP": "exp", "SP": "sp", "CP": "cp",
}
REWARD_TABLE_OFF = ENEMY_TABLES[1]["rewards"]  # rewards, stride 0x10, row = index
REWARD_STRIDE    = 0x10
REWARD_FIELDS = [
    ("EXP", 0x00, 4, "num"),
    ("SP",  0x04, 2, "num"),
    ("CP",  0x06, 2, "num"),
]

# ---------------------------------------------------------------------------
# ITEM DROPS (VERIFIED 2026-08-23) — the rest of the 0x10 rewards row.
#
#   +0x08 u8  common drop rate, percent
#   +0x09 u8  rare   drop rate, percent
#   +0x0A u8  common item CATEGORY   0 = nothing, 1 = consumable, 2 = E.S. gear
#   +0x0B u8  rare   item CATEGORY
#   +0x0C u8  common item id, 1-BASED within its category (0 = nothing)
#   +0x0D u8  rare   item id
#   +0x0E, +0x0F      always 0 across all 125 records
#
# Derived from the strategy guide's ITEM / RARE ITEM lines, mapped onto records
# by exact stat signature. Drop RATES match the guide on 138 of 144 comparisons.
# The category byte explains what looked at first like a "present" flag: it takes
# 0/1/2 (24/100/20 occurrences), and every value of 2 belongs to an enemy the
# guide says drops E.S. equipment.
#
# Consumable ids are solid: all 23 distinct ids seen resolve through
# `consumable_names()[id - 1]` with **zero conflicts** (id 1 = Med Kit S,
# 5 = Ether Pack S, 11 = Antidote L, 33 = Scrap Iron, 34 = Junked Circuit...).
# Two of them expose gaps in our own consumable catalog around Skill Upgrade B/C
# rather than a decoding problem.
#
# E.S. gear ids are NOT resolved. The category is certain, but the id space does
# not line up with `x2_es_equip.json` at any constant offset (the guide's pairs
# imply +1, +4, +7 and +8 for different entries), and that catalog was itself
# only ever confirmed for accessory ids 0-30. So the fields are editable and the
# category is named, but a category-2 id is shown as a bare number rather than
# under a name it has not earned.
DROP_FIELDS = [
    ("DropRate", 0x08, 1, "num"),
    ("RareRate", 0x09, 1, "num"),
    ("DropCat",  0x0A, 1, "num"),
    ("RareCat",  0x0B, 1, "num"),
    ("DropItem", 0x0C, 1, "num"),
    ("RareItem", 0x0D, 1, "num"),
]
# Now that every writable block is defined, the retail baseline can cover all of
# them: break sequence + breakable-zone mask, damage affinities, status
# resistances and drops, keyed by the lowercased field label.
ENEMY_CATALOG_KEY.update(
    {label: label.lower()
     for label, _off, _w, _k in (ZONE_FIELDS + FLAG_FIELDS + ENEMY_AFFINITY_FIELDS
                                 + STATUS_RES_FIELDS + DROP_FIELDS)})

DROP_CAT_NONE, DROP_CAT_CONSUMABLE, DROP_CAT_ES = 0, 1, 2
DROP_CAT_NAMES = {DROP_CAT_NONE: "nothing",
                  DROP_CAT_CONSUMABLE: "consumable",
                  DROP_CAT_ES: "E.S. gear"}

# The disc holds ONE unified item table (names + descriptions) at ISO 0x200C5D4,
# extracted to x2_items.json. Both drop categories index it, each from its own
# base, with a 1-BASED id:
#
#   category 2 (E.S. gear)   -> x2_items[id - 1]        base 0
#   category 1 (consumable)  -> x2_items[id - 1 + 40]   base 40 (Med Kit S)
#
# The table includes 13 "予備" (spare/reserve) placeholder slots that occupy id
# space. Skipping them is precisely why the drop ids appeared not to line up with
# x2_es_equip.json at any constant offset: the drift accumulates at each block of
# placeholders. Counting them, all 15 unambiguous E.S. pairs from the guide
# resolve exactly.
DROP_CAT_BASE = {DROP_CAT_CONSUMABLE: 40, DROP_CAT_ES: 0}

def item_catalog():
    """{index: {name, desc, off, placeholder}} — the disc's unified item table."""
    return {int(k): v for k, v in res_json("x2_items.json").items()}

def item_name(index):
    """Name at a unified-table index, or None for a placeholder / bad index."""
    e = item_catalog().get(index)
    if not e or e.get("placeholder"):
        return None
    return e["name"]

def drop_item_name(category, item_id):
    """Name for a (category, 1-based id) drop, or None if it can't be named."""
    if not category or not item_id:
        return None
    base = DROP_CAT_BASE.get(category)
    if base is None:
        return None
    return item_name(base + item_id - 1)

def drop_label(category, item_id, rate):
    """'Med Kit S 100%' / 'E.S. gear #14 20%' / 'nothing' — for display."""
    if not category or not item_id:
        return "nothing"
    name = drop_item_name(category, item_id)
    if name is None:
        name = f"{DROP_CAT_NAMES.get(category, 'category %d' % category)} #{item_id}"
    return f"{name} {rate}%"

def enemy_catalog():
    """{int idx: {name,id,hp,str,vit,eatk,edef,dex,eva,agl,exp,sp,cp}} — verified."""
    return {int(k): v for k, v in res_json("x2_enemies.json").items()}

def enemy_names():
    """{int idx: name} for the 125 enemy records (Ai Apaec .. Dark Erde Kaiser)."""
    return {i: v["name"] for i, v in enemy_catalog().items()}

# Enemy ids (record +0x52) fall in bands: 501+ field enemies, 561+ the boss band,
# 701+ E.S./special encounters. Useful for *labelling* a record in a browser or
# reference listing — but NOT for deciding what a rebalance may touch: the 561+
# band mixes late-game field Gnosis in with real bosses. Scaling groups records on
# their own HP instead (MAJOR_HP_THRESHOLD below).
BOSS_ID_MIN = 561

# Per-field write caps. Widths allow more, but nothing on the disc exceeds these
# (HP tops out at Proto Omega's 999,999; the u8 stats are 1..255 by width), and
# writing past the game's own range risks display/overflow bugs.
ENEMY_FIELD_CAPS = {
    "HP": 999999, "STR": 999, "VIT": 999, "EATK": 999, "EDEF": 999,
    "DEX": 255, "EVA": 255, "AGL": 255,
    "EXP": 999999, "SP": 9999, "CP": 9999,
}

# Byte ranges inside the 0x5C stat record that are NOT decoded yet — 65 bytes.
# This is where the break/zone data most likely lives (see the combo-system
# section of Xenosaga2_ISO_offsets.md); `x2patch.py enemy-columns` profiles them.
# Still-undecoded byte ranges in the 0x5C record. Solved out of this set on
# 2026-08-23: +0x4C (zone mask), +0x54..+0x57 (break sequence), and +0x58..+0x5F
# (damage affinities) -- the last of which also accounts for +0x00..+0x03, since
# an affinity block straddles the record boundary. +0x10..+0x19 still looks like
# the guide's ten status resistances but does NOT match it, so it stays here.
ENEMY_UNMAPPED = [(0x0C, 0x36), (0x47, 0x4C), (0x4D, 0x52)]

def enemy_record_tail():
    """Bytes a stat-record field reaches PAST the nominal ENEMY_STRIDE.

    The affinity block (+0x58, 8 bytes) and the status-resistance block (+0x6C,
    11 bytes) both start inside one record and end inside the next — see the
    notes. Anything that slices the table into a fixed buffer of
    ENEMY_COUNT * ENEMY_STRIDE therefore reads off the end on the LAST record,
    which is how the web editor briefly showed Dark Erde Kaiser's Ice/Pierce/
    Slash/Hit and every resistance as blank-and-modified. Callers must add this
    many bytes to the span they read."""
    reach = max(off + w for (_l, off, w, _k) in
                (ENEMY_FIELDS + ENEMY_AFFINITY_FIELDS + ZONE_FIELDS + FLAG_FIELDS
                 + STATUS_RES_FIELDS))
    return max(0, reach - ENEMY_STRIDE)


def enemy_unmapped_offsets():
    """Every still-unknown byte offset within a stat record, ascending."""
    return [o for a, b in ENEMY_UNMAPPED for o in range(a, b)]

_DEBUG_NAME = None

def is_dummy_record(rec):
    """True for placeholder/debug records that no rebalance should touch.

    Two signatures, both visible in the verified catalog: the disc's internal
    debug names (GNO013, CRE006, UMA013, MON001-4, BOS026-29 — EUC-JP full-width
    on disc, ASCII once decoded), and unused rows carrying a token EXP value with
    no SP/CP at all (e.g. Testud II: 8,000 HP but 79 EXP). Real scripted fights
    award 0 EXP, so the token-EXP rule deliberately requires 0 < EXP < 100."""
    global _DEBUG_NAME
    if _DEBUG_NAME is None:
        import re
        _DEBUG_NAME = re.compile(r"^[A-Z]{3}\d{3}$")
    if _DEBUG_NAME.match(str(rec.get("name", "")).strip()):
        return True
    return 0 < rec.get("exp", 0) < 100 and not rec.get("sp") and not rec.get("cp")

# ---------------------------------------------------------------------------
# BATTLE-PACING PROFILES (combo-system tuning over the verified tables).
#
# Ep. II's stock -> break -> boost loop is the only efficient way to fight, and
# the loop costs turns before it pays out: bank stocks (up to 3), hit the enemy's
# exact weak-zone sequence to Break (~x1.5 damage, AIR/DOWN doubles), then spend
# the shared Boost gauge to chain the rest of the party in before the Break
# expires at end of turn. The mechanics themselves are code (not located yet —
# tier 2 in the notes), but the *cost* of the ritual is almost entirely enemy
# tuning: HP decides how many stocked chains a kill takes, VIT/EDEF decide
# whether off-loop attacks do anything, AGL decides how often enemies interrupt
# a setup, and SP/CP gate how fast the skill system opens up.
#
# Each profile scales verified fields by a percentage, per group. Groups are
# split on the record's own HP (MAJOR_HP_THRESHOLD) because that is the one
# boss-ness signal we can actually read off the disc — the enemy ID band mixes
# late-game field Gnosis in with bosses, so it is not usable for this.
# ---------------------------------------------------------------------------
MAJOR_HP_THRESHOLD = 20000        # records at/above this scale as "major"

PROFILES = {
    "faster": {
        "label": "Faster fights",
        "note": "Keeps the combo loop, cuts the tax: fewer stocked chains per "
                "kill and quicker skill unlocks. The safe default.",
        "regular": {"HP": 45, "EXP": 150, "SP": 150, "CP": 150},
        "major":   {"HP": 70, "EXP": 150, "SP": 150, "CP": 150},
    },
    "freer": {
        "label": "Freer play",
        "note": "Makes off-combo attacks viable — softer defenses so unbroken "
                "damage lands, on top of a lighter HP cut.",
        "regular": {"HP": 55, "VIT": 70, "EDEF": 70, "EXP": 150, "SP": 150, "CP": 150},
        "major":   {"HP": 75, "VIT": 80, "EDEF": 80, "EXP": 150, "SP": 150, "CP": 150},
    },
    "deeper": {
        "label": "Deeper challenge",
        "note": "For players who like the loop: enemies hit harder and last "
                "longer, but pay out much more.",
        "regular": {"HP": 110, "STR": 115, "EATK": 115, "EXP": 200, "SP": 200, "CP": 200},
        "major":   {"HP": 130, "STR": 115, "EATK": 115, "EXP": 200, "SP": 200, "CP": 200},
    },
    "grindcut": {
        "label": "Reward-only",
        "note": "Leaves every fight exactly as designed and only removes the "
                "grind between them.",
        "regular": {"EXP": 250, "SP": 250, "CP": 250},
        "major":   {"EXP": 250, "SP": 250, "CP": 250},
    },
}

def profile(name):
    """Look up a battle-pacing profile by key. Raises KeyError with the valid list."""
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(f"unknown profile {name!r} — choose from {', '.join(PROFILES)}")

# --- ISO schema stubs (still to be reverse-engineered) ---------------------
TECH_FIELDS = []      # Tech / Ether effect table (names @ISO ~0x2009B58)
GEAR_FIELDS = []      # Weapon / armor / accessory table
SHOP_FIELDS = []      # Shop stock / price tables
# ZONE_FIELDS is no longer a stub — see the BREAK / ZONE block above.


# ---------------------------------------------------------------------------
# Machine-readable export of everything above that a non-Python front-end needs.
#
# The web ISO editor reads the disc directly in JavaScript (no Python runtime on
# that tab), so it used to carry its own hand-copied copy of these offsets. A
# second copy of a byte offset that gets written into a 4.6 GB disc image is a
# data-loss bug waiting to happen, so it now consumes this instead — generated by
# Editor/gen_web_tables.py into web/tables.json and checked in CI for drift.
# ---------------------------------------------------------------------------
def web_tables():
    def fields(spec):
        return [[label, off, width] for (label, off, width, _kind) in spec]
    return {
        "_comment": "GENERATED from Editor/x2fields.py by Editor/gen_web_tables.py "
                    "— do not edit by hand; run the generator instead.",
        "game": GAME_NAME,
        "volumeId": VOLUME_ID,
        "serials": SERIALS,
        "gamedataSize": GAMEDATA_SIZE,
        "bossIdMin": BOSS_ID_MIN,
        # Per-disc table bases. Both discs carry byte-identical enemy data, so a
        # rebalance has to be written to both or it stops at the disc swap; the
        # front-end resolves its bases from here after it reads the serial.
        "enemyTables": {str(d): t for d, t in sorted(ENEMY_TABLES.items())},
        "enemy": {
            "base": ENEMY_TABLE_OFF,          # disc-1 default; see enemyTables
            "stride": ENEMY_STRIDE,
            "count": ENEMY_COUNT,
            "namesOff": ENEMY_NAMES_OFF,
            "idOff": ENEMY_ID_OFF,
            # extra bytes past count*stride that a front-end must read, because
            # the affinity and resistance blocks overhang the record
            "recordTail": enemy_record_tail(),
            "fields": fields(ENEMY_FIELDS),
            # verified: eight signed bytes at +0x58, percent = byte * 5
            "affinityFields": fields(ENEMY_AFFINITY_FIELDS),
            "affinityNormal": ENEMY_AFFINITY_NORMAL,
            "affinityScale": ENEMY_AFFINITY_SCALE,
            "affinityElements": list(AFFINITY_ELEMENTS),
            # break/zone data (verified): the hittable-zone mask and the four
            # one-hot break-sequence slots
            "zoneFields": fields(ZONE_FIELDS),
            # verified battle flags: enemy type (+0x50 bits 0-1) and zone
            # targeting off (+0x51 bit 3) — see the BATTLE FLAGS block
            "flagFields": fields(FLAG_FIELDS),
            "typeOff": ENEMY_TYPE_OFF, "typeMask": ENEMY_TYPE_MASK,
            "typeNames": {str(k): v for k, v in sorted(ENEMY_TYPE_NAMES.items())},
            "noZoneOff": ENEMY_NOZONE_OFF, "noZoneBit": ENEMY_NOZONE_BIT,
            "zoneMaskOff": ENEMY_ZONE_MASK_OFF,
            "breakSeqOff": BREAK_SEQ_OFF,
            "breakSeqSlots": BREAK_SEQ_SLOTS,
            "zoneBits": ZONE_BITS,
            # status resistances: one u8 percent per status, at +0x6C
            "statusResFields": fields(STATUS_RES_FIELDS),
        },
        "reward": {
            "base": REWARD_TABLE_OFF,
            "stride": REWARD_STRIDE,
            "fields": fields(REWARD_FIELDS),
            # item drops share the 0x10 rewards row
            "dropFields": fields(DROP_FIELDS),
            "dropCatNames": {str(k): v for k, v in sorted(DROP_CAT_NAMES.items())},
            "dropCatConsumable": DROP_CAT_CONSUMABLE,
            # unified item table bases, so the front-end can name both categories
            "dropCatBase": {str(k): v for k, v in sorted(DROP_CAT_BASE.items())},
        },
        "character": {
            "base": CHAR_TABLE_OFF,
            "stride": CHAR_STRIDE,
            "count": CHAR_COUNT,
            "fields": fields(CHAR_FIELDS),
            "esFields": fields(ES_EQUIP_FIELDS),
            "caps": CHAR_CAPS,
            "sheetCols": [list(c) for c in SHEET_COLS],
        },
        # Ether + Double skill numeric records. Two disjoint blocks per disc, so
        # the front-end reads one span covering both rather than two buffers;
        # skillSpan is that span's length, identical on both discs.
        "skill": {
            "blocks": {str(d): [[n, b, c, t] for (n, b, c, t) in blocks]
                       for d, blocks in sorted(SKILL_BLOCKS.items())},
            "span": skill_span(),
            "stride": SKILL_STRIDE,
            "fields": fields(SKILL_NUM_FIELDS),
            "elementBits": SKILL_ELEMENT_BITS,
        },
        "catalogKeys": ENEMY_CATALOG_KEY,
        # Battle-pacing profiles, so the web ISO editor runs the same numbers as
        # the CLI instead of a hand-copied duplicate.
        "profiles": PROFILES,
        "majorHpThreshold": MAJOR_HP_THRESHOLD,
        "fieldCaps": ENEMY_FIELD_CAPS,
    }
