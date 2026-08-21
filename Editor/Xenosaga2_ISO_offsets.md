# Xenosaga Episode II (USA) — reverse-engineering notes

Living document. Everything below marked **VERIFIED** was extracted directly from the
retail discs / save samples. Everything marked **TODO** is not known yet — do not write
those regions blind.

## Discs (VERIFIED 2026-08-21)

| Disc | Serial | BOOT2 | Volume ID | Size (bytes) |
|---|---|---|---|---|
| 1 | `SLUS-20892` | `cdrom0:\SLUS_208.92;1` (VER 1.00) | `XENOSAGA_II` | 4,682,121,216 |
| 2 | `SLUS-21133` | `cdrom0:\SLUS_211.33;1` | `XENOSAGA_II` | 4,693,164,032 |

- Serial read from SYSTEM.CNF (`BOOT2` line), near the start of the image.
- Volume ID read from the ISO9660 Primary Volume Descriptor: LBA 16 (`0x8000`), +40, 32 bytes.
- `x2patch.py verify <iso>` confirms both. Two-disc game → the editor must track which disc a
  given table lives on.

## Save containers (VERIFIED magics)

Local samples in `../Saves/` (gitignored). `x2save.py ../Saves` inventories them.

| Format | Ext | Magic | Sample sizes |
|---|---|---|---|
| PS2 memory card | `.ps2` `.mcd` | `Sony PS2 Memory Card Format` | (none local yet) |
| EMS export | `.psu` | dir entries | (none local yet) |
| PS3 export | `.psv` / `.PSV` | `\x00VSP` | 29,468 (×20 slots) |
| AR Max / MAX Drive | `.max` | `Ps2PowerSave` | ~11 KB single; 62–75 KB multi |
| SharkPort / X-Port | `.sps` / `.xps` | u32 len + `SharkPortSave` | ~30 KB |
| CodeBreaker | `.cbs` | `CFU\x00` | ~11 KB (RC4 + zlib) |

Save ids embedded in the containers:
- `BASLUS-20892` — USA (disc-1 serial, used as the game's save id). Folder name looks like
  `BASLUS-20892Xeno201...`.
- `BESCES-82034` — PAL (one local `.max` sample, `10890.max`, is a European save — skip for USA work).

### PSV container (VERIFIED — implemented in x2save.py)
Standard PS3-export layout: 0x84-byte signed header, then a McFsEntry-style table
(each file's `mode` u32 at name−4 = `0x8427` dir / `0x8497` file; `size` u32 at name−8),
then the file bodies concatenated in entry order. Body starts at
`filesize − sum(file sizes)`. A slot holds three files: `system.ico`, the save data
file (named after its folder, e.g. `BASLUS-20892Xeno201`), and `icon.sys` (964 B).
The icon.sys title carries the save name + playtime, e.g. `XenosagaEPII-01[30:18]`.

### gamedata payload (VERIFIED — 20,832 bytes, from 20 PSV slots)
```
0x0000  header: +0x08 u32 checksum-ish ("muY+"); +0x10 u8 counter
0x00D0  u32  GOLD                     (rises/falls with earning+spending)
0x0174..0x0D44  embedded JPEG thumbnail (per-save screenshot)
0x0D44..0x1174  ~1 KB high-entropy block (2nd image / packed state)  [TODO]
0x1174  character table: 15 records x 0x108 bytes
```
Character record (0x108 bytes) — offsets within the record:
```
+0x00 u16  character id   (constant per char; chaos=0x0564, KOS-MOS=0x056A, ...)
+0x02 u16  HP (max)        (verified: grows level 7->54 across the 20 saves)
+0x06 u16  stat 1  \
+0x08 u16  stat 2   |  five stats; grow in lockstep with level (names TBD)
+0x0A u16  stat 3   |
+0x0C u16  stat 4   |
+0x0E u16  stat 5  /
+0x13 u8   LEVEL            (verified: 7 at 1.5h -> 54 at 30h)
+0x23..    tech-level arrays (0x14 x8, then 0x64 x8)  [partial]
```
Record index -> character (inferred, but cross-validated against the pnach EE-RAM
order which matches 1:1): 0 chaos, 1 KOS-MOS, 2 Shion, 3 Jin, 4 Ziggy, 5 MOMO,
6 Jr., 7-9 reserved (unrecruited), 10 E.S. Dinah, 11 E.S. Zebulun, 12 E.S. Asher,
13-14 more E.S. slots. E.S. records carry mech-scale HP (10k-24k), confirming them.

`x2save.py <file.psv>` decodes gold + the party. Validated on all 20 slots.

### Save WRITE (implemented in x2save.py — `set` command)
`apply_edits()` edits gold + character fields in the 20,832-byte payload;
`write_save()` splices it back into the PSV (length preserved), keeps a `.bak`, and
**round-trip verifies** the write. Confirmed surgical (only the 3 targeted byte-runs
change) on a copied save.

### Checksum status (BLOCKER for guaranteed-valid writes)
gamedata `+0x08` is a 4-byte value that changes per save. It resisted **every** standard
algorithm tried across all 20 slots: CRC-32 (all 16 init/reflect/xorout variants, LE/BE),
byte/u16/u32 sums, negate-to-zero, sum-to-constant, Adler-32, Fletcher-32, and truncated
MD5/SHA-1/SHA-256 — over ranges `[0C:L]`, `[10:L]`, `[1174:L]`, image-excluded
concatenations, etc. So it's a **custom** routine (or possibly a value the game doesn't
verify). `fix_checksum()` is the one hook to implement once cracked; today it's a
pass-through and the `+0x08` field is preserved as-is.
- [ ] Determine empirically whether the game validates `+0x08` (load an edited save in
  PCSX2). If it loads → likely unchecked, writes are good as-is. If rejected → crack it.
- [ ] PSV wrapper has a PS3 HMAC-SHA1 signature (header 0x08); editing gamedata
  invalidates it. Fine for emulator/mymc workflows; real-PS3 re-import needs re-signing.

### Save TODO
- [ ] `.max` / `.sps` / `.cbs` gamedata extraction + write (they wrap the same 20,832-byte
  payload; SharkPort layout is `magic → 0 → title → desc → dirname → datalen →
  McFsEntry files → u32 checksum`).
- [ ] Pin the five stat names/order; decode EXP, current-vs-max HP, equipment, techs.
- [ ] Decode the 0x0D44..0x1174 block and the header checksum at +0x08.
- [ ] Crack/confirm the save checksum before enabling any write path.
- [ ] Party / inventory / event-flag tables (item-slot tables seen at ~0x3030+).

## Cheat codes as anchors (VERIFIED present)

`../Cheats/Xenosaga - Episode II ... [Disc1of2] (NTSC-U).pnach` — 4,011 lines of PCSX2
`patch=1,EE,...` codes (GameHacking.org). These are **EE RAM** addresses, not save/ISO
offsets, but the in-RAM struct layout usually mirrors the save layout and gives us named
values to search for. Useful anchors already visible:

- Enemy 1–4 blocks: `0x1FAC..`, stride ≈ `0x108` between enemies (`1FAC1F3C`→`1FAC2044`→…).
- Key items table: base near `0x61CC00`, entries around `0x61CCD4`.
- Skill/class "max stars" (0x0F fills): region around `0x6A59xx`.
- GS Campaign quest flags: `0x3ECA40`.

### ISO TODO
- [ ] Find the boot ELF inside each disc (SYSTEM.CNF → `SLUS_xxx.xx`), note its PT_LOAD
  vaddr↔file mapping (S3 approach: `file_off = (vaddr - PL_VADDR) + PL_FILE`).
- [ ] Translate the pnach EE addresses through the ELF load map to locate the static tables
  on-disc (characters, techs/ether, gear, enemies, shops, text).
- [ ] Build `x2fields.py` schemas as each table is verified byte-for-byte.

## Local resources (all gitignored)
- `../ISO/` — both retail discs.
- `../Saves/` — 44 save samples (20 PSV, several .max/.sps/.cbs).
- `../Cheats/` — the disc-1 pnach.
- Reference: Xenosaga 1 has a third-party editor (`../../Xenosaga 1/OG Editor/`, by Tony H)
  — different game, but a structural reference for what's editable.
