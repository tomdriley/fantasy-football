# Git Blame Copilot — Draft Optimization System

## The problem, stated as engineering

Strip away the sport and this is a **sequential combinatorial resource-allocation problem under
uncertainty**, with an adversarial component and a hard real-time deadline.

- There is a shared pool of ~600 **items** (players). Each item has a *type* (6 types exist).
- 10 **agents** (you + 9 coworkers) take turns claiming items. Claims are exclusive — once an item
  is taken it is gone for everyone.
- Each agent claims exactly 15 items, in a fixed alternating turn order, over 15 rounds.
- Each item has an unknown future scalar payoff. You get a **forecast** of that payoff, with error.
- Your final score is **not** the sum of your 15 items. It is the sum of a *feasible subset* of
  them, chosen weekly, subject to slot constraints (below). Surplus items contribute **zero**.
- You have **60 seconds** per decision. If you exceed it, a default policy picks for you.

**Objective:** choose a claim policy that maximizes expected total payoff of your constrained
lineup over the season.

**The baseline to beat:** the default policy ("autopick") is greedy — it takes the highest item on
a fixed, generic, precomputed ranking list that satisfies an unfilled slot. It is myopic: it
ignores scarcity, ignores what the other 9 agents will do, and ignores that surplus items score
zero. That myopia is the exploitable surface.

## Glossary (domain term → what it actually means)

| Term | Engineering meaning |
|------|--------------------|
| **Player** | An item in the pool with a forecast scalar payoff |
| **Position** (QB, RB, WR, TE, K, DEF) | The item's *type*. Six categories. Slot constraints are expressed per type |
| **Points** | The payoff. A **linear function of raw statistics**: `payoff = Σ stat[k] × weight[k]`. The weight vector is the league's config, and I have it |
| **PPR** | "Points Per Reception" — one specific weight in that vector (a catch is worth 1.0). It matters only because it shifts *relative* value between types. Your league uses 1.0 |
| **Roster slot** | A constraint. Your lineup must fill: 1 QB, 2 RB, 2 WR, 1 TE, 2 FLEX, 1 K, 1 DEF |
| **FLEX** | A *wildcard slot* that accepts type RB, WR, or TE. You have 2 — this raises demand for those types |
| **Bench (BN)** | Held items that score **zero**. Pure option value / injury insurance. You have 5 |
| **Snake draft** | Turn order reverses each round. With 10 agents, if you pick k-th in round 1 you pick (11−k)-th in round 2, and so on. Consequence: your two consecutive picks can be 2 or 18 slots apart — the **gap** drives everything |
| **ADP** ("Average Draft Position") | The empirical mean index at which an item was claimed, measured across thousands of observed drafts. Effectively a **market-consensus prior over selection order** — my opponent model's main input |
| **Replacement level** | The payoff of the best *freely available* item of a type. With 10 agents each needing 2 RB, roughly the 21st-best RB is always obtainable, so claiming RB #5 only buys you the *difference* |
| **VOR / VBD** | Value Over Replacement. `marginal_value = payoff − replacement_payoff_of_same_type`. The correct objective, because surplus scores zero |
| **Bye week** | Each item is guaranteed to score 0 in exactly one known week. A lineup-feasibility constraint |
| **Waiver wire** | The undrafted remainder stays claimable all season. Implication: **depth is cheap, so don't spend early capital on it** |
| **Autopick / CPU** | The greedy baseline policy described above |
| **Keeper** | An item retained from last season, removed from the pool before the draft |

## Feasibility verdict

**Feasible — but the effort should move from *forecasting* to *decision-making*.** Three findings
from live reconnaissance against the real league API:

**1. There is nothing to scrape.** The platform (Sleeper) exposes a public, unauthenticated JSON
API. Config, forecasts, consensus-order data, historical outcomes, and live claim events are all
structured data. Verified working. Your "scrape the rules" step collapses to a few HTTP GETs.

**2. Your custom-scoring report will produce no edge — I checked.** I applied your league's 43
scoring weights to the forecasts and compared to the platform's stock weight vector. They match
**exactly**. Your league is entirely standard. The scoring engine is still the necessary foundation,
but the edge lives in the *constraint structure* (2 wildcard slots, only 10 agents, 15 rounds), not
in the weight vector.

**3. The overnight ML forecasting model is the piece I'd cut.** Learning payoff forecasts from
scratch needs multi-season granular data, feature engineering, and validation. Built in a day it
will be strictly worse than the free forecasts already available — and forecast accuracy is not the
binding constraint anyway. The *decision layer* is.

### The core insight, already demonstrated

Sorting items by raw forecast payoff puts **9 of one single type (QB) in the top 14**:

| Rank | Item | Type | Forecast payoff | Consensus pick index (ADP) |
|------|------|------|-----------------|---------------------------|
| 1 | Josh Allen | QB | 361.5 | 21.0 |
| 2 | Jahmyr Gibbs | RB | 331.4 | 1.5 |
| 3 | Lamar Jackson | QB | 326.0 | 31.0 |
| 4 | Bijan Robinson | RB | 324.9 | 2.5 |
| 5 | Drake Maye | QB | 320.8 | 47.7 |

The market claims Gibbs ~20 picks *earlier* than Allen despite Allen forecasting higher. The market
is right, and replacement level explains why:

| Type | Replacement baseline (computed for 10 agents) | Marginal value of the #1 item |
|------|-----------------------------------------------|-------------------------------|
| RB  | RB23 = 171.0 | **+160.4** |
| WR  | WR37 = 173.6 | **+138.9** |
| TE  | TE10 = 163.6 | +89.9 |
| QB  | QB10 = 296.5 | **+65.0** |
| DEF | DEF10 = 98.0 | +18.0 |
| K   | K10 = 74.0 | **+4.0** |

> These baselines are the refined, wildcard-aware figures: allocating the 20 FLEX slots greedily by
> payoff shows they absorb **17 WR and only 3 RB**, which pushes the WR baseline down to the 37th
> receiver while RB settles at the 23rd. See [`league-rules.md`](./league-rules.md) §7 for the
> derivation. Earlier drafts of this plan used a cruder even-split heuristic.

Only one QB starts per lineup, so with 10 agents the 10th-best QB (296.5) is essentially free.
Claiming the best QB buys you only +65. The best RB buys +160. **QB scores the most raw points and
is simultaneously among the worst early claims.** A naive "maximize forecast payoff" model — which
is exactly what a rushed ML model produces — claims Josh Allen with the first pick and destroys the
season. Encoding this distinction is most of the achievable edge.

Note the last row: the K type is worth **+4 payoff over the entire season**. It is a rounding error.
Claim it with the final pick and never think about it again.

## Verified system parameters (pulled live from the API)

- **League:** "Deeply Unserious League", ID `1400629960678346752`, 10 agents, status `pre_draft`
- **You:** "Git Blame Copilot", user_id `1400880093110239232`
- **Draft:** ID `1400629961529802752`, snake order, 15 rounds, **60-second decision deadline**,
  autopick enabled, begins **2026-09-03 16:00**
- **Slot constraints (15 items):** 1 QB, 2 RB, 2 WR, 1 TE, 2 FLEX, 1 K, 1 DEF, 5 bench (+1 injury slot)
- **Payoff weights:** 43 entries, standard full-PPR; skill-position weights identical to platform default
- **Season structure:** playoffs begin week 15, top 6 of 10 qualify; trades close week 11
- **Turn order is NOT yet assigned** (`draft_order: null`) ⇒ must precompute a policy for all 10 seats
- **Only 6 of 10 agents have joined.** Any seat left empty runs the greedy autopick policy for the
  entire draft — i.e. a **fully deterministic, perfectly predictable opponent** that the model can
  exploit
- **No keepers declared** (verified across all 10 rosters) ⇒ the full item pool is available and the
  consensus-order distribution is undistorted
- 10 agents × 15 rounds = 150 of ~600 items claimed ⇒ the unclaimed remainder is deep, which is why
  hoarding depth is a losing use of early picks

## Approach

Build a **value-over-replacement optimizer with an opponent model**. Not a forecasting model.

1. **Payoff function** — dot product of forecast statistics with the league's weight vector.
2. **Marginal value** — subtract replacement baselines derived from *this* league's true slot
   demand (the 2 wildcard slots materially change the baselines).
3. **Opponent model** — estimate `P(item survives until my next turn)` from the consensus-order
   distribution. *This is the legitimate statistical model in the project*, and it is a small,
   tractable estimation problem with data available tonight. Empty seats get a near-deterministic
   model since greedy autopick is predictable.
4. **Decision rule** — claim the item maximizing expected final *constrained-lineup* value, given
   remaining pool, my unfilled slots, and the snake gap until my next turn. The gap is what converts
   "who do I like" into "who will not be there in 18 picks".
5. **Validation** — prove it beats the greedy baseline on a **non-circular** backtest.

## Where the uncertainty actually comes from

A live Monte Carlo simulation is only as meaningful as its variance model, so it is worth being
explicit about which uncertainties exist and which are measurable. There are **two independent
channels**, and they answer different questions. Conflating them is the classic error here.

### Channel 1 — Opponent behaviour (drives *who is still on the board*)

This is the only channel that affects a draft-day decision. Its components:

| Source | Measurable? | Notes |
|---|---|---|
| Dispersion of human claim order around consensus | **No** | The API exposes six ADP fields and **every one is a mean**, for a different scoring format. No dispersion is published anywhere in the payload |
| Roster-need conditioning | Partly | Opponents claim to fill *their own* unfilled slots. An agent holding 2 QBs will not take a third. Observable live from the pick feed |
| Positional runs / cascades | **No** | Claims are correlated, not independent — once several items of a type go, more follow. i.i.d. sampling **understates** the variance of "how many RBs disappear before my next turn", which is precisely the quantity that matters |
| Empty seats running autopick | **Yes — zero variance** | Up to 4 of 9 opponents may be fully deterministic |

> **Trap, verified:** the field `adp_std` is **standard-scoring ADP, not a standard deviation.**
> Confirmed decisively: high-reception receivers (Chase, 109 catches) sit *later* in `adp_std` than
> in `adp_ppr`, while low-reception backs (Cook, 31 catches) sit *earlier* — the signature of
> non-PPR scoring. Anyone reaching for `adp_std` as σ gets a silently broken simulation.

### Channel 2 — Realized player outcomes (drives *whether the roster wins*)

Injury, usage changes, and game context. This channel has **no effect on availability** and is
therefore irrelevant to the pick decision itself. It matters for two other purposes: evaluating
strategies in the backtest, and risk posture. Note the objective is subtly not "maximize expected
points" — 6 of 10 agents reach the playoffs, so outcome variance carries option value when behind
and is a liability when ahead.

### Consequence: calibrate what you can, and prove indifference to the rest

Attempts to calibrate Channel 1 empirically failed: crawling the league members' other leagues
surfaced no completed 10-team drafts, so **σ is an unmeasurable free parameter.** The correct
response is not to invent a precise value but to demonstrate the decision does not depend on it.

Measured sensitivity (seat 5, claiming at pick 5 with the next turn at pick 16, an 11-pick gap),
scoring each candidate by `VOR(now) + E[best VOR available at next turn]` under a Plackett–Luce
opponent model with temperature τ:

| Candidate | τ=1.5 | τ=4.0 | τ=10.0 |
|---|---|---|---|
| **Jahmyr Gibbs (RB)** | **250.7** | **258.1** | **291.1** |
| Bijan Robinson (RB) | 244.3 | 252.2 | 286.1 |
| Puka Nacua (WR) | 229.2 | 236.1 | 273.6 |
| Ja'Marr Chase (WR) | 228.0 | 235.2 | 272.1 |

Across a ~7× range of opponent randomness the EV *levels* move substantially (250 → 291) but the
*ordering is unchanged*. The continuation value shifts almost equally for every candidate, so it
cancels in the comparison. **The decision is robust to the parameter we cannot measure.**

Two honest caveats: this was tested in round 1, where one candidate dominates on VOR — the easy
case. The parameter is far more likely to bind in **middle rounds**, where candidates have similar
VOR but different scarcity profiles, and that case must be tested before relying on it. Second, a
naive additive-noise model (σ ∝ ADP) is **misspecified**: it gives an ADP-200 item σ=80, letting it
be claimed 5th, which scatters claims across the whole board and absurdly reports ~95% survival for
*everyone*. The opponent model must keep claims concentrated near the top of the board.


## Critical guardrails

- **Circular evaluation is the primary failure mode.** If I let my optimizer draft using forecast
  vector *F* and then score the resulting rosters using that same *F*, it wins by construction and
  the result is meaningless. The backtest must claim items using **2025 pre-season consensus order**
  and score them using **2025 actual realized outcomes**. I verified that endpoint returns 3,305
  player-seasons of real results, so this is doable.
- **Single-source forecast risk.** The forecasts come from one provider, and the defect is already
  visible: Josh Jacobs is projected *below replacement* while the market claims him 37th overall.
  Consensus order (ADP) aggregates many human drafters and is frequently the better estimator, so
  blend the two rather than trusting the forecast vector alone.
- **Consensus order is NOT an independent check on the forecasts.** Both arrive in the same vendor
  payload, and human drafters read projections while drafting, so ADP is causally contaminated by
  the forecasts. Measured agreement (Spearman vs ADP: raw payoff +0.738, VOR +0.840) confirms the
  transform is sane, but it cannot validate the forecasts. Only the realized-outcome backtest can.
- **VOR is an input, not the draft board.** Sorted directly it ranks kickers/defenses ~70 positions
  too high, because it measures *how much better than replacement* an item is with no notion of
  *when it can be obtained*. The optimizer must supply that.
- **Hard 60-second deadline.** All heavy computation happens beforehand; the live tool must respond
  in well under a second, and a printed fallback must exist in case anything breaks mid-draft.
- **Unknown seat assignment.** Every deliverable must work for any of the 10 possible seats.

## Documentation committed to the repo (first work items)

Before any code, two artifacts get committed so the reasoning is durable and reviewable:

- **`docs/draft-strategy-plan.md`** — this plan, committed verbatim into the repository.
- **`docs/league-rules.yaml`** — machine-readable capture of the league configuration **exactly as
  returned by the API** (all 43 payoff weights, slot constraints, draft config, seat/agent list,
  season structure, plus the retrieval timestamp and source endpoints for provenance). This becomes
  the single source of truth that the code later reads, so the rules are never re-typed by hand.
- **`docs/league-rules.md`** — the human-readable companion: every parameter in plain language,
  paired with its optimization consequence, assuming zero football knowledge. Includes the
  replacement-baseline table and the "highest payoff type is a bad early claim" finding.

The YAML is the contract; the Markdown explains it. Later, `rules-report` regenerates the Markdown
programmatically from the YAML so the two cannot drift apart.

## Todos

1. **docs-plan-commit** — Commit this plan to the repo as `docs/draft-strategy-plan.md`.
2. **docs-league-rules** — Commit `docs/league-rules.yaml` (verbatim API values + provenance) and
   `docs/league-rules.md` (plain-language explanation + optimization consequences).
3. **repo-scaffold** — Python project skeleton and dependencies. No secrets committed.
4. **sleeper-client** — API client with on-disk caching for config, item pool, forecasts,
   consensus order, historical outcomes, and live claim events.
5. **rules-report** — Regenerate `docs/league-rules.md` from the YAML programmatically, so the
   committed documentation stays in sync with the source of truth.
6. **scoring-engine** — Linear payoff function; regression-tested against the platform's own
   computed values.
7. **vor-valuation** — Replacement baselines from true slot demand incl. wildcard slots, plus
   gap-based tiering (clustering items into value plateaus so near-equivalent choices are visible).
8. **availability-model** — `P(available at my next turn)` from consensus-order dispersion;
   deterministic special case for greedy/empty seats. Includes a **Monte Carlo draft simulator**
   (Plackett–Luce opponent model, roster-need conditioning, deterministic autopick seats) that runs
   live against the in-progress pick feed. Must ship with a **σ-sensitivity report proving decision
   stability**, since the dispersion parameter is not measurable from the API — see
   "Where the uncertainty actually comes from". Mid-round stability is the case that still needs
   testing.
9. **draft-optimizer** — Expected constrained-lineup-value maximizer. Must not reproduce the naive
   max-payoff failure. Forces the two near-worthless types to the final rounds.
10. **backtest-harness** — **Go/no-go gate.** Optimizer vs 9 greedy baseline agents, across many
   seeds × all 10 seats, scored on real 2025 outcomes.
11. **live-draft-tool** — Polls claim events, renders pool state and ranked recommendations fast
   enough for the 60-second deadline, parameterized by whichever seat you draw.
12. **cheat-sheet** — Printable tiered fallback with a round-by-round plan for each of the 10 seats,
    usable if the tooling fails live.## Notes

- ~~Confirm whether any agent has declared a retained item~~ — **resolved: no keepers declared**
  across all 10 rosters, so the full pool is available.
- With only 5 zero-scoring bench slots, avoid concentrating guaranteed-zero weeks (byes) among
  starters of the same type.
- Using the documented public API is ordinary platform usage. Nothing here submits claims on your
  behalf without you clicking — the tool recommends, you click.
