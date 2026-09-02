# Product Design

Companion to [architecture.md](./architecture.md) (how it's built) and
[draft-strategy-plan.md](./draft-strategy-plan.md) (why this approach). This document covers **what
the operator actually experiences** and the design constraints that follow.

## 1. Who this is for

A single operator: an engineer with **no knowledge of American football or fantasy football**,
drafting live against 9 opponents under a 60-second per-pick deadline, in a league where the team is
named "Git Blame Copilot" and the explicit premise is that decisions are delegated to software.

This user profile is unusual and drives nearly every design decision below. In particular:

> **The operator cannot detect a wrong recommendation.**

If the tool says "claim the kicker in round 2", a domain expert would immediately recognise it as
absurd. This operator would not. Every safeguard that a knowledgeable user provides implicitly must
instead be **encoded in the product**.

## 2. The real time budget

Measured: the full valuation pipeline runs in **60 ms** and the live pick feed returns in **117 ms**.

```
   60 s deadline
   ├─ ~0.2 s   machine  (0.3%)
   └─ ~59.8 s  human    (99.7%)   <- the actual design target
```

**The deadline is a human-attention constraint, not a compute constraint.** Effort spent making the
engine faster is effort spent on 0.3% of the problem. Effort spent reducing what the operator must
read, understand, and decide is effort spent on the other 99.7%.

Consequences:
- Present **3 options, not 200**. Ranking 200 items is the engine's job; reading 200 is nobody's.
- The answer must be on screen *before* the turn arrives, computed during opponents' picks.
- Every element on screen must earn its place in a stressed 20-second read.

## 3. Interaction model

The tool **advises; the operator clicks**. It never submits a claim. Two reasons: it keeps the
operator in control of their own team, and it avoids automating actions against the platform.

```
opponent picks ──> tool updates board ──> tool precomputes our next decision
                                                    │
                          our turn starts ──────────┴──> operator reads (~20 s)
                                                              │
                                                    operator clicks in the app (~10 s)
                                                              │
                                                     ~30 s safety margin
```

The tool runs in a second window beside the draft app. It is a **read-only advisor over a live
feed** — no login, no credentials, no writes.

## 4. The primary screen

Designed for a 20-second read under stress:

```
  YOUR PICK — Round 2, Pick 16                              [ 42s ]

   #  PLAYER               POS   VALUE   TIER    ADP   SURVIVES?
   1  Chase Brown          RB    +89.8   RB-4     15      32%     << TAKE
   2  A.J. Brown           WR    +77.1   WR-4     18      55%
   3  Derrick Henry        RB    +76.4   RB-4     21      69%

   WHY   RB tier 4 has 3 players left and 9 picks until your next turn.
         Expect 0-1 to survive. WR tier 4 has 4 left -> safer to wait.

   ROSTER  QB_  RB1 RB_  WR_ WR_  TE_  FLX_ FLX_  K_  DEF_
   SANITY  pick is within 3 of market consensus ....... OK
   STAKES  gap between #1 and #3 is 13 pts ............ LOW - decide fast
```

Each element exists for a specific reason:

| Element | Purpose |
|---|---|
| **VALUE** | Points above a freely-available replacement. The actual objective |
| **TIER** | Which value plateau. Makes "these are interchangeable" visible at a glance |
| **ADP** | Market consensus. The operator's only independent sanity signal |
| **SURVIVES?** | P(still available at our next turn). Converts "who's best" into "who's at risk" |
| **WHY** | One sentence of scarcity reasoning, in plain language |
| **ROSTER** | Which slots remain unfilled — prevents drafting into a constraint violation |
| **SANITY** | Trust calibration (§5) |
| **STAKES** | Whether this decision matters at all (§6) |

## 5. Trust calibration: making a non-expert able to audit

Since the operator cannot evaluate football claims directly, the product must supply an
independent check they *can* evaluate. Market consensus is that check.

- **SANITY line.** Every recommendation is compared against consensus draft position. A pick within
  ~1 round of consensus is normal. A pick deviating by 40+ positions is flagged loudly with the
  reason, because that is exactly the shape of a forecast error — the Josh Jacobs case in
  [league-rules.md](./league-rules.md) §7, where a single-source projection put a player below
  replacement while the market claimed him 37th overall.
- **No silent extrapolation.** If forecast and consensus disagree sharply, the tool says so rather
  than quietly picking a side.
- **Stable recommendations.** A recommendation that flips between refreshes destroys trust. The
  displayed ordering is stable unless the board actually changes.

## 6. Telling the operator when the decision does *not* matter

A non-obvious but high-value feature. Tier analysis shows the board has sharp plateaus: RB tier 1
holds exactly two players, then a 34-point cliff. Within a tier, choices are near-equivalent.

- **STAKES: LOW** — top options are within a few points. *Any* is fine; decide in 5 seconds and bank
  the time.
- **STAKES: HIGH** — a tier cliff is about to break. This is where attention belongs:
  `"2 players left in RB tier 1 and 11 picks until your turn — likely your last chance at this tier."`

This directly serves the 99.7% human budget: it spends the operator's limited attention only on
decisions that actually move the outcome.

## 7. Encoded guardrails

Football common sense the operator cannot supply, implemented as hard constraints:

| Guardrail | Rationale |
|---|---|
| Never recommend K before the final round | Best kicker is worth ~4 points across an entire season |
| Never recommend DEF before the second-to-last round | Best defense is worth ~18 points |
| Never recommend a 2nd QB, or a 3rd TE | Only one starts; surplus scores zero |
| Never fill all bench slots before all starting slots have a candidate | Unfielded items score zero |
| Warn when a claim creates a bye-week collision among starters | Only 5 bench slots to absorb it |
| Refuse claims already made by another agent | Guards against a stale board |

These are constraints, not preferences: the optimizer cannot trade them away for expected value.

## 8. Pre-draft deliverable: the printed sheet

Because every software fallback shares one point of failure — the machine — the final fallback is
paper. It is generated ahead of time and printed.

Contents:
- Tiered board with explicit **cliff markers** (the boundaries that matter)
- A round-by-round positional plan for the assigned seat, with a general plan for all 10 until the
  seat is known
- The guardrails from §7 restated as simple rules ("kicker last, defense second-to-last")
- The 4 bot seats noted, since their behaviour is deterministic

Design constraint: usable by someone who knows nothing about football, with no computer, in 60
seconds. That means **tiers and rules**, not a ranked list of 200 names.

## 9. Failure modes, from the operator's point of view

| What they experience | What the product does |
|---|---|
| Tool shows stale board | Timestamp on screen; loud staleness warning past 15 s |
| Network drops mid-draft | Serve last-known board, clearly marked; recommendations still computed |
| Tool crashes | Printed sheet |
| Recommendation looks strange | SANITY line explains the deviation and its cause |
| Timer nearly expired | Big clear "TAKE" marker on option 1 — one unambiguous action |
| Opponent takes our target | Board updates; next recommendation ready before our turn |

## 10. Success criteria

1. **Beats the autopick baseline** in the non-circular backtest across many seeds and all 10 seats.
   This is the go/no-go gate — everything else is secondary.
2. **All 15 picks made within the timer.** Zero autopicks.
3. **Zero absurd picks.** No guardrail violations; no claim deviating wildly from consensus without
   a displayed reason.
4. **Operator can explain any pick afterwards** in one sentence. The premise of the team is
   AI-assisted decisions, so the reasoning has to survive coworker scrutiny.

Explicit non-goal: winning the league. That depends on realized outcomes we do not control. The
controllable objective is *drafting well*, which is what the backtest measures.

## 11. Non-goals

- **Automated claim submission.** The operator clicks.
- **In-season management.** Lineups, waivers, and trades are separate problems.
- **A general-purpose tool.** This targets one league, one ruleset, one draft.
- **Teaching football.** The operator should need no domain knowledge at any point — that is the
  product working, not a gap in it.
