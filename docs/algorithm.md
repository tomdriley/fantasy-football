# The Algorithm

A precise specification of the decision engine, written to be **audited and attacked**. Where the
implementation rests on an assumption, this document says so and states how much the assumption is
worth in points.

Companion to [architecture.md](./architecture.md) (how the system is structured) and
[draft-strategy-plan.md](./draft-strategy-plan.md) (why this approach). This document is the one
that matters for correctness.

> **Status: VALIDATED ACROSS FIVE SEASONS, WITH ADVERSARIAL CHECKS.** Against realized outcomes for
> 2021–2025 the engine beats the platform's autopick by **+9.0%** and a competent human heuristic by
> **+10.0%**, winning 172/200 configurations (t=15.8, bootstrap 95% CI [+186, +239] points, excluding
> zero). It is positive in **every** season. It survives a placebo test (edge vanishes to t=0.63 when
> outcomes are decoupled from picks) and a mixed-population test (+9.1% when half the league also
> optimises, so it is not merely exploiting a weak opponent). The gain is distributed across
> positions and is *less* dependent on any single player than the baseline. Two further seasons were
> examined and **excluded for data contamination**. See [§10](#10-backtest-results).

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
| $T$ (rollout trials) | 30 backtest / 120 live | Measured convergence (§6.7) | Low impact on the *mean*, decisive for a *single* draft |
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

Mitigation is to show the *decision* does not depend on it. Tested across an 8× range of $\rho$
(0.5, 1.5, 4.0) at rounds 1, 3, 5, 7, 9 and 11:

| Round | 5 | 25 | 45 | 65 | 85 | 105 |
|---|---|---|---|---|---|---|
| stable? | yes | yes | yes | yes | yes | **flip** |

The recommendation is identical in five of six rounds. The single flip is at round 11, between two
near-equivalent late bench options where the objective values differ by a fraction of a point. The
decision is therefore stable wherever it carries stakes, which is the property needed given the
parameter cannot be measured.

### 6.5 The rollout's continuation policy is itself greedy

$U(c)$ evaluates candidates by simulating a *greedy* continuation, which is suboptimal. This biases
all candidates in the same direction, so comparisons are less affected than levels — but a candidate
whose value depends on sophisticated future play is systematically undervalued.

### 6.6 Forecast uncertainty is ignored entirely

$p(i)$ is treated as a point estimate. There is no variance term anywhere in $V(R)$. Since 6 of 10
agents reach the playoffs, outcome variance has real option value that this model cannot see. A
concrete symptom of single-source risk: one running back is projected *below replacement* while the
market claims him 37th overall.

### 6.7 Monte Carlo noise is not free, even though the mean says it is

$\hat{V}$ is a sample mean over $T$ rollouts, so it carries sampling error. That error cancels when
averaging 200 backtest drafts — which is precisely why $T=30$ looked harmless for two months of
development. It does not cancel in a single live draft, which draws exactly one sample.

Measured on the opening board, holding the board fixed and varying only the random seed:

| $T$ | ms/call | Seeds agreeing on the top pick |
|---|---|---|
| 30 | 180 | 7 / 9 — *flipped between a 144-value RB and a 43-value QB* |
| 60 | 357 | 9 / 9 |
| 120 | 707 | 9 / 9 |
| 250 | 1491 | 9 / 9 |
| 500 | 2940 | 9 / 9 |

The instability is not a defect in the estimator; it is the estimator honestly reporting that those
two candidates are within ~2 points of each other. But the operator cannot act on a distribution.
Two things follow:

1. **The live path is seeded from the board position**, so the same board always yields the same
   advice. Refreshing, changing mode, or reconnecting cannot silently reorder the recommendation.
2. **$T = 120$ live**, double the measured convergence point.

Raising $T$ was verified not to disturb the validated result — 250 controlled drafts, engine in one
seat against nine autopickers, five seasons × five seats × five repetitions:

| $T$ | Drafts | Mean edge | Wins | Edge % |
|---|---|---|---|---|
| 30 | 125 | +246.9 | 108 / 125 | +11.0% |
| 120 | 125 | +247.6 | 114 / 125 | +11.1% |

The difference in mean edge is statistically indistinguishable ($\Delta = +0.7$ points, $t = +0.03$).
More samples buy reproducibility, not accuracy — the win count edges up (114 vs 108) only because
fewer drafts are lost to an unlucky rollout.

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

### Re-validated 2026-09-03 under changed league scoring

Hours before the draft the league switched kicker scoring from fixed per-field-goal buckets to
distance-based (`fgm_yds: 0.1`). That changes every payoff, so the result below had to be
re-established rather than assumed. The golden-file guard caught it: 18/18 replayed drafts changed
behaviour, which is exactly what that file exists to detect.

Re-run over the same five seasons, 100 configurations (5 seasons x 10 seats x 2 repetitions):

| Season | autopick | engine | edge | wins |
|---|---|---|---|---|
| 2021 | 2327 | **2470** | **+6.1%** | 16/20 |
| 2022 | 2267 | **2531** | **+11.6%** | 20/20 |
| 2023 | 2268 | **2529** | **+11.5%** | 20/20 |
| 2024 | 2203 | **2602** | **+18.1%** | 20/20 |
| 2025 | 2235 | **2306** | **+3.1%** | 11/20 |
| **pooled** | **2260** | **2487** | **+10.1%** | **87/100** |

Mean edge **+227 points, t = 11.30**, positive in every season. The old bucket keys did not match
the forecast feed's `fgm_50p`, so long field goals had been scoring zero; the corrected rule raises
the best kicker's value over replacement from +4 to +12 — still the least valuable position on the
board by an order of magnitude, so no decision logic changed shape.

### Headline: five seasons, 200 controlled configurations (original scoring)

| Season | autopick | heuristic | optimizer | vs autopick | vs heuristic |
|---|---|---|---|---|---|
| 2021 | 2371 | 2356 | **2570** | **+8.4%** | +9.1% |
| 2022 | 2313 | 2408 | **2564** | **+10.8%** | +6.5% |
| 2023 | 2341 | 2297 | **2588** | **+10.6%** | +12.7% |
| 2024 | 2361 | 2370 | **2679** | **+13.5%** | +13.0% |
| 2025 | 2301 | 2154 | **2342** | **+1.8%** | +8.7% |
| **pooled** | **2337** | **2317** | **2548** | **+9.0%** | **+10.0%** |

172/200 configurations won, t=15.8, **bootstrap 95% CI [+186, +239] points** — comfortably excluding
zero. `heuristic` is the honest human baseline (fill starters first by consensus, kicker last,
defense second to last); it is reported here because beating only a machine default would be a weak
claim.

**Parameters (λ=0.7, horizon=8) were tuned on 2025 alone**, which is the engine's *weakest* season.
Overfitting produces the opposite pattern. The claim is nonetheless narrower than "out-of-sample":
the objective's design and the `GAMES_MISSED` constants were also chosen while looking at 2025, so
this is *parameter*-out-of-sample, not *design*-out-of-sample. Mitigating that, the injury rates were
re-derived from each season independently and vary by at most 0.8 games from the constants in use —
so the engine ran on slightly wrong rates in four of five seasons and won anyway.

### Data integrity: two seasons excluded

Historical projections were audited for contamination before use, because a large out-of-sample gain
is more often a broken harness than a good model. **2020 is unusable**: 78% of its "projections"
match realized outcomes within 1 point, and rank correlation to outcomes is 0.966. That is hindsight,
not forecasting. 2019 also sits apart from the clean cluster. Among players who actually produced,
the exact-match rate is 1–2% for 2021–2025 against 17% for 2019–2020.

An earlier version of this audit flagged *every* season as suspect at ~10%, which was a false
positive: it counted players who scored near zero and were projected near zero, who trivially agree.
Restricting to producing players separates the signal cleanly.

### The waiver assumption: pessimism dominates

Whether an unfilled starting slot can be covered by adding a free agent that week was an open
question, intended to be settled by asking the operator. Measurement removed the question:

| Objective assumes | if roster IS managed | if roster is NOT managed |
|---|---|---|
| streaming available (realistic) | +7.9% | +7.7% |
| **empty slot scores zero (pessimistic)** | **+9.1%** | **+10.0%** |

The pessimistic assumption is not a hedge trading average for safety — it **dominates in both
worlds**. Mechanism: crediting an empty slot at free-agent level makes leaving one empty look cheap,
which leaves a mandatory slot unfilled in ~1/3 of drafts. Assuming it scores nothing fills every slot
every time, and that slot is worth more than the marginal player gained by skipping it.

Deciding and scoring therefore use *different* waiver assumptions, which is correct rather than
inconsistent: the realistic baselines describe what a roster is worth, the pessimistic one produces
better decisions.

### Exploiting the deterministic seats: a negative result

Some seats are unowned and run the platform's autopick, which follows consensus
order exactly. (Four were unowned when this was measured; two remained by the
morning of the draft. The count is read from the live league, never assumed.) That is a perfectly predictable opponent, and
the rollout can be told either how *many* such seats exist or exactly *which*
ones. The distinction is not cosmetic: the frequency approximation is badly
miscalibrated for a middle seat, where every pick between the first and second
turn is a bot but the model treats 44% of them as such.

It makes no measurable difference.

| Bot model | pooled score | t vs baseline |
|---|---|---|
| none (0 bots assumed) | 2505.3 | — |
| count only (4 bots) | 2504.9 | −0.04 |
| exact seats {7,8,9,10} | 2514.5 | +0.84 |

Tested again with far more erratic human opponents, the condition under which
bots and humans differ most and positional knowledge should pay off best:

| Human behaviour | blind | aware | t |
|---|---|---|---|
| as modelled | 2501.5 | 2501.5 | 0.00 |
| much more erratic | 2491.5 | 2503.2 | 0.64 |

The likely explanation is that the rollout averages over many simulated drafts,
and a bot taking the consensus-best player differs little from a human who
usually takes it, so the distinction washes out. The capability is retained
because it is a more faithful model and is directionally positive, but **it is
not claimed as an improvement**.

Worth noting separately: this investigation found that every backtest to that
point had run with zero modelled bots, because the parameter was being silently
discarded. The validated result was therefore achieved while treating all nine
opponents as humans.

### How often does it actually disagree with the baseline?

Measured across 1,350 decisions (90 drafts, 3 seasons), with both strategies
shown the **identical board** at each of our turns so disagreement is isolated
from downstream drift:

**They pick differently 75% of the time** — and when they do, the chosen player
is worth **+21 points more over replacement** on average (+21.8 vs +1.1), while
sitting at a nearly identical market rank (85 vs 81). The engine is not reaching
past consensus; it is choosing *differently within* it.

Disagreement rises through the draft — 54% in round 1, 89% by round 10 — because
early on the best player and the most valuable player usually coincide, and later
they routinely do not.

The most valuable swaps:

| Swap | Count | Avg value gained | Avg round |
|---|---|---|---|
| RB → DEF | 52 | **+56.6** | 10.6 |
| RB → QB | 78 | **+49.7** | 10.2 |
| RB → WR | 208 | **+38.7** | 7.7 |
| WR → TE | 42 | +15.4 | 7.6 |

Every large gain has the same shape: autopick takes a **fourth running back**
because its cap allows four, while the roster still has no quarterback, tight
end or defense. Its rule — *highest consensus whose position is not at cap* —
cannot see that a position is effectively full, cannot see how replaceable a
player is, and treats a mandatory slot as just another cap.

The replacement levels it never computes are what drive every one of those swaps:

| Position | Best available | Obtainable free later | Difference |
|---|---|---|---|
| RB | 313 | 86 | 227 |
| WR | 301 | 111 | 190 |
| TE | 229 | 130 | 98 |
| QB | 338 | 242 | 97 |
| DEF | 113 | 98 | 16 |
| K | 76 | 73 | **4** |

### The live feed is CDN-cached (measured 2026-09-03, draft day)

Everything up to this point tested the polling path against a mock server, which proves the client
parses the schema but says nothing about the real endpoint. The vendor documentation describes the
endpoint's shape and is silent on liveness and caching. Inspecting response headers directly:

    cache-control: public, s-maxage=30, stale-while-revalidate=300
    cf-cache-status: HIT
    age: 26

An A/B against the live endpoint, 8 polls each one second apart:

| Polling | Cache HIT | MISS | Worst staleness |
|---|---|---|---|
| plain URL | 8/8 | 0 | **18 s** |
| unique query parameter | 0 | 8/8 | **0 s** |

On a 60-second pick timer, 18 seconds is nearly a third of a pick of lag arriving *before any of our
code runs*, and `stale-while-revalidate=300` permits up to five minutes. The fix is a distinct cache
key per request; the cost is origin traffic, and polling at 1.5-5 s is 12-40 requests per minute
against a documented budget of 1000.

**Resolved by direct measurement.** A throwaway league was created and drafted 25 minutes before
the real event, with `scripts/probe_feed.py` polling the feed once a second:

    pick  gap    rtt    cache  age  player
       1    -    0.22s  MISS    -   Jahmyr Gibbs (RB)
       2  1.2s   0.18s  MISS    -   Bijan Robinson (RB)
       3  1.2s   0.19s  MISS    -   Puka Nacua (WR)
       4  1.2s   0.18s  MISS    -   Jonathan Taylor (RB)

One new pick per poll, each arriving individually rather than in a batch, every response served from
origin with no staleness. **The feed publishes picks as they happen**, so assisted mode is viable.

Two caveats the measurement does not cover. The observed picks were bot autopicks, which may be
written by a different path than a human's click. And 24 picks is a small sample. Manual entry
remains the default for the real draft because it depends on none of this; the value of the result
is that assisted mode is now a justified fallback rather than a hopeful one.

A bug in the probe is worth recording, because it produced a confident wrong answer: a later run
started against a board that already held 24 picks, saw them all in its first poll, recorded 23 gaps
of 0.0s and reported "picks arrived in batches -- consistent with a feed written behind the room."
It was measuring its own start time. Backlog is now counted and excluded, and fewer than three
timed arrivals reports inconclusive rather than guessing.

## 10a. The actual draft, 2026-09-03

The engine drafted seat 3 in the real event, in live mode. Scored with its own objective against the
nine rosters it competed with:

| Rank | Seat | Value |
|---|---|---|
| **1** | **3 (this engine)** | **2196.5** |
| 2 | 1 | 2083.4 |
| 3 | 4 | 2026.4 |
| 4 | 6 | 2017.6 |
| 5 | 8 | 1995.0 |
| 6 | 2 | 1994.2 |
| 7 | 7 | 1983.3 |
| 8 | 9 | 1947.7 |
| 9 | 5 | 1912.0 |
| 10 | 10 | 1910.0 |

**First of ten, +10.6% over the field average** — against a backtest prediction of +10.1%. The
roster is legal with every slot fillable, and totals +532 points over replacement.

This is one sample and is *not* independent confirmation of the +10% figure: the same objective
scored both the picks and the outcome, so it measures internal consistency, not truth. The season
result is the real test.

What is genuinely informative is that the specific failure modes the model predicted appeared
unprompted in the field, and cost what it said they would:

| Trap | Who | Where they finished |
|---|---|---|
| Quarterback in round 1 | seat 2 | 6th |
| Quarterback in round 3 | seat 5 | 9th |
| Kicker in round 8 | seat 10 | **10th** |
| Kicker in round 10 | seat 8 | 5th |
| **Never drafted a kicker at all** | seat 7 | 7th |

Teams that spent an early pick on a quarterback or kicker averaged 1953; teams that did not averaged
2042. **The trap cost 90 points**, which is the mechanism in §10 (`RB → QB`, `RB → DEF` swaps) showing
up in a live room rather than a simulation.

Seat 7 is the clearest case: five running backs and five receivers, and no kicker at all. That is the
"never end the draft unable to fill a slot" rule being violated exactly as described — a mandatory
slot scoring zero every week for the whole season.

### Adversarial checks

| Test | Result | What it rules out |
|---|---|---|
| **Placebo** — realized outcomes shuffled within position | +2.6%, **t=0.63** | An artifact of roster *shape* rather than player selection. A real edge must vanish here, and it does |
| **Mixed population** — 5 optimizer seats vs 5 autopick seats | **+9.1%**, t=9.7 | That the edge is merely exploiting a predictable opponent. It survives smart rivals |
| **Per-position attribution** | RB +91, WR +43, QB +25, TE +1, K −1, DEF −4 | Concentration in one position. The gain is distributed across the high-slot-count types |
| **Leave-one-out** | optimizer depends on its best player for 7.5% of score; autopick 8.2% | That one lucky pick drives the result. The optimizer is *less* single-player dependent |

**The edge tracks forecast quality.** The engine leverages projections; autopick follows consensus.
In years when projections ranked outcomes well (2024, Spearman 0.693) the edge was large; in the
year they ranked poorly (2025, 0.533) it nearly vanished. 2026 forecast quality is unknowable in
advance, so the honest expectation for tomorrow is *somewhere in the +2% to +14% range, and the low
end is entirely possible*.

The ordering holds whether or not the roster is managed weekly, so the earlier waiver-dependence was
an artifact of the single weak season rather than a structural property.

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

- **Five seasons share one vendor and one era.** All use the same forecast provider and scoring
  format. A structural change in 2026 would not be captured, and 2019–2020 could not be used to
  widen the window because their data is contaminated.
- **Design-out-of-sample is not established.** The objective and its constants were shaped while
  looking at 2025. Only λ and the horizon are strictly held out.
- **The edge is not stable.** It ranged from +2% to +14% across three seasons and is a function of
  forecast quality, which is unknown ahead of the draft.
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
