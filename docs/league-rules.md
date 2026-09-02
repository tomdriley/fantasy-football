# League Rules — Plain Language + Optimization Consequences

**Companion to [`league-rules.yaml`](./league-rules.yaml).** The YAML is the machine-readable source
of truth, captured verbatim from the platform API. This document explains what each parameter means
and — more importantly — *what it implies for how we should draft*.

This document assumes **no knowledge of American football or fantasy football**. Every domain term
is defined in engineering terms before it is used.

> Retrieved from the Sleeper public API on 2026-09-02. League `1400629960678346752`
> ("Deeply Unserious League"). Our team: **Git Blame Copilot** (`Unranked0283`).

---

## 1. What the game actually is

Ignore the sport for a moment. Mechanically:

- There is a pool of ~600 **items** (real football players). Each item has a **type** (see §3).
- Each week of the real-world season, every item produces **statistics** (yards gained, scores made,
  etc.). Those statistics are converted into a single scalar **payoff** by a fixed linear function:

  ```
  payoff(item) = Σ  statistic[k] × weight[k]
  ```

  The weight vector is fixed for the season and is in the YAML under `scoring_weights`. **We have it
  exactly**, so payoff is fully computable — there is no ambiguity about the objective function.

- 10 **agents** (you + 9 coworkers) each own a set of items.
- Each week, each agent must field a **lineup**: a subset of their items satisfying slot constraints
  (§4). The agent's score that week is the sum of payoffs of the items *in the lineup only*.
- **Items you own but do not field score nothing.** This is the single most important mechanical
  fact in the entire problem.
- Agents are paired off each week; higher weekly score wins. Accumulated wins determine who reaches
  the playoffs.

**The draft** (tomorrow) is a one-shot, exclusive allocation of that item pool among the 10 agents.

---

## 2. Glossary

| Domain term | What it actually means |
|---|---|
| **Player** | An item in the pool, with a forecastable scalar payoff |
| **Position** | The item's **type**. Determines which slots it may occupy |
| **Points** | The payoff — output of the linear scoring function above |
| **PPR** | "Points Per Reception": the specific weight `rec = 1.0`. Each catch is worth 1 point. Matters only because it shifts *relative* value between types |
| **Roster** | The 15 items an agent owns |
| **Lineup / starters** | The 10 items fielded in a given week. Only these score |
| **Bench** | Owned but not fielded. Scores zero. Pure option value (injury/bye insurance) |
| **Slot** | A constraint of the form "exactly one item of type X here" |
| **FLEX** | A **wildcard slot** accepting type RB, WR, *or* TE |
| **Snake draft** | Turn order reverses every round (§5) |
| **ADP** | "Average Draft Position" — empirical mean claim index across thousands of observed public drafts. A **market-consensus prior** over selection order |
| **Bye week** | Each item has exactly one known week where it scores 0 by definition |
| **Waiver wire** | The undrafted remainder of the pool, claimable during the season |
| **Autopick / CPU** | The default greedy policy that claims for you if your 60-second timer expires |
| **Keeper** | An item retained from a prior season. **None declared here** — the full pool is available |

---

## 3. The six item types

| Type | Full name | What it does | How many start per agent |
|---|---|---|---|
| **QB** | Quarterback | Throws the ball. Accumulates large statistical volume | 1 |
| **RB** | Running back | Carries the ball; also catches passes | 2 (+FLEX eligible) |
| **WR** | Wide receiver | Catches passes | 2 (+FLEX eligible) |
| **TE** | Tight end | Catches passes, fewer of them | 1 (+FLEX eligible) |
| **K** | Kicker | Kicks field goals | 1 |
| **DEF** | Team defense | An entire team's defensive unit, treated as one item | 1 |

---

## 4. Roster constraints

From `roster_constraints` in the YAML:

```
Starting lineup (10 slots):  QB, RB, RB, WR, WR, TE, FLEX, FLEX, K, DEF
Bench (5 slots):             score zero
Injured reserve:             1 slot
Total roster:                15 items  =  15 draft rounds
```

| Parameter | Value | Optimization consequence |
|---|---|---|
| Starting lineup size | 10 | Only 10 of your 15 items ever score in a given week |
| Bench slots | 5 | Only 5 slots of pure option value. **Depth is cheap — do not spend early picks on it** |
| **FLEX slots** | **2** | Raises league-wide demand for RB/WR/TE by 20 slots. This materially moves replacement levels (§7) |
| Agents | 10 | Only 150 of ~600 items get claimed. The undrafted remainder is deep and rich |
| K slot | 1 | Worth ~4 payoff points all season (§7). **Effectively a throwaway pick** |
| DEF slot | 1 | Worth ~18 payoff points. Also a late pick |

---

## 5. Draft mechanics

| Parameter | Value | Optimization consequence |
|---|---|---|
| Format | **Snake** | Turn order reverses each round. If you pick k-th in round 1, you pick (11−k)-th in round 2 |
| Rounds | 15 | 150 total claims |
| **Pick timer** | **60 seconds** | Hard real-time deadline. All computation must be precomputed; the live tool must respond in well under a second |
| Autopick | **Enabled** | If the timer expires, a greedy policy claims for you. This is also the baseline we're trying to beat |
| Start time | 2026-09-03 20:00 UTC (16:00 Eastern) | — |
| **Draft order** | **NOT YET ASSIGNED** | We must precompute a strategy for **all 10 possible seats** |
| Occupied seats | **6 of 10** | See below — this is exploitable |

### The snake gap (why turn order dominates everything)

Because order reverses, the number of picks between your consecutive turns varies enormously.
For seat 1: you pick at 1, 20, 21, 40, 41… — an 18-pick gap, then a 1-pick gap.
For seat 5: you pick at 5, 16, 25, 36… — consistent ~10-pick gaps.

This gap is the whole decision problem. Choosing between two items is never "which is better" but
**"which one will still be there in N picks"**. That's what the availability model estimates.

### The four empty seats

Four seats currently have no human owner. If they remain empty, they run the greedy autopick policy
for the entire draft — a **fully deterministic, perfectly predictable opponent**. Modeling them is
trivial and directly exploitable. If they fill before the draft, we fall back to the ADP-based
probabilistic opponent model. The system must handle both cases.

---

## 6. Scoring weights in plain language

All 43 weights are in the YAML. Grouped and translated:

**Passing** (mostly QB)
- 1 point per 25 yards thrown (`pass_yd: 0.04`)
- 4 points per passing score (`pass_td: 4.0`)
- −1 per interception, i.e. throwing it to the opposing team (`pass_int: -1.0`)

**Running** (mostly RB)
- 1 point per 10 yards run (`rush_yd: 0.1`)
- 6 points per rushing score (`rush_td: 6.0`)

**Catching** (WR/TE/RB)
- **1 point per catch (`rec: 1.0`) ← this is "full PPR"**
- 1 point per 10 receiving yards (`rec_yd: 0.1`)
- 6 points per receiving score (`rec_td: 6.0`)

**Turnovers**: −2 for losing the ball (`fum_lost: -2.0`)

**Kicking**: 3–6 points per field goal scaled by distance, 1 per extra point, −1 per miss

**Team defense**: 1 per sack, 2 per interception/fumble recovery, 6 per defensive score, plus a
tiered bonus for allowing few points (10 for a shutout, down to −4 for allowing 35+)

### Critical finding: this weight vector is completely standard

I applied these 43 weights to the forecasts and compared against the platform's own stock full-PPR
computation. **They match exactly.** There is no scoring quirk to exploit — no bonus for tight ends,
no first-down bonus, no unusual passing values.

**Consequence:** a "custom scoring engine" yields **no competitive edge in this league**. Publicly
available rankings are already tuned for exactly these settings. Our edge must come from the
*constraint structure* (2 wildcard slots, 10 agents, 15 rounds) and from *opponent modelling* —
not from the scoring function.

---

## 7. Derived quantities: replacement levels and the real draft board

Because unfielded items score zero, an item's true worth is **not** its payoff. It is its payoff
*minus the payoff of the best freely-available substitute of the same type* — its **value over
replacement**.

Computing replacement level requires knowing how many items of each type the league actually
consumes. The 2 wildcard slots don't distribute evenly — allocating them greedily by payoff, they
absorb **17 WR and only 3 RB**:

| Type | Dedicated slots | Absorbed by FLEX | Total consumed | Replacement payoff |
|---|---|---|---|---|
| QB | 10 | 0 | 10 | 296.5 |
| RB | 20 | 3 | 23 | 171.0 |
| WR | 20 | 17 | **37** | 173.6 |
| TE | 10 | 0 | 10 | 163.6 |
| K | 10 | 0 | 10 | 74.0 |
| DEF | 10 | 0 | 10 | 98.0 |

### The counter-intuitive result

| Type | Best item | Raw payoff | **Value over replacement** |
|---|---|---|---|
| QB | Josh Allen | **361.5** (highest of all) | **+65.0** |
| RB | Jahmyr Gibbs | 331.4 | **+160.4** |
| WR | Puka Nacua | 312.5 | **+138.9** |
| TE | Brock Bowers | 253.5 | +89.9 |
| DEF | LA Rams | 116.0 | +18.0 |
| K | Ka'imi Fairbairn | 78.0 | **+4.0** |

**Quarterbacks produce the highest raw payoff of any type and are simultaneously among the worst
early claims.** Only one QB starts per agent, so with 10 agents the 10th-best QB (296.5) is
essentially free. Paying a first-round pick for Josh Allen buys **+65** over that free substitute.
The best running back buys **+160**.

Sorting the pool by raw payoff puts **9 QBs in the top 14**. A naive "maximize forecast payoff"
model — precisely what a hastily-built ML model produces — claims Josh Allen first overall and
wastes ~95 points of value. **This is the trap the entire system exists to avoid.**

### The actual draft board (top 12 by value over replacement)

| # | Item | Type | VOR |
|---|---|---|---|
| 1 | Jahmyr Gibbs | RB | +160.4 |
| 2 | Bijan Robinson | RB | +153.9 |
| 3 | Puka Nacua | WR | +138.9 |
| 4 | Ja'Marr Chase | WR | +137.5 |
| 5 | Christian McCaffrey | RB | +120.0 |
| 6 | Jaxon Smith-Njigba | WR | +111.0 |
| 7 | Amon-Ra St. Brown | WR | +106.9 |
| 8 | Jonathan Taylor | RB | +101.3 |
| 9 | CeeDee Lamb | WR | +96.9 |
| 10 | Brock Bowers | TE | +89.9 |
| 11 | James Cook | RB | +89.8 |
| 12 | Nico Collins | WR | +88.4 |

**Validation signal:** this ordering closely tracks independent market consensus (Gibbs ADP 1.5,
Bijan 2.5, Chase 3.2, Nacua 4.0). Two methods that share no inputs — our payoff/replacement
computation, and the aggregated behaviour of thousands of human drafters — agree on the top of the
board. That is meaningful evidence the pipeline is correct. Note that **no QB appears in the top 12**.

---

## 8. Strategic conclusions

1. **Never claim by raw forecast payoff.** Claim by value over replacement.
2. **Do not take a QB early.** The 10th-best is nearly as good as the best.
3. **Kicker last, defense second-to-last.** The kicker slot is worth +4 points across an entire
   season — statistical noise.
4. **Prioritise RB and WR early.** They hold essentially all of the concentrated marginal value.
5. **Don't hoard depth.** Only 150 of ~600 items are claimed and the leftover pool stays claimable
   all season, so bench items are nearly free later. Spend early capital on scarcity.
6. **Everything is conditional on the snake gap.** With an 18-pick gap the question is never "who is
   best" but "who survives".
7. **Exploit the empty seats.** Up to 4 opponents may be fully deterministic.

## 9. Open items

- **Draft order is unassigned.** All deliverables must be parameterised by seat.
- **Forecasts are single-source.** Market consensus (ADP) aggregates far more independent
  information and should be blended in rather than trusting one forecast provider.
- **Bye weeks:** with only 5 zero-scoring bench slots, avoid concentrating guaranteed-zero weeks
  among starters of the same type.
- Replacement levels above are computed from preseason forecasts and should be recomputed as the
  board evolves during the draft — they are not static.
