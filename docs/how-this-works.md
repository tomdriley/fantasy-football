# How This Works — Plain Language

Written for someone with **no knowledge of American football or fantasy football**. Start here.

Other documents: [algorithm.md](./algorithm.md) is the mathematics,
[league-rules.md](./league-rules.md) is the rulebook, [product-design.md](./product-design.md) is
the draft-day interface.

---

## 1. You never play football

You act as a manager. You pick real NFL players; when those players do things in real Sunday games,
their statistics are converted into points for you.

### The scoring formula

Your league agreed on 43 rules. The ones that matter:

| Real-world event | Points |
|---|---|
| Catching a pass | **1** |
| 10 yards gained running or catching | 1 |
| 25 yards gained throwing | 1 |
| Scoring a touchdown (running or catching) | 6 |
| Throwing a touchdown | 4 |
| Losing the ball to the other team | −2 |

A player who catches 8 passes for 90 yards and scores once earns `8 + 9 + 6 = 23` points.

### The weekly game

Each week you are matched against one of your 9 coworkers. Whoever's players score more that week
wins. Over the season you accumulate wins, and the **top 6 of 10 reach the playoffs** in week 15.

---

## 2. The constraint that creates the whole problem

You own **15 players**, but only **10 play in any given week**, and they must fill exact slots:

```
1 QB    quarterback   - throws the ball
2 RB    running back  - carries the ball
2 WR    wide receiver - catches passes
1 TE    tight end     - catches passes, fewer of them
2 FLEX  wildcard      - any RB, WR or TE
1 K     kicker        - kicks field goals
1 DEF   defense       - an entire team's defensive unit, treated as one player
```

**The other 5 players sit on the bench and score exactly zero.**

The bench exists because every NFL team has one week off (its "bye week") and players get injured.
When a starter cannot play, a bench player fills the slot instead.

---

## 3. The draft

All 10 managers take turns picking from a shared pool of ~600 players. **Once a player is taken, no
one else can have him.** 15 rounds × 10 managers = 150 picks.

**Snake order** reverses each round, which is what makes picking last bearable:

| | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| Seat 1 | pick 1 | pick 20 | pick 21 |
| Seat 10 | pick 10 | pick 11 | pick 30 |

Seat 1 gets the best player then waits 19 picks. Seat 10 waits at first, then picks twice in a row.

**60 seconds per pick.** Miss it and the computer picks for you. That computer is called *autopick*,
and it is the opponent this project set out to beat.

---

## 4. Why this is genuinely hard

Here is the counter-intuitive core, using real 2026 numbers.

**Quarterbacks score the most points of anyone.** Josh Allen projects to 361 points, more than any
running back or receiver. So take him first?

**No — and this is the single most important idea in the project.**

You start only **one** quarterback. There are 32 NFL teams so ~32 starting quarterbacks exist, and
only 10 of you need one. The **10th-best quarterback is still available late**, and he scores 296.

So spending a premium pick on Josh Allen gains you:

```
361 (Allen)  −  296 (a quarterback you could get for free later)  =  +65 points
```

Running backs are different. You start 2, and they fill most FLEX slots too, so the league consumes
about 23 of them. The 23rd-best running back scores 171. The best, Jahmyr Gibbs, scores 331:

```
331 (Gibbs)  −  171 (a running back you could get for free later)  =  +160 points
```

**+160 beats +65.** Take the running back, even though the quarterback scores more points.

This is **value over replacement**. What matters is never how many points a player scores. It is
*how much better he is than whoever you could have had for free instead.*

The same logic makes kickers almost worthless: the best kicker is worth about **+4 points across an
entire season** compared to a free one. So the kicker is your very last pick.

---

## 5. What the software does

| Piece | What it does |
|---|---|
| Rules reader | Pulls the league's exact 43 scoring rules from the API, so nothing is guessed |
| Scoring engine | Converts projected statistics into points using *your* formula |
| Value engine | Computes value over replacement, accounting for the 2 FLEX slots |
| Opponent model | Estimates whether a player will still be available at your next turn |
| Optimizer | Simulates the rest of the draft many times and ranks your options |
| Live tool | Shows three recommendations during the draft |
| Printed sheet | Paper backup if the computer fails |
| Manual entry | Type picks by hand if the network drops |

---

## 6. Does it actually work?

Tested by replaying five past seasons: draft using **only what was knowable before** that season,
then score using **what actually happened**.

| Season | Autopick | This optimizer | Gain |
|---|---|---|---|
| 2021 | 2371 | 2570 | +8.4% |
| 2022 | 2313 | 2564 | +10.8% |
| 2023 | 2341 | 2588 | +10.6% |
| 2024 | 2361 | 2679 | +13.5% |
| 2025 | 2301 | 2342 | +1.8% |
| **Pooled** | | | **+9.0%** |

About 9% is roughly **200 extra points over a season**, or ~14 points per week — often the margin in
a weekly matchup.

**Honest caveats.** 2025 was nearly a tie. The size of the edge depends on how accurate this year's
projections turn out to be, which nobody knows in advance. This is an edge, not a guarantee.

---

## 7. What to do on draft day

1. Run `python3 scripts/draft.py` alongside the Sleeper app.
2. When it is your turn it shows three players. Take the one marked `<= TAKE`.
3. `STAKES: LOW` means all three are near-equivalent — decide in five seconds.
4. `SANITY` compares the recommendation against what the wider market thinks. This is the fraud
   detector: you cannot evaluate a football claim yourself, but you can check whether the tool has
   wandered far from consensus.
5. If the network fails, run with `--manual` and type each pick as it happens. If the computer fails
   entirely, use the printed sheet.

### If you remember only two things

- **Do not take a quarterback early.**
- **Take your kicker with the last pick.**

Those two rules alone capture a large share of the available edge.
