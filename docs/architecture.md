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
| No web framework available | The interface is a dozen JSON endpoints over an existing engine, so `http.server` plus vanilla JS is sufficient and adds no install risk hours before a one-shot event |

## 2. Layering

The in-season path is separate from draft rollout:

```
refresh_rules -> generated rules -> config
client -> inseason (validated snapshot) -> lineup (pure assignment)
                    |                        |
                    +-> week.py <------------+
                         |-> decisionlog (local append-only journal)
                         +-> reminders (calendar export, no daemon)
```

The weekly path rejects expired-cache fallback and rules drift; the draft
client's original offline fallback is unchanged. Roster membership includes IR
and taxi ownership, while ordered matchup starters retain empty slot sentinels.
Locked starters are pinned to their **original indices**, including FLEX.
Fetch time is recorded separately from the time of the underlying information.

Measurement code remains in `scripts/measure_inseason.py`, is never imported by
the advisor, and does not certify a live policy's advantage. Optional waiver and
trade scenario tools require explicit assumptions. See
[in-season decisions](./in-season-strategy-plan.md) for operating limits.

The research-capable extension separates acquisition from policy execution:

```
client.fetch_document -> collector -> archive (raw bodies + provenance)
                                         |
                                  collector.replay
                                         |
                               inseason.build_state
                                         |
                               policies.compare
                                         |
                        append-only evaluation report
```

The collector preserves unsuccessful runs and captures rules with the inputs.
The shared state assembler is pure: it accepts an explicit decision clock,
configuration and input bundle. SQLite holds local compressed content-addressed
blobs plus capture/observation/evaluation records; no provider framework is
imported by the policy engine. A future HTTP layer can return the same JSON
reports without coupling the engine to a particular UI.

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
| Pick feed slow/unavailable | 10 s timeout, 3 consecutive failures | **Automatic switch to manual entry** (`ffopt/manual.py`); whatever the feed already returned is kept, and recommendations continue |
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

Historical draft prototype: 16 tests, ~0.5 s. The current suite also tests weekly
legality, snapshot integrity, offline replay and policy isolation.

## 9. Key invariants

1. `docs/league-rules.yaml` supplies live rules. Replay uses its immutable captured
   copy, not today's rules. Code that hardcodes a league rule is a bug.
2. Layers never import upward.
3. `adp_*` fields are **never** treated as scoreable stats, and `adp_std` is standard-*scoring*
   ADP, not a standard deviation.
4. VOR is an input to the decision, never the claim order itself.
5. Guardrails are typed: legality and structural rules block; empirical heuristics only warn.
   An empirical rule that has to block is evidence of a bug in the objective function.
6. The backtest never scores with the same forecast vector that drove its claims.

## 10. Deferred / out of scope

- Automated claim submission — the operator clicks; the tool advises.
- In-season lineup and waiver management now have a separate advisor and
  capture/replay path; they do not reuse the draft objective unchanged.
- Multi-source forecast blending — the interface allows it; time before the draft does not.

## In-season hosting direction

Research date: 2026-09-05. This is a shortlist, **not a deployment**. No cloud
resources, accounts, public endpoints or notification integrations were created.

### Frontend

The initial vanilla dashboard has been replaced by React/TypeScript and Material
UI Core after a [manager-first product design review](./in-season-product-design.md).
My week is primary; Past advice is secondary; Research and Data/service are
discoverable under More. The UI consumes framework-independent REST data and
does not own strategy logic.

Vite builds static assets; no Node server is needed at runtime. Hash routes keep
hosting simple. Serving UI and API from the same origin avoids unnecessary
cross-origin authentication complexity.

### Three feasible deployment paths

| Path | Why it fits | Cost/operational constraints |
|---|---|---|
| **Existing always-on machine, privately accessible** | The current local SQLite archive fits directly; a local scheduler can run one-shot captures; no new compute subscription | Hardware, power, connectivity, backups and patching remain your responsibility. Missed captures during downtime cannot be reconstructed later. Use a private VPN or properly configured identity-aware access proxy rather than opening the draft server's port. |
| **One small Linux VM, on Azure credits or a low-cost VPS** | Durable local disk, a normal process supervisor/scheduler, and one archive writer are the smallest migration from today's implementation | Fixed compute/disk charges, backup storage, possible public-IP/network charges, and OS maintenance. Obtain a current quote for the chosen region/account; no reliable all-in monthly price has been established here. |
| **Azure Container Apps + scheduled Jobs + durable object/database storage** | The web API can scale down between requests; collection can run independently as finite jobs; compatible with a Python engine | The current SQLite store needs a deliberate storage adapter. Container-local disk is ephemeral. Blob payloads plus durable manifests/indexes, or a managed DB, are plausible; they are not implemented yet. Compute grants do not make logs, storage, networking or ancillary services automatically free. |

**Initial recommendation:** use a durable single host if the priority is getting
useful personal service online with minimal infrastructure. Choose Azure
Container Apps/Jobs if managed operation and Azure integration justify the
additional storage work. The archive and policy interfaces do not require an
always-running process; a continuously available *service* can combine scheduled
collection, stored reports and request-driven compute.

### Verified Azure constraints

- [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing)
  documents monthly Consumption grants per subscription: 180,000 vCPU-seconds,
  360,000 GiB-seconds and two million HTTP requests. Scaling to zero avoids
  replica resource charges while at zero; keeping replicas warm can incur
  idle/active charges. Cold-start duration is not a fixed guarantee.
- [Scheduled Jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs)
  provide a scheduler for bounded runs. Jobs are billed for active resource use.
  Cron scheduling still needs explicit timezone/deadline handling.
- [Container Apps storage](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts)
  distinguishes ephemeral container/replica storage from persistent Azure Files.
  **Do not put this SQLite WAL archive on an Azure Files/network mount.**
  [SQLite WAL](https://sqlite.org/wal.html) requires its processes on the same
  host. Blindly downloading and re-uploading one database blob also risks lost
  updates with overlapping jobs; it is not a multiwriter storage adapter.
- [Static Web Apps plans](https://learn.microsoft.com/en-us/azure/static-web-apps/plans)
  include a Free plan for personal static sites with HTTPS. Managed backend
  linking has plan restrictions. A free static site may still call an external
  API, but that API needs its own authentication/authorization and CORS setup.
  Hosting the UI with the Container App avoids needing SWA at all.

Azure credits are account-specific and may expire. Before deployment, check
eligibility and region prices in the [pricing calculator](https://azure.microsoft.com/pricing/calculator/),
set cost alerts, and account for disks/blobs, requests, logs, registry images,
networking and backups. Budget alerts are not a hard spending cap.

### Security and notifications remain separate work

The existing `http.server` draft server is not a production deployment target.
[Python's documentation](https://docs.python.org/3/library/http.server.html)
warns against production use. A tunnel or TLS-terminating proxy does **not**
automatically fix application-level authorization, CSRF, request limits or
resource exhaustion. A remotely accessible version needs a supported server
and authenticated, owner-restricted API; do not expose draft mutation routes
unchanged.

Email, private push services and PWA/web push are all feasible. The correct
backend boundary is a durable notification intent/outbox separate from delivery:
deduplicate alerts, suppress superseded advice, record delivery failures and
retry safely. A UI request should not be the only mechanism triggering a
deadline notification. Provider choice, quotas, secrets and email deliverability
can be settled when that feature is implemented; no delivery guarantee or free
email quota is assumed now.

## REST service and demo

The in-season service now uses FastAPI/Uvicorn. The original draft server is
retained separately for its historical draft workflow; it is not the new API.

```
web/season dashboard or any other HTTP client
                 |
          /api/v1 REST API
                 |
       application service
          |             |
  evidence reads     durable job queue
                        |
                 elected host worker
                        |
              collect / evaluate_snapshot
                        |
               immutable evidence archive
```

The framework boundary is `ffopt/api.py`. Application orchestration lives in
`service.py` and `evaluation.py`; the engine, collector and CLI do not depend on
FastAPI. The dashboard uses only the REST contract and can be replaced without
rewriting the engine.

### API contract

| Method / path | Purpose |
|---|---|
| `GET /healthz` | Process liveness, no private data |
| `GET /readyz` | Storage/worker readiness |
| `GET /api/v1/status` | League, storage and job status |
| `GET /api/v1/advice` | Latest successful baseline, freshness guard, deadline and manager-facing actions |
| `GET /api/v1/advice/{id}` | Explicitly historical advice, never promoted to My week |
| `GET /api/v1/snapshots?limit=20` | Bounded snapshot history |
| `GET /api/v1/snapshots/{id}` | Provenance, archived league context and latest evaluation |
| `GET /api/v1/snapshots/{id}/evaluations?limit=20` | Bounded evaluation history, newest first |
| `POST /api/v1/collections` | Queue a capture and baseline evaluation |
| `POST /api/v1/snapshots/{id}/evaluations` | Queue an offline baseline/shadow comparison |
| `GET /api/v1/jobs/{id}` | Poll a durable job |
| `GET /api/v1/jobs?limit=20` | Recent job activity |
| `GET /api/v1/openapi.json` | Machine-readable OpenAPI contract |

Capture accepts `{"refresh": false, "minimum_pickup_gain": null}`. Evaluation
accepts `{"minimum_pickup_gain": 2}`; this is an uncalibrated shadow threshold,
not an automatically promoted policy. Successful submissions return `202` with
a job and polling URL. Supply `Idempotency-Key` to retry a submission without
duplicating it; reusing the key for a different request returns `409`.
The dashboard retains the key and original request after an ambiguous network
failure, offers **Retry same request**, and blocks new submissions until that
uncertainty is resolved. Do not treat a lost response as proof that no job exists.
Non-secret pending-request metadata is retained in session storage when
available; API tokens remain in memory only.

Unknown resources return `404`, invalid inputs `422`, invalid snapshot-state
operations `409`, and a full job queue `429` with `Retry-After`. Provider or
evaluation failures appear as failed jobs, including the preserved capture ID
when available. They do not masquerade as successful recommendations.

### Concurrency, persistence and restart behavior

- Blocking collection and replay run outside the ASGI request loop.
- The queue is bounded (10 queued/running jobs by default).
- Mutable job lifecycle state is in `data/service.sqlite3`, separate from the
  append-only evidence archive.
- A host-local file lock elects one job worker among API processes using the
  same job database. SQLite transactions serialize enqueue/claim operations.
- Queued jobs survive restart. A new worker marks formerly running jobs
  **interrupted/failed**, not automatically successful or blindly retried.
  Inspect snapshot history before retrying: a capture can have completed before
  its job acknowledgement was persisted.
- Shutdown stops taking new jobs. Forced termination can interrupt an active
  request; recovery remains visible rather than silently losing its status.

This is a **single-host, durable-local-disk deployment**. FastAPI supplies the
HTTP concurrency boundary; it does not turn SQLite/file locks into distributed
storage. For multiple hosts/replicas, replace the archive metadata/job store with
a shared transactional database/queue and keep raw payloads in durable object
storage. Preserve the REST contract. Do not mount this WAL database on a network
filesystem or copy one mutable database blob between concurrent jobs.

### Demo and access boundary

```sh
.venv/bin/python scripts/serve_api.py --host 127.0.0.1 --port 8787
```

The default accepts token-free API calls only from loopback clients. The launch
script refuses a non-loopback bind without an API token and explicit allowed
hosts. When a token is configured, all `/api/v1` data routes require
`Authorization: Bearer ...`; the public health endpoints contain no roster data.

Mutation routes require JSON, have a 64 KiB body limit and reject unapproved
origins. The service sets browser content/security headers. The dashboard loads
no external scripts/fonts, renders source text safely, and keeps authentication
tokens in memory rather than browser storage.

The app is built from `web/season/src` into ignored `web/season/dist` assets.
The HTML response injects a unique CSP nonce for Emotion/Material UI styles;
scripts remain same-origin and inline scripts are not enabled. HTML is not
cached, so a nonce is never intentionally reused across responses.

`ffopt/advice.py` creates the manager-facing read model from the saved baseline
and small cached roster facts, without provider requests or new evaluations on
GET. Old source data, a changed rule set, a later failed/incomplete update or a
kickoff since capture prevents unqualified current guidance. The initial
30-minute guard is a product safety default, not a validated polling optimum.

For remote use, configure TLS, an owner-restricted identity policy and explicit
trusted origins/hosts. The token is a single-user starter control, not a
multi-tenant authorization system. Keep a VS Code forwarded port private unless
these deployment controls have been established.

The demo displays archived-time forecasts and job status; it is not a live NFL
score feed or a proven competitive policy. No periodic collection scheduler,
email/push delivery or Sleeper write automation is installed by this server.
