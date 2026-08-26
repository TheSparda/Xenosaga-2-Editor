# Xenosaga II damage formula — static derivation from `OV01.OVL`

Derived by reading R5900 code out of the battle overlay on disc 1 (USA,
SLUS-20892). **No emulator was used.** Everything below is either read directly
out of the instruction stream or cross-checked against an independent source;
each claim is labelled with which.

Regenerate the annotated listing with:

```bash
python3 Research/x2damage.py --iso "ISO/....(Disc 1).iso" --survey
```

## How the routine was located

The formula is code, not table data, so no byte scan finds it. Three anchors a
damage routine cannot avoid:

1. **The 9999 / 99999 clamp.** Only two `0x270F` immediates exist in the whole
   overlay. The clamp at `0xA8EC08`–`0xA8EC14` picks `99999` instead of `9999`
   when the unit id at stat-block `+0x62` is in `11..15` — the E.S. units.
2. **Percent math with a real `div`.** The game divides by an immediate `100`
   rather than a magic-number reciprocal, and there are only **51 divides in
   446 KB**. Thirteen sit next to a literal `100`; seven of those are in one
   function.
3. **That function is `0xA8C778`**, which is the only function in the entire
   overlay that calls *any* of the four stat getters — a clean 1-of-1419.

### A tooling prerequisite

`Research/x2disasm.py` decoded the overlay as **MIPS32**, which leaves **15.0%
of it undecoded** — and the holes land precisely on the arithmetic (`daddu`,
`ld`/`sd`, and the R5900's second multiplier pipeline `mult1`/`div1`/`mflo1`).
Decoding as **MIPS64** drops that to 2.0%; `Research/x2r5900.py` adds the
R5900-specific forms and brings it to **1.82%**, the remainder being genuine
data (jump tables, constants).

`x2r5900.py` also *overrides* capstone on one encoding. capstone decodes the
R5900 3-operand `mult rd, rs, rt` but renders `rd` as a DSP accumulator, so
`mult $v0, $v0, $a1` prints as `mult $ac2, $v0, $a1`. Read literally that says
"writes an accumulator" when it actually writes `$v0` — which silently breaks
every formula it appears in. The shim takes precedence for those words.

## The runtime stat block

Every unit has a stat block at `unit + 0x144`. **Verified independently**: the
disc-1 pnach's per-character stat codes give Shion's block, and the offsets the
getters read are exactly the pnach's:

| offset | field | pnach (Shion) |
|---|---|---|
| `+0x02` | HP, u32, **unaligned** (read via `lwl 5`/`lwr 2`) | `0x61B592` |
| `+0x08` | **STR** | `0x61B598` |
| `+0x0A` | **VIT** | `0x61B59A` |
| `+0x0C` | **EATK** | `0x61B59C` |
| `+0x0E` | **EDEF** | `0x61B59E` |
| `+0x10/11/12` | DEX / EVA / AGL (u8) | `0x61B5A0/A1/A2` |
| `+0x24..0x2B` | 8 element affinities (signed) | — |
| `+0x2C..0x33` | 8 per-element proc chances | — |
| `+0x62` | unit id (`11..15` = E.S. → 99999 cap) | — |
| `+0x70` | battle state (`1` = Break, `2..3` = Air/Down) | — |
| `+0xF4` | elemental chain counter | — |

Block base = `0x61B590` for Shion; the four stat offsets landing in the right
slots is a 4-of-4 match against a source that knew nothing about this overlay.

## The formula (attack category 1 — normal physical/ether attacks)

`0xA8C778(attacker, target) -> damage in $v0`. The action descriptor's `+0x07`
selects one of seven categories via a jump table at `0xABA3B0`; category 1 is
the ordinary attack path. Descriptor `+0x06` picks physical (`0`) or ether (`1`).

### 1. Attack and defence — **verified, read directly**

| | attacker term | target term |
|---|---|---|
| physical | `STR × 4` | `VIT × 3` |
| ether | `EATK × 5` | `EDEF × 4` |

Each is then adjusted by status, identically in all four getters:

```
v = stat × coefficient
if  has_status(unit, class 2, mask):  v -= v/2       # -50%
elif has_status(unit, class 1, mask):  v -= v/4      # -25%
if  has_status(unit, class 4, up_mask):   v += v/4   # +25%
elif has_status(unit, class 4, down_mask): v -= v/4  # -25%
```

### 2. Base damage — **verified**

`power` is a **u8** at `+0x0E` of the action descriptor.

```
base = ATK × power / 20  -  DEF
if base < 0:
    base = 0
else:
    base += rand(0 .. base/10 + 2)        # 0% to +10%, inclusive
```

The variance is **one-sided and upward only** — there is no downward roll.

### 3. Multipliers, applied in source order — **verified unless noted**

`D` below is the base-damage snapshot from step 2. Additive percentage bonuses
are computed from `D`, **not** compounded on the running total; only the `×`
steps compound.

| # | condition | effect | note |
|---|---|---|---|
| 1 | target state `+0x70 == 1` | **×1.5** | Break (guide agrees) |
| 2 | target state `+0x70` in `2..3` | **×2** | Air / Down (guide agrees) |
| 3 | attacker flag `0x2E` | `+= D × f / 4` | `f` from `0xA880F8`, unresolved |
| 4 | target flag `0x2F` | `−= D × f / 4` | same |
| 5 | element coat matches attack | **×0.5** (status `4/0x100`) or **×0.75** | |
| 6 | attacker flag `0x31` | `+= D` | unresolved |
| 7 | target flag `0x3F` | `+= D × rand(10..15) / 100` | |
| 8 | ether **and** event slot `4` (ETR) | **×1.5** | matches the pnach slot list: *"4 - ETR/Ether damage & recovery increased 50%"* |
| 9 | **critical** (`0xA8CF88`) | **×1.5**, or **×2** hi-critical | see below |
| 10 | chain counter `+0xF4 ≥ 2` | `+= D × (10×chain − 10) / 100` | **+10% per chain step above 1** |
| 11 | elemental affinity | **× affinity / 20** | see below |
| 12 | zone bit set for the zone being hit | **×0.5** | see below |
| 13 | target `+0x19 == 2` (**Guarding**) | `−= d × (base + bonus) / 100`, base **50** physical / **25** ether | guide: "Guarded … took only half damage" |
| 14 | target flag `0x28` | **×0.9** | unresolved |
| 15 | always | clamp `≥ 0`, then 9999 / 99999 | |

**Zone hit (step 12).** The target's current zone is `stats +0x1C & 3` (0/1/2).
The action descriptor carries one flag byte per zone at `+0x0F`, `+0x10`,
`+0x11`. For the zone actually being hit:

- **bit 1** set → damage is **halved**,
- **bit 2** set → **+50 critical rate** (used in `0xA8CF88`, step 9).

So the same three bytes drive both the zone damage penalty and the zone crit
bonus, which is why hitting the right zone matters twice over.

### 4. Criticals — `0xA8CF88`, **verified**

```
rate = 10                      # 50 when the event slot is 0
rate += 50   per matching attack zone   (descriptor +0x0F/+0x10/+0x11, bit 2)
rate += back-attack bonus  (0xA8ED88)
rate += 50   if target flag +0x10 == 1
rate  = min(rate, 100)
if rand_pct(rate):
    if event_slot != 0:      damage = damage × 3 / 2      # ×1.5
    elif rand_pct(10):       damage = damage × 4 / 2      # ×2.0  hi-critical
    else:                    damage = damage × 3 / 2
```

Matches the guide's "critical = 1.5x, hi-critical = 2x" exactly. The 10%
hi-critical roll only happens when the event slot is `0` — very likely CRTC,
which would also explain the base rate jumping 10 → 50 on the same condition
(**inferred**, not proven).

### 5. Elemental affinity — **verified, and it confirms the on-disc table**

```
mult = 20
for i in 0..7:
    if attack_elements & (1 << i):
        if rand_pct(stats[0x2C + i]):          # per-element proc chance
            a = stats[0x24 + i]                # signed affinity
            if a < 0:  mult = a; break         # absorb
            else:      mult = mult + a - 20    # stacks across elements
if matched:
    if mult == 0:  damage = 0                  # null, flag 0x2000
    else:          damage = damage × |mult| / 20   (negative -> absorb, flag 0x1002)
```

The multiplier is **affinity / 20**. The enemy-table work recorded in
`Xenosaga2_ISO_offsets.md` derived "percent = byte × 5" by comparison against
the strategy guide; `byte × 5 / 100` **is** `byte / 20`. Two derivations from
completely independent directions — guide comparison vs. instruction stream —
agree exactly.

The per-element **proc chance** at `+0x2C+i` is new; nothing in the guides
mentions affinities being probabilistic.

## Where this contradicts the community guide

The Battle Mechanics Guide says of elemental chains: *"I'm not sure of the exact
formula… a chain of 10 does around 10% more damage"* and guesses `chain × 1%`.
The code computes `(10 × chain − 10)%`, so a chain of 10 is **+90%**, not +10%.
Either the guide's estimate is wrong or `+0xF4` is not the chain counter — the
offset label is **inferred** from context and is the weakest claim on this page.

## Not yet resolved

- Attack categories 2/3 (`0xA8CCA8`, percentage/fixed damage), 6 (`0xA8CDD8`)
  and 7 (`0xA8CE58`). Categories 4 and 5 branch straight to the exit — no damage.
- Flags `0x2E` / `0x2F` / `0x31` / `0x3F`, and `0xA880F8`.
- `0xA8C278` — builds the effective element mask from the attacker and the
  descriptor's `+0x0C`.
- Post-damage passes `0xA8D110`, `0xA8D240`, `0xA8D950`, and `0xA8DB90`
  (called for side effects; its return value is discarded).
- Whether E.S. units use this routine or the `0xA8E9B0` path.
