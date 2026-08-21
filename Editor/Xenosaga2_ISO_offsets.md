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

### Save TODO
- [ ] Extract the inner `gamedata` payload from each container (`extract_gamedata`).
  - `.psv`: `\x00VSP` header + SHA1 signature block, then the PS2 dir/file entries.
  - `.max`: `Ps2PowerSave` header, folder name, then per-file blobs.
  - `.sps`: `SharkPortSave` header, title/desc strings, then payload.
  - `.cbs`: `CFU\0` header (decompressed len at +4), RC4-then-zlib like S3's `.cbs`.
- [ ] Confirm the raw `gamedata` size (compare the 20 PSV slots — identical 29,468 bytes,
  so the payload is fixed-size; subtract the PSV wrapper to get it).
- [ ] Map fields: characters (level/HP/EXP/stats/techs/gear), party, inventory, gold, playtime.
- [ ] Crack the save checksum before enabling any write path (S3 used "all u32 sum to 0";
  check whether X2 uses the same trick).

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
