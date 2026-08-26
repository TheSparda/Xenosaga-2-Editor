# I pulled the damage formulas out of Xenosaga Episode II's battle code. Here's everything.

Nobody's ever documented how XS2 actually calculates damage — the classic Battle Mechanics Guide is great, but it openly guesses on a lot of it ("I'm not sure of the exact formula"). So I disassembled the battle overlay off the US disc (SLUS-20892) and read it directly.

No emulator, no memory watching. This is the actual code.

---

## TL;DR — the core formula

```
ATK  = STR × 4    (physical)      or    EATK × 5   (ether)
DEF  = VIT × 3    (physical)      or    EDEF × 4   (ether)

base = (ATK × Power) / 20  −  DEF
if base < 0: base = 0
else:        base += random(0 .. base/10 + 2)
```

**Power** is a single byte on the attack/skill itself. So an attack's Power isn't a percentage — it's a direct multiplier over 20. Power 20 = "STR × 4 straight up."

Two things jump out immediately:

**1. The random variance is upward only.** There is no low roll. The game rolls `0` to `base/10 + 2` and *adds* it. Your damage floor is the clean formula and you can only roll up from there, by at most ~10%. That "damage range" you think you're seeing is almost entirely the crit roll, not variance.

**2. Defense is flat subtraction, not a divisor.** High Power attacks scale brutally against high-DEF targets, and low-Power multi-hit stuff falls off a cliff. If `ATK × Power / 20` doesn't clear DEF, you do **zero**, not "minimum 1."

---

## Stat coefficients

|  | attacker | defender |
|---|---|---|
| **Physical** | STR × 4 | VIT × 3 |
| **Ether** | EATK × 5 | EDEF × 4 |

Ether has a higher attack coefficient (×5 vs ×4) *and* a higher defense coefficient (×4 vs ×3). Ether swings harder on both ends.

Buffs/debuffs are applied to these values before the formula, not after:

```
−50%   heavy debuff
−25%   light debuff
+25%   attack/defense up
−25%   attack/defense down
```

---

## The full multiplier chain, in the exact order the game applies it

Let `D` = the base damage from step 1 (post-variance). Important: the **additive** percentage bonuses are all calculated from `D`, not compounded on the running total. Only the `×` steps compound.

| # | Condition | Effect |
|---|---|---|
| 1 | Target is **Broken** | **×1.5** |
| 2 | Target is **Air / Down** | **×2** |
| 3 | Attacker has flag `0x2E` | `+ D × f / 4` *(unidentified)* |
| 4 | Target has flag `0x2F` | `− D × f / 4` *(unidentified)* |
| 5 | Target has a matching **element Coat** | **×0.5** or **×0.75** |
| 6 | Attacker has flag `0x31` | `+ D` *(i.e. double — unidentified)* |
| 7 | Target has flag `0x3F` | `+ D × random(10..15)%` |
| 8 | Ether attack **and** **ETR** event slot active | **×1.5** |
| 9 | **Critical** | **×1.5**, or **×2** on a hi-critical |
| 10 | **Elemental chain** ≥ 2 | `+ D × (10 × chain − 10)%` |
| 11 | **Elemental affinity** | **× affinity / 20** |
| 12 | Hit a **resisted zone** | **×0.5** |
| 13 | Target is **Guarding** | **−50%** physical / **−25%** ether (+ extra from buffs) |
| 14 | Target has flag `0x28` | **×0.9** *(unidentified)* |
| 15 | Always | clamp to ≥ 0, then cap at **9999** |

The 9999 cap becomes **99999** when the attacker's unit ID is 11–15 — that's the E.S. units.

---

## Criticals

```
rate  = 10%                         (jumps to 50% when the event slot is 0)
rate += 50   if you hit a zone flagged for crit bonus
rate += 50   if the target is flagged (back attack)
rate += buff bonus
rate  = min(rate, 100)

on success:
    ×1.5  normally
    ×2.0  hi-critical — 10% of crits, and only when the event slot is 0
```

Base crit is **10%**. The guide's "criticals are 1.5x, hi-criticals are 2x" is exactly right.

---

## Elemental affinity — and a genuinely new find

```
mult = 20
for each of the 8 elements the attack carries:
    if random_percent(target.proc_chance[element]):
        a = target.affinity[element]        (signed byte)
        if a < 0:  mult = a; stop           (absorb)
        else:      mult = mult + a − 20     (stacks across elements)

if mult == 0:  damage = 0                   (nulled)
else:          damage = damage × |mult| / 20
```

So the affinity byte is a direct `/20` multiplier: **20 = 100%, 40 = 200%, 10 = 50%, negative = absorb.**

**The new part:** each element affinity is gated behind a **per-element proc chance**. Affinities are *probabilistic*. Enemy weaknesses don't fire 100% of the time — there's a roll per element, per hit, and if it fails that element contributes nothing. I've never seen this mentioned anywhere, and it would explain why weakness damage feels inconsistent in a way the guides never account for.

---

## Zones matter twice

Every enemy has a current zone (A/B/C). Every attack carries one flag byte per zone. For the zone you actually hit:

- one bit → **damage halved**
- another bit → **+50% critical rate**

So zone matching isn't just the break system — it's simultaneously a ×0.5 damage penalty *and* a huge crit swing on the same lookup.

---

## Where this contradicts the Battle Mechanics Guide

The guide says of elemental chains: *"I'm not sure of the exact formula, but from my own observations, the formula appears to be chain# × 1%"* — so a chain of 10 ≈ +10%.

The code says `(10 × chain − 10)%`. A chain of 10 is **+90%**, not +10%. That's an order of magnitude, and it reframes chaining from a minor bonus into one of the biggest damage levers in the game.

**Caveat, and I want to be upfront about it:** this is the weakest claim on the list. I'm reading a counter on the target that I've *inferred* is the chain counter from context. Everything else here is either read straight out of the arithmetic or cross-confirmed. If someone with an emulator wants to verify this one specifically, that's the single most valuable thing anyone could check.

Everything else lines up with the guide: Break ×1.5, Air/Down ×2, Guard halves, crit ×1.5, hi-crit ×2, ETR slot +50% ether.

---

## Why you should believe this

Fair question, since I'm claiming to overturn a 20-year-old guide.

- **The stat offsets are confirmed by a completely independent source.** The code reads the four stats at specific offsets in a unit's data. An old CodeBreaker cheat set (written by someone who never touched this code, working purely from memory scanning) puts Shion's Str/Vit/Eatk/Edef at exactly those offsets. 4-for-4.
- **The affinity math independently confirms the enemy data table.** I'd previously worked out enemy affinity bytes as "percent = byte × 5" by comparing against the printed strategy guide. The code multiplies by `byte / 20`. Those are the same number, derived from opposite directions.
- **The ETR event slot bonus matches the cheat file's own description** of that slot, word for word.
- Every multiplier the guide *does* state confidently, the code agrees with.

## Still unknown

- Four flags (`0x2E`, `0x2F`, `0x31`, `0x3F`, `0x28`) that clearly modify damage but I haven't identified. `0x31` in particular straight-up doubles damage.
- The other attack categories — this covers normal physical/ether attacks. There are separate code paths for percentage/fixed damage attacks that I haven't finished.
- Whether E.S. combat uses this same routine or a parallel one.

If anyone has an emulator and wants to sanity-check the chain formula or help ID those flags, I'd genuinely appreciate it.
