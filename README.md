# Git Blame Copilot — fantasy draft optimizer

Tooling for a 10-team snake draft, treated as a sequential resource-allocation
problem rather than a forecasting one.

## Documentation

| Document | Question it answers |
|---|---|
| [Algorithm](docs/algorithm.md) | **The objective function and decision rule, as equations.** Written to be audited: every parameter's source, every approximation ranked by how much it is distrusted |
| [Strategy plan](docs/draft-strategy-plan.md) | **Why** this approach, and why not machine-learned projections |
| [League rules](docs/league-rules.md) | **What** the rules are, in plain language, assuming no football knowledge |
| [Architecture](docs/architecture.md) | **How** the system is built: layering, budgets, failure modes |
| [Product design](docs/product-design.md) | **What the operator experiences** during a timed live draft |
| [league-rules.yaml](docs/league-rules.yaml) | Machine-readable rules. Source of truth for all code |

Start with the **algorithm doc** to audit correctness, the strategy plan for the
reasoning, or the product design doc for what the tool does on draft day.

> **The backtest gate failed.** Measured against realized 2025 outcomes under
> controlled A/B conditions, the optimizer **loses to the platform's own autopick
> by 4-6%**, as do all the simpler strategies tried. The project's founding
> premise -- that beating autopick is a low bar -- is refuted. Do not use this
> engine for live decisions. See [algorithm.md](docs/algorithm.md) section 11.

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

## Running

Requires Python 3 with `pyyaml`. No other dependencies.

```sh
python3 -m unittest discover -s tests -v
```

API responses cache under `data/cache/` (gitignored), so the 60-second draft
timer is never spent re-fetching reference data.

## Notes

Value over replacement is an *input* to the claim decision, not the claim order.
It has no notion of *when* an item can be obtained and, sorted directly, ranks
kickers and defenses far too high. The optimizer supplies that missing dimension.
