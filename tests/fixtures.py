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
    """15 character records: 7 on-foot, 3 empty, 5 E.S. units."""
    on_foot = [
        (0x0564, "chaos",      41, 1200, 60, 210, 180, 240, 220, 55, 40, 45),
        (0x056A, "KOS-MOS",    43, 1450, 55, 260, 230, 200, 190, 60, 35, 42),
        (0x0570, "Shion",      40, 1050, 70, 150, 140, 280, 260, 50, 38, 40),
        (0x0576, "Jin",        39, 1300, 45, 250, 220, 160, 150, 58, 41, 44),
        (0x057C, "Ziggy",      38, 1380, 40, 240, 250, 140, 170, 52, 30, 36),
        (0x0582, "MOMO",       37,  900, 80, 120, 130, 290, 250, 48, 44, 43),
        (0x0588, "Jr.",        42, 1150, 60, 230, 190, 210, 200, 62, 46, 47),
    ]
    recs = []
    for (cid, _name, lvl, hp, ep, st, vit, ea, ed, dex, eva, agl) in on_foot:
        recs.append({"Character id": cid, "Level": lvl, "HP": hp, "Current HP": hp,
                     "EP": ep, "Str": st, "Vit": vit, "Eatk": ea, "Edef": ed,
                     "Dex": dex, "Eva": eva, "Agl": agl})
    recs += [{} for _ in range(3)]                    # unrecruited slots
    for i, (cid, hp) in enumerate([(0x05A0, 14200), (0x05A6, 16800), (0x05AC, 21000)]):
        recs.append({"Character id": cid, "Level": 40 + i, "HP": hp, "Current HP": hp,
                     "Str": 300, "Vit": 280, "Eatk": 220, "Edef": 210,
                     "Dex": 70, "Eva": 20, "Agl": 30,
                     "Gear 1": 3 + i, "Gear 2": 12, "Gear 3": 0, "Gear 4": 27})
    recs += [{} for _ in range(F.CHAR_COUNT - len(recs))]
    return recs[:F.CHAR_COUNT]


def gamedata(gold=1234567, roster=None, checksum=0xDEADBEEF):
    """A well-formed 20,832-byte Xenosaga II save payload."""
    gd = bytearray(F.GAMEDATA_SIZE)
    struct.pack_into("<I", gd, 8, checksum)             # the un-cracked +0x08 field
    gd[0x10] = 7                                       # misc counter
    struct.pack_into("<I", gd, F.GD_GOLD_OFF, gold)
    gd[0x174:0x17A] = b"\xff\xd8\xff\xe0\x00\x10"      # stand-in JPEG header
    gd[0xD40:0xD44] = b"\xff\xd9\x00\x00"
    for i, rec in enumerate(roster if roster is not None else default_roster()):
        base = F.CHAR_TABLE_OFF + i * F.CHAR_STRIDE
        for label, off, width, _kind in F.CHAR_FIELDS + F.ES_EQUIP_FIELDS:
            gd[base + off:base + off + width] = int(rec.get(label, 0)).to_bytes(
                width, "little")
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


# ---------------------------------------------------------------------------
# A minimal stand-in disc image carrying just the tables the editor writes.
# ---------------------------------------------------------------------------
def write_fake_disc(path, enemies=None):
    """Create a sparse image that passes `verify` and holds the enemy tables.

    `enemies`: {record index: {field label: value}}. Records not named get
    deterministic filler so tests can prove writes are surgical.
    """
    end = max(F.ENEMY_TABLE_OFF + F.ENEMY_COUNT * F.ENEMY_STRIDE,
              F.ENEMY_NAMES_OFF + 0x4000,
              F.REWARD_TABLE_OFF + F.ENEMY_COUNT * F.REWARD_STRIDE) + 0x800
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

        for i in range(F.ENEMY_COUNT):
            rec = bytearray(F.ENEMY_STRIDE)
            rec[0x04:0x0C] = bytes([0x64] * 8)           # element affinities
            # +0x3A is part of the 17-byte run the table locator searches for, so
            # a faithful stand-in has to carry it — including the eleven records
            # that hold something other than 99. Writing 99 across the board (as
            # this fixture used to) hides any comparison that wrongly treats the
            # raw run as a retail-value check.
            struct.pack_into("<H", rec, F.ENEMY_UNK3A_OFF, F.enemy_unk3a(i))
            over = (enemies or {}).get(i, {})
            for label, off, width, _k in F.ENEMY_FIELDS:
                v = over.get(label, (i + 1) * 7 % (1 << (8 * width)))
                rec[off:off + width] = int(v).to_bytes(width, "little")
            struct.pack_into("<H", rec, 0x52, 500 + i)
            # verified break/zone fields: one-hot sequence slots + a zone mask
            # covering exactly the zones the sequence uses
            slots = F.encode_break_seq(("BB", "CB", "CC", "CBB", "", "AA")[i % 6])
            for n, v in enumerate(slots):
                rec[F.BREAK_SEQ_OFF + n] = v
            rec[F.ENEMY_ZONE_MASK_OFF] = slots[0] | slots[1] | slots[2] | slots[3]
            f.seek(F.ENEMY_TABLE_OFF + i * F.ENEMY_STRIDE)
            f.write(bytes(rec))

            row = bytearray(F.REWARD_STRIDE)
            for label, off, width, _k in F.REWARD_FIELDS:
                v = over.get(label, (i + 3) * 11 % (1 << (8 * width)))
                row[off:off + width] = int(v).to_bytes(width, "little")
            f.seek(F.REWARD_TABLE_OFF + i * F.REWARD_STRIDE)
            f.write(bytes(row))
    return path
