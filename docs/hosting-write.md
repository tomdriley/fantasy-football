# Protected synthetic marker checkpoint

**Local implementation; not deployed or published by this change.** The operator
confirmed the Google login → approval → sample → logout checkpoint on September
13, 2026 and authorized this one synthetic write checkpoint. Keep the current
enrolled allowlist private. The live `authentication-only` release remains
`d471de3e65084373a4b3ea9984c108029e72681f`, image digest
`sha256:3728be4b5d5951d575a040861d45d509b715bf4d3f60b889906b7c392f6565dc`
until a separately reviewed deployment.

## Contract and trust boundary

Select **`FFOPT_HOSTING_PHASE=authenticated-write`** explicitly, stage only.
Keep the same Easy Auth Google-only configuration, public-path exclusions,
one-hour sessions, token-store-off setting, provider secret reference and private
provider/subject allowlist. Never add the new paths to Easy Auth exclusions:

| Path | Methods | Effect |
| --- | --- | --- |
| `/fantasy-football/test-write` | GET, HEAD | Protected page with “Record test write” button |
| `/fantasy-football/static/test-write.js` | GET, HEAD | Protected static code; no database operation |
| `/fantasy-football/api/test-write` | GET, HEAD | Read only this approved account's marker/status |
| `/fantasy-football/api/test-write` | POST | Create this account's single fixed synthetic marker, or return the existing record unchanged |

All other application POST/PUT/PATCH/DELETE/OPTIONS/TRACE methods remain denied.
No reset/delete/update API, arbitrary text, client-selected identity, user
management, advisor or real league data is enabled. Readiness performs catalog
and privilege checks but **never inserts a marker**. Status never creates one.

The backend obtains the owner only from the validated Easy Auth identity and
allowlist on **every** request. The external platform strips forged identity
headers; direct local header injection is only a fixture modeling that boundary,
not a valid authentication mechanism. Never expose the container without Easy
Auth. The existing session enrollment endpoint still returns only that signed-in
user's own identity; do not save its output in logs or test evidence.

The private owner key is SHA-256 of UTF-8/ASCII `provider + NUL + subject`. It is
stable across restarts, unambiguous for the allowed identity format, and never
returned by the marker API. This is pseudonymous identity-derived data, **not
anonymous data**; protect it and database backups. Raw subject IDs are not stored
in the marker table. One primary-key row per identity bounds duplicates. Revoking
approval stops access, not retention; there is no application cleanup/reset.

The response contains only `recorded` and `marker`: absent markers are
`{"recorded":false,"marker":null}`; a recorded marker contains the fixed
`value: "synthetic-write-v1"` and original UTC `created_at` timestamp. PostgreSQL
supplies the timestamp. `INSERT ... ON CONFLICT (owner_key) DO NOTHING`, followed
by a separate SELECT in a READ COMMITTED transaction, handles concurrent first
writes without UPDATE privileges, duplicate rows or timestamp changes. A failure
after commit but before the response can be retried safely.

### CSRF and input policy

The only accepted action is JSON `{"action":"record"}` (no additional or duplicate
keys), with exact `Content-Type: application/json` and
`X-FFOPT-Write: record-v1`. **Origin is mandatory** and must equal `https://` plus
the single explicitly configured `FFOPT_HOSTING_ALLOWED_HOSTS` hostname, with no
port, trailing slash or alternative hostname. Forwarded Host/Proto headers are
never used. Missing, foreign or `null` origins fail. `Sec-Fetch-Site`, if present,
must be `same-origin`; duplicate security headers, content encoding, query
parameters and simple form content types fail. No CORS middleware or successful
preflight exists.

This follows the [OWASP custom-header and origin verification approach](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).
Cross-origin browser script cannot supply the custom header without a preflight,
and forms cannot produce the accepted request. Exact mandatory origin validation
is an independent check. This avoids custom sessions or CSRF cryptography; it
does not purport to defend against same-origin script compromise. CSP permits
only same-origin external scripts and connections, never inline/eval, forms,
frames or external destinations. The button is convenience, not authorization.
The fetch explicitly uses `referrerPolicy: "same-origin"`: the page's otherwise
strict `no-referrer` response policy can turn a same-origin-mode POST's Origin
into `null`. This override preserves the mandatory Origin without sending
referrers cross-origin; the request still uses same-origin mode and denies redirects.

Bodies are bounded to **128 bytes while reading the stream**, even when chunked
or the length is omitted/false; reads have a five-second total deadline. Oversized
advertised lengths are rejected before body reading. Actual overflow returns 413;
bad headers 403, bad JSON/action/length 400, timeout 408. Only a confirmed database
operation returns success; outages, permission drift and malformed returned
records fail with sanitized 503 errors.

## Admin-only database preparation

Use only the existing private PostgreSQL 17 server `thomasriley-ff-w3-pg`,
database **`hosting_stage`**, via the separately approved admin-only private ACI
operator path. Do not open a public firewall, use runtime credentials for
migrations, or grant the app/admin access to a broader secret scope.

1. Privately verify the existing reader and exact three synthetic
   `hosting_probe.sample` rows before and after preparation. **Do not change
   `ffopt_stage_reader`, its grants, its settings, schema, or sample rows.**
2. Create the new role as admin, without memberships or ownership:

   ```sql
   CREATE ROLE ffopt_stage_probe_writer LOGIN
       NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
       CONNECTION LIMIT 4;
   ```

   Provision a distinct cryptographically random password through the approved
   private operator path, set the role password without logging SQL or values,
   and save it only in the isolated Key Vault secret
   **`pg-write-probe-password`**. No password literal belongs in this
   migration, source, CLI history, pipeline output or app settings.
3. Privately verify this new role inherits no PUBLIC database CREATE/TEMP
   privileges, no schema CREATE, no other non-system table/column/sequence
   privileges and no callable non-system functions. The current read-only
   database already denies database CREATE/TEMP. If prerequisites differ, stop
   and review with the operator; do not silently change shared PUBLIC grants or
   reader behavior. Do not add SECURITY DEFINER helpers or broad role memberships.
4. Review and run **`hosting/db/002_marker.sql`** as admin in `hosting_stage`.
   It is one-time, transactional and intentionally fails if the schema already
   exists: investigate existing state rather than blindly recreating it.
   It creates only:

   - `hosting_write_probe` schema, owned by the admin/migration owner.
   - `hosting_write_probe.marker` ordinary table:
     `owner_key TEXT PRIMARY KEY CHECK (owner_key ~ '^[0-9a-f]{64}$')`,
     `value TEXT NOT NULL DEFAULT 'synthetic-write-v1' CHECK (value = 'synthetic-write-v1')`,
     `created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP`.
   - Enabled and **FORCED** RLS, one policy `marker_owner`, with both USING and
     WITH CHECK exactly `owner_key = current_setting('ffopt.actor', true)`.
   - `CONNECT` on `hosting_stage`, `USAGE` on `hosting_write_probe`, and
     **only SELECT and INSERT** on `hosting_write_probe.marker` for the writer.
     No grants on the reader's sample, no UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER/MAINTAIN,
     sequences, grant options, DDL, memberships or administrative powers.

The runtime checks its actual session/current role and relevant grants for each
operation. It also checks table ownership/type, expected columns/defaults/primary
key, no triggers/rules, forced RLS, and the single exact policy; mismatches fail
closed. An admin changing schema/grants is trusted administration, not something
the app can secure against, so keep those changes gated. The runtime never reads
or executes migration files; the Docker build context and COPY allowlist exclude
**all `hosting/db/`, infra, tests, docs, repository history and real data**.

For every owner query, the server sets `ffopt.actor` with parameterized,
transaction-local `set_config(..., true)` from the trusted identity. Both an
explicit owner WHERE and RLS apply. Actor state clears on commit/rollback, not
through a best-effort reset callback. RLS guards application query mistakes and
pooled-user isolation, **not possession of the shared writer credential**:
the role can itself set a GUC. Never expose that credential or direct SQL access.

The writer has a separate pool: zero initial/two maximum connections, four
waiters, five-second acquisition/connect/reconnect bounds, three-second statement
and idle-transaction timeouts, one-second lock timeout, 60-second idle and
300-second lifetime bounds. Azure requires certificate/hostname verification.
The existing reader pool and its default read-only transaction setting are
unchanged. A marker read returning unexpected contents is never serialized.

## Writer configuration and staged activation

No step here has been run by this local implementation. Parent/operator retains
deployment, independent security review, CI, publication and private provisioning
ownership; approvals for those actions are not implied by these instructions.

1. Complete scoped source/security review, then the existing private candidate
   CI workflow on the reviewed source. It uses real isolated PostgreSQL 17
   fixtures and builds the non-root candidate without publication. Inspect all
   layers before any separately approved publication. The image must carry both
   `io.ffopt.hosting.authentication=google-allowlist-v1` and
   **`io.ffopt.hosting.write=synthetic-marker-v1`**. Labels alone are not review.
2. Perform the private database preparation above and verify writer denials and
   unchanged reader behavior. Do not insert an account marker as an admin probe;
   the user's protected button is the intentional first write.
3. Preserve persistent `FFOPT_AZURE_STAGE_AUTH_LOCK=google-allowlist-v1` and the
   existing Google client variable. Add the independent workflow variable
   **`FFOPT_AZURE_STAGE_WRITE_LOCK=synthetic-marker-v1`** before enabling writes.
   The scripts receive it as `FFOPT_STAGE_WRITE_LOCK`. Write deployment requires
   both locks and capabilities; nonwrite deployment refuses an active write lock.
   The routine workflow never reads private app settings. Operators must verify
   writer settings and the lock agree; never use an old workflow revision to
   bypass these guards.
4. Stop **only stage** for the transition. Securely snapshot *all* existing
   stage app settings and Easy Auth config. Verify the enrolled allowlist is
   unchanged privately; do not print or publish it. Verify the exact stage host,
   both existing Key Vault references, and absence of a release override.
5. Review/what-if **only `infra/azure/stage-write-settings.bicep`**. Supply the
   freshly captured settings as secure `existingAppSettings`. It merges the
   writer fields below, preserving every existing setting and the actual
   allowlist, and grants stage's existing managed identity Key Vault Secrets
   User on **only the new writer secret**. It does not read the secret value or
   redeploy authsettings, the base app, the parent or PostgreSQL.

   | Setting | Required stage value |
   | --- | --- |
   | `FFOPT_HOSTING_PHASE` | `authenticated-write` |
   | `FFOPT_WRITE_DB_HOST` | Same as retained `FFOPT_DB_HOST`: private Azure PG hostname |
   | `FFOPT_WRITE_DB_NAME` | Same as retained `FFOPT_DB_NAME`: `hosting_stage` |
   | `FFOPT_WRITE_DB_USER` | `ffopt_stage_probe_writer`, not the existing reader or an admin |
   | `FFOPT_WRITE_DB_PASSWORD` | `@Microsoft.KeyVault(SecretUri=https://tr-ff-w3-kv-fcbc.vault.azure.net/secrets/pg-write-probe-password)` |
   | `FFOPT_WRITE_DB_PORT` | `5432` |
   | `FFOPT_WRITE_DB_SSLMODE` | `verify-full` |
   | `FFOPT_WRITE_DB_ROOT_CERTIFICATE` | Normally absent; default `/etc/ssl/certs/ca-certificates.crt` |

   Missing/unresolved credentials, unknown writer keys, local write phase or
   multiple allowed hosts fail startup. **Any `FFOPT_WRITE_*` settings in
   nonwrite phases are rejected by the new image.**

   **Never reapply `stage-auth-settings.bicep` with its default empty
   `authorization` over the enrolled list.** It is not the writer template.
   Do not redeploy `main.bicep` or `production/fantasy-app.bicep`: they replace
   configuration. No parent or sticky-name change is included/authorized here.
   Slot swaps remain forbidden; writer settings exist only in stage. If sticky
   metadata changes are desired later, obtain separate approval and preserve the
   complete existing sticky list.
6. While stage remains stopped, verify the private snapshot diff (only the
   listed fields changed), secret-scoped grant and resolved writer KV reference,
   the same enrolled allowlist, and strict unchanged authsettings using
   `scripts/check_hosting_auth_config.py --expected-phase authenticated-write`
   with the existing approved client ID and auth lock. Select only the separately
   reviewed/published immutable writer-capable digest. Confirm both capabilities
   before explicitly starting stage. Do not run the authentication-only image
   with writer credentials during this transition.
7. Use the reviewed stage deployment workflow with
   `expected_phase=authenticated-write`, expected release and exact digest.
   Its authsettings validation runs before and after the image change. Run the
   hosted smoke checker with the same phase: public technical paths, anonymous
   and forged-header denials on all new protected paths, plus fully shaped
   anonymous/forged POST denial. These credential-free checks must not write.
8. Approved user: sign in, open the protected page, verify no marker yet, click
   **Record test write**, then repeat/reload and confirm the original timestamp
   remains. Sign out and verify page/status/write denial. Unapproved Google
   accounts receive 403; anonymous and forged platform headers receive 401.
   Privately verify exactly one marker row for the approved identity, no cross-user
   rows exposed, unchanged three reader samples, readiness, parent blocked and
   other apps unchanged. Never retain identity/session/token response bodies or
   unsanitized DB errors as test evidence.

## Auth-safe stop and rollback

No automatic rollback, old unauthenticated release, full base redeployment,
restore/reset API or slot swap is permitted.

- Stop stage first. For immediate containment, disable/revoke access to **only**
  the writer secret and/or disable only the writer role as an approved admin;
  never touch the reader or Google secret. Disabling a KV secret alone does not
  instantly invalidate credentials already held in a process, so keep stage
  stopped and rotate/disable the writer DB login when required.
- From a fresh private snapshot, remove **every `FFOPT_WRITE_*` app setting** and
  set `FFOPT_HOSTING_PHASE=authentication-only`. Preserve the enrolled
  `FFOPT_AUTH_ALLOWED_IDENTITIES`, Google secret reference, exact host, complete
  reader configuration, all unrelated settings and unchanged Easy Auth.
- Verify writer settings are gone and stage is still stopped **before** clearing
  only `FFOPT_AZURE_STAGE_WRITE_LOCK`. Never clear
  `FFOPT_AZURE_STAGE_AUTH_LOCK=google-allowlist-v1`. Remove the writer's secret-only
  grant if retiring the checkpoint; keep the protected table/data intact.
- Select the retained reviewed authentication-capable digest above, validate
  unchanged authsettings and settings, then explicitly start stage and verify
  authentication-only behavior/anonymous and forged-header denials. An old
  image cannot detect new writer settings itself; their removal is a mandatory
  operator prerequisite, not an assumed runtime safeguard.

Returning to the write phase later uses the same original marker; no database
rollback or timestamp reset is needed. A disaster-recovery/restore exercise and
advisor migration are separate gates, not authorized by this checkpoint.

## Local and CI validation

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*hosting*.py'
bash -n infra/azure/check-image.sh infra/azure/deploy-stage.sh
az bicep build --file infra/azure/stage-write-settings.bicep --stdout >/dev/null
```

The existing hosting CI service enables `FFOPT_TEST_POSTGRES_PORT` against
isolated loopback PostgreSQL 17 with synthetic credentials. The fixture exercises
sequential/concurrent idempotency, process/reconnect persistence, per-owner RLS
and pooled reset on success/error, absent actor/cross-owner INSERT denial, writer
DDL/grant denials, unchanged reader, admin/excess grants/RLS/default drift
rejection, missing tables and bad/closed connections. Without a local PostgreSQL
fixture these real-DB tests explicitly skip; unit/mock tests do not substitute
for a successful private CI fixture run before deployment.

Local implementation verification: **129 tests discovered, 110 passed, 19 real
PostgreSQL tests explicitly skipped** because no local fixture/Docker was
available (7 retained reader tests, 12 new writer tests). The dependency-free
Node browser-script fixture ran successfully, including no POST on load,
explicit-click requests, safe retry and failure display. JavaScript syntax,
Bash syntax and `git diff --check` also passed. No local Bicep compiler was
available; template compilation, real PostgreSQL and container/layer checks
remain required private-CI gates. No cloud operation, commit, push or publication
was performed by this implementation.

### Scoped implementation file inventory

- Runtime: `hosting/app.py`, `hosting/auth.py`, `hosting/write.py`,
  `hosting/write_database.py`, `hosting/static/index.html`,
  `hosting/static/test-write.html`, `hosting/static/test-write.js`.
- Image allowlist/capabilities: `hosting/Dockerfile`, `.dockerignore`.
- Admin-only migration: `hosting/db/002_marker.sql`.
- Operator/deployment gates: `infra/azure/stage-write-settings.bicep`,
  `infra/azure/check-image.sh`, `infra/azure/deploy-stage.sh`,
  `scripts/check_hosted_app.py`, `scripts/check_hosting_auth_config.py`,
  `.github/workflows/hosting-ci.yml`, `.github/workflows/hosting-stage.yml`.
- Tests: `tests/test_hosting_write.py`, `tests/test_postgres_hosting.py`,
  `tests/test_hosting_auth_config.py`, `tests/test_hosting_deployment.py`.
- Documentation: `docs/hosting-write.md`, `docs/hosting.md`,
  `docs/hosting-authentication.md`, `docs/hosting-azure.md`.

Existing `hosting/database.py`, `hosting/db/001_sample.sql`, authentication
Bicep templates, dependency manifests, and unrelated README/Yahoo work were
left unchanged. The candidate adds four runtime files to the previously
reviewed eight-file `/app` inventory, for twelve files total; SQL/admin
migrations remain outside the image.
