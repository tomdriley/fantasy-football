# Git Blame Copilot — fantasy draft optimizer

Tooling for a 10-team snake draft, treated as a sequential resource-allocation
problem rather than a forecasting one.

## Documentation

| Document | Question it answers |
|---|---|
| [**Draft day**](docs/draft-day.md) | **Read this on the day.** What to run, what to click, what to do when something breaks |
| [**How this works**](docs/how-this-works.md) | What the game is and what the software does, assuming no football knowledge |
| [Algorithm](docs/algorithm.md) | **The objective function and decision rule, as equations.** Written to be audited: every parameter's source, every approximation ranked by how much it is distrusted |
| [Strategy plan](docs/draft-strategy-plan.md) | **Why** this approach, and why not machine-learned projections |
| [League rules](docs/league-rules.md) | **What** the rules are, in plain language, assuming no football knowledge |
| [Architecture](docs/architecture.md) | **How** the system is built: layering, budgets, failure modes |
| [Product design](docs/product-design.md) | **What the operator experiences** during a timed live draft |
| [league-rules.yaml](docs/league-rules.yaml) | Machine-readable rules. Source of truth for all code |

New to the topic? Read [how-this-works.md](docs/how-this-works.md) first. To audit
correctness go to the algorithm doc; for draft-day mechanics, the product design doc.

> **Validated across five seasons (2021-2025), with adversarial checks.** The
> optimizer beats the platform autopick by **+9.0%** and a competent human
> heuristic by **+10.0%**, winning 172/200 controlled configurations
> (bootstrap 95% CI [+186, +239] points). Positive in every season. It survives
> a placebo test and still wins when half the league also optimises. Two further
> seasons were examined and excluded for data contamination. The edge tracks
> forecast quality and ranged from +2% to +14%, so the low end is entirely
> possible in 2026. See [algorithm.md](docs/algorithm.md) §10.

## Layout

| Path | Purpose |
|---|---|
| `docs/league-rules.yaml` | Machine-readable league config, generated from the API. **Source of truth** |
| `docs/league-rules.md` | Human-readable companion, regenerated from the YAML |
| `ffopt/config.py` | Typed accessor over the rules YAML; snake pick schedules |
| `ffopt/client.py` | Cached client for the public JSON API |
| `ffopt/scoring.py` | The linear payoff function |
| `ffopt/pool.py` | Item pool construction |
| `ffopt/valuation.py` | Replacement baselines, value over replacement, tiers |
| `ffopt/season.py` | The objective: expected season value, including bench slots |
| `ffopt/availability.py` | Opponent model |
| `ffopt/optimizer.py` | Rollout decision rule and feasibility constraints |
| `ffopt/backtest.py` | Non-circular validation: preseason claims, realized scoring |
| `ffopt/shrinkage.py` | Shrinks forecasts toward market prior (counters selection bias) |
| `ffopt/cheatsheet.py` | Printable paper fallback |
| `ffopt/live.py` | Live draft advisor |
| `ffopt/manual.py` | Manual board entry if the pick feed fails |

## Running

Requires Python 3 with `pyyaml`. No other dependencies.

```sh
python3 scripts/serve.py                      # web interface (recommended)
python3 scripts/serve.py --offline            # start in manual mode, no network

python3 scripts/make_sheet.py                 # printable draft sheet (paper backup)
python3 scripts/draft.py                      # terminal advisor
python3 scripts/draft.py --manual --seat 5    # terminal, enter picks by hand

python3 -m unittest discover -s tests         # ~200 tests, ~7s
FFOPT_GOLDEN=1 python3 -m unittest discover -s tests   # + engine regression (~70s)
```

### The web interface

`scripts/serve.py` opens a browser interface at `http://127.0.0.1:8777`. It has
three modes:

| Mode | Behaviour |
|---|---|
| **live** | Polls the league API and applies picks automatically |
| **assisted** | Polls, but *proposes* changes for you to accept, so a lagging or wrong feed cannot overwrite a board you are maintaining |
| **manual** | You type every pick; the network is never touched |

All three work offline. If the feed fails the interface says so and keeps
advising from the local board. Picks are persisted server-side, so refreshing or
closing the browser loses nothing.

**Recovery:** any recorded pick can be corrected, removed, or inserted. Insert
matters most — a single missed entry misattributes every later pick to the wrong
seat, and inserting the missing one repairs the whole board.

**Panic button:** returns a defensible pick in under a millisecond with no
simulation and no network, for when the clock is nearly out or the board looks
wrong. It offers several options because a panic usually means the board is
already inaccurate.

If the pick feed fails three times in a row the tool switches to manual entry on
its own, keeping the picks it already has. In manual mode type a surname as each
pick happens (`gibbs`), prefix your own with `me` (`me gibbs`), and use `undo`,
`board`, `sync` or `reset`. The board is saved after every entry, so a crash
mid-draft loses nothing.

API responses cache under `data/cache/` (gitignored), so the 60-second draft
timer is never spent re-fetching reference data.

## Notes

Value over replacement is an *input* to the claim decision, not the claim order.
It has no notion of *when* an item can be obtained and, sorted directly, ranks
kickers and defenses far too high. The optimizer supplies that missing dimension.
