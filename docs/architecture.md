# Technical Architecture

Companion to [draft-strategy-plan.md](./draft-strategy-plan.md) (why) and
[league-rules.md](./league-rules.md) (what the rules are). This document covers **how the system is
built**.

## 1. Design drivers

| Driver | Consequence |
|---|---|
| Hard 60-second decision deadline | Everything expensive is precomputed and cached; live path is read-only and sub-second |
| Draft happens once, cannot be retried | Degrade rather than crash; every layer has a fallback, ending in a sheet of paper |
| League rules must never be re-typed | A single generated YAML is the source of truth; all code reads it |
| Forecasts come from one vendor | Valuation and decision layers are separated so the forecast can be swapped or blended |
| Operator cannot evaluate football claims | Domain guardrails are encoded as hard constraints, not left to judgement (see [product-design.md](./product-design.md)) |
| No scientific Python available | Pure standard library plus `pyyaml`. Zero install risk on draft day |

## 2. Layering

Dependencies point strictly downward. No cycles.

```
 L6  presentation      cli / live tool / cheat-sheet generator
                              |
 L5  decision          optimizer            <- chooses the claim
                              |
 L4  opponent model    availability + Monte Carlo simulator
                              |
 L3  static value      valuation            <- replacement levels, VOR, tiers
                              |
 L2  normalization     pool  <-  scoring    <- items and the payoff function
                              |
 L1  acquisition       client               <- cached HTTP
                              |
 L0  configuration     config  <-  docs/league-rules.yaml   [SOURCE OF TRUTH]

 cross-cutting:  backtest  (drives L5 against historical data; never imported by L0-L5)
```

The rule that keeps this honest: **lower layers never import higher ones.** `valuation` knows
nothing about the draft or opponents; `optimizer` knows nothing about presentation. This is what
makes the backtest possible — it substitutes its own driver at L6 and replays L5 against 2025 data.

## 3. Module responsibilities

| Module | Owns | Deliberately does *not* own |
|---|---|---|
| `config.py` | Typed access to league rules; snake pick schedules; bot-seat identification | Any hardcoded rule value |
| `client.py` | HTTP, disk cache, TTL policy, offline degradation | Interpreting payloads |
| `scoring.py` | The linear payoff function | Anything about types or scarcity |
| `pool.py` | Item construction, filtering, grouping by type | Value judgements |
| `valuation.py` | Replacement baselines, VOR, tier detection | *When* an item can be obtained |
| `availability.py` *(next)* | P(item survives to my next turn); Monte Carlo draft simulation | What to do about it |
| `optimizer.py` *(next)* | Expected constrained-lineup value; the claim decision | Rendering |
| `backtest.py` *(next)* | Non-circular historical validation | Being imported by production code |

## 4. Data flow

**Pre-draft (offline, seconds):**
```
API ──> client ──> disk cache ──> pool ──> valuation ──> precomputed artifact
                                                              │
                          docs/league-rules.yaml ──> config ──┘
```

**Live (per pick, sub-second):**
```
pick feed ──> board state ──> availability ──> optimizer ──> ranked recommendations
     ▲              ▲                              ▲
   117ms      opponent rosters            cached valuation (60ms)
```

## 5. Performance budget

Measured on the target machine, warm cache:

| Stage | Time |
|---|---|
| Load config (YAML) | 7.3 ms |
| Load projections (cache hit) | 41.9 ms |
| Build item pool (3,303 items) | 8.9 ms |
| Replacement baselines | 0.8 ms |
| Tier detection | 0.8 ms |
| **Full valuation pipeline** | **59.7 ms** |
| Live pick feed (network, median) | 117 ms |

**Total machine time per decision: ~0.2 s of a 60 s budget (0.3%).**

This is the single most important architectural finding: **the deadline is not a compute
constraint.** Even a Monte Carlo simulation of thousands of trials fits comfortably. The binding
constraint is human reading and clicking time, which is why the remaining 59.8 s belongs to the
product design, not the engine. Optimizing the code further would be effort spent on the wrong 0.3%.

Corollary: we can afford a *far* richer opponent model than originally planned. Budget is not the
reason to keep the simulation simple — the unmeasurable dispersion parameter is.

## 6. Caching strategy

| Data | TTL | Rationale |
|---|---|---|
| Item pool / projections / historical stats | 12 h | Large, effectively static during the draft |
| League config | 12 h | Fixed once the season starts |
| Draft metadata (seat assignment) | 60 s | Changes exactly once, when order is assigned |
| **Live pick feed** | **never cached** | Correctness depends entirely on freshness |

Two deliberate properties:

- **Atomic writes.** Cache entries are written to a temp file and `replace()`d, so an interrupted
  write cannot leave a truncated JSON file that poisons the next run.
- **Stale-on-failure.** If the network fails, a stale cache entry is returned rather than raising.
  A slightly outdated board beats a crashed tool with 40 seconds on the clock. The live pick feed is
  exempt — stale picks would be actively wrong, so it fails loudly instead.

## 7. Failure modes and degradation

Ordered by severity. Each layer falls back to the one below.

| Failure | Detection | Degradation |
|---|---|---|
| Projection endpoint down | Fetch raises | Serve from 12 h cache |
| Pick feed slow/unavailable | 10 s timeout | Manual board entry; recommendations still computed |
| Draft order differs from expectation | Cross-check `slot_to_roster_id` | Seat is a runtime parameter; recompute |
| Optimizer throws | Exception at L5 | Fall back to static tier ordering from L3 |
| Python environment broken | Tool won't start | **Printed cheat sheet** — the final fallback |
| Operator distracted / timer expires | — | Platform autopick (the baseline we're beating) |

The printed sheet exists because every software fallback shares a single point of failure: the
machine. Paper does not.

## 8. Testing strategy

| Kind | What it protects |
|---|---|
| **Regression against platform** | Independently computed payoff must reproduce the vendor's own figure (<0.5 pts across 300+ items). Catches any drift in the payoff function |
| **Invariant tests** | Roster slots sum to roster size; all 10 seats' schedules cover all 150 picks exactly once |
| **Trap tests** | Pin that the highest-raw-payoff type is *not* the most valuable, and that kicker VOR stays negligible. Prevents silently reintroducing the failure the whole system exists to avoid |
| **Sensitivity tests** | Decisions must be stable across the unmeasurable dispersion parameter |
| **Backtest** | End-to-end, non-circular: claim on 2025 pre-season consensus, score on 2025 realized outcomes |

Current: 16 tests, ~0.5 s.

## 9. Key invariants

1. `docs/league-rules.yaml` is the only place league rules exist. Code that hardcodes a rule is a bug.
2. Layers never import upward.
3. `adp_*` fields are **never** treated as scoreable stats, and `adp_std` is standard-*scoring*
   ADP, not a standard deviation.
4. VOR is an input to the decision, never the claim order itself.
5. Guardrails are typed: legality and structural rules block; empirical heuristics only warn.
   An empirical rule that has to block is evidence of a bug in the objective function.
6. The backtest never scores with the same forecast vector that drove its claims.

## 10. Deferred / out of scope

- Automated claim submission — the operator clicks; the tool advises.
- In-season lineup and waiver management — a different optimization problem.
- Multi-source forecast blending — the interface allows it; time before the draft does not.
