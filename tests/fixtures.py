"""
Synthetic fixtures for the test suite. Every byte here is fabricated — the repo
ships no game data, and the reverse-engineering samples (real saves, retail
discs) are not redistributable.

Two different levels of confidence, worth keeping straight:

* The memory-card and `.psu` builders follow the *published* PS2MFS layout
  (superblock, two-level FAT, 512-byte directory entries, optional per-page
  ECC), so a round-trip through them genuinely exercises the reader against an
  independent description of the format.
* The psv / sharkport / cbs builders encode *our* understanding of those
  wrappers, derived from the real samples during reverse engineering. They lock
  current behaviour in against regressions; they cannot re-prove the format.
"""
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Editor"))

import x2fields as F          # noqa: E402
import x2mc as MC             # noqa: E402
import x2lzari as LZ          # noqa: E402
import x2save as SV           # noqa: E402

SAVE_ID = "BASLUS-20892"
FOLDER = SAVE_ID + "Xeno201"
GD_NAME = FOLDER              # on a card the payload file is named after its folder

DIR_MODE = 0x8427             # DF_EXISTS|0x0400|DF_DIRECTORY|rwx
FILE_MODE = 0x8497            # DF_EXISTS|0x0400|DF_FILE|rwx


# ---------------------------------------------------------------------------
# gamedata payload
# ---------------------------------------------------------------------------
def default_roster():
    """15 character records: 7 on-foot, 3 empty, 5 E.S. units.

    `Name ptr` is the disc unit record's name-pool offset (what the save copies
    in at join time) and `Unit id` is the real id — 1..7 on foot, 101..103 for
    the E.S. units, matching x2_units.json.
    """
    on_foot = [
        (0x0564, 3, "chaos",   41, 1200, 60, 210, 180, 240, 220, 55, 40, 45),
        (0x056A, 4, "KOS-MOS", 43, 1450, 55, 260, 230, 200, 190, 60, 35, 42),
        (0x0570, 6, "Shion",   40, 1050, 70, 150, 140, 280, 260, 50, 38, 40),
        (0x0576, 2, "Jin",     39, 1300, 45, 250, 220, 160, 150, 58, 41, 44),
        (0x057C, 1, "Ziggy",   38, 1380, 40, 240, 250, 140, 170, 52, 30, 36),
        (0x0582, 7, "MOMO",    37,  900, 80, 120, 130, 290, 250, 48, 44, 43),
        (0x0588, 5, "Jr.",     42, 1150, 60, 230, 190, 210, 200, 62, 46, 47),
    ]
    recs = []
    for (ptr, uid, _name, lvl, hp, ep, st, vit, ea, ed, dex, eva, agl) in on_foot:
        recs.append({"Name ptr": ptr, "Unit id": uid, "Level": lvl,
                     "HP": hp, "Current HP": hp,
                     "EP": ep, "Str": st, "Vit": vit, "Eatk": ea, "Edef": ed,
                     "Dex": dex, "Eva": eva, "Agl": agl})
    recs += [{} for _ in range(3)]                    # unrecruited slots
    for i, (ptr, hp) in enumerate([(0x05A0, 14200), (0x05A6, 16800), (0x05AC, 21000)]):
        recs.append({"Name ptr": ptr, "Unit id": 101 + i, "Level": 40 + i,
                     "HP": hp, "Current HP": hp,
                     "Str": 300, "Vit": 280, "Eatk": 220, "Edef": 210,
                     "Dex": 70, "Eva": 20, "Agl": 30,
                     "Slot 1": 3 + i, "Slot 2": 12, "Slot 3": 0})
    recs += [{} for _ in range(F.CHAR_COUNT - len(recs))]
    return recs[:F.CHAR_COUNT]


def default_growth():
    """15 growth records — EXP, the two point pools, and the learned masks.

    chaos knows ethers 0, 2 and 5 and equip/auto skills 110 and 141; Shion
    knows one ether. Everyone else's masks are empty, so a test that asserts a
    mask changed cannot pass on someone else's bits.
    """
    recs = [{} for _ in range(F.GROWTH_COUNT)]
    recs[0] = {"EXP": 250000, "EXP to next": 4200, "Skill Points": 3150,
               "Class Points": 900, "ether": [0, 2, 5], "skills": [110, 141]}
    recs[2] = {"EXP": 240000, "EXP to next": 5000, "Skill Points": 1200,
               "Class Points": 450, "ether": [0], "skills": []}
    return recs


def gamedata(gold=1234567, roster=None, growth=None, checksum=0xDEADBEEF,
             playtime=(30, 18), consumables=None, es_gear=None, key_items=None):
    """A well-formed 20,832-byte Xenosaga II save payload."""
    gd = bytearray(F.GAMEDATA_SIZE)
    struct.pack_into("<I", gd, 8, checksum)             # the un-cracked +0x08 field
    gd[0x10] = 7                                       # misc counter
    struct.pack_into("<I", gd, F.GD_GOLD_OFF, gold)
    gd[0x174:0x17A] = b"\xff\xd8\xff\xe0\x00\x10"      # stand-in JPEG header
    gd[0xD40:0xD44] = b"\xff\xd9\x00\x00"
    gd[F.GD_PLAYTIME_OFF:F.GD_PLAYTIME_OFF + F.PS2_TIME_SIZE] = F.encode_playtime(
        playtime[0], playtime[1], 42)
    for i, rec in enumerate(roster if roster is not None else default_roster()):
        base = F.CHAR_TABLE_OFF + i * F.CHAR_STRIDE
        for label, off, width, _kind in F.CHAR_FIELDS + F.ES_ACCESSORY_FIELDS:
            gd[base + off:base + off + width] = int(rec.get(label, 0)).to_bytes(
                width, "little")
        if not rec:
            continue
        # retail writes a flat 100% on all eight damage affinities
        for k in range(F.CHAR_AFFINITY_COUNT):
            gd[base + F.CHAR_AFFINITY_OFF + k] = F.ENEMY_AFFINITY_NORMAL // F.ENEMY_AFFINITY_SCALE
        for k, v in enumerate(rec.get("equip", [])[:F.EQUIP_SLOT_COUNT]):
            gd[base + F.EQUIP_SLOT_OFF + k] = int(v)
    for i, rec in enumerate(growth if growth is not None else default_growth()):
        base = F.GROWTH_TABLE_OFF + i * F.GROWTH_STRIDE
        for label, off, width, _kind in F.GROWTH_FIELDS:
            gd[base + off:base + off + width] = int(rec.get(label, 0)).to_bytes(
                width, "little")
        for idx in rec.get("ether", []):
            F.set_learned_bit(gd, base + F.ETHER_MASK_OFF, F.ETHER_MASK_COUNT,
                              F.ETHER_MASK_TEXT0, idx, True)
        for idx in rec.get("skills", []):
            F.set_learned_bit(gd, base + F.SKILL_MASK_OFF, F.SKILL_MASK_COUNT,
                              F.SKILL_MASK_TEXT0, idx, True)
    for off, count, vals in (
            (F.INV_CONSUMABLE_OFF, F.INV_CONSUMABLE_COUNT,
             consumables if consumables is not None else {0: 12, 4: 5, 13: 2}),
            (F.INV_ES_GEAR_OFF, F.INV_ES_GEAR_COUNT,
             es_gear if es_gear is not None else {0: 1, 19: 3}),
            (F.INV_KEYITEM_OFF, F.INV_KEYITEM_COUNT,
             key_items if key_items is not None else {0: 1, 76: 1, 77: 1})):
        for slot, qty in vals.items():
            struct.pack_into("<H", gd, off + 2 * int(slot), int(qty))
    return bytes(gd)


def icon_sys(title="XenosagaEPII-01[30:18]", nl=15):
    """A 964-byte icon.sys carrying a two-line, Shift-JIS save title."""
    buf = bytearray(0x3C4)
    buf[0:4] = b"PS2D"
    struct.pack_into("<HH", buf, 0x04, 0, nl)
    enc = title.encode("shift_jis", "replace")[:67]
    buf[0xC0:0xC0 + len(enc)] = enc
    for off, name in ((0x104, "list.ico"), (0x144, "copy.ico"), (0x184, "del.ico")):
        buf[off:off + len(name)] = name.encode()
    return bytes(buf)


# ---------------------------------------------------------------------------
# 512-byte directory entries (shared by the card and .psu builders)
# ---------------------------------------------------------------------------
def dirent(mode, length, cluster, name):
    return MC._DIRENT.pack(mode, 0, length, b"\0" * 8, cluster, b"\0" * 8, 0,
                           b"\0" * 28, name.encode("latin1")[:32].ljust(32, b"\0"))


# ---------------------------------------------------------------------------
# PS2 memory-card image (.ps2 / .mcd)
# ---------------------------------------------------------------------------
PAGE = 512
PPC = 2                    # pages per cluster -> 1024-byte clusters
PPB = 16                   # pages per block
CLUSTER = PAGE * PPC
CLUSTERS = 8192            # 8 MB card
IFC_CLUSTER = 8            # cluster holding the indirect FAT
FAT_CLUSTER0 = 9           # first of 32 FAT clusters
NFAT = 32
ALLOC_OFF = 41
ALLOC_END = 8135

_ecc_memo = {}

def _ecc_page_cached(page):
    hit = _ecc_memo.get(page)
    if hit is None:
        hit = _ecc_memo[page] = MC.ecc_page(page)
    return hit


def memcard(folders, ecc=True, spare_fill=b"\x00" * 4):
    """Build a formatted 8 MB card image holding `folders`.

    folders: [(folder name, {file name: bytes}), ...] — created in order, so a
    caller can lay out several save slots and address them by index.
    """
    fat = [0x7FFFFFFF] * (NFAT * (CLUSTER // 4))       # 0x7FFFFFFF == free
    raw = bytearray(CLUSTERS * CLUSTER)
    cursor = 0

    def alloc(n):
        nonlocal cursor
        chain = list(range(cursor, cursor + n))
        cursor += n
        for a, b in zip(chain, chain[1:]):
            fat[a] = 0x80000000 | b                    # allocated, points on
        fat[chain[-1]] = 0xFFFFFFFF                    # allocated, end of chain
        return chain

    def put(chain, blob):
        for i, c in enumerate(chain):
            off = (ALLOC_OFF + c) * CLUSTER
            raw[off:off + CLUSTER] = blob[i * CLUSTER:(i + 1) * CLUSTER].ljust(
                CLUSTER, b"\0")

    n_root = 2 + len(folders)
    root_chain = alloc((n_root + 1) // 2)              # 2 entries per cluster
    specs = []
    for name, files in folders:
        n_ent = 2 + len(files)
        dir_chain = alloc((n_ent + 1) // 2)
        file_specs = []
        for fname, blob in files.items():
            chain = alloc(max(1, (len(blob) + CLUSTER - 1) // CLUSTER))
            put(chain, blob)
            file_specs.append((fname, len(blob), chain[0]))
        specs.append((name, n_ent, dir_chain, file_specs))

    root = dirent(DIR_MODE, n_root, root_chain[0], ".") + dirent(DIR_MODE, n_root, 0, "..")
    for name, n_ent, dir_chain, _f in specs:
        root += dirent(DIR_MODE, n_ent, dir_chain[0], name)
    put(root_chain, root)

    for name, n_ent, dir_chain, file_specs in specs:
        blob = (dirent(DIR_MODE, n_ent, dir_chain[0], ".")
                + dirent(DIR_MODE, n_root, root_chain[0], ".."))
        for fname, length, first in file_specs:
            blob += dirent(FILE_MODE, length, first, fname)
        put(dir_chain, blob)

    # indirect FAT -> FAT clusters
    ifc = b"".join(struct.pack("<I", FAT_CLUSTER0 + i if i < NFAT else 0xFFFFFFFF)
                   for i in range(CLUSTER // 4))
    raw[IFC_CLUSTER * CLUSTER:(IFC_CLUSTER + 1) * CLUSTER] = ifc
    per = CLUSTER // 4
    for i in range(NFAT):
        blob = b"".join(struct.pack("<I", e) for e in fat[i * per:(i + 1) * per])
        off = (FAT_CLUSTER0 + i) * CLUSTER
        raw[off:off + CLUSTER] = blob

    sb = bytearray(PAGE)
    sb[0:28] = MC.PS2MC_MAGIC
    sb[0x1C:0x23] = b"1.2.0.0"
    struct.pack_into("<HHHH", sb, 0x28, PAGE, PPC, PPB, 0xFF00)
    struct.pack_into("<IIII", sb, 0x30, CLUSTERS, ALLOC_OFF, ALLOC_END, 0)
    struct.pack_into("<II", sb, 0x40, 1023, 1022)      # backup blocks
    for i in range(32):
        struct.pack_into("<I", sb, 0x50 + 4 * i, IFC_CLUSTER if i == 0 else 0xFFFFFFFF)
        struct.pack_into("<I", sb, 0xD0 + 4 * i, 0xFFFFFFFF)
    sb[0x150], sb[0x151] = 2, 0x52
    raw[0:PAGE] = sb

    if not ecc:
        return bytes(raw)
    out = bytearray()
    for p in range(CLUSTERS * PPC):
        page = bytes(raw[p * PAGE:(p + 1) * PAGE])
        out += page + _ecc_page_cached(page) + spare_fill
    return bytes(out)


def x2_memcard(n_slots=1, gold=1234567, ecc=True, **kw):
    """A card carrying `n_slots` Xenosaga II saves plus one unrelated save."""
    folders = [("BASLUS-21118OtherGame", {"OtherGame": b"\x11" * 3000,
                                          "icon.sys": icon_sys("Some Other Game")})]
    for i in range(n_slots):
        name = f"{SAVE_ID}Xeno2{i + 1:02d}"
        folders.append((name, {
            "icon.sys": icon_sys(f"XenosagaEPII-{i + 1:02d}[{10 + i}:30]"),
            name: gamedata(gold=gold + i),
            "system.ico": b"\x22" * 1800,
        }))
    return memcard(folders, ecc=ecc, **kw)


# ---------------------------------------------------------------------------
# EMS .psu
# ---------------------------------------------------------------------------
def psu(gd=None, folder=FOLDER):
    gd = gamedata() if gd is None else gd
    files = [("icon.sys", icon_sys()), (folder, gd), ("system.ico", b"\x33" * 1800)]
    out = bytearray()
    out += dirent(DIR_MODE, 2 + len(files), 0, folder)
    out += dirent(DIR_MODE, 0, 0, ".")
    out += dirent(DIR_MODE, 0, 0, "..")
    for name, blob in files:
        out += dirent(FILE_MODE, len(blob), 0, name)
        pad = (-len(blob)) % MC.PSU_ALIGN
        out += blob + b"\0" * pad
    return bytes(out)


# ---------------------------------------------------------------------------
# PS3 export .psv
# ---------------------------------------------------------------------------
def psv(gd=None, folder=FOLDER):
    gd = gamedata() if gd is None else gd
    files = [("system.ico", b"\x44" * 1800), (folder, gd), ("icon.sys", icon_sys())]
    head = bytearray(0x84)
    head[0:4] = SV.PSV_MAGIC
    table = bytearray()
    table += dirent(DIR_MODE, len(files), 0, folder)          # the folder itself
    for name, blob in files:
        # x2save locates entries by scanning for the name and reading size at
        # name-8 / mode at name-4, so the entry must place them there.
        ent = bytearray(0x80)
        struct.pack_into("<I", ent, 0x38, len(blob))
        struct.pack_into("<I", ent, 0x3C, 0x8497)
        ent[0x40:0x40 + len(name)] = name.encode("latin1")
        table += ent
    body = b"".join(blob for _n, blob in files)
    return bytes(head) + bytes(table) + body


# ---------------------------------------------------------------------------
# SharkPort / X-Port .sps
# ---------------------------------------------------------------------------
def sharkport(gd=None, folder=FOLDER):
    gd = gamedata() if gd is None else gd
    files = [("icon.sys", icon_sys()), (folder, gd)]

    def hdr(name, length):
        return struct.pack("<H64sL8xH2x8s8s", 98,
                           name.encode("latin1").ljust(64, b"\0"), length,
                           FILE_MODE, b"\0" * 8, b"\0" * 8)

    out = bytearray()
    out += struct.pack("<I", len(SV.SPS_MAGIC)) + SV.SPS_MAGIC
    out += b"\0\0\0\0"                                        # savetype
    for text in (b"Xenosaga II", b"2026/08/23", b"synthetic fixture"):
        out += struct.pack("<I", len(text)) + text
    out += struct.pack("<I", sum(len(b) for _n, b in files))   # flen
    out += struct.pack("<H64sL8xH2x8s8s", 98,
                       folder.encode("latin1").ljust(64, b"\0"),
                       len(files) + 2, DIR_MODE, b"\0" * 8, b"\0" * 8)
    for name, blob in files:
        out += hdr(name, len(blob)) + blob
    out += struct.pack("<I", 0)                               # trailing checksum
    return bytes(out)


# ---------------------------------------------------------------------------
# CodeBreaker .cbs
# ---------------------------------------------------------------------------
def cbs(gd=None, folder=FOLDER):
    gd = gamedata() if gd is None else gd
    files = [("icon.sys", icon_sys()), (folder, gd)]
    body = bytearray()
    for name, blob in files:
        body += struct.pack("<8s8sLHHLL32s", b"\0" * 8, b"\0" * 8, len(blob),
                            FILE_MODE, 0, 0, 0,
                            name.encode("latin1").ljust(32, b"\0"))
        body += blob
    comp = SV._cbs_rc4(zlib.compress(bytes(body), 9))
    hlen = 0x80
    head = bytearray(hlen)
    head[0:4] = SV.CBS_MAGIC
    struct.pack_into("<I", head, 8, hlen)
    struct.pack_into("<I", head, 12, len(body))                # decompressed size
    struct.pack_into("<I", head, 16, len(comp))                # compressed size
    head[0x20:0x20 + len(folder)] = folder.encode("latin1")
    return bytes(head) + comp


def max_save(gd=None, folder=FOLDER, checksum=0x11223344):
    """An AR Max (.max) container: 0x58 header, then LZARI over the entry stream.

    The entry padding on real files is not a constant alignment (2 bytes after
    one entry, 12 after the next), so this builder uses a deliberately AWKWARD
    padding — 6 bytes after the first entry — to prove the reader locates entries
    by scanning rather than by assuming a rule."""
    gd = gamedata() if gd is None else gd
    files = [("system.ico", b"\x01\x02\x03" * 400), (folder, gd),
             ("icon.sys", icon_sys())]
    body = bytearray()
    for i, (name, blob) in enumerate(files):
        body += struct.pack("<I32s", len(blob), name.encode("latin1").ljust(32, b"\0"))
        body += blob
        if i == 0:
            body += b"\0" * 6
        elif i == 1:
            body += b"\0" * 12
    comp = LZ.compress(bytes(body))
    head = bytearray(SV.MAX_HDR)
    head[0:12] = SV.MAX_MAGIC
    struct.pack_into("<I", head, 0x0C, checksum)
    head[0x10:0x10 + len(folder)] = folder.encode("latin1")
    head[0x30:0x30 + 10] = b"XenosagaII"
    struct.pack_into("<II", head, 0x50, len(comp) + 4, len(files))
    return bytes(head) + struct.pack("<I", len(body)) + comp


# ---------------------------------------------------------------------------
# A minimal stand-in disc image carrying just the tables the editor writes.
# ---------------------------------------------------------------------------
def write_fake_disc(path, enemies=None):
    """Create a sparse image that passes `verify` and holds the enemy tables.

    `enemies`: {record index: {field label: value}}. Every writable field is
    honoured, not just stats and rewards — the retail comparison covers all of
    them, so a fixture that silently ignored break sequences or affinities could
    not stand in for a pristine disc. Records not named get deterministic filler
    so tests can prove writes are surgical.

    Fields are written at absolute table offsets rather than into a per-record
    buffer, because two blocks run past the end of their own record: affinity
    slots 4-7 and every status resistance live in the next record's space (and
    the last record's in the table's tail). Building the whole table first and
    then placing fields is the only way that comes out right.
    """
    end = max(F.ENEMY_TABLE_OFF + F.ENEMY_COUNT * F.ENEMY_STRIDE,
              F.ENEMY_NAMES_OFF + 0x4000,
              F.REWARD_TABLE_OFF + F.ENEMY_COUNT * F.REWARD_STRIDE) + 0x800
    over = enemies or {}
    stats = bytearray(F.ENEMY_COUNT * F.ENEMY_STRIDE + F.enemy_record_tail())
    rewards = bytearray(F.ENEMY_COUNT * F.REWARD_STRIDE)

    def place(buf, i, stride, off, width, value):
        at = i * stride + off
        buf[at:at + width] = int(value).to_bytes(width, "little")

    for i in range(F.ENEMY_COUNT):
        at = i * F.ENEMY_STRIDE
        # +0x04..0x0B is a constant 0x64 block on disc (not affinities).
        stats[at + 0x04:at + 0x0C] = bytes([0x64] * 8)
        # +0x3A is part of the 17-byte run the table locator searches for, so a
        # faithful stand-in has to carry it — including the eleven records that
        # hold something other than 99. Writing 99 across the board (as this
        # fixture used to) hides any comparison that wrongly treats the raw run
        # as a retail-value check.
        struct.pack_into("<H", stats, at + F.ENEMY_UNK3A_OFF, F.enemy_unk3a(i))
        struct.pack_into("<H", stats, at + 0x52, 500 + i)
        for label, off, width, _k in F.ENEMY_FIELDS:
            place(stats, i, F.ENEMY_STRIDE, off, width,
                  (i + 1) * 7 % (1 << (8 * width)))
        # one-hot sequence slots + a zone mask covering exactly the zones used
        slots = F.encode_break_seq(("BB", "CB", "CC", "CBB", "", "AA")[i % 6])
        for n, v in enumerate(slots):
            stats[at + F.BREAK_SEQ_OFF + n] = v
        stats[at + F.ENEMY_ZONE_MASK_OFF] = slots[0] | slots[1] | slots[2] | slots[3]
        for label, off, width, _k in F.REWARD_FIELDS:
            place(rewards, i, F.REWARD_STRIDE, off, width,
                  (i + 3) * 11 % (1 << (8 * width)))

    # blocks that straddle the record boundary, placed absolutely: a flat 100%
    # affinity is what most retail records hold
    for i in range(F.ENEMY_COUNT):
        for _n, off, _w, _k in F.ENEMY_AFFINITY_FIELDS:
            place(stats, i, F.ENEMY_STRIDE, off, 1, F.affinity_byte(100))
        for _n, off, _w, _k in F.STATUS_RES_FIELDS:
            place(stats, i, F.ENEMY_STRIDE, off, 1, 50)

    for i, fields in over.items():
        for label, value in fields.items():
            spec = next((f for f in (F.ENEMY_FIELDS + F.ENEMY_AFFINITY_FIELDS
                                     + F.ZONE_FIELDS + F.FLAG_FIELDS
                                     + F.STATUS_RES_FIELDS)
                         if f[0] == label), None)
            if spec is not None:
                place(stats, i, F.ENEMY_STRIDE, spec[1], spec[2], value)
                continue
            spec = next((f for f in (F.REWARD_FIELDS + F.DROP_FIELDS)
                         if f[0] == label), None)
            if spec is None:
                raise KeyError(f"unknown enemy field {label!r}")
            place(rewards, i, F.REWARD_STRIDE, spec[1], spec[2], value)

    # the player-unit table: 15 records before the enemy table, same layout.
    # Deterministic filler + the real name pointers, so unit_name() resolves.
    # + the tail the affinity block overhangs into, same as the enemy table
    units = bytearray(F.UNIT_COUNT * F.ENEMY_STRIDE + F.unit_record_tail())
    uptrs = [0x564, 0x56A, 0x572, 0x578, 0x57C, 0x582, 0x587,
             0x58B, 0x592, 0x599, 0x5A0, 0x5AA, 0x5B6, 0x5C0, 0x5C7]
    unames = (b"chaos\0KOS-MOS\0Shion\0Jin\0Ziggy\0MOMO\0Jr.\0"
              b"sp1\0\0\0\0sp2\0\0\0\0sp3\0\0\0\0"
              b"E.S.Dinah\0E.S.Zebulun\0E.S.Asher\0sp4\0\0\0\0sp5\0\0\0\0")
    for i in range(F.UNIT_COUNT):
        at = i * F.ENEMY_STRIDE
        struct.pack_into("<H", units, at + F.UNIT_NAME_PTR_OFF, uptrs[i])
        struct.pack_into("<H", units, at + F.UNIT_ID_OFF,
                         (i + 1) if i < 7 else (101 + i - 10 if 10 <= i < 13 else 0))
        for label, off, width, _k in F.UNIT_FIELDS:
            v = over.get(("unit", i), {}).get(label, (i + 2) * 9 % (1 << (8 * width)))
            units[at + off:at + off + width] = int(v).to_bytes(width, "little")
    # affinities placed absolutely, because the block straddles into the next
    # record (and past the table on the last one)
    for i in range(F.UNIT_COUNT):
        for label, off, _w, _k in F.UNIT_AFFINITY_FIELDS:
            v = over.get(("unit", i), {}).get(label, F.affinity_byte(100))
            units[i * F.ENEMY_STRIDE + off] = int(v)

    with open(path, "wb") as f:
        f.truncate(end)

        f.seek(0x8000)                                   # ISO9660 PVD
        pvd = bytearray(2048)
        pvd[0] = 1
        pvd[1:6] = b"CD001"
        pvd[40:40 + len(F.VOLUME_ID)] = F.VOLUME_ID.encode()
        pvd[40 + len(F.VOLUME_ID):72] = b" " * (32 - len(F.VOLUME_ID))
        f.write(bytes(pvd))

        f.seek(0x9000)                                   # SYSTEM.CNF payload
        f.write(b"BOOT2 = cdrom0:\\SLUS_208.92;1\r\nVER = 1.00\r\n")

        f.seek(F.UNIT_TABLES[1])
        f.write(bytes(units))
        f.seek(F.ENEMY_TABLE_OFF)
        f.write(bytes(stats))
        # the unit name pool AFTER the enemy stats, because it genuinely runs
        # into enemy record 0's undecoded head — exactly as on the real disc,
        # where those leading bytes ARE the E.S. name text
        f.seek(F.UNIT_NAME_BASE[1] + 0x564)
        f.write(unames)
        f.seek(F.REWARD_TABLE_OFF)
        f.write(bytes(rewards))
    return path
