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

## 3a. The web interface

The terminal tool remains, but the primary surface is now a browser interface
(`scripts/serve.py`). It exists because a timed draft is a poor moment to read
scrolling terminal output, and because recovery from a mis-entry needs to be a
click rather than a remembered command.

Three modes, differing only in who controls the board:

| Mode | Feed | Board control |
|---|---|---|
| live | polled | applied automatically |
| assisted | polled | **proposed for acceptance** |
| manual | not touched | operator only |

Assisted mode is the interesting one. A feed that lags the room, or disagrees
with it, would otherwise silently overwrite a board the operator has been
maintaining by hand. Instead it surfaces the difference -- "the feed has 3 picks
you do not", or "you have X at pick 24, the feed says Y" -- and waits.

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

## 7. Encoded guardrails — and which ones are actually justified

Football common sense the operator cannot supply. But "hard constraint" is a strong claim, so each
rule below was tested rather than assumed. **They are not all the same kind of rule**, and treating
them uniformly was a mistake in an earlier draft of this document.

### Three categories

| Category | Enforcement | Why |
|---|---|---|
| **Legality** | Hard block | Violating produces an invalid action the platform rejects |
| **Structural** | Hard block | Provably always true given the rules — a theorem, not a heuristic |
| **Empirical** | **Warning** | Contingent on this season's data. Strength = the measured margin |

### Measured opportunity cost (seat 5, median best-available VOR by round)

| Round | Best non-K/DEF | Best DEF | Best K |
|---|---|---|---|
| 8 | 24.0 | 18.0 | 4.0 |
| **9** | **7.8** | **18.0** | 4.0 |
| 12 | −0.8 | 10.0 | 4.0 |
| 15 | −7.7 | 4.0 | 2.0 |

The correct comparison is not the level but the **cost of waiting** from round 9 to round 15:

| | Value now | Value if we wait | Cost of waiting |
|---|---|---|---|
| Kicker | 4.0 | 2.0 | **2.0** |
| Defense | 18.0 | 4.0 | **14.0** |
| Everything else | 7.8 | −7.7 | **15.5** |

### Verdicts

| Rule | Verdict | Evidence |
|---|---|---|
| **Kicker last** | **Justified — hard block** | Waiting costs 2.0 points. 150 kickers go undrafted. Effectively free |
| **Defense second-to-last** | **NOT justified — downgrade to warning** | Waiting costs 14.0 vs 15.5 for the field: nearly a wash. From round 9 the best defense (+18) genuinely beats the best remaining player (+7.8). Only 25 defenses go undrafted, so supply is far tighter than kickers |
| **Never a 2nd QB** | **Justified — but scoped, and for a different reason than assumed** | A backup covering the starter's single bye week is worth ~17 points, comparable to the best defense — so "no value" is wrong. It is justified only because **338 QBs go undrafted**, so one can be added from waivers for that week at zero draft cost. Scope the rule to "not before starting slots are filled" |
| **Never a 3rd TE** | Justified — same waiver logic | Surplus at a 1-slot type, with deep waivers |
| **Starters before bench** | **Structural — hard block** | An unfilled starting slot scores zero; a bench item also scores zero. Filling bench first is dominated by definition |
| **Bye-week collision** | Warning (already correct) | Only 5 bench slots to absorb it; depends on the specific roster |
| **Already-claimed item** | **Legality — hard block** | Guards against a stale board |

### The underlying principle

**If the objective function is correct, most of these should emerge on their own.** A guardrail that
duplicates a correct objective is redundant; one that *contradicts* it is masking a modelling bug.
Hard-coding an empirical heuristic destroys the signal — if the optimizer wants a kicker in round 2,
that is a bug worth seeing, not suppressing.

So empirical rules are **warnings that surface the reasoning**, not silent blocks:

```
   NOTE  Defense (+18.0) currently outranks the best available player (+7.8).
         This is unusual before round 12 but is supported here: waiting until
         round 15 costs 14 points, and only 25 defenses go undrafted.
```

The one concession to the operator's inability to detect errors (§1): a **circuit breaker** on
catastrophic actions — claiming a kicker in the first half of the draft, or a fourth player at a
one-slot type. It blocks and logs loudly. It exists to catch a crash-level bug at 4pm tomorrow, not
to encode strategy.

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
| Network drops mid-draft | After 3 failed polls the tool switches itself to manual entry, seeded with the picks already retrieved. Type a surname as each pick happens; lookup is fuzzy, so "gibbs" or even "gibs" resolves. Ambiguous names offer a numbered choice. `undo` reverses a mistype, and the board is saved after every entry so a crash loses nothing |
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
