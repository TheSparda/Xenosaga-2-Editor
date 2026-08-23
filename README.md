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
- **Reference** — searchable browser of item, key-item, E.S.-gear, and enemy names/descriptions
  extracted from the disc.
- **ISO Editor** — under active reverse-engineering (see Status).

> Supply your own legally-obtained saves/ISOs. The repo ships **no game data**.

## Status

Working today: **save editing** (gold + full character sheet, all containers) plus the
read-only **Reference** browser. One caveat — the in-game **save checksum isn't cracked yet**,
so an edited *save* may be rejected by the game until it is.

**ISO / balance editing is in progress.** The disc's uncompressed area holds the game's *text*
(names & descriptions), but the numeric *balance* tables (enemy HP/stats, skill power &
targeting, cast times, boost/break rules) are packed inside the large `XENOSAGA.*` archives —
cross-checking against the [xenoserieswiki](https://www.xenoserieswiki.org) and a strategy
guide showed the on-disc stats first found were not the real battle values, so no ISO writer
ships until the packed table is located and edits can be verified. Party, inventory, and
character-growth editing are likewise pending — see
[`Editor/Xenosaga2_ISO_offsets.md`](Editor/Xenosaga2_ISO_offsets.md) for the reverse-engineering
notes.

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
web/            hosted browser PWA (Pyodide save editor + reference browser)
Editor/
  x2editor.py   local web app (desktop)
  x2save.py     save engine (psv/sps/cbs containers, decode + edit)
  x2patch.py    ISO engine + CLI (verify / extract / dump)
  x2fields.py   verified offsets + schema
  x2_*.json     reference data (item / key-item / E.S.-gear / enemy names)
  Xenosaga2_ISO_offsets.md   reverse-engineering notes
```

## Privacy & scope

Everything runs on your device — nothing is uploaded, ever. The repository contains **no game
ROM/ISO, saves, or audio** — only small reverse-engineered reference data (serials, offsets,
id→name maps, item descriptions) the editor needs to show meaningful labels. That's
interoperability data, not the game.

Made by **Sparda**. · **v1.0.1** — see [Releases](https://github.com/TheSparda/Xenosaga-2-Editor/releases).
