# Xenosaga Episode II — ISO & Save Editor

A save & disc editor for **Xenosaga Episode II: Jenseits von Gut und Böse** (PS2, USA —
2 discs: **`SLUS-20892`** / **`SLUS-21133`**).

## ▶ Use it now (no install)

### **[→ Open the Web Editor](https://thesparda.github.io/Xenosaga-2-Editor/web/)**

Runs entirely **in your browser** — desktop or Android. Your files **never leave your
device** (no server, no upload). It's a PWA, so you can **Install** it and use it offline.

- **Save Editor** (works everywhere, incl. phones) — edit gold and every character's level,
  HP, stats, and E.S. mech gear. Opens **PCSX2 memory-card images** (`.ps2`/`.mcd`) and lets
  you pick which in-game slot to edit, plus **`.psu`** (EMS), **`.psv`** (PS3),
  **`.sps`/`.xps`** (SharkPort), **`.cbs`** (CodeBreaker) and **`.max`** (AR Max / MAX
  Drive). Powered by the real Python engine compiled to WebAssembly (Pyodide).
- **ISO Editor** (desktop Chrome/Edge/Brave/Opera) — **open both discs and edit them as
  one**, in six tabs:
  - **Enemies** — **stats** (HP, STR, VIT, EATK, EDEF, DEX, EVA, AGL), **battle rewards**
    (EXP, SP, CP), **damage affinities**, **status resistances**, **item drops**, **Break
    sequences** and **breakable zones** for all 125 records, with search, one-click
    **battle-pacing profiles**, **bulk Break shortening**, and **JSON export/import** for
    spreadsheet-scale edits.
  - **Skills** — **name, EP cost, target (single ↔ AoE), power and element** for all **176**
    verified skill records: Ether, Double and Dual skills, every character's single techs,
    E.S. attacks and Special attacks — each shown by its in-game name.
  - **Passives** — the **equip skills**: the ten Guards, the eight Coats, HP/ST Mind,
    Break B10/B15, Rare+10/+30, the +2 stat skills and the rest, across **64** verified
    12-byte records. Retune what a passive is worth — its magnitude, or for the Coats and
    Guards the element/status it resists, shown as checkboxes rather than a raw mask — and
    rename it. About a quarter of them read zero across the effect field because their
    behaviour is battle code rather than table data; the editor says so instead of offering
    a number that does nothing.
  - **Gear** — the **E.S. accessory effects**: Auxiliary Armor, the EF Circuits, the four
    Anti-element Armors, the thirteen G-guards — all 31, each named from the disc's own
    catalog. Turn Auxiliary Armor A's +30 Arm into +90, or repoint an Anti-Fire Armor at
    Ice. Names are read-only here (E.S. equipment names resolve through menu code, not a
    pointer table), so an accessory keeps its old name when you change what it does.
  - **Costs** — what each skill costs in **Skill Points** to learn, for all **112**
    purchasable skills, grouped the way the game groups them: auto skills, equip skills and
    ethers. The other half of skill pacing from the SP the Enemies tab hands out — make a
    late-game ether cheap, or price a strong one out of reach. Verified against the
    walkthrough's class tree, 112 of 112.
  - **Units** — the **new-game starting stats** (HP, EP, STR, VIT, EATK, EDEF, DEX, EVA,
    AGL) for every character and E.S. unit, plus their eight **damage affinities** — give a
    character a fire weakness or beam immunity. Verified against the save format, which
    copies these stats in at join time.

  Everything writes **in place** into your disc images. Share your work as a readable
  **patch file** or a standard **`.xdelta`** patch, and use **compare-to-retail** to see
  exactly how your disc differs from an unmodified one — across *every* editable field —
  and to put it back.
- **Reference** — searchable bestiary (verified stats & rewards, filter by ID band or major
  fights, sort, CSV export) + item / key-item / E.S.-gear catalogs extracted from the disc.
- **Reopen recent** — your last save *and* last ISO are remembered, so a return visit is one
  tap. On desktop the writable file handle is kept too, so a reopened file still saves **in
  place**. Stored locally in your browser (IndexedDB); the ISO entry keeps only a file
  reference, never disc contents.

> Supply your own legally-obtained saves/ISOs. The repo ships **no game data**.

## Status

Working today:

- **Save editing** — gold + the full character sheet, across **every** common container:
  PCSX2 memory-card images (one entry per in-game slot), `.psu`, `.psv`, SharkPort,
  CodeBreaker and AR Max `.max`.
- **ISO enemy editing** — stats, rewards, drops, affinities, resistances and **Break
  sequences** for all 125 records, on **both discs**, plus battle-pacing profiles, patch
  files, and comparison against the retail values (with restore). The enemy tables are
  verified against two independent sources: 74/76 enemies from a strategy guide match the
  disc **exactly** on an 8-field signature, and all 46 published Break sequences decode
  exactly from the disc bytes (see
  [`Editor/Xenosaga2_ISO_offsets.md`](Editor/Xenosaga2_ISO_offsets.md) for both derivations).
- **ISO skill editing** — EP, element, power and the status-effect fields for the 86 Ether
  and Double skills, in the web editor as well as the CLI.
- **Reference** — bestiary + item / key-item / E.S.-gear catalogs.

Two things worth stating plainly:

- The in-game **save checksum isn't cracked yet**, so an edited *save* may be rejected by
  the game until it is. ISO edits are unaffected. A `.bak` is always kept.
- Nothing is written to the undecoded bytes — which matters more than it sounds: the
  character and E.S. name table physically occupies the leading bytes of enemy record 0.
- The enemy record now has **50 undecoded bytes**, down two: `+0x50` carries the enemy
  **type** (Bio / Gnosis / Mechanism) and `+0x51` bit 3 is the **zone-targeting** flag that
  decides breakability. Both are editable, and both match a strategy guide 57/57 on both
  discs.

Next reverse-engineering targets: pairing the **tech blocks** with their name pools and
working out their record layout (mapped but unexposed), then the save checksum, party, and
inventory, which need a PCSX2 session to anchor.
**Every common save container is now supported**, `.max` included.

### Both discs, edited as one

Xenosaga II ships on two discs and **both carry the same enemy tables** — so an edit made to
one alone silently reverts to retail values at the disc swap, giving you a retuned first half
and a stock second half with nothing to warn you.

The editor handles this for you: open both discs and every change is written to each of them
at its own offsets. There is one set of values being edited, mirrored on save, so the two
discs cannot drift apart. If you open a second disc that *already* disagrees with the first
(because it was patched on its own earlier), the editor says so and asks which disc's values
to keep rather than guessing. You can also deliberately target a single disc.

On the command line the same thing is one flag — `--also` — or the `sync` command:

```bash
python3 x2patch.py rebalance "…(Disc 1).iso" --profile faster --also "…(Disc 2).iso"
python3 x2patch.py sync "…(Disc 1).iso" "…(Disc 2).iso"     # copy tables disc-to-disc
```

### Skill editing

**86 skills are numerically editable** — EP cost, power, element and status-effect fields —
covering the 57 castable ethers (Medica through Erde Kaiser Fury) and the 29 combo/double
skills (Double Medica through Pocket Rare), on both discs.

```bash
python3 x2patch.py skills --grep storm                    # browse, with numbers
python3 x2patch.py skill-set "…(Disc 1).iso" 79 --set Power=50 --set EP=2 --also "…(Disc 2).iso"
```

The numeric table hid through two releases because the skill index space contains
placeholder entries that our catalog silently skipped — with the true indices rebuilt, the EP
costs printed in the skills' own descriptions matched a 32-byte-stride column **56 of 56** on
the first scan. The doubles block was then confirmed independently: each double's EP equals
that of the base skill named in its own description, **25 of 25**.

The wider region holds more blocks of the same size — per-character techs, two-character
combination attacks, E.S. craft techs — and they're mapped in the notes. They are *not*
editable, on purpose: the 32-byte stride is shared but the field layout is not, so the combo
block would read as sixteen identical 20-power skills under the ether layout. The editor
refuses to address anything outside the two verified blocks. The full 174-skill text catalog
(targeting, range, element, descriptions) is browsable regardless.

### Status resistances

Every enemy carries a percentage per status effect — **Slow, Blind, Heavy, Weak, EthPD,
EthDD, ResDw, Junk** — and all eight are editable. They're verified against a strategy
guide at 98.6% agreement (479 of 486 published values). The block holds three more bytes
we haven't identified, so they aren't shown.

### Bulk editing

Two ways to change a lot at once:

- **Shorten every Break sequence** by 1–3 hits, from the battle-pacing card. Before you press
  anything it compares all three options side by side — how many enemies each touches, what
  each length becomes (`4→3  3→2  2→1`), and the total break hits a full pass through the
  bestiary costs — then lists every affected enemy. Trimming takes hits off the *end*, so the
  opening zone you already know stays right, and an already-unbreakable enemy is never
  touched — including the 15 whose sequence bytes are *inert* because zone targeting is off
  for them, which the game never reads.

  A **"Keep every enemy breakable"** shield is on by default. Emptying a sequence doesn't
  shorten the break, it *removes* it, and with the shield off `−2 hits` would strand 84 of
  the 125 records. You can turn it off deliberately; the editor then tells you exactly how
  many enemies it would make unbreakable.
- **Export the whole table as JSON**, edit it in a text editor or spreadsheet, and import it
  back. Values are in readable units — affinities as signed percentages, Break as zone
  letters, drops with the item name alongside the id. Import is strict on purpose: an unknown
  element, an off-step percentage or a bad zone letter is rejected with the enemy named,
  rather than half-applied.

```bash
python3 x2patch.py shorten-breaks "…(Disc 1).iso" --steps 1 --dry-run
python3 x2patch.py export-json "…(Disc 1).iso" --out enemies.json
python3 x2patch.py import-json "…(Disc 1).iso" enemies.json --dry-run
```

### Item drops

Each enemy has a common and a rare drop — a percentage, a category, and an item — and all of
it is editable. Both slots are picked **by name**, consumables and E.S. gear alike, because a
raw id is meaningless on its own: the disc keeps one unified item table and each category
indexes it from its own base, so the same number means different items depending on which
category it sits in. Changing the category reloads the item list.

Drop rates are verified against the guide on 138 of 144 comparisons. The catch that hid the
id space for a while is that the table contains thirteen unused "spare" slots which still
occupy ids — skip them and every id past the first block drifts.

### Damage affinities

Every enemy has eight per-element damage multipliers — **Beam, Aura, Thunder, Fire, Ice,
Pierce, Slash, Hit**. 100% is normal, lower resists, higher takes extra, **0% is immune** and
**negative absorbs** (Svarozic takes −200% Fire, so fire heals it for double). They're stored
as a signed byte ×5, so values move in 5% steps.

These were previously shipped as eight unnamed, unverified slots — and they were reading the
wrong bytes entirely, so editing them did nothing. Both the location and the element order
are now verified against a strategy guide: 71 of 71 enemies with complete published data
match the disc exactly.

### Battle pacing (the combo system)

Ep. II's stock→break→boost loop is the only efficient way to fight, and running the whole
ritual for every enemy is what makes battles drag. The loop's *rules* live in code we
haven't located yet, but what it **costs** is enemy tuning we can write, so the editor
ships four profiles over the verified tables:

| Profile | What it does |
|---|---|
| **Faster fights** | Keeps the loop, cuts the tax — fewer stocked chains per kill, quicker skill unlocks. The safe default. |
| **Freer play** | Softer enemy defenses so off-combo attacks actually land. |
| **Deeper challenge** | Enemies hit harder and last longer, but pay out much more. |
| **Reward-only** | Fights exactly as designed; only the grind between them goes. |

Records are grouped by their own HP (20,000+ = "major"), because the enemy ID band mixes
late-game field enemies in with bosses. Debug/unused records are never touched.

**Break sequences are editable too** — the combo loop's actual gate, rather than a stat
multiplier. Every enemy stores the zones you must hit *in order* to Break it (zones are
attack heights: **A** above 3 m, **B** 1–3 m, **C** below 1 m), and the editor exposes it as
a short text box: turn a boss's `C-B-A-A` into `C-B`. Shortening a 4-hit sequence is the
single biggest cut to how long a fight drags. Clearing a sequence outright is possible but
guarded, because it removes the Break rather than shortening it. The field was found by mapping a guide's published sequences onto records by exact
stat signature — all 46 of them decode from the disc bytes exactly.

### Sharing your work

Two formats, for two jobs.

A **patch file** names fields — `record 6, HP, 22400 → 1337`. It's readable, it applies onto
a disc that already has other edits, and every field is validated before anything is written.
That's the one to share, and it's interchangeable between the web editor and the CLI.

An **`.xdelta` patch** is bytes at offsets — it can carry anything, and any VCDIFF decoder
applies it:

```bash
xdelta3 -d -s "<pristine ISO>" patch.xdelta out.iso
```

The CLI diffs two images with `xdelta3`. The web editor never holds a pristine copy, so it
*synthesizes* the patch from the byte runs it already has staged — nothing reads the 4.6 GB
image, and a handful of field edits comes out around 14 KB. Two consequences, which the
editor states rather than leaving you to find out: it's **one patch per disc** (disc 2's
tables sit `0x800` lower, so one file can't serve both), and there's **no integrity
checksum**, because computing one means hashing the whole disc — apply it only to a pristine
image, or use the patch-file format, which is source-verified.

```bash
python3 x2patch.py xdelta-make "…edited.iso" --pristine "…pristine.iso" --out mymod.xdelta
python3 x2patch.py xdelta-apply mymod.xdelta --pristine "…pristine.iso" --out patched.iso
```

### Import a mod — "balance my game like HardType"

Difficulty mods for this game ship as **PPF** patches. Instead of applying one
blind, the editor imports it: **⬆ Import .ppf** parses the patch, stages every
byte that lands in a table it maps — enemy stats, rewards, drops, units, all 176
skill/tech records, the 64 passive/equip records, the 31 E.S. accessory
effects, the 112 skill purchase costs, and skill names and descriptions — and
tells you exactly what it could not reach. Nothing writes
until you review and Save, the
staged values show up field-by-field in every tab with retail comparison intact,
and you can tweak them before committing. The CLI equivalent:

```bash
python3 x2patch.py apply-ppf "…(Disc 1).iso" mod.ppf --dry-run
```

On Landon Ray's XS2HT v3.9 Hard, the web editor stages **652 of 661** records.
The nine it does not are duplicate copies of a renamed skill's *battle caption*,
which live outside every table and are found by scanning the image instead — the
command line does apply those, so `apply-ppf` reaches all 661. The only
user-visible difference is that a skill renamed in the web editor keeps its
retail name in battle captions.

You do not need the `.ppf` to get this one: the ISO editor ships **HardType
(Normal)** and **HardType (Hard)** as one-click presets, generated from the
mod's own patches, staged for review exactly like an import.

### What does someone else's mod change?

The reliable way to know whether this editor can reproduce a third-party mod is
to read its bytes, not its description:

```bash
python3 x2patch.py explain-diff "…hardtype.iso" --pristine "…pristine.iso" --verbose
```

It walks every differing byte run and says which table it lands in, down to the
record and field:

```
6 changed byte run(s), 23 byte(s) total

  unmapped            1 run(s)        16 byte(s)   <-- this editor cannot reach it
  enemy stats         2 run(s)         3 byte(s)
  skill blocks        2 run(s)         2 byte(s)
  enemy rewards       1 run(s)         2 byte(s)

enemy stats:
  0x001FFF84E     2B  record 6 HP
  0x001FFF869     1B  record 6 NoZone
```

The **unmapped** bucket is the useful part — it's either a table nobody has
decoded yet or it's code, and either way it's the honest answer to "can we edit
everything this mod does?". A full 4.6 GB comparison takes about 8 seconds.

## Desktop app (optional)

Prefer a local app? The same engine runs as a small local web app (Python stdlib only, no
`pip install`):

- **macOS:** double-click `Start Editor (Mac).command`
- **Windows:** double-click `Start Editor (Windows).bat`
- **Any:** `cd Editor && python3 x2editor.py` → opens `http://127.0.0.1:8748/`

CLI bits:
```bash
cd Editor
python3 x2patch.py verify "../ISO/....(Disc 1).iso"          # identify a disc
python3 x2patch.py verify-tables "../ISO/...iso"              # confirm/locate the enemy table
python3 x2patch.py enemies "../ISO/...iso" --csv             # dump the bestiary
python3 x2patch.py enemy "../ISO/...iso" 6                   # stats, drops, zones, Break, affinities
python3 x2patch.py enemy-set "../ISO/...iso" 6 --break CB     # shorten Perun's Break sequence
python3 x2patch.py rebalance "../ISO/...iso" --profile faster --dry-run
python3 x2patch.py diff "../ISO/...iso"                      # how it differs from retail
python3 x2patch.py export-patch "../ISO/...iso" --out mod.json
python3 x2patch.py apply-patch "../ISO/...iso" mod.json      # share a rebalance
python3 x2patch.py sync "../ISO/...(Disc 1).iso" "../ISO/...(Disc 2).iso"
python3 x2patch.py restore "../ISO/...iso"                   # back to retail values
python3 x2save.py slots "…/Mcd001.ps2"                       # list a card's saves
python3 x2save.py "…/Mcd001.ps2" --slot 2                    # decode one of them
python3 x2save.py set "…/BASLUS-….PSV" --gold 9999999 --char 0 --level 99 --hp 9999
python3 x2selftest.py                                         # engine self-test, no game data
```

Tests (stdlib `unittest`, no game data needed):
```bash
cd tests && python3 -m unittest discover
```

## Layout

```
web/            hosted browser PWA (Pyodide save editor + ISO enemy/skill editor + reference)
  vcdiff.js     VCDIFF (.xdelta) encoder, shared with the Node tests
  tests/        front-end tests (VCDIFF round-trip, incl. a cross-check vs real xdelta3)
Editor/
  x2editor.py   local web app (desktop)
  x2save.py     save engine (container decode + edit, gamedata layout)
  x2mc.py       PS2 memory-card filesystem (PS2MFS + ECC) and .psu containers
  x2lzari.py    LZARI codec for AR Max (.max) saves
  x2patch.py    ISO engine + CLI (verify / extract / enemy + skill read-write / rebalance /
                patches / xdelta / zone hunt)
  x2fields.py   verified offsets + schema + battle-pacing profiles
  x2selftest.py engine self-test against a synthetic disc (needs no game data)
  gen_web_tables.py    generates web/tables.json from x2fields (CI checks for drift)
  gen_enemy_catalog.py rebuilds the retail baseline from both discs (cross-checked)
  x2_*.json     reference data (items / key items / E.S. gear / verified bestiary)
  x2_zones_template.csv      ground-truth template for the weak-zone hunt
  Xenosaga2_ISO_offsets.md   reverse-engineering notes
tests/          synthetic-fixture test suite (no game data)
```

## Privacy & scope

Everything runs on your device — nothing is uploaded, ever. The repository contains **no game
ROM/ISO, saves, or audio** — only small reverse-engineered reference data (serials, offsets,
id→name maps, item descriptions) the editor needs to show meaningful labels. That's
interoperability data, not the game.

Made by **Sparda**. · **v1.10.0** — see [Releases](https://github.com/TheSparda/Xenosaga-2-Editor/releases).
