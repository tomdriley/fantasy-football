# Git Blame Copilot — fantasy draft and in-season research

Draft tooling, a manager-facing in-season assistant, and a reproducible research
archive for the configured Sleeper league.

## Web demo and REST API

The in-season app uses **React + Material UI** over **FastAPI + Uvicorn**, sharing
the engine, SQLite evidence archive and CLI operations. My week prioritizes
freshness, the next deadline and what needs attention. Past advice and research
are separate destinations, following the [documented product design](docs/in-season-product-design.md).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -c requirements.lock
npm --prefix web/season ci
npm --prefix web/season run build
.venv/bin/python scripts/serve_api.py
```

Open **http://127.0.0.1:8787/** and choose **Update advice**. Make any changes in
Sleeper, then update again to check your lineup. Old advice is labeled as
reference only. Optional pickups are collapsed; experiments and technical
details live under **More**. Updates use durable background jobs.
API schema: http://127.0.0.1:8787/api/v1/openapi.json.

On Debian images without `ensurepip`, the tested bootstrap alternative is:

```sh
python3 -m venv --without-pip .venv
python3 -m pip --python .venv/bin/python install -r requirements-dev.txt -c requirements.lock
```

The default is a **loopback-only demo**, not a public deployment. Remote binding
requires `FFOPT_API_TOKEN` and explicit `--allowed-host` values; TLS and
production identity/deployment controls are still required. The UI keeps an
entered token in memory only. No Sleeper transactions are submitted.

See [REST contracts and scaling limits](docs/architecture.md#rest-service-and-demo)
for the single-host SQLite boundary and migration path.

## Documentation

| Document | Question it answers |
|---|---|
| [**Draft day**](docs/draft-day.md) | **Read this on the day.** What to run, what to click, what to do when something breaks |
| [**In-season decisions**](docs/in-season-strategy-plan.md) | Weekly guidance, locks, pickups, external data, research limitations and the human checklist |
| [**In-season product design**](docs/in-season-product-design.md) | Manager-first workflows, progressive disclosure, safety states and acceptance criteria |
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

The core CLI requires Python 3.10+, `pyyaml`, and the system timezone database.
The web service adds FastAPI/Uvicorn; API tests add HTTPX. Direct dependencies are
pinned in `requirements.txt` and `requirements-dev.txt`.
`requirements.lock` records the tested transitive versions as installation constraints.
The frontend build and its built-in Node tests require Node 24+ and npm 11+.
`npm --prefix web/season run build` includes TypeScript checking;
`npm --prefix web/season test` runs the frontend behavior tests. Node is not
needed to serve an already-built app.

### During the season

```sh
python3 scripts/refresh_rules.py
python3 scripts/week.py --refresh
python3 scripts/week.py --refresh --calendar data/lineup-reminders.ics
python3 scripts/week.py --score-week 1     # after the week's games finish
```

The briefing preserves locked slots, shows exact lineup edits, compares legal
DEF/K add/drop options, and records decisions locally. **Nothing is submitted to
Sleeper.** Import the exported calendar to enable alarms; no background service
is installed. Confirm injury status and each player's waiver countdown in the
app before acting.

In-season gains and championship odds have **not** been validated. Earlier
streaming gain estimates were withdrawn after a hindsight-bias audit; see the
[evidence corrections](docs/in-season-strategy-plan.md#1-what-changed-during-implementation).
Optional research commands and assumption-dependent trade/waiver tools are
documented in the [operating guide](docs/in-season-strategy-plan.md#optional-scenario-tools).

### Capture evidence and replay offline

```sh
python3 scripts/snapshots.py collect
python3 scripts/snapshots.py list
python3 scripts/snapshots.py replay SNAPSHOT_ID --min-pickup-gain 2
python3 scripts/week.py --capture --refresh
python3 scripts/week.py --snapshot SNAPSHOT_ID
python3 scripts/snapshots.py backup /path/to/backup.sqlite3
```

The local archive stores raw HTTP bodies, original receipt/cache metadata,
captured rules and explicit failures. Replay uses the archived time and inputs,
not today's mutable cache. The optional pickup threshold is an **uncalibrated
shadow policy**, not a promoted strategy.

`collect` is a one-shot command suitable for a future scheduler; it does not
install a daemon. The archive is gitignored and needs a durable local disk and
backups. See [archive operation and limitations](docs/in-season-strategy-plan.md#7-capture-and-replay-backend)
and [frontend/hosting options](docs/architecture.md#in-season-hosting-direction).

### Draft commands

```sh
python3 scripts/serve.py                      # web interface (recommended)
python3 scripts/serve.py --offline            # start in manual mode, no network

python3 scripts/make_sheet.py                 # printable draft sheet (paper backup)
python3 scripts/draft.py                      # terminal advisor
python3 scripts/draft.py --manual --seat 5    # terminal, enter picks by hand

.venv/bin/python -m unittest discover -s tests
FFOPT_GOLDEN=1 .venv/bin/python -m unittest discover -s tests   # + optional engine regression
```

### Draft-day web interface (legacy)

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
