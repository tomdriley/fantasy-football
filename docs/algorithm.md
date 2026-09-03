# The Algorithm

A precise specification of the decision engine, written to be **audited and attacked**. Where the
implementation rests on an assumption, this document says so and states how much the assumption is
worth in points.

Companion to [architecture.md](./architecture.md) (how the system is structured) and
[draft-strategy-plan.md](./draft-strategy-plan.md) (why this approach). This document is the one
that matters for correctness.

> **Status: CONDITIONALLY VALIDATED.** An initial backtest showed the engine losing to the
> platform's autopick by 6%. Auditing the *evaluation* found four blocking flaws, all biased against
> the engine. Corrected, the result is **+1.6% over autopick when the roster is managed weekly**
> (42/60 configurations, all 6 seed-clusters positive, sign test p≈0.016) and **statistically
> indistinguishable from autopick when it is not**. The founding premise — that beating autopick is
> a *low* bar — remains **refuted**: autopick is strong, and the margin is small and conditional.
> See [§10](#10-backtest-results).

---

## 1. Notation

| Symbol | Meaning |
|---|---|
| $i$ | An item (player) |
| $\tau(i)$ | Its type: QB, RB, WR, TE, K, DEF |
| $p(i)$ | Forecast season payoff, in league points |
| $R$ | Our roster (a set of items) |
| $W = 18$ | Weeks in the season calendar |
| $d(\tau)$ | Dedicated starting slots for type $\tau$: QB 1, RB 2, WR 2, TE 1, K 1, DEF 1 |
| $F = 2$ | Wildcard (FLEX) slots |
| $\Phi$ | Flex-eligible types: {RB, WR, TE} |
| $q(\tau)$ | Per-week probability an item of type $\tau$ cannot be started |
| $w(\tau)$ | Payoff of a realistically obtainable free agent of type $\tau$ |
| $a(\tau)$ | Starting slots a bench item of type $\tau$ could occupy |
| $n_\tau(R)$ | Count of type $\tau$ items in $R$ |

---

## 2. The objective function

**This is the equation that was missing.** Its absence — not any coding error — caused every
pathology in [§8](#8-regression-history-four-bugs-and-what-each-revealed).

### 2.1 Starter assignment

$S(R)$ is built in two stages, because wildcard slots are shared but **type minimums bind**:

1. For each type $\tau$, the top $d(\tau)$ items of that type by payoff take the dedicated slots.
2. From items not yet used with $\tau \in \Phi$, the top $F$ by payoff take the wildcard slots.

A lineup needs 2 RB and 2 WR regardless of which items score most. Treating all flex-eligible types
as interchangeable produces rosters that cannot field a legal lineup — observed, see §8.

### 2.2 Availability

$$q(\tau) = \frac{\text{GAMES\_MISSED}(\tau) + 1}{W}$$

The $+1$ is the bye week: teams play 17 games across an 18-week calendar, so every player is
unavailable at least once regardless of injury.

### 2.3 Accessible slots for bench items

$$a(\tau) = d(\tau) + F \cdot \mathbb{1}[\tau \in \Phi]$$

A spare running back can cover an injured starting running back *or* slide into a wildcard slot, so
its depth is measured against both. RB and WR get $2 + 2 = 4$; TE gets $1 + 2 = 3$; QB, K, DEF get 1.

### 2.4 Season value

$$
V(R) \;=\; \underbrace{\sum_{i \in S(R)} \bigl(1 - q(\tau(i))\bigr)\, p(i)}_{\text{(a) starters}}
\;+\; \underbrace{\sum_{\tau} \max\bigl(0,\, d(\tau) - n_\tau(R)\bigr)\,\bigl(1 - q(\tau)\bigr)\, w(\tau)}_{\text{(b) unfilled slots, covered by free agents}}
\;+\; \underbrace{\sum_{\tau} \sum_{j=1}^{|B_\tau|} W \cdot \Pr\bigl[X_\tau \ge j\bigr] \cdot \max\left(0,\, \frac{p(b_{\tau j})}{W} - \frac{w(\tau)}{W}\right)}_{\text{(c) bench depth}}
$$

where $B_\tau$ are the non-starting items of type $\tau$ sorted by descending payoff, $b_{\tau j}$ is
the $j$-th of them, and

$$\Pr[X_\tau \ge j] \;=\; \sum_{m=j}^{a(\tau)} \binom{a(\tau)}{m} q(\tau)^m \bigl(1 - q(\tau)\bigr)^{a(\tau) - m}$$

is the probability that at least $j$ of the $a(\tau)$ accessible slots are vacant in a given week.

**Reading term (c):** a bench item is worth *how often it actually starts* times *how much better it
is than the free agent you would otherwise start*. Both factors matter, and their product is what
makes the model behave:

- A backup **defense** almost never starts ($q_{\text{DEF}} = 1/18$, one slot) **and** the free-agent
  defense is nearly as good. Both factors are near zero, so it is worth ~0.
- A spare **running back** starts often (4 accessible slots) **and** free-agent running backs are far
  worse. Both factors are large, so depth there is genuinely valuable.

This is why "never draft two defenses" is not a rule in the code. It is a *consequence*.

### 2.5 Marginal value

$$\Delta(R, i) \;=\; V(R \cup \{i\}) - V(R)$$

---

## 3. The decision rule

One-step lookahead with rollout evaluation. For each candidate $c$:

$$U(c) \;=\; \frac{1}{T}\sum_{t=1}^{T} V\bigl(\rho_t(R \cup \{c\})\bigr)$$

where $\rho_t$ simulates the remainder of the draft under trial $t$ and returns our final roster. We
claim $\arg\max_c U(c)$.

This supplies the dimension that value-over-replacement lacks: **when** an item can be obtained.
Scarcity and claim timing fall out of the objective rather than being imposed.

### 3.1 Rollout

For each pick index from the current pick to 150:

- **Our picks** use a greedy continuation: $\arg\max_i \bigl(\Delta(R, i),\; \text{VOR}(i)\bigr)$,
  lexicographically, over feasibility-filtered candidates.
- **Opponent picks** use the opponent model (§4).

### 3.2 Feasibility constraint

If

$$\text{picks remaining} \;\le\; \sum_\tau \max\bigl(0,\, d(\tau) - n_\tau(R)\bigr)$$

candidates are restricted to types with unfilled dedicated slots.

This is a **legality** constraint, not strategy. A kicker is worth ~4 points over a free agent, so it
never wins a value comparison — yet a roster that cannot field one is invalid. Without this the
engine drafts zero kickers, which it did.

### 3.3 Tiebreak

Ties in $\Delta$ break on **value over replacement, never raw payoff**. Raw payoff reintroduces the
central trap: the highest-scoring type (QB) is the least scarce. This tiebreak decides late rounds
outright and is load-bearing.

---

## 4. The opponent model

Opponents claim the $j$-th best available item by consensus order, with

$$j \sim \text{Geometric}(p), \qquad p = \frac{1}{1 + \rho}, \qquad \mathbb{E}[j] = \rho$$

so $j = 0$ (strict consensus) is most likely and larger reaches decay geometrically. Deterministic
autopick seats always take $j = 0$.

---

## 5. Parameters

Every constant, its provenance, and what it is worth. **This table is the primary audit surface.**

| Parameter | Value | Source | Trust |
|---|---|---|---|
| $W$ | 18 | NFL calendar | Structural |
| Bye weeks | 1 | NFL rules | Structural |
| $d(\tau)$, $F$, $\Phi$ | from league config | `league-rules.yaml`, generated from the API | Structural |
| $p(i)$ | vendor forecast × league weights | Regression-tested to <0.5 pts vs the platform's own figure across 300+ items | High on arithmetic, **unknown on forecast quality** |
| GAMES_MISSED | QB 3.4, RB 2.4, WR 3.0, TE 2.6, K 1.8, DEF 0.0 | 2025 realized data, players with ≥8 games | **Medium** — survivorship-biased (see §6.3) |
| WAIVER_THRESHOLD | 150 | $= \text{rounds} \times \text{agents}$, exact | Structural |
| **Waiver contention rank** | $\lfloor N/2 \rfloor = 5$ | **Assumed. No empirical basis.** | **LOW — see §6.1** |
| $\rho$ (opponent reach) | 1.5 | **Unmeasurable from the API** | Low value, but decision-stable (§6.4) |
| $T$ (rollout trials) | 4–60 | Compute budget | Low impact |
| Shortlist width | 80 / 120 | Compute budget | Low impact |

---

## 6. Approximations, ranked by how much I distrust them

### 6.1 Waiver contention rank — **the weakest link**

Term (b) and term (c) both depend on $w(\tau)$, the free agent you could realistically get. Using the
single best undrafted item implies all 10 agents simultaneously hold the same player. The code
instead takes the $\lfloor N/2 \rfloor$-th best. **That choice is an assumption with no evidence
behind it**, and it does real work:

| Contention rank | TE baseline | TE marginal value |
|---|---|---|
| 1 (best undrafted) | 161.0 | 74.0 |
| 3 | 150.9 | 82.1 |
| **5 (current)** | **142.4** | **88.9** |
| 7 | 130.3 | 98.6 |
| 10 | 122.3 | 105.0 |

A **42% swing** in marginal value across plausible settings. In full-draft tests, moving from rank 1
to rank 5 changed roster composition from **0 tight ends to 2**. If one parameter in this system is
wrong in a way that matters, it is most likely this one.

*How to attack it:* it should be derived from how many agents actually need each type after the
draft, not from a fixed fraction. That calculation is doable and has not been done.

### 6.2 Independence of availability

Term (c) treats slot vacancies as independent Bernoulli draws. They are not: teammates share a bye
week, so unavailability is positively correlated within a team. Positive correlation means more
weeks with multiple simultaneous vacancies than the binomial predicts, so bench depth is **slightly
undervalued**. Direction known, magnitude not estimated.

### 6.3 Survivorship bias in GAMES_MISSED

Rates were computed over players with ≥8 games, excluding those who missed most of the season. This
**understates** true missed time. The filter exists to avoid counting fringe players who were never
in a lineup, but it biases the result. Note the empirical ordering contradicts the common assumption:
quarterbacks miss more time than running backs here.

### 6.4 Opponent reach $\rho$ is unmeasurable

Every published ADP field is a *mean* for a different scoring format. `adp_std` is standard-**scoring**
ADP, not a standard deviation — a trap that yields a silently broken simulation. No dispersion data
exists, and no completed 10-team drafts were reachable to calibrate against.

Mitigation is to show the *decision* does not depend on it. In round 1 it does not: across a 7× range
of $\rho$ the ordering of candidates is unchanged, though EV levels move 250 → 291. **Middle rounds
remain untested**, and that is where similar-value candidates with different scarcity profiles could
flip.

### 6.5 The rollout's continuation policy is itself greedy

$U(c)$ evaluates candidates by simulating a *greedy* continuation, which is suboptimal. This biases
all candidates in the same direction, so comparisons are less affected than levels — but a candidate
whose value depends on sophisticated future play is systematically undervalued.

### 6.6 Forecast uncertainty is ignored entirely

$p(i)$ is treated as a point estimate. There is no variance term anywhere in $V(R)$. Since 6 of 10
agents reach the playoffs, outcome variance has real option value that this model cannot see. A
concrete symptom of single-source risk: one running back is projected *below replacement* while the
market claims him 37th overall.

---

## 7. What the algorithm deliberately does not do

- **No hard-coded strategy rules.** "Kicker last" is not in the code; it emerges from term (c). Only
  legality constraints are enforced.
- **No use of VOR as a claim ordering.** VOR is a tiebreak input only. Sorted directly it ranks
  kickers and defenses ~70 positions too high.
- **No automated claim submission.**

---

## 8. Regression history: four bugs, and what each revealed

Each was caught by a *behavioural* test — running a draft and inspecting the roster — not by
inspection or component tests. The component suite passed 16/16 throughout the first two.

| # | Symptom | Cause | Lesson |
|---|---|---|---|
| 1 | 5.9 QBs per roster | Objective priced all 5 bench slots at 0, so an arbitrary tiebreak (raw payoff) decided a third of the draft | A missing objective term, not a tiebreak bug |
| 2 | 5.5 DEFs per roster | Same cause; changing the tiebreak to VOR only moved the symptom | When a fix relocates a symptom, the cause is structural |
| 3 | 0 RBs per roster | Pooling flex types dropped binding type minimums | Over-correction; shared ≠ interchangeable |
| 4 | 0 TEs, then 0 Ks | Waiver baseline too generous; then a genuine feasibility gap | Legality is not the same kind of rule as strategy |

---

## 9. What would falsify this

Concrete, executable checks. **None have been run.**

1. **The backtest (go/no-go).** Claim using 2025 *pre-season* consensus order; score using 2025
   *realized* outcomes. Must never score with the forecast vector that drove the claims, or it wins
   by construction.
2. **Beat a trivial heuristic.** "Take the highest-consensus item that fills an unfilled starting
   slot; kicker last, defense second-to-last." If the rollout engine cannot beat five lines of code,
   the complexity is unjustified. **This is the most likely way this project fails**, and it is a
   more informative baseline than autopick.
3. **Mid-round $\rho$ stability.** Re-run the sensitivity analysis at rounds 5–10.
4. **Contention-rank sensitivity end-to-end.** Vary rank 1–10 through full backtests; if final
   scores move materially, the parameter must be derived rather than assumed.
5. **Seat invariance.** Performance should not depend strongly on seat. A large gap suggests the
   snake-gap logic is wrong.

---

## 10. Backtest results

Claims use 2025 pre-season information only; scores come from 2025 realized outcomes. Controlled
A/B: one seat varies strategy, the other nine run autopick, identical seeds.

### Headline

| Scenario | autopick | optimizer | delta | wins | clustered t |
|---|---|---|---|---|---|
| **Roster managed weekly** | 2302.6 | **2339.1** | **+1.6%** | 42/60 | **3.49** |
| **Never touched after draft** | 2234.6 | 2226.9 | −0.3% | 28/60 | −0.66 |

All six independent seed-clusters favour the optimizer in the managed case (+23, +88, +31, +34, +4,
+39), a sign test at p≈0.016. The margin is real but small, and **entirely conditional on weekly
roster management.**

### The first run was wrong, and the audit is the lesson

The initial result was −6.2%. Four flaws in the *evaluation*, every one biased against the engine:

1. **The engine was benchmarked crippled.** 6 rollout samples and 4 candidates, against defaults of
   60 and 8. That measured a noisy approximation, not the algorithm.
2. **Season length disagreed.** Evaluator used 17 weeks, objective used 18.
3. **Opponent model was unconstrained.** The rollout modelled rivals as pure consensus followers,
   but real rivals stop claiming a type once their slots fill. Only ~10 of 22 viable quarterbacks
   are ever taken, so the model badly overstated scarcity at single-slot types.
4. **The objective and evaluator scored different games.** The objective assumed an unfillable
   starting slot could be covered from the free-agent pool; the evaluator scored it as **zero**.
   Worth ~50 points a season at a one-slot type. This single mismatch is what made carrying a backup
   look mandatory, and it is the whole reason the two scenarios above differ.

**Generalisable lesson: when every strategy you write loses to the baseline, audit the harness before
the strategy.**

### The optimizer's curse, and the fix

A genuine defect was found underneath the artifacts. Measuring realized minus projected payoff for
the players each strategy *selected*:

| Strategy | mean (realized − projected) | QB only |
|---|---|---|
| autopick | −20.6 | −63 |
| optimizer | **−52.4** | **−179** |

Optimizer-selected players underperformed their forecasts by 32 points more per pick — roughly
**480 points of selection bias across 15 picks.**

This is the **optimizer's curse**. Forecasts carry noise; taking the argmax over noisy estimates
preferentially selects items whose error happens to be *positive*, so the winner's realized value
sits systematically below its estimate, and harder optimisation makes it worse. Consensus-following
is immune because its choice is uncorrelated with the forecast's individual errors. **This is how a
better predictor produces worse decisions** — projections beat consensus at Spearman +0.55 vs +0.38,
yet strategies maximising over them lost.

The remedy is shrinkage toward a market-implied prior ([`shrinkage.py`](../ffopt/shrinkage.py)).
Results improved monotonically in λ, and reaching (mean ADP minus pick number) fell from 6.6 to 2.1,
matching autopick's discipline. λ=0.7 is used.

### Why autopick is strong

It is not myopic. It orders by consensus **subject to per-type caps**, and those caps implicitly
encode scarcity: capping the single-slot types forces remaining picks into the types that accumulate
value — exactly what value-over-replacement prescribes. It is a sound strategy expressed as a
constraint rather than an objective. Positional caps are structural, so a capped baseline is the
correct comparison, not an unfair one.

### Truncating the rollout helps

| Horizon | vs autopick | sec/draft |
|---|---|---|
| 3 picks | −1.4% | 3.3 |
| 5 picks | +0.0% | 5.7 |
| **8 picks** | **+1.9%** | 8.2 |
| full (15) | +1.1% | 11.5 |

Truncating at 8 beats simulating the full draft. Deep simulation compounds model error, so the
horizon acts as regularisation. Default is 8.

### Remaining weaknesses

- **Single season.** One draw of the world; 2025 injuries and busts are not 2026's.
- **Clustered observations.** 60 configurations are 6 independent seeds × 10 correlated seats. The
  clustered statistic is reported for that reason; the naive t (1.71) overstates precision in the
  other direction.
- **The margin is small.** +1.6% is not a decisive edge, and it evaporates without weekly management.

## 11. Audit checklist

- [ ] Does $V(R)$ credit any item that cannot be started? (Should be no — term (c) is capped at the
      surplus over a free agent.)
- [ ] Does removing the feasibility constraint produce illegal rosters? (Should be yes — it is
      load-bearing.)
- [ ] Does changing the tiebreak from VOR to raw payoff reproduce QB hoarding? (Should be yes.)
- [ ] Is any strategy rule hard-coded outside `mandatory_filter`? (Should be no.)
- [ ] Do the empirical rates in §5 match a fresh recomputation from the realized-stats endpoint?
- [x] Does the engine beat autopick? **Only when the roster is managed weekly (+1.6%). Without
      management it is indistinguishable. See §10.**
- [x] Why did a strategy drafting better players score worse? **Answered: the objective assumed an
      empty slot could be covered from waivers while the evaluator scored it zero.**
- [x] Is selection bias from maximising over noisy forecasts controlled? **Partly — shrinkage at
      λ=0.7; the residual is unmeasured.**
- [ ] Does the result hold in a season other than 2025? **Untested — the largest open risk.**
