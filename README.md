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
  **`.sps`/`.xps`** (SharkPort), and **`.cbs`** (CodeBreaker). Powered by the real Python
  engine compiled to WebAssembly (Pyodide).
- **ISO Editor** (desktop Chrome/Edge/Brave/Opera) — edit every enemy's **stats** (HP, STR,
  VIT, EATK, EDEF, DEX, EVA, AGL) and **battle rewards** (EXP, SP, CP) for all 125 enemy
  records, written **in place** into your disc image — plus one-click **battle-pacing
  profiles** that retune what the stock→break→boost combo loop costs, **shareable patch
  files**, and a **compare-to-retail** view that shows exactly how your disc differs from an
  unmodified one (and can restore it).
- **Reference** — searchable bestiary (verified stats & rewards, filter by ID band or major
  fights, sort, CSV export) + item / key-item / E.S.-gear catalogs extracted from the disc.
- **Reopen recent** — your last save *and* last ISO are remembered, so a return visit is one
  tap. On desktop the writable file handle is kept too, so a reopened file still saves **in
  place**. Stored locally in your browser (IndexedDB); the ISO entry keeps only a file
  reference, never disc contents.

> Supply your own legally-obtained saves/ISOs. The repo ships **no game data**.

## Status

Working today:

- **Save editing** — gold + the full character sheet, across every common container
  including PCSX2 memory-card images (one entry per in-game slot).
- **ISO enemy editing** — stats and rewards for all 125 records, battle-pacing profiles,
  patch files, and comparison against the retail values (with restore). The enemy tables are
  verified against two independent sources: 74/76 enemies from a strategy guide match the
  disc **exactly** on an 8-field signature (see
  [`Editor/Xenosaga2_ISO_offsets.md`](Editor/Xenosaga2_ISO_offsets.md) for the derivation).
- **Reference** — bestiary + item / key-item / E.S.-gear catalogs.

Two caveats worth stating plainly:

- The in-game **save checksum isn't cracked yet**, so an edited *save* may be rejected by
  the game until it is. ISO edits are unaffected. A `.bak` is always kept.
- The eight enemy **damage-affinity slots** are editable but **unverified** — we know there
  are eight percentages and that they read 100 in ordinary records, but not which element
  each slot is, so they're numbered rather than named and kept behind an opt-in.

Next reverse-engineering targets: the save checksum, skill/tech editing (power, targeting,
cast times), party, and inventory — all of which need a PCSX2 session to anchor. `.max`
(AR Max / LZARI) is the one container still unsupported.

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
late-game field enemies in with bosses. Debug/unused records are never touched. Next up:
the per-enemy **weak-zone/break data** — the scanner for it is written and waiting on a
disc (`x2patch.py enemy-columns` / `find-zones`, see the notes).

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
python3 x2patch.py rebalance "../ISO/...iso" --profile faster --dry-run
python3 x2patch.py diff "../ISO/...iso"                      # how it differs from retail
python3 x2patch.py export-patch "../ISO/...iso" --out mod.json
python3 x2patch.py apply-patch "../ISO/...iso" mod.json      # share a rebalance
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
web/            hosted browser PWA (Pyodide save editor + ISO enemy editor + reference)
Editor/
  x2editor.py   local web app (desktop)
  x2save.py     save engine (container decode + edit, gamedata layout)
  x2mc.py       PS2 memory-card filesystem (PS2MFS + ECC) and .psu containers
  x2patch.py    ISO engine + CLI (verify / extract / enemy read-write / rebalance / zone hunt)
  x2fields.py   verified offsets + schema + battle-pacing profiles
  x2selftest.py engine self-test against a synthetic disc (needs no game data)
  gen_web_tables.py  generates web/tables.json from x2fields (CI checks for drift)
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

Made by **Sparda**. · **v1.3.0** — see [Releases](https://github.com/TheSparda/Xenosaga-2-Editor/releases).
