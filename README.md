# Git Blame Copilot — fantasy draft optimizer

Tooling for a 10-team snake draft, treated as a sequential resource-allocation
problem rather than a forecasting one. See [docs/draft-strategy-plan.md](docs/draft-strategy-plan.md)
for the approach and [docs/league-rules.md](docs/league-rules.md) for the league
parameters in plain language (no football knowledge assumed).

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
