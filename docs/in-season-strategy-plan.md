# In-season decisions: model, evidence and operating guide

**Status: read-only advisor, not a validated winning policy.** This extends the
[draft system](./draft-strategy-plan.md), but its reported draft results do not
establish an in-season advantage.

## 1. What changed during implementation

Several earlier planning claims were too strong and are withdrawn:

| Earlier claim | Audit finding |
|---|---|
| Streaming earns DEF +5.60 / K +4.36 points per week | The experiment clipped negative realized changes to zero *after observing the outcome*. It also lacked persistent ownership and realistic acquisition. Those are not attainable-policy gain estimates. |
| Good management gives this team a 20-25% title probability | The simulation assumed a points advantage rather than establishing one. It was not fitted to this league, its schedule, or its managers. No personalized probability is supported. |
| Forecasts cannot be improved | One unsuccessful trailing-average blend cannot rule out other forecasts, timely news, usage models, or ensembles. |
| Win-probability optimization is useless or expected points is "97% correct" | Two policies agreeing on 68/70 cases is not an accuracy estimate or a sufficiently powered comparison. The Normal/independence assumptions were restrictive. |
| No scope for RL | Too categorical. Offline/model-based RL is possible; uncertain transition models and validation, not just speed or one live episode, are the obstacles. |
| Projections on players who scored zero prove uncontaminated history | They do not. Retrieved historical forecasts can contain later updates without equaling outcomes. |

Tests now enforce **algorithm and measurement invariants**, not these numerical
claims. Reproducing a flawed formula does not validate it.

## 2. The decision process, before choosing algorithms

Use discrete **event epochs**, not just one decision each week:

```
observe state and new evidence
  -> enumerate legal actions
  -> estimate near-term gain, future cost and uncertainty
  -> advise a human
  -> observe what actually happened
  -> update the next decision
```

State includes our roster, ordered starters and locked slots, IR, opponents'
public rosters and current lineups, standings, waiver order, available players,
league rules, kickoff times, injuries, projections, and the remaining season.
Track the time **each observation was available**, not merely the game date.

Opponent intentions, pending claims and trade preferences are hidden. Future
performance, news and availability are uncertain. Public rosters do not make
this a perfect-information game. The policy must distinguish a new observation
from a known future deadline, and a planned action from a successfully executed
one.

### Events and times that matter

| Event | What becomes known or irreversible | Useful response |
|---|---|---|
| Weekly game results and later stat corrections | Updated outcomes, usage and standings | Update forecasts, evaluate decisions, revise roster needs |
| Injury/practice/news update | Availability and role may change | Recheck affected lineup and pickups, not the entire league unnecessarily |
| Opponent add/drop or trade | Ownership and substitutes change | Recompute available options; a dropped player may still be on waivers |
| Opponent lineup change | Current scoring threshold changes | Update matchup context; it can change again before lock |
| Waiver submission/processing window | Claims resolve, priority changes | Rank alternatives, specify conditional drops, verify success in the app |
| About 90 minutes before each game | Official inactives usually become available | Refresh and check directly; a feed can lag |
| Each game's kickoff | Those players' lineup assignments lock | Pin exact original slots; only remaining players can change |
| Between game waves | Partial scores and injury news arrive | Reconsider the still-open decisions, without changing locked starters |
| Trade deadline | Trade action disappears | Evaluate remaining-season alternatives before review/processing delays |
| Playoff qualification and seeding | Horizon and tournament path change | Balance current wins, byes, future matchups and championship prospects |
| Keeper deadline, if applicable | Future-season commitment | Verify actual keeper rules; a stored setting alone does not establish the format |

The captured league has 10 teams, 10 starting slots, five bench slots and one
IR slot. Trades close in week 11; six teams qualify starting week 15.
The usual one-week six-team bracket ends in week 17, but refresh the actual
bracket/settings rather than treating week 18 as universally irrelevant.
Points still matter for seeding/tiebreakers even when a weekly win is already safe.

### Actions and parameters

| Action | Parameters / constraints |
|---|---|
| Start, bench or reposition | Player IDs and **slot indices**, eligibility, original locks; place later games in FLEX when equivalent |
| Add/drop | Incoming and outgoing IDs, active roster capacity, IR legality, lock status, immediate vs future value |
| Submit/edit/cancel/reorder waiver claims | Ordered alternatives and conditional drops; rolling priority or FAAB depending on rules; a request can fail |
| Wait / do nothing | Preserve priority, a bench slot, a player, or the option to use later news |
| Place on / activate from IR | Eligible designation, free reserve/active space; an ineligible IR occupant can block other edits |
| Propose/counter/accept/reject/cancel a trade | Counterparty, packages, required drops, review time, deadline and mutually useful value |
| Keep/stash/stream | A sequence of the above, not a separate magic action |
| Communicate | Trade block and offers; no automated impersonation or negotiation |

Lineup lock and **permission to drop a bench player** are different rules.
This advisor conservatively does not suggest dropping a player whose game has
started, even if the league permits some bench drops. No arbitrary bench-player
sacrifice is recommended to fill an empty K/DEF slot on a full roster.

## 3. Data and its limitations

| Source | Useful information | Important limitation |
|---|---|---|
| [Sleeper documented API](https://docs.sleeper.com/) | Rules, rosters, ordered starters, matchups, transactions, brackets, player IDs | Read-only, non-commercial use; no public write API. Completed transactions are not opponents' pending intentions. |
| Sleeper projections/stats/scores endpoints used by this repository | Weekly per-stat forecasts, outcomes, kickoff timestamps and game state | Accessible but not part of the documented v1 contract; history fetched now is not necessarily an as-of archive. |
| Sleeper research/trending | Global ownership/start rates and add/drop momentum | Not this 10-team league's availability. Started percentage is not a causal human baseline or a performance probability. |
| [nflverse](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html) | Play-by-play, usage, snaps, rosters, depth charts and historical statistics | Check each dataset's coverage and update schedule. Participation data from 2023 onward is postseason-only; the published injury-data page warns of a source outage after 2024. A successful download alone proves little. |
| [DynastyProcess IDs](https://github.com/dynastyprocess/data) | Crosswalk between Sleeper and GSIS/provider IDs | Validate coverage, duplicate IDs and rookies; report unmatched records instead of automatically fuzzy-matching names. |
| FantasyPros / other projection vendors | Consensus forecasts, rankings, expert disagreement | Ranking standard deviation is **not** fantasy-score variance. Validate access/licensing and historical snapshots separately. A redistributed file is not a license exemption. |
| Odds providers | Spreads, game/team totals, player markets | Key/licensing requirements, bookmaker margin, and forecast-time timestamps. Do not assume an opaque `moneyline` field is a calibrated win probability. |
| Official NFL/team reports and a reliable news provider | Transactions, practice status, gameday inactives | Timeliness matters; an injury tag may be stale. A practice DNP is not a game-day inactive designation. |
| Weather (e.g. Open-Meteo) | Wind, precipitation and temperature near kickoff | Stadium roof and forecast issue time matter. Historical observed weather is not a pregame forecast. |

External data remains a research option, not a rejected workstream. CSV/gzip
formats can be read without adding Python dependencies. First prove coverage and
incremental decision value; do not infer current-season availability from HTTP 200.

### Waiver clock: documented rhythm, not a guessed scheduler

[Sleeper's official guide](https://support.sleeper.com/en/articles/3978868-waivers-for-regular-season-playoffs)
describes **Tue After Day** as 12:05 a.m. Pacific on Wednesday. It also describes
the chosen post-drop delay and the **24-hour hold exception**: a free agent
dropped before being held 24 hours returns directly to free agency, rather than
being blocked for rivals.

That resolves the general weekly rhythm; it does **not** guarantee the clearing
time of each individual player, or prove the numeric setting's mapping in every
league. Processing may be delayed or overridden. This tool leaves claimability
and the specific countdown to the Sleeper app and never fabricates a release time.
Use timezone-aware dates: "Pacific" and "Eastern" change offset with DST.

IR flags are recorded, not expanded into invented rules. See
[Sleeper IR eligibility](https://support.sleeper.com/en/articles/1983643-how-does-injured-reserve-ir-work).
Missing flags do not establish every designation's eligibility (notably PUP).

## 4. Algorithms: reliable baseline plus ambitious research

**Deployed baseline:** exact slot assignment maximizing filled legal slots, then
expected points. Preserve exact locked assignments even for an injured starter;
retain later games in FLEX at equal value; minimize unnecessary moves.
"Exact" means optimal under the supplied forecasts and constraints, not guaranteed
correct about real outcomes.

**Pickups:** evaluate the complete roster after an explicit same-position
add/drop, or use an actually open slot. Report projected one-week gain, waived
priority uncertainty and lost future value. The release focuses on K/DEF as a
manageable initial action set, **not because a validated gain was proved**.

**Waiver DP:** backward induction under explicitly supplied opportunity and
transition probabilities. Exact only for that compressed scenario model. The
real state also includes changing rosters, rival needs and multiple claims; a
rank-only model is not an exact solution of fantasy football.

**Trade search:** search legal 1-for-1 and bounded 2-for-1 scenarios, charging the
recipient for required drops. Compare both teams' projected lineup value.
Positive value to both does not predict acceptance, and a single forecast week
is not rest-of-season, keeper or championship value.

**O1/O2/O3 remain research alternatives:**

- O1: expected points.
- O2: current matchup win probability, accounting for partial scores and the
  *joint* distribution of both lineups. Covariance with the opponent matters as
  well as covariance within our lineup.
- O3: season/tournament utility under a complete schedule, seeding, byes and
  future actions. Championship probability and playoff qualification are
  different objectives, not interchangeable labels.

An underdog does not automatically benefit from any increase in variance.
The mean, tails, correlations, opponent and available alternatives matter.
Normal approximations and correlation estimates must be checked, not declared
correct by the central limit theorem.

### Where RL could fit

Model-based RL, offline RL and approximate value functions could help with
multiweek roster management, priority conservation and trade timing. They need
not explore on the live team. However:

- Simulated episodes reduce Monte Carlo error, not simulator bias.
- Historical player outcomes are unusually useful: our lineup choices do not
  change NFL performance, so many fixed-roster lineup alternatives can be replayed.
- Acquisition and trades are harder: actions change future ownership and rivals'
  responses; unsupported counterfactuals cannot be recovered from one league log.
- Rollout/MPC can overfit a simulator too. It is easier to inspect, not immune.
- A useful advanced first step is calibrated distributional forecasting,
  shrinkage, scenario sensitivity and receding-horizon planning. RL should beat
  those baselines out of time before receiving live decision authority.

Streaming is not a standard bandit when outcomes for **unselected** players are
also observable. Value-of-information reasoning still matters around injury news
and staggered locks, but the usual bandit exploration argument does not transfer
unchanged.

## 5. Operating the advisor

```sh
python3 scripts/refresh_rules.py
python3 scripts/week.py --refresh
python3 scripts/week.py --refresh --calendar data/lineup-reminders.ics
python3 scripts/week.py --score-week 1
```

- The briefing uses the current season/week only. Do not mix historical matchup
  weeks with today's rosters; historical research has a separate command.
- `--refresh` re-fetches the player map as well as projections and league state.
  By default the large player map is cached daily. Source retrieval times are
  displayed, but do not prove the provider's news is up to date.
- Missing mandatory sources or rules drift produces an explicit error rather
  than stale success-shaped advice. Known out/bye/IR/unknown-forecast players are
  excluded from new starting assignments, not guaranteed to score zero in reality.
- Journal entries are saved by default in `data/decisions.jsonl`; use `--no-log`
  or `--log-path` when testing. Keep a backup: this local journal is gitignored.
- `--calendar` exports UTC events with 90/15-minute alarms. **Import the file**
  into a calendar to receive notifications. No daemon, background notification
  service or Sleeper write integration is installed. Replace/update the imported
  calendar if games are rescheduled.
- `--score-week` requires completed games and known player scores. It appends a
  separate outcome; it does not rewrite the recommendation. Observed-at-decision
  and final lineups are distinct. Stat corrections can require a new outcome run.

### Weekly human checklist

1. After games: review roster needs and last week's signed outcomes, not just
   successes. Check bye weeks and injury news.
2. Before waivers: inspect claim priority and the app's exact countdown. Choose
   ordered alternatives and drops. Confirm results after processing.
3. Before each kickoff wave: refresh, read warnings, check official inactives,
   and **save the complete target lineup in Sleeper**. Ensure early players are
   not unnecessarily consuming FLEX flexibility.
4. After changing ownership or a roster assignment: rerun the briefing. Treat
   small projected upgrades as options, not commands to churn valuable players.
5. Before the trade deadline: assess remaining weeks and playoff needs without
   sacrificing qualification for a speculative favorable schedule.

This buys disciplined decisions, not a promised finish in a one-shot season.

### Optional scenario tools

These are not required for the weekly checklist and are not calibrated policies:

```sh
# At week 1: evaluate a future week, accounting for transaction review delay.
python3 scripts/scenarios.py trades --week 2 --candidate-limit 6
# Optional: restrict to a specific roster with --opponent ROSTER_ID.

# Supply your OWN probability model and net reward, in consistent utility units.
python3 scripts/scenarios.py waiver --model assumptions.json --rank 8 --reward 5
```

The trade output names players and required drops, reports both sides' gains,
and lists excluded rosters with missing forecasts or ineligible players. Singles
are exhaustive within included rosters; pair offers are restricted to the top
`candidate-limit` projected players. Reserve/taxi assets are not offered. This is
a hypothetical **future-week** scenario; it does not predict anyone's willingness
to accept or long-term value.

The waiver JSON schema is defined in
[`WaiverModel.from_dict`](../ffopt/waivers.py). Required fields:

- `horizon`: periods including the current decision;
- `opportunities`: objects with `probability`, `reward`, and optional `available`;
- `success_probabilities`: one supplied probability per rank;
- `wait_transitions` and `spend_transitions`: square row-stochastic rank matrices.

There are no default claim-success probabilities. Spend transitions are
**unconditional**, including failed claims; they must already account for
priority reset and competitors. Optional `label`, `assumptions` and `uncertainty`
travel with the result. A precise numerical threshold is only as credible as
these inputs. Do not invent them just to obtain a decisive recommendation.

## 6. Evidence and project discipline

The repaired measurement script reads caches without making network requests:

```sh
python3 scripts/measure_inseason.py --what data-audit
python3 scripts/measure_inseason.py --what streaming --availability-rank 1
python3 scripts/measure_inseason.py --what exp-stacking --what exp-playoff-window
# Add --cache-dir PATH for separately archived inputs; --json-output PATH records
# input hashes, configuration and results.
```

Caches are local, not bundled. Missing inputs are reported; supply an archive
with `--cache-dir` rather than treating absent data as zero observations.

The implementation audit used 105 cached inputs across 2023-2025. Its results are
**descriptive/exploratory, not an as-of backtest**:

| Probe | Result | Interpretation |
|---|---|---|
| Projection-selected player-weeks, weeks 1-17 | 17,928; four outcomes unknown under the declared scoring-row convention | Unknown was not silently zero. Very high coverage is not a contamination pass. |
| Snapshot provenance | Season records carried January modification dates; 20,203 weekly records, including unscored rows, had modification dates after event dates | No genuine decision-time archive was established. A date field cannot prove pre-kickoff availability. |
| Signed rank-6 single-swap probe | DEF +1.074, K +0.155 points per evaluable cell; 41 DEF loss cells and 21 K loss cells | Only 204/420 cells per position were evaluable; missing baseline lineups were excluded. Fixed ownership resets each cell. These are not persistent streaming-policy edges. |
| Availability sensitivity | Rank-1 K swaps averaged **-1.191** per evaluable cell | The earlier positive-only streaming conclusion was not robust. Rank is an assumption, not simulated competition. |
| Same-game QB/WR residual correlation | 0.296 / 0.262 / 0.294 by season | Covariance is worth investigating; shared games make rows dependent. No stacking advantage proved. |
| Week 15-17 forecast coverage in the fixed roster pool | 80.22% / 82.00% / 82.22% | Historical files do not show those forecasts were available at the week-11 trade deadline. |
| Symmetric toy tournament | Each team approximately 10% title probability; maximum deviation 0.46 percentage points in this run | A simulation sanity check, **not this team's odds**. |

A held-out, synthetic two-player threshold comparison found only one selection
change across 84 O1/O2 comparisons. That probe has almost no power to establish
objective equivalence. Recency-blend results likewise do not establish that
forecasts are unbeatable. Historical forecast timestamp provenance is a gating
limitation, not something passing unit tests can establish.

Validation must include signed losses, fixed-information action selection,
legal roster capacities, exclusive ownership, correct locks, missing data,
opponent behavior sensitivity, untouched season holdouts and reasonable baselines.
Report effective sample size clustered by season/league, not thousands of
synthetic rosters as independent evidence.

Keep claims and evidence in versioned documents; task briefs link to them and
label **observed / assumed / decided / unvalidated**. Give workers permission to
reject their premise. Independent review and parallel investigations can expose
correlated errors; a single context is not inherently safer.

As approved, GitHub issues/Projects are a post-week-1 milestone, not installed
in this release. New issues should specify invariants and acceptance checks, not
demand that a model reproduce an asserted advantage.

## 7. Capture and replay backend

The collection/replay foundation is implemented independently of a web framework:

```sh
# Capture once and save a baseline recommendation. No background process installed.
python3 scripts/snapshots.py collect

# List captures, including failures and interrupted runs.
python3 scripts/snapshots.py list
python3 scripts/snapshots.py show SNAPSHOT_ID

# Re-evaluate identical inputs offline, with an optional explicit shadow policy.
python3 scripts/snapshots.py replay SNAPSHOT_ID --min-pickup-gain 2

# Operator-friendly briefing with complete evidence capture and journal linkage.
python3 scripts/week.py --capture --refresh

# Historical display only: no network, live journal write, or reminder export.
python3 scripts/week.py --snapshot SNAPSHOT_ID

# Use SQLite's consistent backup API, not a copy of only the active database file.
python3 scripts/snapshots.py backup /path/to/backup.sqlite3
```

The archive defaults to `data/archive.sqlite3`. Use `snapshots.py --db PATH ...`
or `week.py --archive PATH ...` to choose a different durable location.
`--refresh` bypasses local archive reuse, including the large player reference.

### What is recorded

- Full response bodies for NFL-state bookends, league settings, every returned
  roster and matchup, the full player map, weekly projections, game state,
  current-week transactions and previous-week transactions when relevant.
- The exact configuration used, a code fingerprint, schema version, and the
  collection start/end times.
- Per-source request and receipt timestamps, source URL, a non-secret HTTP
  header allowlist (including Date, Age, ETag and cache headers), body SHA-256,
  and whether an earlier archived observation was reused.
- HTTP error bodies, transport errors, failed validation and unfinished captures.
- Versioned policy names/parameters, recommended alternatives and disagreements,
  separately from the input evidence.

Bodies are gzip-compressed and deduplicated by exact byte hash. Evidence rows
cannot be updated/deleted through the archive API, and a completed capture is
sealed against later source insertion. Payload hashes are checked when read.
A backup is still necessary: hashes do not protect against disk loss.

Reused data keeps its **original receipt time**. It is not relabeled as a new
observation. Mutable legacy cache files are not imported as trusted historical
evidence. Expired sources are refetched; a fetch failure is not replaced with
stale success-shaped data.
Only observations from successful, validated captures are eligible for reuse.
After a context-validation failure, the next collection for that league forces
a recovery refresh rather than repeatedly reusing incompatible references.
The baseline's lineup **and pickup/report path** must succeed before a capture
is sealed as complete.

Initial reuse limits are 30 seconds for NFL/game/matchup state, 60 seconds for
rosters/current transactions, 120 seconds for league settings, five minutes for
projections, ten minutes for previous-week transactions, and one day for the
large player map. These are engineering defaults, **not experimentally optimal
polling frequencies**. Before deadline-sensitive advice, refresh and verify
official inactives; a newly retrieved response may itself contain stale data.

### Replay and policy comparison

Replay rebuilds state from archived bodies, rules and the original decision
time. It makes no network requests and reads no current configuration or mutable
API cache. Current code is used; its fingerprint is reported alongside the code
fingerprint at capture. A code change is disclosed rather than pretending the
original executable was restored.

The deployed expected-points policy is unchanged. An initial demonstration
challenger applies a caller-specified minimum pickup gain. Its threshold is
**not fitted** and does not estimate future bench/priority costs. It runs only
in comparison reports and cannot replace the live baseline automatically.
Each policy receives isolated copies of identical inputs. Comparisons record
lineup and pickup disagreements, not invented realized benefits.

### What this does not establish

A `complete` capture satisfies the present input/validation contract; it does
not certify vendor forecasts, injuries or every real-world fact. Sequential
requests are not an atomic Sleeper transaction. Bookend and roster-consistency
checks catch some races, but cannot reconstruct undisclosed pending claims.

This archive begins **now**. It cannot recover lost decision-time data from past
seasons. External news/projection adapters, multiweek policy evaluation, a
periodic collection scheduler and notification delivery are still future work.
The [FastAPI service and dashboard](./architecture.md#rest-service-and-demo)
now provide versioned HTTP access and durable on-demand background jobs.
An offline replay cannot validate a policy's counterfactual effects on opponents.

The first real capture in this implementation check took about 5.5 seconds and
peaked at 127.5 MiB RSS on the development host. Its unique bodies/config totaled
16.8 MB raw and 2.37 MB compressed, excluding SQLite metadata/WAL overhead.
These are a **single local measurement**, not a cloud sizing guarantee or an
estimate of season-long storage. Later identical bodies are shared.
