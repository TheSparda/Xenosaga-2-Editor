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
  records, written **in place** into your disc image — plus **rebalance presets** for the
  game's infamous HP bloat, **shareable patch files**, and a **compare-to-retail** view that
  shows exactly how your disc differs from an unmodified one (and can restore it).
- **Reference** — searchable bestiary (verified stats & rewards, filter by field/boss/E.S.,
  sort, CSV export) + item / key-item / E.S.-gear catalogs extracted from the disc.

> Supply your own legally-obtained saves/ISOs. The repo ships **no game data**.

## Status

Working today: **save editing** (gold + full character sheet, all containers), **ISO enemy
stat/reward editing + global rebalance**, and the **Reference** bestiary. The enemy tables
are verified against two independent sources — 74/76 enemies from a strategy guide match the
disc **exactly** on an 8-field signature (see
[`Editor/Xenosaga2_ISO_offsets.md`](Editor/Xenosaga2_ISO_offsets.md) for the derivation).

One caveat — the in-game **save checksum isn't cracked yet**, so an edited *save* may be
rejected by the game until it is (ISO edits are unaffected). Skill/tech editing (power,
targeting, cast times), party, and inventory editing are the next reverse-engineering
targets.

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
python3 x2patch.py enemies "../ISO/...iso" --csv             # dump the bestiary
python3 x2patch.py rebalance "../ISO/...iso" --hp 50 --dry-run
python3 x2patch.py diff "../ISO/...iso"                      # how it differs from retail
python3 x2patch.py export-patch "../ISO/...iso" --out mod.json
python3 x2patch.py apply-patch "../ISO/...iso" mod.json      # share a rebalance
python3 x2patch.py restore "../ISO/...iso"                   # back to retail values
python3 x2save.py slots "…/Mcd001.ps2"                       # list a card's saves
python3 x2save.py "…/Mcd001.ps2" --slot 2                    # decode one of them
python3 x2save.py set "…/BASLUS-….PSV" --gold 9999999 --char 0 --level 99 --hp 9999
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
  x2patch.py    ISO engine + CLI (verify / extract / enemy read-write / rebalance)
  x2fields.py   verified offsets + schema
  x2_*.json     reference data (items / key items / E.S. gear / verified bestiary)
  Xenosaga2_ISO_offsets.md   reverse-engineering notes
tests/          synthetic-fixture test suite (no game data)
```

## Privacy & scope

Everything runs on your device — nothing is uploaded, ever. The repository contains **no game
ROM/ISO, saves, or audio** — only small reverse-engineered reference data (serials, offsets,
id→name maps, item descriptions) the editor needs to show meaningful labels. That's
interoperability data, not the game.

Made by **Sparda**. · **v1.1.0** — see [Releases](https://github.com/TheSparda/Xenosaga-2-Editor/releases).
