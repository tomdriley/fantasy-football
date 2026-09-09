# In-season product design: a manager's next decision

Status: manager decision contract updated September 7, 2026.

## 1. Product purpose

Help a fantasy-football novice answer, in order:

1. **Can I use this advice, or must I update it?**
2. **Should I change something or keep my team as it is?**
3. **When do I need to act?**
4. **What should my lineup look like?**
5. **Where do I make the changes?**

The primary product is an assistant for managing a team, not a database browser.
Research remains valuable, but is a separate, deliberately discoverable mode.
The forecast-led lineup baseline remains unchanged. The manager layer makes an
explicit conservative hold decision for optional pickups whose net benefit has
not been established; it does not claim that holding is empirically optimal.

## 2. Diagnosis of the current interface

Observed problems:

| Problem | Consequence | Design response |
|---|---|---|
| Snapshot history, evaluation selection and the shadow lab precede the lineup | The user must understand implementation concepts before finding useful advice | Make My week the default; move historical data and research out of its main flow |
| "Capture", "run baseline", "compare", and "reload workspace" compete | It is unclear which action obtains current advice | One primary action: **Update advice** |
| Large disclaimers and technical status cards consume the first screen | Warnings are visible but the actual decision is not | A compact, contextual freshness warning; technical health under More |
| Projections, recommendations and experimental alternatives share one surface | A user can mistake an experiment or an old report for current guidance | My week always uses the baseline; historical and research pages are explicitly labeled |
| Slot moves and small pickup gains look equally urgent | Optional roster churn can crowd out injury/lineup checks | Separate lineup work, injury checks, and optional pickups |
| IDs, worker counts and source headers are routinely visible | Noise without an immediate managerial decision | Keep detailed evidence one deliberate action away |

Simply restyling the existing cards would not solve these problems.

## 3. Audience and design principles

Primary user: one manager with limited fantasy-football knowledge, checking on a
phone or desktop before a deadline. They need explicit guidance, not a dashboard
they must interpret like an analyst.

- Start with the task and its urgency, not a metric inventory.
- Explain uncertainty where it affects a decision.
- Prefer plain language over internal nouns.
- Show the recommended end state, not just independent low-level mutations.
- Do not imply that advice has been applied in Sleeper.
- Keep the current week separate from exploration of the past.
- Use standard components and a restrained visual hierarchy.

## 4. Information architecture

### My week (default)

1. Team and saved week, with a concise last-update label.
2. Freshness/availability state and **Update advice**.
3. "What to do now", with explicit lineup and roster decisions:
   - keep the lineup or make the specified changes;
   - keep the roster rather than treating projection comparisons as instructions;
   - keep Questionable starters while checking their status at the stated time;
   - resolve an actual lineup gap or data/roster issue, never label it all-clear.
4. The next advice check and lineup lock, before detailed game-day injury checks.
   With unresolved starters, recheck before the first remaining roster lock,
   including bench players, rather than waiting until an early alternative is lost.
5. Recommended lineup, with ordinary position labels and snapshot-time projections.
6. Pickups considered in a collapsed section, clearly marked as comparisons and
   not recommended moves when the roster decision is hold.
7. A secondary **Open Sleeper** link and a reminder to update advice after editing there.

Do not show snapshot IDs, experiment controls, saved-evaluation selectors, worker
counts, or payload statistics on this page. A projected total may appear as
secondary information near the lineup; it is not the page's hero metric.

### Past advice

A compact chronological list of update attempts. Selecting one opens historical
advice, labeled **Historical report - do not treat as current advice**.

Failures are visible and explain that no new usable advice was obtained.
The user can return to My week without accidentally replacing its current
selection with an old report.

### More

Secondary menu items:

- **Research lab:** explicit shadow comparisons on selected archived inputs.
- **Data and service:** source provenance, update/job details, storage and API schema.
- **Connection:** token entry/disconnection when the server requires it.

Research results never change My week's policy. A difference between policies is
not described as an improvement.

## 5. Core workflows

### Update -> understand -> act -> verify

1. The user opens My week.
2. If data is old, failed, missing or invalidated by a kickoff, the interface
   prioritizes **Update advice** rather than actionable-looking stale suggestions.
3. Updating starts the existing durable collection job with fresh references.
   Show "Updating advice..." and a concise progress state, not job IDs.
4. On success, show a concrete lineup decision, roster decision and target lineup.
5. The user makes only recommended changes in Sleeper, or makes no changes when
   the recommendation is hold. This app does not submit anything.
6. The same Update advice action checks the newly observed lineup.

The user does not need to learn what a snapshot or an evaluation is to complete
this workflow.

### Retry an uncertain request

If the response to an update is lost, retain its endpoint, body and idempotency
key. Offer **Retry update**, using the same request. Do not enable a new update
that might duplicate accepted work. Authentication/reconnection must not discard
that pending operation. Non-secret pending-request metadata may be kept in
session storage; API tokens remain memory-only.

### Explore without changing guidance

Past advice and Research are distinct routes. Research has its own selected
snapshot and clearly experimental controls. Returning to My week reloads the
latest baseline, not the last research selection.

## 6. States and safeguards

| State | Primary message/action | Treatment of recommendations |
|---|---|---|
| No saved advice | "Get your first advice" / Update advice | No invented sample recommendations |
| Loading | Concise skeleton/progress | No false "all set" state |
| Recent valid advice | Show specific act/hold decisions and deadline | Forecast baseline and explicit conservative roster policy, subject to current news |
| Old advice | "Update before making changes" | Clearly label previous lineup; suppress imperative stale action cards |
| A kickoff occurred after capture | "Lineup locks have changed" / Update advice | Do not suggest moves based on an obsolete lock state |
| Update failed | Explain that no new usable advice was saved; retry | Retain prior data only as visibly old reference |
| Updating | One active update state | Prevent duplicate actions; history remains browseable |
| Ambiguous submission | Retry the same request | Preserve idempotency state until resolved |
| Historical report | Explicit historical label | No "act now" or "current" framing |
| Questionable player | "Keep this starting lineup" with a check time | No unvalidated probability discount; check-now updates as the clock advances |
| Missing data / invalid IR / incomplete lineup | Explicit limitation and next step | Never describe unknown production as zero or the lineup as ready |

Initial recency guard: 30 minutes from capture, plus a kickoff-since-capture
check and source-age checks. This is a conservative **product safeguard**, not a
validated optimal polling cadence or proof that provider data is current.
The user can always inspect older reports through Past advice.

### Explicit manager decision contract

The advice API includes `decisions.lineup` and `decisions.roster`, each with an
action, title, instruction, reason, evidence basis and blocked flag. A hold is a
recommendation, not an empty list the manager must interpret.

- A valid, complete lineup follows the existing forecast/eligibility baseline.
  Apply specified changes, otherwise keep it. Questionable alone does not cause
  a new injury discount or an instruction to bench.
- Optional one-week pickup comparisons do not authorize roster churn. Recommend
  keeping the roster while its starting slots can be filled and a net advantage
  after future-player and acquisition costs has not been established.
- A known lineup gap or blocking data/roster issue must override the all-clear
  hold state and retain required repair instructions.
- When an owned replacement can solve an unavailable starter, recommend the
  exact lineup change without calling the status explanation a data blocker.
  An already-locked slot is held unchanged, not presented as repairable.
- A real unfilled slot can receive one staged emergency pickup. Prefer an open
  roster place; otherwise use an unlocked, unflagged, projected bench candidate.
  Protect starters, reserves, injuries, byes, missing data and immediate
  single/pair flagged-starter coverage. A missing forecast alone does not justify
  an acquisition. The bounded search is not a rest-of-season valuation model.
- A proposed repair names the add and any drop, and requires an atomic add/drop,
  claim-route verification and a fresh update before the remaining lineup is set.
  Its deadline includes the added player, drop and required starter slot moves.
  It expires at the relevant source TTL or lock deadline, whichever comes first;
  emergency ownership advice can therefore expire sooner than the usual recency
  guard. No claim success or safe future value is asserted.
- If no protected, legal positive-forecast repair is found, show the specific
  blocker and do not authorize a drop. Never replace this condition with an
  all-clear hold. Other projection comparisons remain unapproved.
- Missing, stale, historical or unconfirmed responses cannot issue current
  act/hold recommendations. Older responses without the decision contract are
  shown as requiring an update, not interpreted as a successful hold.
- Check-now wording advances with the browser clock without mutating saved
  advice or extending its validity. The handoff does not tell a manager to make
  changes when none are recommended.
- `next_review_at_ms` provides an earlier review before the first remaining roster
  lock when starters are flagged. It is a conservative opportunity safeguard,
  not a claim that a fallback is guaranteed or that 90 minutes is an optimal
  polling interval. Player-specific inactive checks remain separately visible.

This is a deliberate low-churn policy under limited evidence, not proof of an
optimal hold strategy. Confidence is not invented from projection margins.
The September 7 qualified availability prototype found only modest Q-only
improvement and material miscalibration; its near-game official-report cohort
does not establish a transferable probability for early-week Sleeper flags.
Probability weighting remains research-only. Known-Out legal replacement and
slot/lock safety do not require an invented play probability.

## 7. Content and action vocabulary

| Internal concept | Primary product wording |
|---|---|
| Capture snapshot | Update advice |
| Latest completed capture | Last successful update |
| Evaluation | Advice / analysis, according to context |
| MOVE actions among the same starters | Adjust position slots; explain preserving later FLEX options |
| A start/sit recommendation | Suggested lineup changes |
| One-week streaming screen | Pickup comparison, not an approved add/drop |
| Worker/job queue | Updating / waiting, with technical details under More |
| Policy disagreement | Different suggestions, not demonstrated gains |

Slots use familiar labels (QB, RB, WR, TE, FLEX, K, DEF). Explain FLEX briefly
where relevant. The full target lineup removes ambiguity when several moves
must be made together.

Availability instructions distinguish **check time** from **game/lock time**.
Group flagged players into check-ins about 90 minutes before kickoff, show the
next check time even when details are collapsed, and use "Check now" once that
window opens. Explain the sequence: return, Update advice, verify Sleeper status,
and replace a ruled-out player before kickoff. Questionable alone is not a bench
instruction. State explicitly that no automatic reminder is sent yet.

## 8. Framework and design system

Use **React + TypeScript**, **Material UI Core**, and **Vite**:

- Standard AppBar, navigation, Alert, Button, Table, Accordion, Dialog, Skeleton
  and form components instead of another bespoke component library.
- A small light theme, one primary accent, system fonts, standard spacing and
  restrained status colors. No custom hero graphics or decorative stat cards.
- Hash-based routes keep deployment simple with the existing static/API server.
- No paid MUI components, external fonts or CDN scripts.
- Bundle assets locally. Use a server-issued CSP nonce for Material UI/Emotion
  styles rather than broadly allowing inline scripts/styles.

The REST interface remains independent of this choice. The backend supplies a
manager-facing read model for freshness, deadlines and action summaries so the
browser does not invent domain advice.

## 9. Accessibility and responsive behavior

- Keyboard-operable navigation and forms, visible focus, labeled controls.
- Semantic headings/tables; color is never the only status signal.
- Polite live announcement for update progress; clear error recovery.
- Touch targets approximately 44px; no horizontal page overflow at 390px.
- Injury lists, optional pickups and evidence details expand only when requested.
- Keep essential context visible without long warning paragraphs.
- Respect reduced-motion preferences; no decorative animation.

## 10. Acceptance criteria

1. At desktop 1440x900, the first viewport shows update state, next deadline and
   the start of actionable guidance/lineup, not a research console.
2. At mobile 390x844, the first screen answers whether an update is needed and
   presents its primary action; no page-level horizontal overflow.
3. My week has one primary update action and no snapshot/evaluation/experiment
   controls.
4. Stale data, kickoff changes and failed updates cannot produce an unqualified
   "all set" or actionable-looking current recommendation.
5. A real update completes through the existing REST job flow and refreshes advice.
6. Research comparison works, but returning to My week still shows the baseline.
7. Ambiguous submissions retry with the same key/body; no duplicate job is created.
8. React build/typecheck, backend regression tests and browser checks pass.
9. The running demo serves the built Material UI application, not the old UI.

## 11. Non-goals for this change

No new fantasy strategy, automated Sleeper transactions, email delivery, periodic
collector, cloud deployment, or multi-tenant account system. No claim that the
advice is a validated winning policy.

## 12. Implementation and acceptance evidence

- React/TypeScript and Material UI Core replace the old vanilla research page.
  Vite builds same-origin assets; the Python service provides the Emotion style
  nonce. Earlier hashed assets are retained during local rebuilds so open tabs
  can still load lazy routes.
- Manager-facing API views use the saved baseline, with no provider calls or
  new evaluations on read. History never becomes My week's selected report.
- Missing current-player forecasts block advice rather than becoming bench
  instructions or a zero-valued total. Changed FLEX eligibility and pickup
  kickoffs also invalidate affected advice.
- Collection transitions require a subsequent confirmed advice response.
  Ordinary background reads preserve stable valid content; network transit and
  monotonic clock tracking cannot extend a validity deadline.
- Ambiguous requests retain endpoint, payload and idempotency key through
  authentication and reload. Unavailable browser storage produces a warning;
  lazy-page failures retain usable navigation and recovery controls.
- Typecheck/build and **45 frontend behavior tests** passed. The full Python
  suite ran **555 tests successfully, with 3 optional skips**.
- Real Update advice and Research comparison jobs succeeded. Returning from
  Research and Past advice kept the current baseline and its two optional
  pickups, rather than the experimental result.
- Desktop and mobile were checked at 1440x900 and 390x844. The mobile update
  button, deadline and attention heading were at approximately y368, y466 and
  y626; no horizontal page overflow. There is one Sleeper handoff.
- Material UI styles matched the server-issued CSP nonce. Backend and frontend
  review findings were fixed and rechecked.

The running demo is local at `http://127.0.0.1:8787/`. This is implementation
validation, not a usability study with independent participants or evidence of
competitive fantasy performance.
