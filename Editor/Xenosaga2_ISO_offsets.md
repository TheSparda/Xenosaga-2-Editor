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
| AR Max / MAX Drive | `.max` | `Ps2PowerSave` | ~11 KB single; 62–75 KB multi (LZARI) |
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
+0x23..0x32  constant per-character config (0x14 x8, 0x64 x8 — affinities/base tech)
+0x33..    growing list of learned tech/skill ids (0x1D,0x1E,0x1F,... as you level)
+0x5C u16  CURRENT HP       (== base HP early; exceeds it later via gear bonus)
+0x60 u16  unknown, level-correlated
```
The 0x108 record is now essentially fully mapped. **Total EXP is NOT in the record** —
EXP-to-next lives in a separate RAM table (pnach 0x61C4xx), not yet located in the save.

### Containers supported (x2save.py + x2mc.py)
All of these decode to the same 20,832-byte gamedata, and all are writable:

| Container | Read | Write | Notes |
|---|---|---|---|
| memcard `.ps2`/`.mcd` | yes | yes | PS2MFS filesystem — see below. What PCSX2 uses. |
| `.psu` | yes | yes | EMS export: leading dirent, bodies padded to 1024 |
| `.psv` | yes | yes | PS3 export; the wrapper's HMAC is not re-signed |
| sharkport `.sps`/`.xps` | yes | yes | uncompressed |
| `.cbs` | yes | yes | RC4 + zlib; re-compressed on write |
| `.max` | yes | yes | Ps2PowerSave: 0x58 header + LZARI (`x2lzari.py`) |

**AR Max `.max` (implemented in `x2lzari.py` + `x2save.py`).** Header is `0x58`
bytes — magic, an unidentified u32 at `+0x0C`, dir name, display name,
compressed size (counted from `0x58`), file count — then a u32 decompressed
length and an LZARI bitstream. LZARI is Okumura's LZSS + adaptive arithmetic
coding; the position model is his fixed empirical curve (`10000 / (i + 200)`),
which is part of the format and cannot be changed.

Decoding was validated against all **eight** local samples (including two 6-file
Japanese saves): every one yields a 20,832-byte gamedata that decodes to sensible
gold, levels and HP. Our compressor round-trips bit-exactly and lands within
**0.2%** of the size AR Max itself produced (10,945 vs 10,925 bytes on one
sample), which is good evidence the implementation matches the reference.

Two things deliberately not done:

- **Entry padding is not modelled.** The decompressed blob is a flat run of
  `u32 size, char name[32], data, padding`, but the padding is not a constant
  alignment — 2 bytes after one entry and 12 after the next, in the same file.
  Rather than guess a rule, the reader locates entries by scanning for the next
  plausible header, and writes **splice the gamedata in place** at the offset it
  was found, so whatever padding the original had is preserved byte for byte.
- **The `+0x0C` checksum is unidentified** and is preserved rather than
  recomputed. It matches no CRC-32 variant tried (standard, BZIP2, MPEG-2, POSIX,
  JAMCRC, CRC-32C) nor any byte/word sum, over the compressed data, the
  decompressed data or the header, across all eight samples. mymc — the reference
  PS2 save tool — writes literal `0` there and its output works, so the field is
  evidently not enforced.

**Memory cards (implemented in `x2mc.py`, per the published PS2MFS layout).** The
superblock is page 0: pagesize/pages-per-cluster at +0x28, `clusters_per_card`,
`alloc_offset`, `alloc_end`, `rootdir_cluster` at +0x30, `ifc_list[32]` at +0x50.
The FAT is two levels (ifc_list -> indirect-FAT cluster -> FAT cluster, 256 u32
entries each); an entry's top bit means allocated, the low 31 bits are the next
cluster with `0x7FFFFFFF` ending the chain (a *free* entry is `0x7FFFFFFF` with the
top bit clear). Directory entries are 512 bytes (mode u16 @0, length @4, first
cluster @0x10, 32-byte name @0x40) — the same record `.psu` is built from, and the
same `0x8427` dir / `0x8497` file modes the PSV table uses.

The game stores **one folder per in-game save slot** (`BASLUS-20892Xeno201`,
`…02`, …), so a card holds several saves. `x2save.list_slots()` enumerates them and
every read/write takes a `slot=` index.

Images come in two physical flavours: raw (512-byte pages back to back) and
with-ECC (each page followed by 16 spare bytes, the first 12 holding a Hamming
code over each 128-byte chunk). PCSX2 writes the ECC flavour. We recompute the
code for every page we touch, but only after checking our implementation
reproduces the codes already on that image; if it does not, the write is refused
rather than risking a save the console reads as damaged (`Ps2Card.ecc_mode()`).
Writes are length-preserving in-place patches only — nothing allocates, frees, or
creates, so the filesystem can never be reshaped under the console.

### Inventory (item catalog mapped; save offset needs ground truth)
From the disc-1 pnach: **36 consumables** at EE RAM `0x61C800` (u16 quantity per id,
cap 99, id = (addr-0x61C800)/2) and **107 key items** at EE RAM `0x61CC00` (u16
have-flag per id). Full id→name maps saved as `Editor/x2_consumables.json` /
`x2_keyitems.json` and exposed via `x2fields.consumable_names()` / `keyitem_names()`.

The SAVE-side inventory offset is **not confirmed**: the save re-serializes the work
RAM (not a linear copy), the 36 local samples hold almost no consumables (so there's
little signal), and there's no known-inventory reference to verify a candidate. Two
0/1 flag tables that accumulate with story progress were located at gamedata `0x28BA`
(len ~255) and `0x2AE4` (len ~452) — these are key-items and/or event flags, but which
is which can't be confirmed without ground truth. Cheapest unblock: one PCSX2 save
with a known set of items → diff pinpoints both the consumable array and key-item table.

### Active party — needs ground truth (open)
The active-party list could not be reliably identified from the 36 save samples: index
scans over the header + post-char state (excluding the JPEG at 0x174-0x1174 and the char
table) turned up only high-variability regions, and the SharkPort scene descriptions
("Jin vs. Margulis", "Albedo", ...) name story points, not rosters. Unlike level/HP/gold
(verifiable by correlation), a party field can't be *confirmed* without a known-party
reference save or the running game. Cheapest unblock: 2-3 PCSX2 saves with deliberately
different parties → diff pinpoints it in minutes.
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

### Online research findings (2026-08 sweep)
- **No public Xenosaga II save editor exists** (checked save-editor.com, ps2savetools,
  GameHacking.org, romhacking.net, GBAtemp). We're first — so the checksum is undocumented
  and won't be found online; it has to come from the game's own code or an emulator test.
- **PS2 games generally do validate a save checksum on load** and mark the save corrupt if
  it fails — so our `+0x08` field probably matters. (save-editor.com, ps2savetools.)
- **Stat names + caps** came from the almarsguides CodeBreaker lists cross-checked with our
  pnach: the save character record is `[char id u16]` + the in-RAM stat struct, so its
  offsets map 1:1 onto the pnach stat addresses (Shion stat base EE `0x61B592`). This is how
  CHAR_FIELDS got named (HP/EP/Str/Vit/Eatk/Edef/Dex/Eva/Agl) — validated by Shion decoding
  as a low-Str/high-Ether build.
- **pleonex/Xenosaga** (GitHub) is Xenosaga *I* only (ISO file extraction), no save/checksum.
- **Method to crack the checksum** (from GameHacking.org "save hashing routines" + PS2DIS):
  run the game in PCSX2, find the save block in EE RAM, set a **write breakpoint on the
  checksum word**, trigger a save, and read the routine that computes it; or disassemble the
  boot ELF (we have it on the ISO) around the mc write. This is the definitive next step.

### Boot ELF map (SLUS_208.92, extracted from disc 1)
`x2patch.py extract <iso> --name SLUS_208.92` pulls the 5,877,344-byte boot ELF
(MIPS32 LE, R5900). Program headers:

| Seg | file off | vaddr | filesz | flags | notes |
|---|---|---|---|---|---|
| 0 | 0x1000 | 0x120000 | 0x195D38 | R-X | **code** (entry 0x120008) |
| 1 | 0x196D80 | 0x2B5D80 | 0x3E83FC | RW- | **data** (+BSS to memsz 0x71FFF0) |
| 2 | 0x580000 | 0xA80000 | 0x3E28 | RWX | small |

va→file for code: `off = va - 0x120000 + 0x1000`; for data: `off = va - 0x2B5D80 + 0x196D80`.
The save RAM (char/stat tables 0x61xxxx) and the **gamedata work buffer @ VA 0x695160**
all live in the initialized-data segment, so they exist at those fixed EE addresses at
runtime. Two overlays (OV01/OV02.OVL) also present but the save code is in the main ELF.

### Save subsystem (located via disassembly — capstone MIPS)
- Save-file registration passes `(ptr, size)` per file: `icon.sys`=0x3C4, **gamedata=0x5160**
  (20,832), system.ico, etc. (around va 0x1EB190).
- Save path string `/BASLUS-20892Xeno2` @ va 0x68DF88, referenced at va 0x19DE8C.
- Memcard file-I/O dispatcher (jump table by file index 1..9) @ va 0x19DE98.
- Gamedata buffer pointer getter @ va 0x187D58 → returns **0x695160**.
- **Save serializer region ≈ va 0x186000–0x188900** (50+ field writers over a common
  primitive 0x1867A0) — the checksum is computed here, then stored to `buffer+8` (0x695168)
  via a register offset (no absolute xref, so it's not directly greppable).
- Game RNG: 64-bit PCG/Knuth LCG (mult `0x5851F42D4C957F2D`) @ va 0x2AA130 — this is `rand`,
  NOT the save checksum (the LCG-hash family was tested against +0x08 and did not match).

### Checksum status (BLOCKER for guaranteed-valid writes)
**Not cracked. Two of the three leads recorded here were WRONG and are corrected
below** (2026-08-24, static pass over the boot ELF) — following them would have
burned a PCSX2 session pointing at the wrong code.

* ~~EE write breakpoint on **0x695168**, "the gamedata buffer's checksum
  word".~~ **`0x695160` is a string literal** — `cvFsMakeDir #1:illegal
  directory name`. Its neighbours are `cvFsGetMaxByteRate #2:vtbl error`
  (`0x695138`) and `cvFsMakeDir #3:device not found` (`0x6951B0`). The only two
  static references to it pass it in `$a0` to an error-printing call.
* ~~Disassemble the **0x186000–0x188900 serializer**.~~ That range is the
  **`cvFs` memory-card filesystem library**, not the save serializer — the two
  references above live inside it, at `0x187D70` and `0x187DA0`.
* **There are ZERO static references to the checksum word**, which is the real
  finding: the gamedata buffer is allocated at runtime, so it has no fixed
  address to break on or to search for. A breakpoint session has to find the
  buffer first (break on the memory-card write, walk back to its source), not
  assume an address from these notes.

**Plain sums are now ruled out over EVERY range, not just three.** The earlier
pass tried `[0C:L]`, `[10:L]`, `[1174:L]`. Building prefix sums and *solving*
for `(start, end)` covers all ranges at once, for u8, u16 and u32 words: across
24 saves the only fits are degenerate ones that contain the checksum word itself
(`[0x8,0xC)` is literally "the word equals itself"). Still standing from before:
CRC-32 (16 variants, LE/BE), Adler/Fletcher, truncated MD5/SHA, the LCG family.

**One unexplained clue.** The 24 observed values are all distinct but *not*
uniformly distributed: 21 of 24 sit below `0x2F000000`, with three outliers at
`0xBBAEBFE0`, `0xDF2D0D7B` and `0xFB7B198E`. A good 32-bit hash would be
uniform. Something about the low ~2/3 of the range is meaningful and is not yet
accounted for — that is the thread to pull next.

The first question in the list below is still the one that matters most: whether
the game validates `+0x08` at all. If it does not, `fix_checksum()` staying a
pass-through is already correct and none of this blocks anything.
Until then, `x2save.fix_checksum()` preserves +0x08 as-is.
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
- [x] Container coverage: memcard / psu / psv / sharkport / cbs read+write (only
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

### ISO data/text region (VERIFIED — disc 1, uncompressed)
The game's menu text tables sit uncompressed in **XENOSAGA.01** and are directly
readable at raw disc offsets. `x2patch.py strings <iso> --off 0x200CE1C` dumps them.
Layout (disc-1 raw offsets):

| Offset | Table |
|---|---|
| ~0x2009B58 | **Ether skills** (name + `target (EP n)\ndesc`): Medica, Refresh, Veils, Swords, Blasts, Erde Kaiser, Double/XSB/XBK variants |
| ~0x200CE1C | **Consumable items** (name + desc) — same set as the pnach 36, in display order |
| ~0x200D464 | **Secret Keys 1-31** (name + "unlocks Class X skill Y") |
| ~0x200DF34 | **Key items** (name + desc): Decoders, letters, ZAZA clues, keys, seeds, rings, Robot Parts, ... (107) |
| ~0x200FC10 | **Character techs / specials** (Twin Buster, Phoenix Blade, Cross Fist, ...) + E.S. weapon attacks (MINIGUN, MICRO MISSILE, DRAGON BLADE, X-BUSTER @ ~0x20107F0) |
| ~0x2011611 | **Status/buff labels** (Beam Sword, Veils, Speed +25%, Safety Level, ...) |
| ~0x2011800 | **internal skill/class ids** (`ck_*`, `rk_*` — resource names, not display) |

Item/key catalogs now enriched with these descriptions in `x2_consumables.json` /
`x2_keyitems.json` (36/36 and 103/107 matched). **Still needed:** the equipment
data table (id→name+slot) that maps the E.S. gear ids (record +0x86/+0x88/+0x8A) to
weapon/frame/armor names — the names exist above but the id→name pointer table isn't
mapped yet. Likely a binary table with u32 pointers into this string pool.

### E.S. equipment (accessory table CONFIRMED, ids 0-30)
The E.S. accessory/circuit list is in the ISO at ~0x200C5D4 (31 named entries with
effects: Auxiliary Armor, EF Circuit, Anti-Fire/Ice/Thunder/Beam Armor, status Guards,
Quick Charge, EMAX300, Auto Recover). Saved as `x2_es_equip.json`. **id base
(Auxiliary Armor A = 0) is confirmed** by cross-checking all 36 saves: every E.S. gear
slot value in 0-30 maps to a sensible accessory. The save's 4 gear slots
(record +0x86/+0x88/+0x8A/+0x90) index a unified equipment list — accessories are
0-30, and a small cluster of higher ids (34-37 observed) are weapon/frame items.

No public source has equipment ids: the disc-1 pnach AND almarsguides CodeBreaker
pages only cover stats + consumables + key items (checked exhaustively). The ISO is
the sole source.

### E.S. weapon/frame ids 31+ — RETRACTED 2026-08-25: there is no such gear

This entry chased equipment that does not exist. Three things settle it:

- **The unified item table has no weapon or frame entries.** Ids 0..36 are the 31
  accessories interleaved with 予備 placeholders, 37..39 are placeholders, and 40+ are
  consumables (Med Kit S...). There is no id band left for weapons or frames.
- **The names at `0x20107F0` are ATTACK names, not item names.** That pool is
  `TECH_NAME_POOL`'s E.S. weapon-tech group — MINIGUN, DRAGON BLADE and friends are the
  E.S. attack/special *skills*, already mapped as verified 32-byte records in
  `SKILL_BLOCKS` and editable on the Skills tab. Reading them as equippable items is what
  created a phantom id band.
- **The game has no such equipment.** The skills FAQ is explicit that on-foot characters
  "don't equip any equipment at all (no weapons, armor, accessories, nothing!)", and the
  walkthrough's own E.S. loadout advice lists only accessories (Auto Recover, Charge
  Recover, Tuned Circuit, Power Shield, Anti-Fire Armor).

So the game's entire equipment system is two lists, and both are now solved and editable:
the **64 equip/passive skills** at `0x200B304` (on-foot) and the **31 E.S. accessories**
at `0x200B604`. The "accessories are 0-30" note that seeded this was using
`x2_es_equip.json`'s compacted numbering; in unified item ids the same 31 run 0..36.

Still genuinely open on the save side: which id space the save's four gear slots
(`+0x86/+0x88/+0x8A/+0x90`) use — the observed 34..37 are Quick Charge / EMAX300 /
Auto Recover / placeholder under unified ids, which is plausible, but unconfirmed.

### ISO ENEMY table (VERIFIED — disc 1)
**97 enemy stat records** at raw offset **0x2000000**, stride **0x5C** (92 B), directly
followed by the **enemy name table at 0x2002342** (sequential, record[i] ↔ name[i]).
Verified by HP alignment: Perun 860 → Stribog 2560 → ... → Margulis 32000 → Albedo 57600
→ Orgulla 999999 → Patriarch 192000. Names saved to `x2_enemies.json`;
`x2fields.enemy_names()` + `ENEMY_TABLE_OFF/STRIDE/COUNT/FIELDS`.

Record layout (0x5C), verified/inferred via range + HP-correlation across 97 enemies:
+0x00 4 param bytes; +0x04 8× element affinity (0x64=100%); **+0x36 u32 HP** (verified);
+0x3A 99 (const); **+0x3E u16 Atk**, **+0x42 u16 Def** (stats, 1-999, corr +0.77/+0.69);
**+0x4E u16 Cash**, **+0x50 u16 EXP** (rewards, corr +0.80). Editing writes to
ISO 0x2000000 + id*0x5C + field. ~30 more boss names (Albedo/Orgulla/Dark Erde Kaiser/ZU)
exist past the 97 — likely a second stat block, TODO.

### Character-growth & shop tables (BLOCKED — need runtime)
The gap between the enemy name table and the skills strings (ISO ~0x2002900-0x2009B58)
is **pure binary** — data tables with no embedded name anchors. Char growth/base stats
and shop stock/price tables are almost certainly here (or in the ELF data seg), but:
- ~~No name anchor (unlike the enemy table, whose adjacent name table let us align it).~~
  **Superseded 2026-08-24** — there is one, at `0x1FFF5B8`; see "Character + E.S. name
  table" below.
- No code anchor: the ELF has no shop/buy/sell/growth strings to disassemble from, and
  this data is **dynamically loaded from XENOSAGA.01** (not at a static ELF address), so
  there's no straightforward disassembly entry point.
Realistically these need the running game (PCSX2 RAM search + write-breakpoints) — the
same tool gap as the save checksum/party/inventory. Deferred until PCSX2 is available.

### ISO TODO (refreshed 2026-08-24)
Done since this list was written — kept short, the sections above have the detail:
- [x] Enemy stats / rewards / drops / affinities / resistances / break+zones / battle flags
- [x] Player unit table (characters + E.S.), stats and affinities
- [x] Skill numeric table — 176 records: ethers, doubles, duals, techs, E.S. attacks/specials
- [x] Schemas in `x2fields.py`, each gated on a disc cross-check and a ground-truth match

Open, in the order they are worth attempting:
- [x] **Skill/tech description + name text.** Done 2026-08-25. The text span is a staged
  buffer (`TX`), so names and descriptions are editable and a patch's text records stage
  like any other. Coverage of the HardType patch went 528 → 648 of 661 records. What is
  left was the 9 duplicate copies of a renamed skill's *battle caption*, and those are
  **done too** (2026-08-26) — located by content, rewritten in place, wired into
  `skill-rename` and `apply-ppf`. The CLI now reaches **661 of 661** HardType records.
  The web editor still stages 652: `gen_hardtype.py` filters by fixed extent and a
  caption has no fixed offset, so a browser front end would have to scan a 4.6 GB
  `File` to match. That is a deliberate front-end difference, recorded below.
- [x] **Passive / equip skill effects.** Done 2026-08-25 — 12-byte records at `0x200B304`,
  64 exposed (catalog 110..173). See the section above.
- [x] **Skill / class learning costs.** Done 2026-08-25 — they were in the flat data region
  after all, at `0x35E958`. The earlier "ruled out" write-up searched for the wrong thing
  with the wrong ground truth; see the solved section. The class *tier* half (which level a
  skill sits at) is still unread — likely the `slot` field.
- [x] **Equip abilities / E.S. accessory effects.** Done 2026-08-25. Both halves: the
  passives at `0x200B304` and the 40-record E.S. accessory tail at `0x200B604`, verified
  three ways each (see the sections above). Effects editable on both; accessory *names* stay
  read-only because they resolve through menu code.
- [x] **Skill purchase costs — `0x35E958`, 112 records, SOLVED** (the old "`0x35EA60`
  unidentified" entry, base corrected). `[type][id][SPTS u16][slot][pad]`; type = Auto /
  Equip / Ether skill, id = rank within type in catalog order, 112/112 against the
  walkthrough's class tree. Per-disc base (disc 2 at `0x410158`, `+0xB1800`).
  Editable in the web editor's Costs tab; both discs are written.
- [ ] Blocked on runtime (PCSX2) or deep static RE: character growth curves, global battle
  constants, field-enemy placement/detection.

## Local resources (all gitignored)
- `../ISO/` — both retail discs.
- `../Saves/` — 44 save samples (20 PSV, several .max/.sps/.cbs).
- `../Cheats/` — the disc-1 pnach.
- Reference: Xenosaga 1 has a third-party editor (`../../Xenosaga 1/OG Editor/`, by Tony H)
  — different game, but a structural reference for what's editable.

## 2026-08-23 — Enemy stat table is NOT on-disc raw (B12 correction)

Retracted an earlier claim. We had read 97 records at `0x2000000` (stride `0x5C`,
name table right after at `0x2002342`) as enemy battle stats, labelling `+0x36`
u32 as HP and inferring Atk/Def/Cash/EXP by range + HP-correlation. **Wrong.**

Ground truth (two independent sources) says record-0 "Perun" should be a
22,400-HP boss:
- xenoserieswiki `Perun_(XS2)`: HP 22400, EXP 30000, S.Pts 1200, STR 85, VIT 20,
  EATK 70, EDEF 45, DEX 70, EVA 22, AGL 12.
- Strategy guide (`enemy data`): identical HP/EXP/stat block.

Our record-0 reads `+0x36` = 860, and **22,400/30,000 as a u16/u32 pair does not
occur anywhere on Disc 1** (whole-disc byte scan). The consecutive HP|EXP pairs for
other guide enemies (Stole Marine 1500/700, Kfuga Lily 600/420) also don't form a
table. Conclusion: the real **balance** tables (enemy stats, and by extension skill
power/target, cast times, boost/break rules) are **packed inside the large
`XENOSAGA.*` archives**, not the uncompressed text region.

What the `0x2000000` records *are* is still unknown — the `+0x04` block of 8×`0x64`
does resemble element-affinity (100%) fields, so they may be a partial/derived
display structure, but their numbers are not the battle stats. **Do not ship a
writer over them.** Enemy stat editing, skill editing, and cast/combo tuning are all
blocked on cracking the `XENOSAGA.*` archive format (or a PCSX2 RAM anchor).

Verified & still-good: the enemy **name** table (Perun..Patriarch), and all the
**text** catalogs (items / key items / skills / E.S. gear). Those match the game.

### Packed-table hunt — enemy stats are not raw integers on disc (6 strategies)

Went looking for the real enemy-stat table. Ground truth = Perun (HP 22400, EXP
30000, STR 85, VIT 20, EATK 70, EDEF 45, DEX 70, EVA 22, AGL 12). Whole-disc-1
scans, all negative:

1. HP|EXP as adjacent u16 (`80 57 30 75`) — 0 hits.
2. HP u32 + EXP u32 co-occurring within 0x60 — 0 hits.
3. HP u16 + EXP u16 co-occurring within 0x40 — only odd-aligned coincidences at
   0x2000 stride (not a 97-entry table); the 3 test enemies never share a region.
4. Stat byte-run `55 14 46 2D 46 16 0C` (STR..AGL as u8) — 0 hits.
5. Sub-runs (EATK..AGL u8, DEX/EVA/AGL u8) — 0 hits.
6. Stats as u16 sequences (STR..AGL, EATK..AGL, DEX/EVA/AGL) — 0 hits.

Disc geometry: `XENOSAGA.01` (LBA 3801, >1 GiB) holds the uncompressed TEXT at
raw 0x2000000; `XENOSAGA.02` (158 MiB), `.11/.12/.13` (>1 GiB each), `.14`; plus
battle-ish overlays `OV01.OVL` (450 KiB) / `OV02.OVL`.

**Conclusion:** enemy battle stats (and by extension skill power/target, cast
times, boost/break rules) are NOT stored as plaintext integers. They are either
LZ-packed in an archive/overlay or computed at runtime from base+level. Locating
them offline now needs battle-code disassembly (ELF/overlay) to find the loader
or the packed blob — the same class of wall as the save checksum. The practical
unblock is a PCSX2 RAM search during a battle to anchor the live values, then map
back to a disc offset (if raw) or a formula (if computed).

## 2026-08-23 (later) — RETRACTION OF THE RETRACTION: enemy table SOLVED

The "stats are packed/not raw" conclusion above was wrong. The tell was hiding in
the negative scan itself: the HP-22400 hits at `0x1FFF7F2/84E/8AA/BE6/C42` are
exactly stride-0x5C apart and fall *precisely on the record lattice* (offset +0x36
within stride-0x5C records) — the table simply starts BEFORE 0x2000000, and the
original error was (a) wrong base and (b) blindly zipping name[0] with record 0.

Winning method: 8-field relative signature per guide enemy — HP u32 at p, then
STR u16@p+6, VIT@p+8, EATK@p+0xA, EDEF@p+0xC, DEX u8@p+0xE, EVA@p+0xF, AGL@p+0x10.
Whole-disc scan → exactly ONE hit per enemy; **74/76 guide enemies matched
uniquely** with all 8 fields agreeing (misses: Margulis-1st = guide errata, disc
says HP 1200 not 1000; Ai Apaec matched on name/position anyway).

### VERIFIED enemy tables (disc 1, raw byte offsets)

- **Stat table**: base `0x1FFF5F0`, stride `0x5C`, **125 records** (index 0..124).
  - `+0x04` u8×8 element affinities (0x64 = 100%)
  - `+0x36` u32 HP
  - `+0x3C` u16 STR/POW · `+0x3E` u16 VIT/ARM · `+0x40` u16 EATK · `+0x42` u16 EDEF
  - `+0x44` u8 DEX · `+0x45` u8 EVA · `+0x46` u8 AGL
  - `+0x52` u16 enemy ID (501+ field, 561+ boss, 701+ E.S./special; "2nd version"
    encounters are separate records sharing the ID)
- **Name table**: `0x2002310`, 125 null-terminated strings (ASCII + some EUC-JP
  debug names like ＧＮＯ０１３), **parallel to stat records** (same index).
- **Rewards table**: base `0x201094C`, stride `0x10`, row = record index.
  - `+0x00` u32 EXP · `+0x04` u16 SP · `+0x06` u16 CP (16/16 anchors each)
  - `+0x08..0x0F` drop rates/categories/item ids (partially decoded)

#### Affinity slots at `+0x04` — exposed, NOT verified
Eight u8 percentages, `0x64` in ordinary records. That there are eight of them and
that they hold 100 is solid; **which element each slot is has not been confirmed**.
The game's element set is known from elsewhere (the E.S. Anti-Fire/Ice/Thunder/Beam
accessories, plus the physical attack types in the status labels) but nothing on
disc ties a slot to a name, and pairing a name to a byte on vibes is exactly the
mistake the B12 retraction above came from. So they are exposed as `Aff1..Aff8`:
numbered, gated behind an explicit opt-in in the UI, and flagged on the CLI. They
They do now have entries in `x2_enemies.json`, taken from the discs rather than
from a guide, so they can be diffed against retail and restored — see "Retail
baseline" below for what that claim rests on. That is a statement about the
*bytes*, not the labels: `Aff3` is still only "the third slot", and comparing it
against retail says the slot changed, not which element changed.

Next step to actually verify them: a PCSX2 battle with a known element-resistant
enemy, or the damage-calculation routine in the battle overlay.

### 2026-08-24 — BREAKABILITY FLAG SOLVED (`+0x51` bit 3) + enemy type (`+0x50`)

The open question below is answered. Partition scan over the stat record against
the guide's per-enemy property columns, with the guide's **enemy type** used as a
**positive control** — a property that certainly exists, so a null result there
would have meant "not in this record" rather than "the scan is too weak".

The control succeeded, which is what makes the rest trustworthy:

| field | meaning | agreement |
|---|---|---|
| `+0x50` bits 0-1 | enemy type: 0 = Bio, 1 = Gnosis, 2 = Mechanism | **57/57 exact** |
| `+0x51` bit 3 | the guide's `Hit zone: None` | **57/57 exact, both discs** |

The first scan produced only false positives — `+0x52` is the enemy id, and the
guide covers a low-id band, so it "separates" the two sets by ordering and means
nothing. That is the same trap the `+0x16` retraction came from. Excluding the id
and its overlapping reads, and testing **bits** rather than only bytes, left
exactly one candidate. Bit 3's set-records are scattered across the index space
(2, 4, 14, 0, 7 per 25-record band), so it is not an ordering artifact either.

**The rule:**

```
unbreakable  ==  (+0x51 bit 3 set)  OR  (empty break sequence)
```

That reproduces the guide's `Break: Cannot` column **57/57 on both discs**, and
puts the unbreakable set at **36 of 125** records — not the 16 you get from the
sequence bytes alone. 15 records carry a perfectly hittable `BB` whose bytes are
**inert** because the bit is set.

Two consequences, both now handled:

* the enemy card states the type and whether the game will honour a Break
  sequence at all, naming an inert sequence as inert
* bulk Break shortening skips those records. It was previously writing 20
  records' worth of bytes the game never reads (108 affected → 88)

Named for what was verified, not for what it does. The guide column it matches
is `Hit zone`, so it is recorded as **zone targeting off**, even though
breakability is the consequence — the caution the `+0x04` affinity retraction
earned. The other bits of both bytes are **not** identified: counter-boost, air
effect and down effect were all tested against every bit of both bytes and none
reached 100% (down-effect tracks breakability at 96%, which is a consequence, not
an encoding). They stay unexposed.

#### The evidence this replaced (kept for the method)



Vetting the claim "an empty Break sequence means the enemy cannot be broken"
against the strategy guide's own per-enemy `Break` column produced a clean result
in the direction claimed, and an unexpected one in the other.

Of the 16 records that carry no sequence bytes, the guide covers 6, and all 6 are
listed `Break: Cannot`. Zero contradictions. Empty ⇒ unbreakable holds.

The converse does not. Matching all 125 records by name against the guide's 75
entries gives 42 agreements and **15 disagreements — every one the same shape**:
the disc holds `BB`, the guide says `Cannot`.

| | count |
|---|---|
| disc has a reachable sequence, guide says breakable | 36 |
| disc has no sequence, guide says Cannot | 6 |
| **disc has `BB`, guide says Cannot** | **15** |

All 15 are `type=Mechanism, Hit zone: None` in the guide. Two hypotheses tested:

* **The zone mask makes the sequence unreachable.** *Disproved.* All 15 have zone
  mask 6 (B+C), so `BB` is perfectly hittable. Zones and breakability are
  independent — a published battle-mechanics guide records enemies with all of
  A, B and C that still cannot be broken.
* **`Mechanism` type ⇒ cannot break.** *Disproved.* The same source lists
  Mechanism enemies that *can* be broken.

So the disc carries a breakability flag we have not located, and the sequence
bytes for these 15 are inert. Consequences, stated in the README rather than left
to be discovered:

* the editor can give an enemy a sequence the game may still refuse to honour
* "16 unbreakable" is a floor, not a count — the true set is at least 31

Where to look: the 52 undecoded bytes of the stat record. This is a clean
partition problem of exactly the kind `column_profile()` / `_partition_scores()`
were written for — 15 known-unbreakable against 36 known-breakable, both sets
name-matched to the guide, is unusually good ground truth. Not yet run.

### 2026-08-24 — PPF import (mod → staged edits)

`apply-ppf` (CLI) and **Import .ppf** (web) parse PPF3.0 — the format the
HardType mod ships in — and apply only the records that land in mapped tables,
reporting the rest. The web side stages into the normal edit buffers, so a mod
import gets the same review/retail-compare/Save pipeline as hand edits, and
interprets the patch under whichever disc's table layout explains more of it
(a disc-2 patch against disc-1 buffers would land 0x800 off). Verified against
the real mod + real disc: 528 of 661 records staged, enemy record 6's HP
doubling and the readme's tech powers (Heaven's Wrath 17, Iron Blade 80)
landing exactly. The unreachable 133 are the description strings and the
`0x35EA60` table.

### 2026-08-24 — SINGLE TECHS / E.S. ATTACKS / SPECIALS SOLVED (all 74)

The "located, not yet exposed" lead from the HardType map is closed. The missing
piece was the name pool, which turned out to live nowhere near the records: it
is a **menu-string area at `0x1D86349`** (disc 2 the usual `-0x800`), found by
streaming the whole image for "Spirit Touch" after the data region came up
empty. It lists every tech in block order — chaos(7), KOS-MOS T-ARTS(7),
Shion(3 + four literal "Shion reserve" placeholders), Jin(7), Ziggy(6+1),
MOMO(3+4), Jr.(7), then per-E.S. attack triplets interleaved with their
7-special blocks, the dual techs again, and KOS-MOS's four specials.

The mapping (block → pool group, record position → name) was verified the
strong way: for every record the HardType mod patches in these blocks, the
patched power must equal the readme's published number for the name the mapping
assigns. **71 of 71 exact, zero mismatches, zero untouched.** Two shifts were
caught and fixed on the way — the pool interleaves E.S. attacks with their
specials rather than grouping all attacks first, and record order within a
block follows the pool, not the `+0x16` string-id (Shion's three techs carry
ids 5-7).

Block bases (disc 1; all byte-identical on disc 2 at `-0x800`):

| block | base | n | catalog idx |
|---|---|---|---|
| chaos tech | 0x20028E0 | 7 | 220 |
| KOS-MOS tech | 0x20029C0 | 7 | 227 |
| Shion tech | 0x2002AA0 | 3 | 234 |
| Jin tech | 0x2002B00 | 7 | 237 |
| Ziggy tech | 0x2002BE0 | 6 | 244 |
| MOMO tech | 0x2002CA0 | 3 | 250 |
| Jr. tech | 0x2002D00 | 7 | 253 |
| Dinah attack / special | 0x2002DE0 / 0x2002E40 | 3 / 7 | 260 / 263 |
| Zebulun attack / special | 0x2002F20 / 0x2002F80 | 3 / 7 | 270 / 273 |
| Asher attack / special | 0x3060 / 0x30C0 (+0x2000000) | 3 / 7 | 280 / 283 |
| KOS-MOS special | 0x20031A0 | 4 | 290 |

Same 32-byte record, same fields — Power/Element/Target behave exactly as for
ethers (Jin's techs read Slash, Jr.'s read Pierce, Rain Arrow's target reads
"all enemies"). EP is genuinely 0 on every tech. `+0x00` holds 90/95/100 values
consistent with the accuracy reading. Element sanity note: chaos's techs carry
0x82 = Aura+Hit, which is also what his in-game flavour says.

With these, **176 skill records are editable** — everything the HardType mod
touches in the skill space is now reachable by this editor.

### 2026-08-24 — Skill name text is editable

Each catalog entry records its own `nameOff`, and the blob is
`NAME \0 META \0` for the ether/double and dual pools, or a bare name in the
single-tech/special menu list. Renaming writes over the name in place.

Three things had to be right, and the first two were wrong in the first cut:

* **The budget must come from the RETAIL name, not the disc.** Deriving it from
  the current terminator looks correct and breaks immediately: shortening
  "Aura Blast" to "Flare" moves the NUL, so the next read reports a 5-byte
  budget and the name can never be restored. The retail name in the catalog
  defines the space the layout allotted.
* **The description's start is fixed by the same retail length.** Parsing it
  after the *current* terminator made "Aura Blast" → "Flare" report its
  description as `last` — the tail of the old name. And the single-tech pool has
  no description at all, so reading one there printed MINIGUN's as
  `MICRO MISSILE`, i.e. the next name.
* **Writes pad the whole budget**, so a shorter name leaves no fragment of the
  old one between the terminator and the description. Verified end to end:
  renaming and restoring on a real disc leaves it **byte-identical**.

What this deliberately does NOT do is what HardType does. That patch rewrites
every disc-wide occurrence of the old byte sequence, which also hits menu and
tutorial prose that merely *contains* the name — `Miracle` inside
`Miracle Star`, truncating an unrelated skill. Only the authoritative blob at
`nameOff` is rewritten; prose elsewhere is left alone.

`skill_text_span()` is a bounding box over three scattered pools, for a
front-end that needs one read span. It is **not** an identity: it spans
megabytes and contains the enemy tables. `skill_name_at()` matches individual
name blobs, and `explain-diff` uses that — otherwise the box would claim every
unmapped byte between the pools, exactly as the enemy-name window once claimed
the dual-tech block.

### 2026-08-24 — Skill/class learning costs: NOT in the flat data region (NEGATIVE RESULT)

Ground truth was excellent — `Guides/skills.rtf` publishes **110 Skill Point
costs and 27 Class Point costs**, in class order, and the menu strings
`Required C.Pt:` / `Required S.Pt:` / `CLASS A..H` sit at `0x1D862AA` just
before the tech name pool. Distinct SP costs are
100/150/200/300/400/500/600/800/1000/1200/1500/1800/2400/2800/3200/3600/4000/
4800/7200/8000/9600; CP costs are 300/600/1200/2400/4800.

Four hypotheses tested against `0x1D00000..0x2200000`, all **zero matches**:

1. **A field of the 32-byte skill record.** 0 of 24 name-matched skills had
   their published cost at any offset/width in the record. Learning cost is not
   battle data — reasonable in hindsight.
2. **Literal u16 costs in the guide's class order**, any stride 2..64: the
   12-value anchor 200,150,100,100,200,200,100,100,150,100,150,150 never occurs.
3. **A column indexed by skill text index**, strides 2..64: with 49 name-matched
   (index, cost) anchors, no base/stride reproduced even 70% of them.
4. **Byte-encoded costs** — every cost divides by 50, so `cost/50` fits a u8;
   also tried `cost/100` and a rank into the 21 distinct values. None occurs at
   any stride 1..48.

The likely explanation is the one the notes already record for character growth
and shops: the class/skill-learning tables are **dynamically loaded from
`XENOSAGA.01`** rather than living at a static address in the flat region this
project maps. Same blocker, same fix — a PCSX2 session, or unpacking that
archive.

Worth noting the earlier `0x35EA60` lead is *not* revived by this: its u16s
(200/400/500/600/800/1000/1200/1500) all happen to be valid SP costs, which is
why it looked promising, but scan 2 above covers that region and found nothing.
Coincidence of value range is not identity — the same trap as `+0x04`.

### 2026-08-24 — Encounter rate: there is nothing to edit (NEGATIVE RESULT)

Asked whether the random-encounter rate could be tuned. **Episode II has no
random encounters.** Enemies are visible on the field and a battle starts on
contact, so there is no encounter-rate constant of the kind a random-encounter
RPG carries — the strategy guides describe no such mechanic, and published
descriptions of the game confirm the party "can see enemy units in the field,
choosing whether or not to engage them".

Recorded as a negative result so nobody spends a scan hunting for a table that
cannot exist. What *would* be tunable, none of it located:

* field-enemy placement and count per area
* detection/chase range — the closest thing to "how often you get dragged in"
* respawn on re-entering an area

All three are per-area field data, which the notes already establish is
dynamically loaded from `XENOSAGA.01` rather than sitting at a static ELF
address — the same blocker as character growth and shops, needing a PCSX2
session. The pnach cheat set offers no anchor either: it covers battle actors
and stats, nothing field-side.

The practical lever that already exists for "I fight too much" is making fights
shorter rather than rarer: battle-pacing profiles, bulk Break shortening, and
now per-skill power.

### 2026-08-24 — PLAYER UNIT TABLE SOLVED (15 records @ 0x1FFF020)

The name anchor below led somewhere better than expected: the character records
sit at **`0x1FFF020`** (disc 2: `0x1FFE820`), fifteen `0x5C` records with the
SAME layout as enemy records — chaos, KOS-MOS, Shion, Jin, Ziggy, MOMO, Jr.,
three spares, E.S. Dinah/Zebulun/Asher, two spares. It is the same battle-actor
structure; the first wrong guess was assuming the table would share the enemy
table's base alignment (it has its own), found by anchoring on the `0x64`-fill
signature at `+0x04` instead.

Verification, in increasing order of strength:

* both discs byte-identical (the usual `-0x800`)
* the verified battle flags read coherently: humans type 0 (Bio), E.S. units
  type 2 (Mechanism) with zone targeting off
* **the save format's "Character id" field is actually the record's `+0x34`
  name pointer** (0x564 chaos, 0x56A KOS-MOS...), and a save character block at
  join time carries **the same nine stat values** as the disc record — KOS-MOS
  1066/30/34/31/32/31/33/30/6, Shion, E.S. Dinah and E.S. Zebulun all matched
  exactly, while leveled characters sit above the base values. This table is
  what a new game copies into the save.

  *Correction (same day):* this was first written as the save block being
  "byte-identical" to the disc record. It cannot be — the save record is `0x108`
  bytes and the disc record `0x5C`, and the field offsets differ by a constant
  `0x34`. The nine stat values matching is the actual (still strong) evidence;
  the stronger phrasing was never what the check tested. Recorded rather than
  quietly edited, because the same over-reach is what the `+0x04` affinity and
  `+0x16` string-id retractions came from.

**Damage affinities** are present at the same `+0x58`, straddling into the next
record exactly as they do for enemies — the `0x14` fill at `+0x00..+0x03` of
every unit record *is* the previous unit's slots 4-7, which is what makes the
shared structure visible. Editable, with a caveat stated in the UI and worth
repeating: **every retail unit reads a flat 100% on all eight**, so nothing
cross-checks that the game reads this block for player characters the way it
demonstrably does for enemies. The offsets are verified; the behaviour is
inferred from the shared record layout. Uniformity is not proof, and this file
exists partly because that distinction was blurred twice before.

The overhang means `unit_record_tail()` is 4, and anything slicing exactly
`UNIT_COUNT * stride` reads off the end on unit 14 — the same bug that once
showed Dark Erde Kaiser's last four affinities as blank-and-modified.

**Starting gear is NOT in this table**, and the size settles it: the save keeps
equipment at `+0x86..+0x90`, which under the constant `0x34` offset difference
would land at `+0xBA..+0xC4` — past the end of a `0x5C` record. Whatever seeds a
new game's equipment lives elsewhere and has no lead yet.

`+0x3A` — still the unexplained "99" halfword on enemy records — is **EP**
here (all seven characters + Zebulun's 52 match the save exactly). The record
head `+0x00..+0x33` is 13 ascending u32s, most likely resource offsets:
undecoded, unwritten. The name pool lives in the gap after the table and runs
into enemy record 0's head (the previously-recorded fact). One observation left
deliberately unnamed: `+0x51` bit 0 is set for exactly chaos/Jin/Ziggy/Jr. and
clear for KOS-MOS/Shion/MOMO — plausibly a gender flag, but nothing verifies
it, and naming bits on plausibility is the mistake this file keeps recording.

Editable as the **Units** tab (web), `units` / `unit-set` (CLI), with the
retail baseline in `Editor/x2_units.json` (gen_unit_catalog.py, disc
cross-check gated). Disc sync copies the table; explain-diff names its fields.

### 2026-08-24 — Character + E.S. name table (a name anchor that was said not to exist)

The "Character-growth & shop tables (BLOCKED)" note above states there is *"no
name anchor (unlike the enemy table, whose adjacent name table let us align
it)"*. That is wrong. At **`0x1FFF5B8`**, immediately before the enemy stat
table, sits a packed NUL-terminated name list:

```
0  0x1FFF5B8  chaos          5  0x1FFF5D6  MOMO           10  0x1FFF5F4  E.S.Dinah
1  0x1FFF5BE  KOS-MOS        6  0x1FFF5DB  Jr.            11  0x1FFF5FE  E.S.Zebulun
2  0x1FFF5C6  Shion          7  0x1FFF5DF  予備１          12  0x1FFF60A  E.S.Asher
3  0x1FFF5CC  Jin            8  0x1FFF5E6  予備２          13  0x1FFF614  予備４
4  0x1FFF5D0  Ziggy          9  0x1FFF5ED  予備３          14  0x1FFF61B  予備５
```

**Exactly 15 entries**, matching `CHAR_COUNT = 15` in the save's character table
— and with the same `予備` ("spare") placeholders that solved the E.S. item id
space, in the same role: occupying id space so the ids stay aligned.

Two things follow.

First, a safety note worth recording on its own: entries 10-14 sit at `0x1FFF5F4`
and beyond, which is **inside enemy record 0** (`ENEMY_TABLE_OFF = 0x1FFF5F0`).
The name pool's tail physically occupies that record's undecoded leading bytes.
Writing there would corrupt E.S. names. Nothing does — every write goes to a
verified field at `+0x36` or later — but "the undecoded bytes are unused" is not
true, and this is why nothing should be written there speculatively.

Second, this is the anchor the character-growth hunt was said to lack. The next
step is the same one that worked for the enemy table: find a record table whose
entry count and ordering line up with these 15, then confirm it against ground
truth (a level-1 character's stats) rather than on shape alone. Not yet run.

### 2026-08-24 — HardType as a labelled map (XS2HT v3.9, Landon Ray)

A third-party difficulty mod, distributed as four PPF patches (one per disc,
Normal and Hard). A PPF is a list of (offset, bytes), so the patch **is** a diff
— and its readme publishes an exact value for most of what it changes. That
makes it labelled ground truth: the readme says what, the patch says where.

661 records per disc. Two tricks made it readable:

* **Diff the two difficulty versions against each other.** 366 records differ
  between Hard and Normal — those are the enemy buffs. The 294 identical ones
  are the shared rebalance (skills, techs, gear, text), which is the interesting
  half and is otherwise buried.
* **Match published numbers to patched bytes.** Every Dual Tech power in the
  readme lands at 32-byte stride from `0x20032E0` at `+0x0A`, in the readme's
  own order — see the dual-tech block above, which this is how we found.

Where the 661 records land:

| region | records | status |
|---|---|---|
| enemy stats | 375 | editable |
| **single techs + specials** | 71 | **located, not yet exposed** |
| skill blocks (ether/double/dual) | 57 | editable |
| enemy rewards + drops | 25 | editable |
| skill/tech description text | 119 | text editing, not built |
| **item/shop-like table @0x35EA60** | 4 | **new lead** |
| duplicate text copies | 9 | same strings, 9 places on disc |

**Single techs + specials** are all at `+0x0A` of a 32-byte record, spanning
`0x20028EA..0x200320A` — the same Power field as every other skill block. What
is missing is not the layout but the **name pools** needed to label them; the
dual-tech pool at `0x200FC10` covers only dual techs, and "Minigun", "Quick
Draw" and friends are not in it.

**`0x35EA60`** is a table this project has never mapped. Entries look like
6 bytes — `02 32 90 01 00 00`, `01 38 90 01 00 00` — reading as
(category, id, u16, flags), where the u16 values (200, 400, 500, 600, 800, 1000,
1200, 1500) look like **prices**. The mod's four edits there swap the *second*
byte between entries (`0x13`↔`0x05`, `0x23`↔`0x26`), i.e. it changes which item
an entry points at. This is the neighbourhood the notes list as BLOCKED for
"shop stock/price tables". It is a lead, not a finding: nothing here is verified
against ground truth yet.

**Equip Abilities and E.S. Accessories** are named in the readme with exact
values (+4 Str, Masamune +10, Gorgon Frame +60 Arm/Edef...) but no cluster of
patched bytes carries those numbers. Either the mod achieves them by
re-pointing ids (the `0x35EA60` edits) rather than editing effect values, or the
values live somewhere the patch reaches through a route not yet traced.

### Retail baseline (`x2_enemies.json`)

The catalog began as the eleven stat and reward numbers that could be checked
against a printed guide (74/76 exact matches). Everything verified afterwards —
break sequences, breakable-zone masks, damage affinities, status resistances,
item drops — was made editable without being added to the catalog, and every
retail-facing feature is driven by the catalog. So the editor would shorten every
boss's break sequence, then report the disc matched retail, export a patch that
omitted the change, and "restore to retail" would leave it in place.

`Editor/gen_enemy_catalog.py` fills the remaining 27 fields from the discs. Since
these values come from the same media the editor writes to, the reasoning is
circular unless something breaks the circle, so two checks gate the write:

* **both discs must agree, field for field, on all 125 records.** Disc 1 and
  disc 2 are separate pressings carrying independent copies of these tables. A
  disagreement means one image is modified or an offset is wrong — not that
  retail is ambiguous. All 125 × 38 fields matched.
* **the guide-verified numbers already in the catalog must still match the
  discs.** That is the tie back to the external source and the check that
  catches a wrong offset quietly yielding plausible bytes.

A pristine disc 1 now reports zero differences across all 38 fields, which is the
end-to-end version of the same statement.

### xdelta / VCDIFF patches

Two ways to share a change, for two different jobs.

A **patch file** (below) names fields — `record 6, HP, 22400 -> 1337`. It is
readable, it survives being applied to a disc that already has other edits, and
the importer validates every field before writing. That is the format to share.

An **xdelta patch** is bytes at offsets. It can express anything, including
regions this tool does not decode, and any VCDIFF decoder applies it:

```
xdelta3 -d -s <pristine ISO> patch.xdelta out.iso
```

The CLI (`x2patch.py xdelta-make`) shells out to `xdelta3`, which diffs the two
images. The web editor cannot — it never holds a pristine copy, and diffing
4.6 GB in a browser tab is not sensible — so it **synthesizes** the patch from
the byte runs it has already staged: `web/vcdiff.js` emits COPY-from-source plus
ADD-literal windows directly. Nothing reads the whole disc, and the patch for a
handful of field edits comes out around 14 KB.

Two things follow from that, and the UI says both:

* **One patch per disc.** The same edit buffer lands at different offsets on each
  disc (disc 2's tables sit 0x800 lower), so a single file could only ever be
  correct for one of them.
* **No integrity checksum.** Computing one means hashing 4.6 GB. `xdelta3` warns
  about this itself on decode; applying such a patch to a wrong or
  already-modified image corrupts it silently. The patch-file format is the
  source-verified alternative.

Verified end to end against a real disc: a patch built by `web/vcdiff.js` for
three staged edits (a stat, a reward, a skill), decoded by real `xdelta3` 3.x
against the 4,682,121,216-byte disc 1 image, reproduced a file differing from the
source in exactly those five bytes and no others.

### Patch files (`x2-enemy-patch`, version 1)
Because `x2_enemies.json` holds the verified retail values, a disc can be diffed
against retail without a pristine copy — which makes edits exportable as a small
JSON document others can apply to their own disc:

```json
{"format":"x2-enemy-patch","version":1,"game":"SLUS-20892","note":"half HP",
 "edits":{"6":{"HP":11200,"EXP":45000}}}
```

`x2patch.py export-patch / apply-patch / diff / restore` and the web ISO editor
read and write the same file. Parsing is deliberately strict (unknown field,
out-of-range record, or non-integer value is an error, not something to skip) and
validation happens before anything is written, so a bad patch cannot half-apply.
The CLI exports verified fields against the retail catalog; the web editor also
exports affinity slots, measured against the disc it opened, since retail has no
baseline for those.

Sanity anchors: Perun rec 6 HP 22,400 / EXP 30,000 / SP 1,200; Proto Ω 999,999;
Mikumari 200,000; Baal Zebul 99,999; Phobos Rigas 55,555; Patriarch 21,600;
Dark Erde Kaiser 192,000 (rec 124 — this was the record previously mislabelled
"Patriarch"). Guide EVA for Perun (72) matches the disc; the wiki's 22 is a typo.

**Lesson (B12 addendum):** a failed value-search isn't proof data is packed — first
check whether partial hits are lattice-consistent with a known stride. Use
relative-offset multi-field signatures, never contiguous byte runs, and never pair
a name table with a record table by index without an independent anchor.

## 2026-08-23 — Combo system: what's editable, what's blocked

Ep. II's battle system is the game's most-criticised feature, so it's worth
writing down exactly which parts of it we can reach.

### The system, and why it's disliked

The loop: **Stock** (spend a turn banking a stock, max 3) → **Break** (hit the
enemy's fixed weak-zone sequence with the zone buttons — A above 3 m/○, B
1-3 m/□, C below 1 m/△) → **Boost** (shared party gauge, cut in line) to chain
the rest of the party in before the Break expires at end of turn. Break is worth
about ×1.5 damage; AIR/DOWN doubles it; a full stock gauge buys 4-5 chained
attacks. Sources: GameFAQs *Battle Mechanics Guide* (VertigOne, faqs/35857),
Mogg's walkthrough (faqs/36481, publishes per-enemy weak zones), supercheats
battle-system page, Neoseeker battle-mechanics guide.

The recurring complaints, in the order they cost the player time:

1. **Mandatory.** The loop is the only efficient way to fight; anything else is
   chip damage.
2. **Dead turns up front.** Full value needs ~2 turns of doing nothing.
3. **HP bloat multiplies it** — ~3 stocked chains per enemy even overleveled, so
   trash fights run as long as mid-bosses in other games.
4. **Break expires at end of turn**, so boost-chaining isn't a choice.
5. **Zones are trivia, not decisions** (fixed per enemy, looked up in a FAQ), and
   skill homogeneity makes every character combo the same.

Monolith's own fix in Ep. III was to delete Stock and repurpose Break as a stun
gauge — so "cut the ritual tax" is the design-validated direction. Prior art for
the tuning route: the Insane Difficulty Ep. II re-balance patch, which only ever
moved enemy HP/STR/EATK.

### Tier 0 — SHIPPED: battle-pacing profiles (`x2fields.PROFILES`)

Complaints 2/3 are tuning, not mechanics, and the tuning is in tables we've
already verified. `x2patch.py rebalance --profile {faster,freer,deeper,grindcut}`
(and the web ISO tab) scales the verified stat/reward fields per record:
HP decides how many stocked chains a kill costs, VIT/EDEF whether off-loop
attacks matter, STR/EATK enemy pressure, SP/CP how fast the skill system opens.

Two judgement calls worth remembering:
- **Grouping is on HP, not the enemy ID band.** IDs 501-579 mix late-game field
  Gnosis (Ai Apaec 8,160 HP) in with bosses (Perun 22,400), so the ID is not a
  boss signal. Records at/above `MAJOR_HP_THRESHOLD` (20,000 catalog HP) scale
  as "major". The old web checkbox that treated `id >= 561` as "bosses" was
  wrong on both ends and is gone.
- **Dummy records are excluded** (`is_dummy_record`) — 13 of the 125: the debug
  names (GNO013, CRE006/018, UMA013, MON001-4, BOS026-29) plus unused rows
  carrying a token EXP with no SP/CP (Testud II: 8,000 HP, 79 EXP). Scripted 0-EXP fights
  like Margulis (2) are *not* dummies and do get scaled — deliberate, but it
  means a scripted loss can become winnable. Note it if that ever matters.

A zero field means "none" (no CP, no SP) and is never scaled up. Every field
clamps to `ENEMY_FIELD_CAPS`, not just to its byte width.

### Tier 0.5 — ANSWERED (2026-08-23): disc 2 carries its own copy

**Yes, and it must be patched too.** `verify-tables` on `SLUS-21133`
signature-scanned the image and found the stat table at **`0x1FFEDF0`** — exactly
**`0x800` below** disc 1's `0x1FFF5F0`. The whole table region is shifted by the
same delta:

| table   | disc 1 (SLUS-20892) | disc 2 (SLUS-21133) | delta    |
|---------|---------------------|---------------------|----------|
| stats   | `0x1FFF5F0`         | `0x1FFEDF0`         | `-0x800` |
| names   | `0x2002310`         | `0x2001B10`         | `-0x800` |
| rewards | `0x201094C`         | `0x201014C`         | `-0x800` |

Verified against the catalog: **125/125** stat records and **125/125** reward rows
match retail on both discs, and the 125 × `0x5C` stat records are
**byte-for-byte identical** between the two images (a full `0x5C`-wide diff of all
125 records reports zero differing bytes at any offset). The name blob reads the
same entries in the same order.

Consequences, now handled in code:

- The bases live in `x2fields.ENEMY_TABLES[disc]`, resolved per-image from
  SYSTEM.CNF by `x2patch.Iso.disc` / `.tables`. Nothing hardcodes disc 1 any more.
- `rebalance` no longer requires disc 1, and prints a reminder to run the same
  profile on the other disc.
- The web ISO editor accepts either disc (it used to hard-reject disc 2) and shows
  a standing warning naming the other disc.
- **A rebalance applied to one disc only leaves the second half of the game on
  retail values.** Export a patch from the disc you tuned and apply it to the
  other — `apply-patch` warns about the serial mismatch and continues, which is
  the intended workflow. Covered by the `a disc-1 patch replays onto disc 2`
  self-test check.

#### `+0x3A` is not the constant it looks like (and it broke `rebalance`)

The 17-byte search needle assumes `0x0063` at `+0x36+4`. That is true for 114 of
the 125 records; **eleven real enemies hold something else** — recorded in
`x2fields.ENEMY_UNK3A_EXCEPTIONS`:

| rec | name | `+0x3A` | | rec | name | `+0x3A` |
|----|------|----|--|----|------|----|
| 37 | Kfuga Lily     | 0 | | 54 | Executus Arma   | 1 |
| 38 | E2 Hauser      | 2 | | 55 | Cera 7 S        | 1 |
| 43 | Yacud Cannon   | 1 | | 56 | Cera 6 S        | 1 |
| 50 | Stole Marine   | 0 | | 65 | U-TIC Soldier A | 10 |
| 52 | Cera 7 F       | 1 | | 66 | U-TIC Soldier B | 10 |
| 53 | Cera 6 F       | 1 | |    |                 |   |

Identical on both discs, so it is real per-enemy data (a small count of some
kind — still undecoded). All five anchor records hold 99, which is why the table
locator was never affected.

`_confirm_base()` used to answer *"does this disc still hold retail values?"* by
comparing that same raw 17-byte run, so it reported **114/125 on pristine retail
media**. `disc_is_pristine()` therefore returned False on an unmodified disc, and
`rebalance` refused with *"refusing to compound — pass --force"*. The flagship
v1.3.0 feature could not run on a real disc at all; only the synthetic fixtures
passed, because they wrote 99 across the board. Fixed by comparing the **verified
fields** (`F.ENEMY_FIELDS`) instead of the raw run — both discs now report 125/125
— and both fixtures now reproduce the real `+0x3A` distribution so the regression
stays caught (reverting the fix drops the self-test to 9/11).

Lesson, same shape as the B12 correction above: a byte run that is *good enough to
locate* a table is not automatically *correct to compare* against it. Keep the
needle and the equality test separate.

### Tier 1 — SOLVED (2026-08-23): the break sequence and the zone mask

Both fields were in the stat record all along. **Two** separate fields, both
one-hot on the same three zone bits (`A = 0x01`, `B = 0x02`, `C = 0x04`):

| offset | width | field |
|--------|-------|-------|
| `+0x4C` | u8 | **hittable-zone mask** — which of the three attack heights this enemy has at all. All 125 records hold a value `<= 7`. |
| `+0x54..+0x57` | u8 x4 | **break sequence** — the zones to hit, *in order*, to Break it. One slot per hit, `0` = end of sequence, never a gap before a non-zero. `"CBB"` is `(4, 2, 2, 0)`. |

Zones are the attack heights: **A** above 3 m (O), **B** 1-3 m (square),
**C** below 1 m (triangle).

#### How it was found — and the mistake that nearly buried it

Ground truth came from `enemy data.rtf`, which publishes a `Hit zone` set *and* a
`Break` sequence per enemy. The first attempt mapped its 75 entries onto records
**by name** and the scan returned nothing: every `consistency = 1.0` column was
degenerate (constant across the truth rows, so `resolution = 0.0`), and the
`--region` sweep found nothing either.

The mapping was the problem. Cross-checking each assignment against the catalog's
own stats showed **13 of 64 rows contradicted the disc on HP**, plus 3 collisions
— the guide's names are per-*encounter* (E2 Hauser appears at 320, 1000 and 1760
HP), so name matching silently attached ~20% of the zone labels to the wrong
record. Exactly the failure the tooling warns about: one wrong row breaks the
perfect-consistency test the scan depends on.

Re-mapping by **exact 8-field stat signature** (the same eight fields the table
was solved with) gave **72 unique, conflict-free, zero-ambiguity** assignments.
Against that truth:

| field | consistency | resolution | rows |
|-------|-------------|------------|------|
| `+0x4C` u8 | 1.000 | 1.000 | 51 (breakable) |
| `+0x54` u16 | 1.000 | 0.944 | 46 |
| `+0x54` u8 | 1.000 | 0.696 | 46 — exactly 4 distinct values |

Decoding `+0x54..+0x57` then reproduced **all 46 published sequences exactly**:

```
AA -> (1,1,0,0)   BB -> (2,2,0,0)   BC -> (2,4,0,0)   CB -> (4,2,0,0)
CC -> (4,4,0,0)   ABB -> (1,2,2,0)  CBB -> (4,2,2,0)  CCB -> (4,4,2,0)
BCBC -> (2,4,2,4) BCCB -> (2,4,4,2) CBAA -> (4,2,1,1) CCBB -> (4,4,2,2)
```

Confidence checks, all clean:

- every slot value across all 125 records is in `{0, 1, 2, 4}` — 0 exceptions
- no record has a `0` slot before a non-zero one — 0 gaps
- `+0x4C <= 7` for all 125 records
- **no record's sequence uses a zone missing from its own `+0x4C` mask** (0/125
  violations) — an independent check that the two fields share one bit encoding
- byte-identical on disc 2

`+0x4C` is **not** merely the OR of the sequence slots (80/125 disagree): an
enemy can have a zone it is never broken through. That is also why the guide's
`Hit zone` column matched `+0x4C` rather than the sequence — `Hit zone` is which
heights the model *has*, not which ones Break it.

16 of the 125 records have an empty sequence: the guide's `Cannot` entries
(mechanisms and scripted fights).

### 2026-08-23 — STATUS RESISTANCES SOLVED (8 of 10 named)

Enemy `i`'s resistance block sits at **`base + i*0x5C + 0x6C`** — which is `0x10`
bytes *into record `i+1`*. One `u8` percentage per status; higher resists more.

| byte | status | agreement |
|---|---|---|
| `+0`  | Slow  | 50/51 |
| `+2`  | Blind | 70/71 |
| `+3`  | Heavy | 70/71 |
| `+4`  | Weak  | 70/71 |
| `+6`  | EthPD | 70/71 |
| `+7`  | EthDD | 70/71 |
| `+9`  | ResDw | 50/51 |
| `+10` | Junk  | 29/29 |

**479/486 overall (98.6%)**, byte-identical on both discs.

#### The shifted frame, again

This is the third field to sit outside our nominal record, and the pattern is now
unmistakable. Per enemy `i`, relative to `base + i*0x5C`:

```
+0x36..  stats          125/125
+0x58    affinities      71/71   (runs 4 bytes past the record)
+0x6C    resistances    479/486  (entirely inside record i+1)
```

So the game's real record boundary is **not** where our stat base puts it. Every
field is verified at the offset above and the editor addresses them absolutely,
so nothing is wrong in practice — but a scanner that slices `0x5C` per record
cannot see the affinity or resistance blocks at all, which is exactly why both
searches came up empty until the window was widened past the record end. **When a
field "isn't in the record", check past the record end before concluding it isn't
in the table.**

#### What is NOT resolved

- Bytes `+1`, `+5`, `+8` of the block. `+5` carries real per-enemy data (15
  distinct values); `+1` and `+8` are almost always 0 with a few exceptions.
- The guide's other two columns, **Lost** and **Curse**. Lost peaks at 38%
  agreement against any byte; Curse "matches" a zero byte 96% of the time only
  because Curse is nearly always 0, which is not evidence. Both are left
  unassigned rather than guessed. The disc block has room for them — the three
  unidentified bytes are the obvious candidates — but nothing here distinguishes
  which.
- Bytes `+11` onward are 0 across all 125 records.

The earlier disc-wide sweep for a *separate* resistance table found nothing, which
was correct: the data was in the enemy table all along, just past the record edge.

### 2026-08-23 — E.S. ITEM IDS SOLVED (one unified item table)

The disc holds **one** item table at ISO **`0x200C5D4`** — name/description pairs
covering E.S. gear, consumables, Awakenings and Secret Keys. Extracted to
`Editor/x2_items.json`: **139 entries, 126 real and 13 placeholders**.

The placeholders are the key. Thirteen slots hold the Japanese string **`予備`**
("spare/reserve") — unused item ids the localisation never filled. They occupy id
space. Skipping them, which `x2_es_equip.json` did, makes ids drift by a growing
amount at each block of spares, which is precisely why the drop table's category-2
ids appeared to match the accessory catalog at no constant offset (the guide's
pairs implied +1, +4, +7 and +8 for different entries — the accumulating drift).

Counting them, both drop categories index this one table with a **1-based** id,
each from its own base:

```
category 2 (E.S. gear)  -> x2_items[id - 1]        base 0
category 1 (consumable) -> x2_items[id - 1 + 40]   base 40  (Med Kit S)
```

All 15 unambiguous E.S. pairs from the guide resolve exactly (id 1 = Auxiliary
Armor A, 6 = EF Circuit A past three spares, 22 = G Blind Guard, 30 = G Boost
Guard, 36 = EMAX300, 37 = Auto Recover). Drop labelling went from 119/144 exact
with 21 unnamed, to **137/144 exact with none unnamed**.

#### This also settles the Skill Upgrade B/C conflict

The unified table has Skill Upgrade **A(61) B(62) C(63) D(64) E(65)** with no gap.
Consumable base 40 makes those ids 21, 22, 23, 24, 25 — so `x2_consumables.json`,
derived from the disc-1 pnach, is **missing id 22 and mislabels id 23 as "Skill
Upgrade B" when it is Skill Upgrade C**. The guide was right and our catalog was
wrong; the earlier note recording this as an unresolved two-source conflict is
now resolved in the guide's favour. The pnach-derived catalog is left in place for
its existing uses, and drop naming goes through the disc's own table.

#### Still open: the SAVE-side gear slots

The four `Gear 1..4` slots in a save record are a **different** id space, and this
does not settle them. Checking every non-zero gear value across the 24 local
saves: under `index = id` five values land on `予備` placeholders, and under
`index = id - 1` three do — neither is clean. That fits the standing note that the
four slots are probably weapon/frame/armor/anima indexing *separate* tables. The
save editor's gear picker is unchanged and remains explicitly experimental.

### 2026-08-23 — SKILL / TECH catalog extracted (174 skills)

The skill table's text lives at **ISO `0x2009B58`..`0x20108D4`** as alternating
NAME then DESCRIPTION strings, and the description's first line is *structured*:

```
"All enemies/Long/P/Pierce/Fire\nScorching rain of bullets."
 target     range type element
```

with the cost carried inline as `(EP 4)` on the skills that have one. So
targeting, physical-vs-ether, damage type, element and EP cost all come **off the
disc directly** — the guide is not needed for any of it. Extracted to
`Editor/x2_skills.json`: **174 skills, zero unparsed**, 56 with an EP cost, and a
tag vocabulary of Long/Short, P/E, Beam/Strike/Slash/Pierce and
Fire/Ice/Thunder/Aura.

Gotcha worth recording: the text is ASCII **with occasional EUC-JP glyphs** —
`0xA1 0xDF` is the multiplication sign, used in names like
`All allies (Medica × 2)`. A naive ASCII-only string scan silently truncates 25
of the 174 into fragments, which is exactly what the first pass did.

`x2patch.py skills [--grep X] [--csv] [--verbose]` lists it.

### 2026-08-24 — the record tail (a bug worth recording)

Three field groups reach past the nominal `0x5C` record: affinities (`+0x58`, 8
bytes) and status resistances (`+0x6C`, 11 bytes). Anything that slices the table
into a fixed buffer of `ENEMY_COUNT * ENEMY_STRIDE` therefore reads **off the end
on the last record** — record 124, Dark Erde Kaiser. The web editor did exactly
that, and showed its Ice/Pierce/Slash/Hit and all eight resistances as blank and
"modified", because the reads returned `undefined`.

`F.enemy_record_tail()` computes the overhang from the field table itself (27
bytes today) and is exported to `web/tables.json` as `enemy.recordTail`;
`x2patch.read_records()` includes it too, so the column scanners stop truncating
the last record. Three tests guard it: the tail matches the fields, the generated
tables carry it, and `iso.js` is asserted to add it to its slice.

The write path was never wrong — writes address `base + off` absolutely, and
out-of-range typed-array stores are silently dropped rather than corrupting
anything — so this was a display bug, not a data one. Worth stating because the
same overhang will bite the next front-end that assumes a record is a record.

### 2026-08-24 — SKILL NUMERIC TABLE SOLVED (the two failed searches, explained)

**32-byte records at ISO `0x2007CA0` (disc 1) / `0x20074A0` (disc 2, the usual
`-0x800`), 57 records covering the ether skills — text indices 0..56, Medica
through Erde Kaiser Fury.** Byte-identical across discs.

| offset | field | verification |
|---|---|---|
| `+0x00` u8 | accuracy-like (100 across the block) | name unverified |
| `+0x03` u8 | category: 1 attack / 2 heal / 4 support / 0 self | pattern |
| `+0x06` u8 | **EP cost** | **56/56** vs the "(EP n)" in each skill's own description |
| `+0x08` u16 | **element mask** — Aura 0x02, Thunder 0x04, Fire 0x08, Ice 0x10 | 4/4 elemental Blasts; same bit order as the affinity elements; Beam 0x01 inferred |
| `+0x0A` u16 | **power** — Medica 5, Medica 2 10, Medica All 5, Blasts 20, EKF 250 | family consistency (no guide publishes ether powers) |
| `+0x12` u16 | effect chance (100 everywhere seen) | pattern |
| `+0x13` u8 | effect kind: 1 inflict / 2 block / 3 add-buff / 4 damage-cut | pattern |
| `+0x14` u16 | effect's own element/flag mask (Flame Veil 0x08, Ice Veil 0x10) | element bits |
| `+0x16` u16 | pool index, 1-based | see the correction below |
| `+0x1C` u16 | animation/VFX id | name unverified |

Exposed as `F.SKILL_NUM_FIELDS` (EP / Element / Power / EffPct / EffMask),
editable via `x2patch.py skill-set <iso> <idx> --set Power=50 [--also <other>]`,
carried by disc sync, covered by the `ether-skill numeric table` self-test check,
and merged into `x2_skills.json` as a `numeric` block per entry.

#### CORRECTION to v1.8.0: `+0x16` is not a global text index

v1.8.0 claimed `+0x16` equals *skill text index + 1*, "57/57 exact". That was
over-claimed. The ether block's ids run 1..57 and its text indices run 0..56 —
**both are simply sequential**, so the agreement could not distinguish an offset
of 1 from any other constant. It was one sequence matching another sequence.

The doubles block disproves the general form: its 29 records carry ids
**80..108** while mapping to text **59..87** — offset **+21**, not +1. So `+0x16`
is a 1-based index into whatever pool its block uses, and the offset between that
pool and our text-pair walk varies per block. It is recorded as a pool index, and
each block's text range is established by semantic anchoring instead (below).

The ether block's field map itself is unaffected — that rests on the EP column
(56/56 against the descriptions) and the element bits, not on `+0x16`.

#### Why both earlier searches failed — and what finally worked

The v1.7.0 attempt scanned for the 56 EP costs as a strided column using indices
from the old skill catalog. That catalog had **two silent index-compaction bugs**:
it dropped `予備` placeholder pairs (there are placeholders at true indices 57, 58
and 173) *and* dropped every skill whose description has no `\n` — which is all
the passive equip skills. So the scan searched for the right values at the wrong
indices, everywhere, and correctly found nothing. Same failure shape as the E.S.
item ids: **placeholders occupy index space**.

Rebuilding the true index space (every name/description pair from `0x2009B58`
kept, including placeholders → exactly 174 entries, 0..173) and re-running the
identical scan found the EP column at stride 32 **immediately, 56/56**, in the
first region tried — the bytes right before the name pool.

The frame (where each 32-byte record begins) was then fixed two independent ways:
records begin with the `64 55 01 xx` accuracy block (the preceding table rows
start `5A 55` — real data, not a constant), and the `+0x16` string id equals
text index + 1 on 57/57. The pointer-array search also gets its explanation:
records carry their string id **inside** the record, so no pointer table into the
name pool ever existed to find.

#### The block map (2026-08-24) — and a trap inside it

The region `~0x2002800..0x2009B58` is many blocks of 32-byte records. `+0x16`
runs identify block boundaries cheaply, but **the record LAYOUT is not shared
across block types** — same stride, different meaning. Applying the ether field
map to another block silently produces nonsense.

| block | disc 1 | recs | text / pool | layout | exposed |
|---|---|---|---|---|---|
| ether actives | `0x2007CA0` | 57 | skill text 0..56 | ether | **yes** |
| doubles/combos | `0x2008400` | 29 | skill text 59..87 | ether | **yes** |
| two-char combination attacks | `0x20077A0` | 16 | pool `0x200FC10` (16 entries) | *different* | no |
| per-character techs | `0x2006AE0`+ | 7×7 | pool not yet paired | unknown | no |
| E.S. craft techs | `0x20072C0`+ | 4×7 | pool `0x201016C` (25 entries) — not paired | unknown | no |
| enemy-skill blocks | `0x20087C0`, `0x20089A0` | 6, 4 | ids 501..506 / 100..103 | unknown | no |

**The doubles block is editable** and was confirmed two independent ways:
elements agree 7/7 (Flame Storm `0x08` Fire, Thunder Storm `0x04`, Ice Storm
`0x10`, Aura Storm `0x02`, and the three Bursts), and — the stronger check —
**each double's EP equals the EP of the base skill named in its own description**
on **25/25** (`Double Refresh L` 4 = `Refresh L` 4, `Lost Mist` 6 = `Misty` 6,
`Miracle Stars` 8 = `Miracle Star` 8...). That cross-check comes from the text,
entirely independently of the numbers.

**The combination-attack block is the trap.** Its 16 records pair with the
16-entry pool at `0x200FC10` by count and by a clean 1..16 id run — but its
records are nearly all constant: power reads `0x14` for every entry, EP reads 0,
element reads 0. Under the ether layout it would look like sixteen identical
20-power skills. The only field that varies is `+0x03` (values 01/02/03/04/08/40
— almost certainly a character-pair mask, since combos require two specific
characters). So it is mapped and named but **deliberately not exposed**, and
`skill_record_off()` returns None for anything outside the two verified blocks
rather than letting a write land there.

Still to pair: the per-character tech pool (not yet located — pools A/B/C at
`0x200FC10`, `0x201016C`, `0x20107F0` are combination attacks, E.S. craft techs
and E.S. weapon techs respectively), and the E.S. tech blocks' layout. The save's
learned-skill ids (character record `+0x33..`) should index one of these.

### 2026-08-25 — The 予備 spare-skill slots exist physically but are blank templates

Issue #5's cheapest lead, now checked on both discs. The gap between the ether
block (`0x2007CA0 + 57×32 = 0x20083C0`) and the doubles block (`0x2008400`) is
exactly 64 bytes = 2 records — precisely where text indices 57/58
(`予備１`/`予備２`) would fall. What's actually in it:

- Both slots are **formatted records in the ether layout**, opening `64 55 01
  01` like every ether record. Byte-identical on disc 2 at the usual `-0x800`.
- But `+0x16` reads **1 in both** — Medica's pool index, not 58/59. The stats
  are generic defaults: category 1 (attack), EP 2, element 0, power 20 (the
  tech-block default power). Only the VFX ids differ (`0x22` / `0x29`).

So the slots are **inert templates, not wired spares**. Writing stats into them
is trivial; making the game *look* at them is the unknown — nothing observed
references pool ids 58/59, and the engine reaches records through per-block
indexing whose bounds live in code.

What "add Medica 3 / Flare" actually costs, revised: (a) stats into a gap
record — pokeable today; (b) a name — renaming a `予備` string in place is
already covered by the name-budget rule; (c) **the real gate**: getting a
character to learn/select the new skill. That is the learn-list / skill-shop
data — the same table already shown NOT to be in the flat data region
(2026-08-24 negative result), so it joins the #4 runtime bundle. Until then the
spare slots are storage without a doorway.

### 2026-08-25 (later) — PASSIVE/EQUIP TABLE SOLVED — and the negative result below is RETRACTED

**64 records of 12 bytes at ISO `0x200B304` (disc 1) / `-0x800` (disc 2),
covering skill catalog indices 110..173** — HP/ST Mind, the ten Guards, the
eight Coats, Break B10/B15, the +2 stat skills, Inner Peace, Double Power.
Exposed as `F.PASSIVE_*` and editable in the web editor's new Passives tab.

| offset | field | verification |
|---|---|---|
| `+0x00` u16 | name offset, relative to the table base | **64/64** resolve to the record's own catalog entry |
| `+0x02` u16 | description offset, same base | the gap to `+0x00` is the game's real rename budget |
| `+0x04` u32 | flags | unverified |
| `+0x08` u8 | sub-selector | unverified |
| `+0x09` u8 | effect **kind** (see `PASSIVE_KIND_NAMES`) | groups cleanly by passive family |
| `+0x0A` u8 | **parameter** — magnitude, or a mask on typed kinds | **20/20** vs the number in each skill's own description; **8/8** element bits on the Coats |
| `+0x0B` u8 | stat mask on kind `0x80` | STR/VIT/DEX/EVA/EATK/EDEF bits |

Verified two independent ways, neither of which was used to find it: the
parameter equals the number the skill's own description publishes on every
scalar passive that publishes one (Break B10 → 10, Guard → 20, Rare+30 → 30,
the six stat skills → 2), and on the eight Coats it is an element mask matching
the bit order the enemy damage affinities use — a table reached from a
completely different direction.

**Why the four searches below missed it, which is the lesson worth keeping:**
every one of them assumed the *active* skill layout. The scans looked for
32-byte records and for a magnitude column at a uniform stride. These records
are **12 bytes**, and their magnitude is one byte inside a packed 4-byte effect
field whose meaning changes with the kind — so there is no uniform column to
find, and `+0x16` (the active blocks' self-naming field) does not exist here.
The negative result was not wrong about the evidence; it was wrong to
generalise "not findable under the layout I assumed" into "not in the data
region". A shape assumption inherited from the last table is exactly what made
the two earlier skill-table searches fail too.

**What is genuinely still code:** ~18 of the 64 read zero across the whole
effect field — Inner Peace, Damage-10, Revenge Power, Combo Boost, Samurai and
Knight Soul, Rebound, First Combo, Ether Burst. Those remain #4 material, and
the editor says so rather than offering a number that does nothing.

**The tail — a strong lead for the accessory table.** Records 64..103 (40 of
them) continue with the identical layout and a mirrored effect set (their own
run of Coats and Guards), but their name pointers land in a numeric string pool
instead of the skill catalog, so nothing names them yet. Equipment names are
already known to resolve through menu code rather than a pointer table, which
is exactly the shape this has. Not exposed until something names them; this is
research target 2 of issue #5, now with an address to start from.

### 2026-08-25 — E.S. ACCESSORY EFFECTS SOLVED (the passive table's 40-record tail)

**40 records at `0x200B604` (disc 1), same 12-byte layout as the passives**, one
per entry of `x2_es_equip.json` plus 予備 placeholders. Exposed as `F.GEAR_*`
and editable in the web editor's Gear tab. Effects only — names stay read-only,
because these records' `+0x00`/`+0x02` pointers land in a numeric pool rather
than the skill name pool, exactly as the standing "equipment names resolve
through menu code" finding predicts.

Three independent confirmations:

1. **The shipped catalog's own descriptions predict the retail bytes** on 9 of 9
   checkable anchors: Auxiliary Armor A "Arm +30" is (kind `0x80`, param 30,
   mask ARM), Auxiliary Armor B +40, EF Circuit A/B "Edef +20/+30", the four
   Anti-* Armors each carry precisely their element bit, Tuned Circuit
   "Agility +1".
2. **The thirteen G-guards carry the same status mask *and* kind byte as their
   non-G passive twins** — G Slow Guard `0x0100` = Slow Guard `0x0100`, G Lost
   Guard kind `0x52` = Lost Guard kind `0x52` — 10 of 10 checkable. Two tables
   located separately, agreeing bit for bit.
3. **HardType's readme names its rebalanced accessories with exact values, and
   all 11 records it patches here match**: POW/EATK +20/+40/+60/+100 land as
   (`0x80`, that number, POW or EATK), Gorgon Frame +60 and Prism Frame +100 as
   ARM|EDEF, Fine Circuit +10 as DEX|EVA.

Point 3 **retracts** this file's claim that "HardType gives no anchor — its
readme names +4 Str and +60 Arm but no patched bytes carry them". It patches
eleven; they were in a table nobody had located.

Placeholders occupy index space here too (three between Auxiliary Armor B and
EF Circuit A, three more before Anti-Fire Armor, three trailing), so
`F.GEAR_ES_ID` maps record index → catalog id with `None` for a spare. Same
shape as the E.S. item ids and the skill table.

**A fourth confirmation, found afterwards and worth recording because it is
free:** record index equals the **unified item id** directly. `x2_items.json`
holds 予備 at ids 2, 3, 4, 7, 8, 9 and 37, 38, 39 — precisely the nine spare
positions derived independently from the effect bytes. Two sources that know
nothing about each other agreeing on where the holes are.

**The stat mask is now all eight bits.** The six `+2` skills name their own stat
(STR `0x80`, VIT `0x40`, DEX `0x20`, EVA `0x10`, EATK `0x08`, EDEF `0x04`), and
the last two come from the only records using them — Tuned Circuit
"Agility +1" (`0x02`) and Limiter Up "Increase max HP & EP 10%" (`0x01`). Those
two rest on one anchor each. The E.S. side names the top two POW and ARM for the
same bits, which is why `GEAR_STAT_BITS` exists alongside `PASSIVE_STAT_BITS`.

### 2026-08-26 — Web editor: a Templates tab, and the enemy card collapsed

Not a data finding; recorded because two of the decisions constrain future work.

**Preview runs on scratch buffers.** `withScratch()` swaps every edit buffer for
a throwaway copy, runs the caller, and restores in a `finally`. The Templates tab
applies a template to the copies, renders the diff off them, and throws them
away — so selecting a template stages nothing, and a preview that throws cannot
strand the editor on scratch buffers. Anything else added to the pane must go
through it; reading the live buffers would stage a template merely by displaying
it, and the user's own pending edits would go with it.

**`reviewRows()` became `changeRows(pick)`**, parameterised by which baseline
each buffer is compared against — `T.orig` for the write confirmation, the
current staged bytes for the template preview. Same rows, same grouping, one
function. While generalising it, three panes turned out to be missing entirely:
**passives, gear and skill costs were never listed in the write review**, so
staging the HardType preset showed a confirmation dialog that silently omitted
everything it did to those three tables. They are in now. A review that omits a
pane is worse than no review, because it reads as "that is everything".

The cost rows show **Type/Id/Slot as well as Cost** on purpose: the mod re-prices
four ethers by *swapping id bytes*, which a Cost-only view renders as "nothing
changed" on records whose cost is untouched.

**What the preview cannot itemise**, and says so rather than hiding: rewritten
skill *descriptions*. They are text in the same region as fields we model but
belong to no field, so they are counted as byte runs instead of rendered as rows.

**Composability was the open question and it is settled as: layer, but say so.**
The preview counts how many of the user's own changed bytes a template would
overwrite, Accept layers, and Replace reverts first. Silently layering (what the
old preset buttons did) is wrong for something presented as a coherent whole; so
is silently replacing.

The enemy card's six control blocks are now `<details>`, closed by default, with
the open set remembered in `localStorage` per SECTION rather than per enemy —
`loadEnemy()` refills the tables inside them without replacing the elements, so a
section stays open while paging through 125 enemies. Without that, a disclosure
control is worse than none.

### 2026-08-25 — The 13 records no front end applies, both identified

These are what `apply-ppf` and the presets report as unreachable. Neither is a
mystery any more; both are characterised, and one now has a proven safe method.

#### 9 of them: `$zoom13;` battle captions — SOLVED 2026-08-26, 9/9 reached

The renamed skills' captions, duplicated per battle script: `$zoom13;Miracle
Star` appears **7 times** and `$zoom13;Annihilation` **twice**, and the mod
patches exactly those nine and no others. A content scan finds all of them, so
these were never really "unconfirmed offsets" — they are locatable the same way
`locate_enemy_table()` works, by signature rather than by a constant.

Two facts that settle how to support them:
- **The mod is precise, not blind.** "Annihilation" also occurs 6 more times
  *without* the `$zoom13;` prefix, and the mod leaves every one alone. So this
  file's older warning about a "disc-wide byte replace that truncates Miracle
  Star" overstates it for these records: the mod targets only the caption pool.
- **Disc 2 carries them at the usual `-0x800`** — confirmed from the mod's own
  disc-2 patch, whose entries for these nine sit exactly `0x800` below.

There are 1,221 `$zoom13;` strings on disc 1, so this is a general mechanism,
not a special case for two skills.

**What was built** (`scan_captions`, `caption_owners`, `caption_spans`,
`rename_captions` in `x2patch.py`; `CAPTION_PREFIX` / `caption_budget` in
`x2fields.py`):

- **One needle, one pass.** The scan searches for the bare prefix and reads back
  what follows, rather than putting 190 name needles in the pass. Both find the
  same nine records; the second costs 190 buffer searches per chunk instead of
  one, and it cannot produce the census. Measured 1,221 captions / 418 distinct
  texts on disc 1 in ~8 s.
- **Attribution is by text, against BOTH the retail catalog name and the name
  currently in the pool.** Retail alone stops working the moment a caption has
  been rewritten once, so a second rename would silently do nothing; current
  alone misses the half-applied disc a patch import leaves, where the pool says
  "Flare" and seven captions still say "Miracle Star". Of the 190 skills, **18
  have captions at all** (156 of the 1,221) — the rest of the pool is enemy
  attacks and system labels. **Zero ambiguous**: no two skills claim one text.
- **Locate before writing.** `cmd_skill_rename` scans first and writes second.
  Getting this backwards is a real bug and it was written that way first: the
  name write lands, attribution then matches nothing, and the second rename
  reports "no battle caption on this image" while seven of them sit there.
- **The budget is the retail name's, and it is a SEPARATE function from
  `skill_name_budget()`** even though the two agree for every active skill. They
  are different pools — a passive's name budget is its record's name/desc pointer
  gap — and sharing one number would make "it fitted the name" imply "it fits the
  caption".

**Verified on the real disc**, not just the fixture: `Miracle Star` → `Flare` →
`Fire` → `Miracle Star` rewrites all seven copies each time and comes back
**byte-identical to retail**, name blob included. `apply-ppf` on XS2HT v3.9
Normal D1 goes from 657/661 to **661/661**, and the bytes land exactly where the
mod put them.

**Two things the mod told us that the fixture now encodes.** It writes
`Flare\0` over `Miracl` and leaves `e Star\0` behind as a live-looking string,
and it turns `Annihilation` into `Angel's Rain` by replacing the nine bytes
`nihilatio` in the middle. That the game is fine with both means captions are
**referenced by offset, not walked as a sequential pool** — which is what makes
NUL-padding the slack (what this editor does instead, so no fragment survives)
safe rather than a gamble.

**The remaining honest limit.** A caption can be orphaned by renaming with
`--no-captions` and then renaming again: the disc says "Flare", the pool says
"Fire", and no string links them. `captions --grep` finds it by text. This is
inherent to content addressing, not a gap in the implementation.

#### 4 of them: the SKILL PURCHASE COST table at `0x35E958` — SOLVED, 112/112

**112 records of 6 bytes: `[type u8][id u8][cost u16 = SPTS][slot u8][pad u8=0]`.**
This is what a skill costs to learn, and it retires both the "`0x35EA60`
unidentified" TODO *and* this file's "skill/class learning costs are not in the
flat data region" negative result.

`type` is the skill's **type**, and each type's cost multiset is *identical* to
the walkthrough's class-tree listing for that type:

| type | meaning | records |
|---|---|---|
| 0 | Auto skill | 28 |
| 1 | Equip skill | 34 |
| 2 | Ether skill | 50 |

`id` is the skill's rank **within its type, ordered by skill-catalog index**.
Under that mapping every record's cost equals the SPTS the walkthrough publishes
for that skill — **112 of 112, all three types, zero mismatches**. 31 records
also carry a nonzero `slot` forming a clean 1..31 run; unread, but it tracks the
expensive skills, so it is probably the class-tree tier.

**Why the first attempt at this failed, and it is a ground-truth lesson, not a
search lesson.** The initial check used `Guides/skills.rtf`, whose "N Skill
Points" column disagrees with the disc (it gives STR+2 and VIT+2 100 each; the
disc says 100 and 150). The cost *histogram* still matched well enough to look
promising while every per-skill check failed, which reads exactly like a wrong
table — so the table was nearly written off. The right source was
`Guides/walkthru.rtf`'s class tree (`NAME | type | target | N SPTS`), and it
agrees with the disc perfectly. **When a histogram matches but the sequence does
not, suspect the ground truth before suspecting the table** — the same shape as
the "map guide rows by signature, not by name" lesson above.

The disc arbitrates between the two guides: `skills.rtf` is wrong on VIT+2.

**Disc 2 carries it too — at its own base `0x410158`.** This is the only table
in this file whose second-disc copy is not a fixed shift: it is `+0xB1800` away
and byte-identical over all 112 records, so it lives in a per-disc base map like
`ENEMY_TABLES`. An earlier draft said "disc 1 only" after probing `0`, `±0x800`
and `±0x1000` and finding nothing. The table was there the whole time, further
out. **Probing a handful of likely shifts is not a search — scan for the
content.** (The mod's own disc-2 patch does not touch this table, which is what
made "disc 1 only" look corroborated. It is a gap in the mod, not the disc: a
cost change applied only to disc 1 would revert at the disc swap.)

What the mod does here is now fully legible, and it is the last unexplained
thing in its whole patch. It writes **only the id byte** of four records, which
swaps *which skill sits at each price*:

| record | retail | after the mod |
|---|---|---|
| 54 | Junk Beam @ 500 SP | Refresh H @ 500 SP |
| 64 | Refresh H @ 800 SP | **Junk Beam @ 800 SP** |
| 55 | Miracle Star @ 400 SP | Prayer @ 400 SP |
| 84 | Prayer @ 1200 SP | **Miracle Star @ 1200 SP** |

Junk Beam and Miracle Star are exactly the two ethers the mod renames — to
**Medica 3** and **Flare** — so it moves its two new premium skills up the price
ladder and drops the skills they displaced into the cheap slots. That is a real
check on the whole mapping, not just a consistency note: the two records it
re-prices are, independently, the two skills it renames.

This also answers issue #5's research target 4 (skill gating) on its cost axis:
"make Heaven's Rain expensive" is now a two-byte edit. The *level* axis — which
class tier a skill sits in — is likely the `slot` field or the class-tree data,
and is not yet read.

#### The original write-up (kept — the structure it derived was right)

This retires the "`0x35EA60` — unidentified" TODO entry, which had the base
wrong by 0x108 and mistook part of the table for a bare u16 array.

**6-byte records: `[category u8][id u8][cost u16][slot u8][pad u8=0]`**, 112
records with a real cost, followed by 7 more at cost 0 before the padding.

| category | ids | records |
|---|---|---|
| 0 | 1..28 | 28 |
| 1 | 29..62 | 34 |
| 2 | 1..51 | 50 |

Categories 0 and 1 share one contiguous id space (1..62); category 2 has its
own. **31 records carry a nonzero `slot`, and those form a clean 1..31 run**
across all three categories — an ordering over a subset, and the slotted records
skew expensive (300..9600).

The costs are on the **Skill Point scale** — confirmed against the right guide
above. (This paragraph originally read the cost column against `skills.rtf`,
matched 15 of 21 histogram buckets but no per-skill value, and concluded the
table might be a second cost axis. It is not: the ground truth was wrong.)

Disc-1-only, and the mod's two swaps, are covered above.

#### RETRACTED — the negative result this replaced (kept for the method)

~~Issue #5 step 2 proposed finding the passive band by the self-naming `+0x16`
field. Four independent searches, all empty:~~ *(All four ran as described and
found nothing; the conclusion drawn from them was too strong. Retained because
the searches themselves are reusable and because the failure mode — carrying a
record shape across table boundaries — is the recurring one in this file.)*

1. **`+0x16` run scan**, `0x1FF0000..0x2018000` at 32-byte alignment: every
   ascending run is already accounted for by the block map above. No run covers
   111..173 (pool `+1`) or 131..193 (pool `+21`, the doubles' offset).
2. **Magnitude signature, whole disc**: the passive names/descriptions publish
   22 exact values (10/15 pairs, six `+2`s, 5/10/30/10, 20/30, eight 25%
   coats). No uniform-stride u8 layout holds that vector anywhere on disc 1
   (strides 1..64), and the tight local sub-patterns alone (the `20,30,25×8`
   coat run) hit only float geometry — nothing structural.
3. **Membership clustering**: no window anywhere on the disc concentrates the
   band's ids at 16/32-byte alignment.
4. **Pointer-table delta hunt** near the name pool (±4 MB, u16 and u32):
   nothing matches the name-offset deltas — consistent with the earlier finding
   that no pointer table into the pool exists.

~~Conclusion: passive equip effects almost certainly have no numeric records in
the flat data region.~~ **Wrong — see the section above.** They are 12-byte
records at `0x200B304`, and every search here missed them by assuming the
32-byte active-skill shape. The part that survives: the ~18 passives whose
effect field is all zeroes really are coded, and for those the PCSX2 entry
point still stands — equip `HP Mind 10`, breakpoint the max-HP recompute, and
`Inner Peace` / `Combo Boost` should land in the same routine family.

The counter-boost hunt (issue #5 step 3) also resolves by reasoning rather than
scanning: the downed-counter-boost mechanic applies to every enemy uniformly,
so there is no per-enemy behavioural difference for a flag bit to explain and
nothing data-side to correlate a candidate against. It is battle code → #4.

### 2026-08-23 — STATUS RESISTANCES SOLVED (8 of 10 named)

Enemy `i`'s resistance block sits at **`base + i*0x5C + 0x6C`** — which is `0x10`
bytes *into record `i+1`*. One `u8` percentage per status; higher resists more.

| byte | status | agreement |
|---|---|---|
| `+0`  | Slow  | 50/51 |
| `+2`  | Blind | 70/71 |
| `+3`  | Heavy | 70/71 |
| `+4`  | Weak  | 70/71 |
| `+6`  | EthPD | 70/71 |
| `+7`  | EthDD | 70/71 |
| `+9`  | ResDw | 50/51 |
| `+10` | Junk  | 29/29 |

**479/486 overall (98.6%)**, byte-identical on both discs.

#### The shifted frame, again

This is the third field to sit outside our nominal record, and the pattern is now
unmistakable. Per enemy `i`, relative to `base + i*0x5C`:

```
+0x36..  stats          125/125
+0x58    affinities      71/71   (runs 4 bytes past the record)
+0x6C    resistances    479/486  (entirely inside record i+1)
```

So the game's real record boundary is **not** where our stat base puts it. Every
field is verified at the offset above and the editor addresses them absolutely,
so nothing is wrong in practice — but a scanner that slices `0x5C` per record
cannot see the affinity or resistance blocks at all, which is exactly why both
searches came up empty until the window was widened past the record end. **When a
field "isn't in the record", check past the record end before concluding it isn't
in the table.**

#### What is NOT resolved

- Bytes `+1`, `+5`, `+8` of the block. `+5` carries real per-enemy data (15
  distinct values); `+1` and `+8` are almost always 0 with a few exceptions.
- The guide's other two columns, **Lost** and **Curse**. Lost peaks at 38%
  agreement against any byte; Curse "matches" a zero byte 96% of the time only
  because Curse is nearly always 0, which is not evidence. Both are left
  unassigned rather than guessed. The disc block has room for them — the three
  unidentified bytes are the obvious candidates — but nothing here distinguishes
  which.
- Bytes `+11` onward are 0 across all 125 records.

The earlier disc-wide sweep for a *separate* resistance table found nothing, which
was correct: the data was in the enemy table all along, just past the record edge.

### 2026-08-23 — E.S. ITEM IDS SOLVED (one unified item table)

The disc holds **one** item table at ISO **`0x200C5D4`** — name/description pairs
covering E.S. gear, consumables, Awakenings and Secret Keys. Extracted to
`Editor/x2_items.json`: **139 entries, 126 real and 13 placeholders**.

The placeholders are the key. Thirteen slots hold the Japanese string **`予備`**
("spare/reserve") — unused item ids the localisation never filled. They occupy id
space. Skipping them, which `x2_es_equip.json` did, makes ids drift by a growing
amount at each block of spares, which is precisely why the drop table's category-2
ids appeared to match the accessory catalog at no constant offset (the guide's
pairs implied +1, +4, +7 and +8 for different entries — the accumulating drift).

Counting them, both drop categories index this one table with a **1-based** id,
each from its own base:

```
category 2 (E.S. gear)  -> x2_items[id - 1]        base 0
category 1 (consumable) -> x2_items[id - 1 + 40]   base 40  (Med Kit S)
```

All 15 unambiguous E.S. pairs from the guide resolve exactly (id 1 = Auxiliary
Armor A, 6 = EF Circuit A past three spares, 22 = G Blind Guard, 30 = G Boost
Guard, 36 = EMAX300, 37 = Auto Recover). Drop labelling went from 119/144 exact
with 21 unnamed, to **137/144 exact with none unnamed**.

#### This also settles the Skill Upgrade B/C conflict

The unified table has Skill Upgrade **A(61) B(62) C(63) D(64) E(65)** with no gap.
Consumable base 40 makes those ids 21, 22, 23, 24, 25 — so `x2_consumables.json`,
derived from the disc-1 pnach, is **missing id 22 and mislabels id 23 as "Skill
Upgrade B" when it is Skill Upgrade C**. The guide was right and our catalog was
wrong; the earlier note recording this as an unresolved two-source conflict is
now resolved in the guide's favour. The pnach-derived catalog is left in place for
its existing uses, and drop naming goes through the disc's own table.

#### Still open: the SAVE-side gear slots

The four `Gear 1..4` slots in a save record are a **different** id space, and this
does not settle them. Checking every non-zero gear value across the 24 local
saves: under `index = id` five values land on `予備` placeholders, and under
`index = id - 1` three do — neither is clean. That fits the standing note that the
four slots are probably weapon/frame/armor/anima indexing *separate* tables. The
save editor's gear picker is unchanged and remains explicitly experimental.

### 2026-08-23 — SKILL / TECH catalog extracted (174 skills)

The skill table's text lives at **ISO `0x2009B58`..`0x20108D4`** as alternating
NAME then DESCRIPTION strings, and the description's first line is *structured*:

```
"All enemies/Long/P/Pierce/Fire\nScorching rain of bullets."
 target     range type element
```

with the cost carried inline as `(EP 4)` on the skills that have one. So
targeting, physical-vs-ether, damage type, element and EP cost all come **off the
disc directly** — the guide is not needed for any of it. Extracted to
`Editor/x2_skills.json`: **174 skills, zero unparsed**, 56 with an EP cost, and a
tag vocabulary of Long/Short, P/E, Beam/Strike/Slash/Pierce and
Fire/Ice/Thunder/Aura.

Gotcha worth recording: the text is ASCII **with occasional EUC-JP glyphs** —
`0xA1 0xDF` is the multiplication sign, used in names like
`All allies (Medica × 2)`. A naive ASCII-only string scan silently truncates 25
of the 174 into fragments, which is exactly what the first pass did.

`x2patch.py skills [--grep X] [--csv] [--verbose]` lists it.

### 2026-08-24 — the record tail (a bug worth recording)

Three field groups reach past the nominal `0x5C` record: affinities (`+0x58`, 8
bytes) and status resistances (`+0x6C`, 11 bytes). Anything that slices the table
into a fixed buffer of `ENEMY_COUNT * ENEMY_STRIDE` therefore reads **off the end
on the last record** — record 124, Dark Erde Kaiser. The web editor did exactly
that, and showed its Ice/Pierce/Slash/Hit and all eight resistances as blank and
"modified", because the reads returned `undefined`.

`F.enemy_record_tail()` computes the overhang from the field table itself (27
bytes today) and is exported to `web/tables.json` as `enemy.recordTail`;
`x2patch.read_records()` includes it too, so the column scanners stop truncating
the last record. Three tests guard it: the tail matches the fields, the generated
tables carry it, and `iso.js` is asserted to add it to its slice.

The write path was never wrong — writes address `base + off` absolutely, and
out-of-range typed-array stores are silently dropped rather than corrupting
anything — so this was a display bug, not a data one. Worth stating because the
same overhang will bite the next front-end that assumes a record is a record.

### 2026-08-24 — SKILL NUMERIC TABLE SOLVED (the two failed searches, explained)

**32-byte records at ISO `0x2007CA0` (disc 1) / `0x20074A0` (disc 2, the usual
`-0x800`), 57 records covering the ether skills — text indices 0..56, Medica
through Erde Kaiser Fury.** Byte-identical across discs.

| offset | field | verification |
|---|---|---|
| `+0x00` u8 | accuracy-like (100 across the block) | name unverified |
| `+0x03` u8 | category: 1 attack / 2 heal / 4 support / 0 self | pattern |
| `+0x06` u8 | **EP cost** | **56/56** vs the "(EP n)" in each skill's own description |
| `+0x08` u16 | **element mask** — Aura 0x02, Thunder 0x04, Fire 0x08, Ice 0x10 | 4/4 elemental Blasts; same bit order as the affinity elements; Beam 0x01 inferred |
| `+0x0A` u16 | **power** — Medica 5, Medica 2 10, Medica All 5, Blasts 20, EKF 250 | family consistency (no guide publishes ether powers) |
| `+0x12` u16 | effect chance (100 everywhere seen) | pattern |
| `+0x13` u8 | effect kind: 1 inflict / 2 block / 3 add-buff / 4 damage-cut | pattern |
| `+0x14` u16 | effect's own element/flag mask (Flame Veil 0x08, Ice Veil 0x10) | element bits |
| `+0x16` u16 | pool index, 1-based | see the correction below |
| `+0x1C` u16 | animation/VFX id | name unverified |

Exposed as `F.SKILL_NUM_FIELDS` (EP / Element / Power / EffPct / EffMask),
editable via `x2patch.py skill-set <iso> <idx> --set Power=50 [--also <other>]`,
carried by disc sync, covered by the `ether-skill numeric table` self-test check,
and merged into `x2_skills.json` as a `numeric` block per entry.

#### CORRECTION to v1.8.0: `+0x16` is not a global text index

v1.8.0 claimed `+0x16` equals *skill text index + 1*, "57/57 exact". That was
over-claimed. The ether block's ids run 1..57 and its text indices run 0..56 —
**both are simply sequential**, so the agreement could not distinguish an offset
of 1 from any other constant. It was one sequence matching another sequence.

The doubles block disproves the general form: its 29 records carry ids
**80..108** while mapping to text **59..87** — offset **+21**, not +1. So `+0x16`
is a 1-based index into whatever pool its block uses, and the offset between that
pool and our text-pair walk varies per block. It is recorded as a pool index, and
each block's text range is established by semantic anchoring instead (below).

The ether block's field map itself is unaffected — that rests on the EP column
(56/56 against the descriptions) and the element bits, not on `+0x16`.

#### Why both earlier searches failed — and what finally worked

The v1.7.0 attempt scanned for the 56 EP costs as a strided column using indices
from the old skill catalog. That catalog had **two silent index-compaction bugs**:
it dropped `予備` placeholder pairs (there are placeholders at true indices 57, 58
and 173) *and* dropped every skill whose description has no `\n` — which is all
the passive equip skills. So the scan searched for the right values at the wrong
indices, everywhere, and correctly found nothing. Same failure shape as the E.S.
item ids: **placeholders occupy index space**.

Rebuilding the true index space (every name/description pair from `0x2009B58`
kept, including placeholders → exactly 174 entries, 0..173) and re-running the
identical scan found the EP column at stride 32 **immediately, 56/56**, in the
first region tried — the bytes right before the name pool.

The frame (where each 32-byte record begins) was then fixed two independent ways:
records begin with the `64 55 01 xx` accuracy block (the preceding table rows
start `5A 55` — real data, not a constant), and the `+0x16` string id equals
text index + 1 on 57/57. The pointer-array search also gets its explanation:
records carry their string id **inside** the record, so no pointer table into the
name pool ever existed to find.

#### The wider architecture (mapped, not yet name-verified)

The whole region `~0x20065A0..0x2009B58` is more blocks of the same 32-byte
record, identifiable by their `+0x16` runs: **seven runs of 1..7** (per-character
tech blocks, seven characters), runs at `0x20072C0..0x2007620`
(E.S.-craft techs), a run of **1..16** at `0x20077A0` (the E.S. tech block whose
names are the MINIGUN.. pool at `0x20107F0`), the 57 ether skills, then
doubles/enemy-skill blocks (`+0x16` 80..108 and 501..506) whose string-id space
does not line up with our pair walk yet. Only the ether block is exposed;
mapping the tech blocks to their name pools is the obvious next step, and the
save's learned-skill ids (`+0x33..` in the character record) should index one of
these block layouts.

### 2026-08-23 — STATUS RESISTANCES SOLVED (8 of 10 named)

Enemy `i`'s resistance block sits at **`base + i*0x5C + 0x6C`** — which is `0x10`
bytes *into record `i+1`*. One `u8` percentage per status; higher resists more.

| byte | status | agreement |
|---|---|---|
| `+0`  | Slow  | 50/51 |
| `+2`  | Blind | 70/71 |
| `+3`  | Heavy | 70/71 |
| `+4`  | Weak  | 70/71 |
| `+6`  | EthPD | 70/71 |
| `+7`  | EthDD | 70/71 |
| `+9`  | ResDw | 50/51 |
| `+10` | Junk  | 29/29 |

**479/486 overall (98.6%)**, byte-identical on both discs.

#### The shifted frame, again

This is the third field to sit outside our nominal record, and the pattern is now
unmistakable. Per enemy `i`, relative to `base + i*0x5C`:

```
+0x36..  stats          125/125
+0x58    affinities      71/71   (runs 4 bytes past the record)
+0x6C    resistances    479/486  (entirely inside record i+1)
```

So the game's real record boundary is **not** where our stat base puts it. Every
field is verified at the offset above and the editor addresses them absolutely,
so nothing is wrong in practice — but a scanner that slices `0x5C` per record
cannot see the affinity or resistance blocks at all, which is exactly why both
searches came up empty until the window was widened past the record end. **When a
field "isn't in the record", check past the record end before concluding it isn't
in the table.**

#### What is NOT resolved

- Bytes `+1`, `+5`, `+8` of the block. `+5` carries real per-enemy data (15
  distinct values); `+1` and `+8` are almost always 0 with a few exceptions.
- The guide's other two columns, **Lost** and **Curse**. Lost peaks at 38%
  agreement against any byte; Curse "matches" a zero byte 96% of the time only
  because Curse is nearly always 0, which is not evidence. Both are left
  unassigned rather than guessed. The disc block has room for them — the three
  unidentified bytes are the obvious candidates — but nothing here distinguishes
  which.
- Bytes `+11` onward are 0 across all 125 records.

The earlier disc-wide sweep for a *separate* resistance table found nothing, which
was correct: the data was in the enemy table all along, just past the record edge.

### 2026-08-23 — E.S. ITEM IDS SOLVED (one unified item table)

The disc holds **one** item table at ISO **`0x200C5D4`** — name/description pairs
covering E.S. gear, consumables, Awakenings and Secret Keys. Extracted to
`Editor/x2_items.json`: **139 entries, 126 real and 13 placeholders**.

The placeholders are the key. Thirteen slots hold the Japanese string **`予備`**
("spare/reserve") — unused item ids the localisation never filled. They occupy id
space. Skipping them, which `x2_es_equip.json` did, makes ids drift by a growing
amount at each block of spares, which is precisely why the drop table's category-2
ids appeared to match the accessory catalog at no constant offset (the guide's
pairs implied +1, +4, +7 and +8 for different entries — the accumulating drift).

Counting them, both drop categories index this one table with a **1-based** id,
each from its own base:

```
category 2 (E.S. gear)  -> x2_items[id - 1]        base 0
category 1 (consumable) -> x2_items[id - 1 + 40]   base 40  (Med Kit S)
```

All 15 unambiguous E.S. pairs from the guide resolve exactly (id 1 = Auxiliary
Armor A, 6 = EF Circuit A past three spares, 22 = G Blind Guard, 30 = G Boost
Guard, 36 = EMAX300, 37 = Auto Recover). Drop labelling went from 119/144 exact
with 21 unnamed, to **137/144 exact with none unnamed**.

#### This also settles the Skill Upgrade B/C conflict

The unified table has Skill Upgrade **A(61) B(62) C(63) D(64) E(65)** with no gap.
Consumable base 40 makes those ids 21, 22, 23, 24, 25 — so `x2_consumables.json`,
derived from the disc-1 pnach, is **missing id 22 and mislabels id 23 as "Skill
Upgrade B" when it is Skill Upgrade C**. The guide was right and our catalog was
wrong; the earlier note recording this as an unresolved two-source conflict is
now resolved in the guide's favour. The pnach-derived catalog is left in place for
its existing uses, and drop naming goes through the disc's own table.

#### Still open: the SAVE-side gear slots

The four `Gear 1..4` slots in a save record are a **different** id space, and this
does not settle them. Checking every non-zero gear value across the 24 local
saves: under `index = id` five values land on `予備` placeholders, and under
`index = id - 1` three do — neither is clean. That fits the standing note that the
four slots are probably weapon/frame/armor/anima indexing *separate* tables. The
save editor's gear picker is unchanged and remains explicitly experimental.

### 2026-08-23 — SKILL / TECH catalog extracted (174 skills)

The skill table's text lives at **ISO `0x2009B58`..`0x20108D4`** as alternating
NAME then DESCRIPTION strings, and the description's first line is *structured*:

```
"All enemies/Long/P/Pierce/Fire\nScorching rain of bullets."
 target     range type element
```

with the cost carried inline as `(EP 4)` on the skills that have one. So
targeting, physical-vs-ether, damage type, element and EP cost all come **off the
disc directly** — the guide is not needed for any of it. Extracted to
`Editor/x2_skills.json`: **174 skills, zero unparsed**, 56 with an EP cost, and a
tag vocabulary of Long/Short, P/E, Beam/Strike/Slash/Pierce and
Fire/Ice/Thunder/Aura.

Gotcha worth recording: the text is ASCII **with occasional EUC-JP glyphs** —
`0xA1 0xDF` is the multiplication sign, used in names like
`All allies (Medica × 2)`. A naive ASCII-only string scan silently truncates 25
of the 174 into fragments, which is exactly what the first pass did.

`x2patch.py skills [--grep X] [--csv] [--verbose]` lists it.

**Not located: the numeric table** behind these — raw power, cast time, accuracy.
The catalog gives names, targets and EP; changing what a skill *does* needs that
table.

#### Searched for, and NOT found (2026-08-23) — don't repeat these

Two independent approaches, both negative:

1. **Pointer array into the name table.** Built all 174 name offsets and scanned
   `0x1FE0000..0x2030000` for `u32` arrays holding them, absolute and relative to
   six plausible bases. Absolute: **0 hits**. `0x2000000`: 5. `0x2009000`: 6.
   (Relative to the table's own start gives ~20k hits, which is just small
   integers matching everything — noise, not a table.) So skill names are
   resolved by **string index in code**, not by a scannable offset table — the
   same wall the E.S. weapon/frame ids hit.

2. **The EP-cost column.** 56 of the 174 skills carry an EP cost in their own
   description text, which is ground truth needing no guide. Scanned for a strided
   `u8` column reproducing that sequence in name-table order, requiring ≥75-80%
   agreement, with three well-separated anchors for early rejection:
   - data region `0x1F00000..0x2060000`, strides 1..64 — **no candidate**
   - boot ELF `SLUS_208.92` (5.8 MB), strides 1..48 — **no candidate**
   - `OV01.OVL`, `OV02.OVL`, `XENOSAGA.00`, `XENOSAGA.10` — **no candidate**

The assumption most likely to be wrong is that **skill index order equals
name-table order**. If the game indexes skills by a class/level id rather than by
position in the text run, every scan above is looking for the right values in the
wrong order and would find nothing regardless. Next attempt should establish the
index mapping first — from a save's learned-skill array (character record
`+0x33..`, which grows with observed ids `0x1D, 0x1E, 0x1F…`) cross-referenced
against which skills a character actually knows.

### 2026-08-23 — ITEM DROPS SOLVED (the rest of the rewards row)

The `0x10` rewards row is now fully accounted for:

```
+0x00 u32  EXP        +0x08 u8  common drop rate %
+0x04 u16  SP         +0x09 u8  rare   drop rate %
+0x06 u16  CP         +0x0A u8  common item CATEGORY
                      +0x0B u8  rare   item CATEGORY
                      +0x0C u8  common item id (1-BASED)
                      +0x0D u8  rare   item id
                      +0x0E, +0x0F  always 0 (all 125 records)
```

Category: **0 = nothing, 1 = consumable, 2 = E.S. gear** (24 / 100 / 20
occurrences across the mapped records). It first looked like a boolean "has a
drop" flag; the 2s are exactly the enemies the guide says drop E.S. equipment.

Ids are **1-based within the category**, so `consumable_names()[id - 1]`.
All 23 distinct consumable ids seen resolve with **zero conflicts** — id 1 =
Med Kit S, 5 = Ether Pack S, 11 = Antidote L, 33 = Scrap Iron, 34 = Junked
Circuit, 35 = Ether Core. Drop rates match the guide on **138 of 144**
comparisons, and full labels match on 119 of 144 with 21 more being E.S. gear
that we deliberately leave unnamed.

**E.S. gear ids are NOT resolved.** The category is certain but the id space does
not line up with `x2_es_equip.json` at any constant offset — the guide's pairs
imply +1, +4, +7 and +8 for different entries, and that catalog was only ever
confirmed for accessory ids 0-30 anyway. `drop_item_name()` returns None for
category 2 and the front-ends print `E.S. gear #14`. Resolving it needs the disc's
own E.S. item table, not more guide data.

Four residual disagreements, none structural:

- rec 13 Arvakv — guide says `Skill Upgrade C`, disc id 24 resolves to
  `Skill Upgrade B`. **Two independent sources conflict here** and neither was
  changed: our consumable catalog comes from the disc-1 pnach (id 23 =
  Skill Upgrade B, "+30 SP") and has a *gap* at id 22, while the guide's drop ids
  imply catalog 22 = Skill Upgrade B and 23 = Skill Upgrade C. One of them is off
  by one in this narrow range. Everything either side of it agrees, so this is
  recorded rather than "fixed".
- rec 18 Wraith Feeler — guide lists EMAX300 / Auto Recover, the disc row is all
  zeros. Likely the guide describing a different encounter of the same statline.
- rec 51 Executus Sagitta — guide says no rare drop, the disc has one.

### 2026-08-23 — DAMAGE AFFINITIES SOLVED (and the old +0x04 slots were wrong)

**`+0x58`, eight SIGNED bytes, percent = byte × 5.** Element order is the guide's
column order: **Beam, Aura, Thunder, Fire, Ice, Pierce, Slash, Hit**.
100% normal, below resists, above takes extra, 0% immune, **negative absorbs**.
Verified against 71 guide entries with complete damage rows: **71/71 exact**.
Byte-identical on both discs.

Values seen on disc: -200, -100, 5, 25, 50, 75, 85, 100, 110, 115, 120, 125, 135,
150, 175, 200, 225, 250, 300, 400 — all multiples of 5, which is what the ×5
encoding buys. Only one record is outside the guide's range (rec 5 Svarozic,
-200% Fire: it heals for double).

#### The block straddles the record boundary

Enemy `i`'s eight bytes live at `base + i*0x5C + 0x58`, which runs **four bytes
past** the nominal `0x5C` record. So a block is the last four bytes of record `i`
plus the first four of record `i+1`. Verified, not assumed: record `i`'s
`+0x00..0x03` equals enemy `i-1`'s elements 4..7 for **all 124 pairs**, and the
value distribution there is affinity-shaped (20=100%, 15=75%, 236=-100%). The
last record's block lands in the 52-byte gap before the name table, and reads a
flat 100%.

Consequences:
- `+0x00..0x03` was never "unknown" — it is affinity data. Removed from
  `F.ENEMY_UNMAPPED`, which is now 52 bytes.
- No special handling is needed for read/write (the path computes `base + off`,
  so offsets `0x58..0x5F` address the right bytes), but **a scanner that slices
  `0x5C` per record cannot see a whole block** — which is exactly why the
  in-record search for these percentages came up empty at first.
- A write to enemy `i` touches bytes inside record `i+1`. Pinned by tests in both
  `x2selftest.py` and `tests/test_patch.py`.

#### The previous definition was wrong

Up to v1.4.0 this project exposed eight "damage affinity" slots at **`+0x04`**,
labelled `Aff1..Aff8` behind an opt-in and documented as "unverified". They are
not affinities at all: `+0x04..+0x0B` reads `0x64 0x64 …` (100%) in **124 of the
125 records**, with ASCII in the one exception. A field that never varies per
enemy cannot be the per-enemy table the guide describes — and the guide's rows
vary heavily (70 of 72 mapped entries have a non-100 value), which is what
exposed it. `+0x04..+0x0B` is now recorded as a constant block of unknown
purpose (`F.ENEMY_CONST64_OFF`) and is no longer editable.

Lesson: "unverified" was too generous a label. The slots had the right *shape*
(eight bytes, 100 in vanilla) and that was mistaken for weak evidence of the
right *identity*. Shape is not identity — the guide data was available the whole
time and would have falsified it immediately.

### 2026-08-23 — Multi-disc editing

`F.enemy_tables(disc)` gives per-disc bases; `x2patch.sync_discs(src, dst)` copies
every verified field (stats, rewards, affinities, zone mask, break sequence) from
one disc to the other, and is the single primitive behind both
`x2patch.py sync` and `--also` on `rebalance` / `apply-patch` / `restore` /
`enemy-set`. The web editor loads both discs and mirrors one edit buffer into
each disc's own bases on save, so there is no code path that can write different
values to the two discs; if the two images disagree when the second one opens, it
says so and makes the user pick a source of truth rather than guessing.

#### Exposed as

`F.ZONE_FIELDS` (`Zones`, `Brk1..Brk4`), with `F.encode_break_seq()` /
`F.decode_break_seq()` / `F.zone_mask_text()` as the codec. Editable from the CLI
(`x2patch.py enemy-set <iso> <n> --break CBB`, and `enemy` prints both fields),
from the web ISO editor (one text box over the four bytes, with a warning if the
sequence uses a zone the enemy lacks), and through patch files. Covered by the
`break sequence round-trips through the record` self-test check.

Shortening a boss's 4-hit sequence is the single largest cut available to how long
a fight drags — it is the combo loop's actual gate, not a stat multiplier.

#### Still undecoded in the record

`+0x00..0x03`, `+0x0C..0x35`, `+0x47..0x4B`, `+0x4D..0x51`, `+0x58..0x5B`
(`F.ENEMY_UNMAPPED`, now 60 bytes). One strong lead: **`+0x10..+0x19` is very
likely the guide's ten STATUS RESISTANCE percentages** — ten consecutive bytes
whose values cluster on 0/10/20/25/50/60/120, exactly the shape of that table.
Not verified, so not exposed; the same signature method would confirm it.

### Overlay load map + PCSX2 prep (2026-08-24)

Both overlays are ELFs with a single RWX `PT_LOAD`, so their runtime EE address
is known statically:

| overlay | file offset | VA | size |
|---|---|---|---|
| `OV01.OVL` (battle) | `0x1000` | **`0xA80000`** | `0x6D088` (446,600 b) |
| `OV02.OVL` | `0x0` | **`0xA7F000`** | `0x4E28` |

`va -> file` = `va - 0xA80000 + 0x1000`.

**The disc-1 pnach hands us free anchors.** Several of its codes poke addresses
that land inside OV01's loaded range — `0xAC2460` (documented as the battle
"Event Slot", whose listed effects include *1 = BST / Accelerated Boost Gauge*),
`0xAC2478`/`247C`/`2480` (Exp / Skill Points / Class Points accumulators), and
the item-use guards around `0xA9C768`. Someone already found the battle-state
struct empirically; we just have to read code near it.

`Research/x2disasm.py` (capstone; outside `Editor/` so the engine stays
stdlib-only) disassembles the overlay — 111,650 instructions, 2,284 candidate
functions, 805 reconstructed data addresses — reconstructs `lui`/`lo` address
pairs, and reports which functions touch each anchor. `Research/OV01_map.md` is
the generated output, with a shortlist of the densest battle-state functions
(`0xA8A8B8`, `0xA914DC`, `0xA91624`, …) to break on first.

**This is scaffolding, not a result** — nothing in it is verified against a
running game.

### Tier 2 — BLOCKED on runtime: the global battle constants

Stock cap, boost cost/regen, the ×1.5 break multiplier, AIR/DOWN doubling, and —
the big one — **break expiring at end of turn** are code, not table data. They
live in the boot ELF or the battle overlays (`OV01.OVL` 450 KiB, `OV02.OVL`).
Static disassembly is possible (we have the va→file map), but the cheap unblock
is one PCSX2 session: EE RAM search on the boost/stock value during a battle →
write breakpoint → map back through the load map. That's the *same* session that
unblocks the save checksum, party, inventory and growth tables — bundle them.

### Self-test

`python3 x2selftest.py` builds a synthetic 34 MB disc (sparse) carrying only the
verified structures, populated from `x2_enemies.json`, and exercises the table
locator, the rebalance planner/writer and the zone scanner against it — including
a *planted* zone column at `+0x2A` so the scanner has a known right answer. It
proves the code paths, not the game facts. No game data required, so CI-safe.
