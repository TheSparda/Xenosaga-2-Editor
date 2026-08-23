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
+0x23..0x32  constant per-character config (0x14 x8, 0x64 x8 — affinities/base tech)
+0x33..    growing list of learned tech/skill ids (0x1D,0x1E,0x1F,... as you level)
+0x5C u16  CURRENT HP       (== base HP early; exceeds it later via gear bonus)
+0x60 u16  unknown, level-correlated
```
The 0x108 record is now essentially fully mapped. **Total EXP is NOT in the record** —
EXP-to-next lives in a separate RAM table (pnach 0x61C4xx), not yet located in the save.

### Containers supported (x2save.py)
psv (full), **sharkport `.sps/.xps`** (uncompressed), **cbs** (RC4+zlib) — all decode to
the same 20,832-byte gamedata. `.max` (Ps2PowerSave/LZARI) still TODO. WRITE is PSV-only
so far (sharkport/cbs need splice-back + their own container checksums).

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
**Not cracked yet, but pinpointed.** Ruled out (all 20 saves): CRC-32 (16 variants, LE/BE),
byte/u16/u32 sums, sum-to-const, Adler/Fletcher, truncated MD5/SHA, and the LCG-hash family
— so it's a bespoke routine inside the save serializer. Two ways to finish it:
1. **Runtime (fast):** in PCSX2, set an **EE write breakpoint on 0x695168** (the gamedata
   buffer's checksum word), trigger an in-game save; the game halts on the exact store
   instruction inside the checksum routine — read that loop and implement `fix_checksum()`.
2. **Static:** disassemble the 0x186000–0x188900 serializer for the loop that writes
   `buffer+8`; slower (blind) but doable.
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

### E.S. weapon/frame ids 31+ (blocked on menu-code disassembly)
Attempted to map ids 31-37 to names via the equipment data table. A base-independent
delta search (match the accessory name-offset delta sequence, any base K, word-strides
1-23) over ISO 0x2000000-0x2040000 found **no pointer/record array** — so equipment
names are resolved by **string index in the menu code**, not a scannable file-offset
table. The weapon/frame names exist (~0x20107F0: MINIGUN, MICRO MISSILE, DRAGON BLADE,
X-BUSTER, Moonlight Blade, Corona/Odin Buster, ...) but their id↔name mapping needs
either ELF disassembly of the equip menu or a ground-truth save. Left raw in the editor.

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
- No name anchor (unlike the enemy table, whose adjacent name table let us align it).
- No code anchor: the ELF has no shop/buy/sell/growth strings to disassemble from, and
  this data is **dynamically loaded from XENOSAGA.01** (not at a static ELF address), so
  there's no straightforward disassembly entry point.
Realistically these need the running game (PCSX2 RAM search + write-breakpoints) — the
same tool gap as the save checksum/party/inventory. Deferred until PCSX2 is available.

### ISO TODO
- [ ] Find on-foot accessory storage (candidate record slots were constant in samples).
- [ ] Map the pointer/index tables so every string gets an authoritative id.
- [ ] Locate editable ISO tables (character growth, enemy stats, shops) for new-game edits.
- [ ] Translate the pnach EE addresses through the ELF load map to locate the static tables
  on-disc (characters, techs/ether, gear, enemies, shops, text).
- [ ] Build `x2fields.py` schemas as each table is verified byte-for-byte.

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

### Tier 0.5 — OPEN QUESTION: does disc 2 carry these tables?

Never checked, and it matters: if `SLUS-21133` has its own copy, a rebalance
applied to disc 1 alone silently stops working at the disc swap.
`x2patch.py verify-tables <iso>` answers it — it confirms the known base first,
then signature-scans the whole image (one pass, all anchors at once) for the
17-byte `+0x36..+0x46` run and reports any base it finds. **Run it on disc 2 and
record the result here.**

### Tier 1 — READY TO RUN: find the break/weak-zone field

The 0x5C stat record still has **65 undecoded bytes** (`+0x00..0x03`,
`+0x0C..0x35`, `+0x47..0x51`, `+0x54..0x5B` — `F.ENEMY_UNMAPPED`), and the
guides publish a weak-zone sequence per enemy. That's the same shape of problem
the stat table was, and it doesn't need PCSX2.

The method avoids guessing the encoding entirely: if a byte column *is* the zone
field, enemies sharing a zone string must share its value. So score each column
by how well its value partition agrees with the zone partition —

- `consistency` = P(same value | same zone string) — **must be 1.0** for the field
- `resolution` = P(different value | different string) — <1.0 means the column is
  lossy (a mask or a sequence length, not the sequence itself)

Tools, in the order to run them:

```bash
cd Editor
python3 x2patch.py enemy-columns "../ISO/….(Disc 1).iso" --interesting
#   survey first, no ground truth needed. Flags: P = values decode as packed
#   2-bit zone symbols (1=A,2=B,3=C, 0=pad), N = every nibble <=3, M = all <=7.
#   A low-cardinality column flagged P is the prime suspect.

cp x2_zones_template.csv x2_zones.csv      # then paste guide data into it
python3 x2patch.py find-zones "../ISO/….(Disc 1).iso" --truth x2_zones.csv
#   perfect hit prints the value <-> zone map, i.e. the encoding.

python3 x2patch.py find-zones … --truth x2_zones.csv --region 0x1FF0000,0x40000
#   fallback if the record has no such column: sweeps for a separate
#   parallel-indexed table (the rewards table is laid out that way).
#   Slow path — roughly 3 minutes per 0x40000 x 9 strides.
```

Ground truth needs **30+ enemies, several sharing a zone string** (the shared
ones are the entire signal). One wrong row hides the real column, because the
test demands perfect consistency.

Payoff once found: edit each enemy's break sequence — shorten 4-hit boss
sequences to 2, normalise weak zones so the fight stops being a FAQ lookup, or
randomise them. Break durability, AIR/DOWN susceptibility and the break-bonus
stock grant are likely neighbours in the same record.

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
