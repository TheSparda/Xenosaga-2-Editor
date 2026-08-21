# Xenosaga Episode II ISO & Save Editor

A cross-platform editor for **Xenosaga Episode II: Jenseits von Gut und Böse** (PS2, USA —
2 discs: **`SLUS-20892`** / **`SLUS-21133`**). It runs as a local web app in your browser.
Built in the mold of the [Suikoden III editor](https://github.com/TheSparda/Suikoden-3-Editor):

- **ISO editing** — (planned) rebalance characters, techs/ether, gear, enemies, shops, and
  in-game text directly in the disc image.
- **Save editing** — (planned) open a PS2 save export and edit a playthrough: levels, HP,
  EXP, stats, techs, equipment, party, inventory, gold. **No ISO required.**

Everything runs locally — nothing is uploaded. The repo ships with **no game data**; supply
your own legally-obtained ISOs and/or saves.

> **Status: scaffold.** Disc identification and save inventory work today. The editable
> data tables are still being reverse-engineered — see
> [`Editor/Xenosaga2_ISO_offsets.md`](Editor/Xenosaga2_ISO_offsets.md).

## Run

- **macOS:** double-click `Start Editor (Mac).command`
- **Windows:** double-click `Start Editor (Windows).bat`
- **Any:** `cd Editor && python3 x2editor.py`

Requires Python 3.8+ (standard library only — no `pip install`). Opens your browser at
`http://127.0.0.1:8748/`, which currently lists the discs and saves it finds under the
project folder.

## What works now

```bash
cd Editor
python3 x2patch.py verify "../ISO/....(Disc 1).iso"   # identify a disc (serial/volume)
python3 x2patch.py info   "../ISO/....(Disc 2).iso"
python3 x2save.py ../Saves                            # inventory local saves by format
```

## Layout

```
Editor/
  x2editor.py   local web app (landing page + JSON API; tabs land here as tables are found)
  x2patch.py    ISO engine + CLI (verify / info / find-bytes / dump-region)
  x2save.py     save engine (container sniff + inventory; decode is WIP)
  x2fields.py   verified constants + schema stubs
  Xenosaga2_ISO_offsets.md   reverse-engineering notes
Start Editor (Mac).command / (Windows).bat   launchers
make_release.py   builds a single-file .pyz release (stdlib zipapp)
```

## Privacy & scope

The repository contains **no game ROM/ISO, saves, audio, or story assets** — only small
reverse-engineered reference data (serials, offsets, id→name maps) the editor needs to show
meaningful labels. That's interoperability data, not the game. `ISO/`, `Saves/`, and
`Cheats/` are gitignored and kept local for research.
