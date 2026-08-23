# Xenosaga Episode II — ISO & Save Editor

A save & disc editor for **Xenosaga Episode II: Jenseits von Gut und Böse** (PS2, USA —
2 discs: **`SLUS-20892`** / **`SLUS-21133`**).

## ▶ Use it now (no install)

### **[→ Open the Web Editor](https://thesparda.github.io/Xenosaga-2-Editor/web/)**

Runs entirely **in your browser** — desktop or Android. Your files **never leave your
device** (no server, no upload). It's a PWA, so you can **Install** it and use it offline.

- **Save Editor** (works everywhere, incl. phones) — edit gold and every character's level,
  HP, stats, and E.S. mech gear. Opens **`.psv`** (PS3), **`.sps`/`.xps`** (SharkPort), and
  **`.cbs`** (CodeBreaker). Powered by the real Python engine compiled to WebAssembly (Pyodide).
- **ISO Editor** (desktop Chrome/Edge/Brave/Opera) — edit every enemy's **stats** (HP, STR,
  VIT, EATK, EDEF, DEX, EVA, AGL) and **battle rewards** (EXP, SP, CP) for all 125 enemy
  records, written **in place** into your disc image — plus a one-click **global HP
  rebalance** to fix the game's infamous HP bloat.
- **Reference** — searchable bestiary (verified stats & rewards) + item / key-item /
  E.S.-gear catalogs extracted from the disc.
- **Reopen recent** — your last save *and* last ISO are remembered, so a return visit is one
  tap. On desktop the writable file handle is kept too, so a reopened file still saves **in
  place**. Stored locally in your browser (IndexedDB); the ISO entry keeps only a file
  reference, never disc contents.

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
python3 x2save.py "…/BASLUS-….PSV"                            # decode a save
python3 x2save.py set "…/BASLUS-….PSV" --gold 9999999 --char 0 --level 99 --hp 9999
```

## Layout

```
web/            hosted browser PWA (Pyodide save editor + ISO enemy editor + reference)
Editor/
  x2editor.py   local web app (desktop)
  x2save.py     save engine (psv/sps/cbs containers, decode + edit)
  x2patch.py    ISO engine + CLI (verify / extract / enemy read-write)
  x2fields.py   verified offsets + schema
  x2_*.json     reference data (items / key items / E.S. gear / verified bestiary)
  Xenosaga2_ISO_offsets.md   reverse-engineering notes
```

## Privacy & scope

Everything runs on your device — nothing is uploaded, ever. The repository contains **no game
ROM/ISO, saves, or audio** — only small reverse-engineered reference data (serials, offsets,
id→name maps, item descriptions) the editor needs to show meaningful labels. That's
interoperability data, not the game.

Made by **Sparda**. · **v1.2.0** — see [Releases](https://github.com/TheSparda/Xenosaga-2-Editor/releases).
