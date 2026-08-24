#!/usr/bin/env python3
"""
PS2 memory-card containers: raw card images (`.ps2` / `.mcd`) and EMS single-save
exports (`.psu`). Game-agnostic — x2save.py layers the Xenosaga II payload lookup
on top, the same way it does for the psv / sharkport / cbs wrappers.

This is the format PCSX2 uses, so it is the one most players actually have: a
memory-card image is a PS2MFS filesystem (superblock, indirect FAT, 512-byte
directory entries) and the save is a file inside a `BASLUS-…` directory.

Layout summary (all little-endian):

  superblock @ page 0
    0x00 char  magic[28]        "Sony PS2 Memory Card Format "
    0x1C char  version[12]
    0x28 u16   pagesize                 (512)
    0x2A u16   pages_per_cluster        (2  -> 1024-byte clusters)
    0x2C u16   pages_per_block          (16)
    0x30 u32   clusters_per_card
    0x34 u32   alloc_offset             first cluster of the allocatable area
    0x38 u32   alloc_end
    0x3C u32   rootdir_cluster          (relative to alloc_offset)
    0x50 u32   ifc_list[32]             clusters holding the indirect FAT
    0x150 u8   card_type / card_flags

  FAT is two levels: ifc_list -> indirect-FAT cluster -> FAT cluster. Each cluster
  holds cluster_size/4 u32 entries; an entry's top bit means "allocated" and the
  low 31 bits are the next cluster (0x7FFFFFFF ends the chain).

  Images come in two physical flavours: "raw" (512-byte pages back to back) and
  "with ECC" (each page followed by 16 spare bytes, the first 12 holding a
  per-128-byte Hamming code). PCSX2 writes the ECC flavour by default.
"""
import struct
from collections import namedtuple

PS2MC_MAGIC = b"Sony PS2 Memory Card Format "

# Directory-entry mode bits.
DF_READ      = 0x0001
DF_WRITE     = 0x0002
DF_EXECUTE   = 0x0004
DF_PROTECTED = 0x0008
DF_FILE      = 0x0010
DF_DIRECTORY = 0x0020
DF_EXISTS    = 0x8000

DIRENT_SIZE = 512
_DIRENT = struct.Struct("<HHI8sI4x8sI28s32s416x")   # == 512 bytes
assert _DIRENT.size == DIRENT_SIZE

DirEnt = namedtuple("DirEnt", "mode length cluster name created modified attr")

def parse_dirent(raw):
    if len(raw) < DIRENT_SIZE:
        raise ValueError("short directory entry")
    mode, _unused, length, created, cluster, modified, attr, _pad, name = \
        _DIRENT.unpack(raw[:DIRENT_SIZE])
    return DirEnt(mode=mode, length=length, cluster=cluster,
                  name=name.split(b"\x00")[0].decode("latin1"),
                  created=created, modified=modified, attr=attr)

def is_dir(ent):  return bool(ent.mode & DF_DIRECTORY)
def is_file(ent): return bool(ent.mode & DF_FILE)
def exists(ent):  return bool(ent.mode & DF_EXISTS)


# ---------------------------------------------------------------------------
# ECC. Each 128-byte chunk yields 3 bytes: one of column parities (mask 0x77)
# and two of line parities (mask 0x7F each), so a 512-byte page needs 12 bytes.
# The column contribution of a byte is a fixed XOR-linear function of its bits,
# which is why it can be tabulated. Bit b contributes the pair-of-parities
# (odd/even, mod-4 half, mod-8 half) it belongs to.
# ---------------------------------------------------------------------------
def _col_mask(bit):
    return ((0x01 if bit % 2 == 0 else 0x10)
            | (0x02 if (bit // 2) % 2 == 0 else 0x20)
            | (0x04 if (bit // 4) % 2 == 0 else 0x40))

def _build_ecc_table():
    tbl = []
    for byte in range(256):
        acc, parity = 0, 0
        for bit in range(8):
            if byte >> bit & 1:
                acc ^= _col_mask(bit)
                parity ^= 1
        tbl.append(acc | (parity << 7))    # bit 7 carries the byte's parity
    return tbl

_ECC_TBL = _build_ecc_table()

def ecc_chunk(buf):
    """Three ECC bytes for one 128-byte chunk."""
    a = b = c = 0
    for i, v in enumerate(buf):
        t = _ECC_TBL[v]
        a ^= t
        if t & 0x80:                       # odd parity -> fold the line index in
            b ^= ~i & 0xFF
            c ^= i
    return bytes((~a & 0x77, ~b & 0x7F, ~c & 0x7F))

def ecc_page(page):
    """The 12 ECC bytes for a 512-byte page."""
    return b"".join(ecc_chunk(page[i:i + 128]) for i in range(0, len(page), 128))


class Ps2Card:
    """Read/patch access to a PS2 memory-card image held in memory.

    Writes are length-preserving in-place file patches only — this deliberately
    does not allocate, free, or create anything, so it can never reshape the
    filesystem out from under the console.
    """

    def __init__(self, data):
        self.data = bytearray(data)
        if not self.data.startswith(PS2MC_MAGIC):
            raise ValueError("not a PS2 memory-card image (bad superblock magic)")
        sb = bytes(self.data[:0x160])      # fits inside page 0 either flavour
        self.version = sb[0x1C:0x28].split(b"\x00")[0].decode("latin1")
        self.pagesize, self.pages_per_cluster, self.pages_per_block = \
            struct.unpack_from("<HHH", sb, 0x28)
        (self.clusters_per_card, self.alloc_offset, self.alloc_end,
         self.rootdir_cluster) = struct.unpack_from("<IIII", sb, 0x30)
        self.ifc_list = list(struct.unpack_from("<32I", sb, 0x50))
        self.card_type, self.card_flags = sb[0x150], sb[0x151]
        if not (self.pagesize and self.pages_per_cluster and self.clusters_per_card):
            raise ValueError("implausible superblock geometry")
        self.cluster_size = self.pagesize * self.pages_per_cluster
        self.entries_per_cluster = self.cluster_size // 4

        total_pages = self.clusters_per_card * self.pages_per_cluster
        if len(self.data) >= total_pages * (self.pagesize + 16):
            self.spare = 16                # ECC flavour (PCSX2 default)
        elif len(self.data) >= total_pages * self.pagesize:
            self.spare = 0                 # raw flavour
        else:
            raise ValueError(
                f"card image is {len(self.data):,} bytes, too small for "
                f"{self.clusters_per_card:,} clusters")
        self._fat_cache = {}

    # --- physical access ---------------------------------------------------
    def _page_off(self, page):
        return page * (self.pagesize + self.spare)

    def read_page(self, page):
        o = self._page_off(page)
        return bytes(self.data[o:o + self.pagesize])

    def read_cluster(self, cluster):
        """Data of a *physical* cluster (FAT clusters are addressed this way)."""
        p = cluster * self.pages_per_cluster
        return b"".join(self.read_page(p + k) for k in range(self.pages_per_cluster))

    def data_cluster(self, n):
        """Physical cluster holding allocatable (file/dir) cluster n."""
        return self.alloc_offset + n

    # --- FAT ---------------------------------------------------------------
    def fat(self, n):
        """Raw FAT entry for allocatable cluster n."""
        epc = self.entries_per_cluster
        fat_index, slot = divmod(n, epc)
        ifc_index, ifc_slot = divmod(fat_index, epc)
        if ifc_index >= len(self.ifc_list):
            raise ValueError(f"cluster {n} beyond the indirect FAT")
        if ifc_index not in self._fat_cache:
            self._fat_cache[ifc_index] = self.read_cluster(self.ifc_list[ifc_index])
        fat_cluster = struct.unpack_from("<I", self._fat_cache[ifc_index], ifc_slot * 4)[0]
        key = ("fat", fat_cluster)
        if key not in self._fat_cache:
            self._fat_cache[key] = self.read_cluster(fat_cluster)
        return struct.unpack_from("<I", self._fat_cache[key], slot * 4)[0]

    def chain(self, start, limit=None):
        """Cluster chain from `start`, at most `limit` clusters."""
        out, seen, n = [], set(), start
        while n not in (0xFFFFFFFF, 0x7FFFFFFF):
            if n in seen:
                raise ValueError("cyclic cluster chain")
            if n >= self.clusters_per_card:
                raise ValueError(f"cluster {n} out of range")
            seen.add(n)
            out.append(n)
            if limit is not None and len(out) >= limit:
                break
            entry = self.fat(n)
            if not entry & 0x80000000:
                raise ValueError(f"chain runs into unallocated cluster {n}")
            n = entry & 0x7FFFFFFF
        return out

    # --- directories -------------------------------------------------------
    def read_dir(self, first_cluster, count):
        per = self.cluster_size // DIRENT_SIZE
        need = (count + per - 1) // per
        raw = b"".join(self.read_cluster(self.data_cluster(c))
                       for c in self.chain(first_cluster, need))
        return [parse_dirent(raw[i * DIRENT_SIZE:(i + 1) * DIRENT_SIZE])
                for i in range(count)]

    def root(self):
        """Directory entries of the card root (excluding '.' and '..')."""
        head = self.read_dir(self.rootdir_cluster, 1)[0]
        ents = self.read_dir(self.rootdir_cluster, head.length)
        return [e for e in ents if e.name not in (".", "..") and exists(e)]

    def listdir(self, dir_ent):
        head = self.read_dir(dir_ent.cluster, 1)[0]
        ents = self.read_dir(dir_ent.cluster, head.length)
        return [e for e in ents if e.name not in (".", "..") and exists(e)]

    def walk(self):
        """Yield (dir_ent, [file_ents]) for every save folder on the card."""
        for d in self.root():
            if not is_dir(d):
                continue
            try:
                yield d, [e for e in self.listdir(d) if is_file(e)]
            except ValueError:
                continue

    # --- files -------------------------------------------------------------
    def file_spans(self, ent):
        """[(image byte offset, byte count, page index)] covering the file body."""
        need = (ent.length + self.cluster_size - 1) // self.cluster_size
        spans, left = [], ent.length
        for c in self.chain(ent.cluster, need):
            p0 = self.data_cluster(c) * self.pages_per_cluster
            for k in range(self.pages_per_cluster):
                if left <= 0:
                    break
                n = min(self.pagesize, left)
                spans.append((self._page_off(p0 + k), n, p0 + k))
                left -= n
        if left:
            raise ValueError(f"{ent.name}: cluster chain is {left} bytes short")
        return spans

    def read_file(self, ent):
        return b"".join(bytes(self.data[o:o + n]) for o, n, _p in self.file_spans(ent))

    # --- ECC handling ------------------------------------------------------
    def ecc_mode(self, pages=None):
        """How to treat the spare area of this image:

        "none"      no spare area at all (raw image) — nothing to maintain.
        "verified"  the stored ECC matches what we compute, so recomputing it on
                    write is safe.
        "absent"    the spare area is blank (all 00 or all FF) — the image does
                    not carry ECC, so leave it blank rather than inventing it.
        "mismatch"  the image carries ECC we cannot reproduce. Refuse to write:
                    a stale code can make the console read the save as damaged.
        """
        if not self.spare:
            return "none"
        if pages is None:
            pages = range(0, min(64, self.clusters_per_card * self.pages_per_cluster))
        blank = True
        for p in pages:
            o = self._page_off(p) + self.pagesize
            stored = bytes(self.data[o:o + 12])
            if stored not in (b"\x00" * 12, b"\xff" * 12):
                blank = False
                if stored != ecc_page(self.read_page(p)):
                    return "mismatch"
        return "absent" if blank else "verified"

    def _refresh_ecc(self, page, mode):
        if mode != "verified":
            return
        o = self._page_off(page) + self.pagesize
        self.data[o:o + 12] = ecc_page(self.read_page(page))

    def write_file(self, ent, new):
        """Overwrite a file's body in place. The length must not change."""
        if len(new) != ent.length:
            raise ValueError(
                f"{ent.name}: refusing to change length ({ent.length} -> {len(new)})")
        spans = self.file_spans(ent)
        mode = self.ecc_mode([p for _o, _n, p in spans])
        if mode == "mismatch":
            raise ValueError(
                "this card image carries error-correcting codes we cannot reproduce, "
                "so writing it could make the console see a damaged save. Please "
                "report the image's size and origin.")
        pos = 0
        for o, n, page in spans:
            self.data[o:o + n] = new[pos:pos + n]
            pos += n
            self._refresh_ecc(page, mode)
        return bytes(self.data)


# ---------------------------------------------------------------------------
# EMS `.psu` — a single save folder serialized flat: the folder's own directory
# entry, then one entry per member; file entries are followed by their body
# padded up to a 1024-byte boundary.
# ---------------------------------------------------------------------------
PSU_ALIGN = 1024

def psu_root(data):
    ent = parse_dirent(data[:DIRENT_SIZE])
    if not is_dir(ent) or not exists(ent) or not 2 <= ent.length <= 1024:
        raise ValueError("not a .psu export (no leading directory entry)")
    return ent

def psu_files(data):
    """[(name, body offset, length)] for the files inside a .psu."""
    root = psu_root(data)
    pos, out = DIRENT_SIZE, []
    for _ in range(root.length):
        if pos + DIRENT_SIZE > len(data):
            break
        ent = parse_dirent(data[pos:pos + DIRENT_SIZE])
        pos += DIRENT_SIZE
        if is_dir(ent):                       # "." and ".." carry no body
            continue
        out.append((ent.name, pos, ent.length))
        pos += (ent.length + PSU_ALIGN - 1) // PSU_ALIGN * PSU_ALIGN
    return out

def looks_like_psu(head):
    """Cheap structural sniff for a .psu, for files without a magic number."""
    try:
        psu_root(head)
        return True
    except ValueError:
        return False
