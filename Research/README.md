# Research

Tooling and findings that are **not** part of the shipped editor.

The engine in `Editor/` is stdlib-only on purpose and CI enforces it. Anything
needing a third-party package lives here instead.

| file | what |
|---|---|
| `x2r5900.py` | R5900 decode shim over capstone MIPS64 — the other tools import this |
| `x2damage.py` | locates and annotates the damage routine (needs `capstone`) |
| `DAMAGE.md` | **the damage formula**, derived statically — no emulator |
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

Nothing in `OV01_map.md` is verified against a running game. It is scaffolding
for a PCSX2 session, not a result.

**The damage formula turned out not to need one.** `DAMAGE.md` derives it
statically: the routine is `0xA8C778`, found by following the 9999/99999 clamp
and the divides-by-100, and its stat offsets are confirmed against the disc-1
pnach's per-character stat addresses. That needed one fix first — `x2disasm.py`
decodes as MIPS32 and loses 15% of the overlay, with the holes falling exactly
on the arithmetic. `x2r5900.py` (MIPS64 + the R5900 `mult1`/`div1`/`mflo1`
forms) brings it to 1.8%.

## Regenerating

```bash
pip install capstone
python3 x2disasm.py --iso "../ISO/....(Disc 1).iso" --report OV01_map.md

# damage routine, annotated, plus the anchor survey it was found with
python3 x2damage.py --iso "../ISO/....(Disc 1).iso" --survey
```
