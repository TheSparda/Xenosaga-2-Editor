# Research

Tooling and findings that are **not** part of the shipped editor.

The engine in `Editor/` is stdlib-only on purpose and CI enforces it. Anything
needing a third-party package lives here instead.

| file | what |
|---|---|
| `x2disasm.py` | MIPS/R5900 static map of the battle overlay (needs `capstone`) |
| `OV01_map.md` | generated report — load map, anchor cross-references, breakpoint targets |

## Why this exists

The stock/break/boost constants — stock cap, boost cost, the ×1.5 break
multiplier, Break expiring at end of turn — are **code, not table data**. Five
independent data searches came back empty, and the ×1.5 multiplier does not
appear as a float anywhere in either overlay, so it is integer math: an
instruction pattern, not a value any scan can find.

Reading it needs a disassembler with somewhere to start, and "somewhere to start"
was the whole problem: `OV01.OVL` is 446 KB of unsymbolised code with no symbols
and no entry point. This tool turns that into a short list of specific functions,
by leaning on anchors somebody else already found empirically — the disc-1 pnach
pokes several EE addresses that land inside the overlay's loaded range, one of
them documented as the battle Event Slot including an *Accelerated Boost Gauge*
effect.

Nothing in the generated report is verified against a running game. It is
scaffolding for a PCSX2 session, not a result.

## Regenerating

```bash
pip install capstone
python3 x2disasm.py --iso "../ISO/....(Disc 1).iso" --report OV01_map.md
```
