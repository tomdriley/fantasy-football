# Hosting foundation

This is a small deployment probe, **not the hosted fantasy advisor**. The existing
CLI, API, SQLite stores and React application are unchanged.

The original staging probe passed an A/B/A deployment-and-rollback rehearsal on
September 12, 2026; see the [historical release ledger](hosting-azure.md#verified-live-target).
The retained [WestUS3 stage](https://thomasriley-fantasy-w3-pilot-stage.azurewebsites.net/fantasy-football/)
uses private, synthetic read-only PostgreSQL. Its production parent remains
disabled and publicly blocked. See [current operations](production-operations.md).

The checkpoints remain deliberately separate:

1. Deploy the harmless test app to **fantasy staging**, verify the exact release,
   and rehearse deployment rollback.
2. Private PostgreSQL with an application role that can only read synthetic rows.
3. Locally prepared, not yet enabled: Azure-managed Google sign-in and a small
   provider/subject allowlist, still without application database writes.
4. Later: one authenticated synthetic write and a database restore drill.

The [Google authentication-only checkpoint](hosting-authentication.md) is
implemented locally but not enabled in Azure; application writes are not implemented. There
are no workers, league configuration, real data, or write routes.
No website proxy, production deployment, slot swap, or repository publication is
part of this checkpoint.

## Run locally

Use the existing development environment described in the [README](../README.md).
No frontend build or new Python dependency is required.

```sh
FFOPT_HOSTING_ENVIRONMENT=local \
  .venv/bin/python -m uvicorn hosting.app:create_app --factory \
  --host 127.0.0.1 --port 8791 --no-proxy-headers
```

Open <http://127.0.0.1:8791/fantasy-football/>.

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_hosting*.py'
.venv/bin/python scripts/check_hosted_app.py http://127.0.0.1:8791 \
  --allow-http --expected-environment local --expected-release development
```

The standalone smoke checker uses only Python's standard library. It verifies
health, readiness, the exact application/environment/release response, and the
prefixed page. It rejects redirects, credentials in URLs, remote plain HTTP,
oversized responses and a healthy response from the wrong app.

## Configuration

| Setting | Meaning |
|---|---|
| `FFOPT_HOSTING_ENVIRONMENT` | `local` or `stage`; the image defaults to `stage`. Production is deliberately unsupported. |
| `FFOPT_HOSTING_RELEASE` | Full lowercase commit SHA baked into the image using build argument `RELEASE`. Local development may use `development`. Do not override the image's release in Azure settings. |
| `FFOPT_HOSTING_ALLOWED_HOSTS` | Comma-separated exact DNS names/IPv4 hosts, without schemes, ports, or wildcards. Required in staging. Use the slot's actual Azure hostname. |
| `FFOPT_HOSTING_PHASE` | `deployment` (default), `database-readonly`, or stage-only `authentication-only`. The latter two require explicit read-only database settings and never create a schema. |
| `FFOPT_AUTH_ALLOWED_IDENTITIES` | Private JSON provider/subject allowlist for `authentication-only`; missing/empty denies all protected access. See the [auth boundary and operator gates](hosting-authentication.md). |

Local defaults permit `localhost` and `127.0.0.1`. Stage refuses a missing
hostname, a local test hostname, or the `development` release. These checks are
configuration safeguards, **not authentication**.

| Endpoint | Expected behavior |
|---|---|
| `/fantasy-football/` | Harmless HTML page, release marker and status link |
| `/fantasy-football` | Relative redirect to the canonical trailing-slash path |
| `/fantasy-football/api/status` | Exact application, environment, release and checkpoint identity |
| `/healthz` | Process liveness: `{"status":"ok"}` |
| `/readyz` | Ready to serve this dependency-free probe: `{"status":"ready"}` |

GET and HEAD are supported. Other methods and advertised request bodies are
rejected. Unrelated routes, including the real advisor API, are absent. Requests
and responses do not disclose arbitrary environment variables. Proxy headers
are not trusted to construct redirects.

In default `deployment` mode, readiness proves only this small app has initialized. It does not
claim database, authentication, worker or football-data readiness.

## Read-only PostgreSQL canary

In `database-readonly` mode, `/readyz` performs a real database read and returns
503 if the connection, privileges or synthetic data checks fail. `/healthz`
remains process liveness. `/fantasy-football/api/sample` exposes only the three
approved synthetic rows; unexpected contents are rejected rather than exposed.
No database errors fall back to hard-coded successful sample responses.

Set `FFOPT_DB_HOST`, `FFOPT_DB_NAME`, `FFOPT_DB_USER` and `FFOPT_DB_PASSWORD`.
For Azure, use a slot-specific Key Vault reference for the reader password.
The serving identity must not have access to the administrator secret. An
unresolved Key Vault reference is a configuration error, not a password fallback.

Stage requires an Azure PostgreSQL hostname, port 5432 and `verify-full` TLS
using the CA bundle at `/etc/ssl/certs/ca-certificates.crt`. An explicit
`FFOPT_DB_ROOT_CERTIFICATE` can select an approved CA bundle. Disabling TLS is
accepted only for an explicitly configured local loopback test fixture.

The connection pool is bounded to two connections and four waiting callers,
with connection/query/lock timeouts. Each read checks the role's relevant
privileges and validates the exact synthetic dataset. The role must have only
the necessary CONNECT, schema USAGE and table SELECT privileges, no elevating
memberships, and no schema/database creation or sample-table write privileges.
Schema and credentials are provisioned through a separate private operator path,
not application startup.

```sh
python3 scripts/check_hosted_app.py https://THE-CANARY-HOST \
  --expected-environment stage --expected-release FULL_COMMIT_SHA \
  --expected-phase database-readonly
```

The release workflow accepts the same expected phase. Its local container
identity check remains dependency-free `deployment` mode; the hosted check for
`database-readonly` additionally verifies the actual SQL sample.

Real PostgreSQL tests use an isolated PostgreSQL 17 service in CI, with only
synthetic test credentials. Locally, set `FFOPT_TEST_POSTGRES_PORT` to the
loopback port of an explicitly started matching test container:

```sh
FFOPT_TEST_POSTGRES_PORT=YOUR_LOCAL_TEST_PORT \
  .venv/bin/python -m unittest discover -s tests -p 'test_*hosting*.py'
```

That fixture must use the documented CI-only `postgres`/`local-test-only`
credentials, never an Azure database. Tests create randomly named local
databases/roles, verify actual SQL permission failures with transaction
read-only mode switched off, and clean up only those test objects. The normal
`test_hosting*.py` selector does not require a running PostgreSQL fixture.

## Container

From the repository root:

```sh
docker build -f hosting/Dockerfile \
  --build-arg RELEASE="$(git rev-parse HEAD)" \
  -t fantasy-football-hosting:local .
docker run --rm --name fantasy-football-hosting-local \
  -p 127.0.0.1:8791:8080 \
  -e FFOPT_HOSTING_ENVIRONMENT=local \
  fantasy-football-hosting:local
```

In another terminal:

```sh
python3 scripts/check_hosted_app.py http://127.0.0.1:8791 \
  --allow-http --expected-environment local \
  --expected-release "$(git rev-parse HEAD)"
```

Stop only this named container when finished. A commit SHA labels the selected
source baseline; local uncommitted changes are not a publishable release.
Release workflows build approved committed source and record the image digest.

The image pins its Python base digest, installs the existing constrained
dependencies, runs as a non-root user, and copies only the foundation files.
The deny-by-default [build context](../.dockerignore) excludes Git history,
credentials, data, docs, tests, the advisor source, frontend and local environment.
No new `.env` file is needed.

## Publication and Azure gates

See [staging infrastructure and release instructions](hosting-azure.md) for the
prepared Azure/GitHub configuration and explicit deployment procedure.

The repository stays **private**. Only the reviewed hosting-probe image may be
made public in GHCR. New GHCR packages can default to private independently of
the repository; package visibility needs explicit verification.

Before any publication:

- Review all published image layers, config/history, labels and provenance,
  not just the final visible filesystem.
- Run the security review for the precise app/image/workflow changes.
- Confirm the package and approved source revision. Do not broaden the
  container allowlist to include the real app without another disclosure review.

Before Azure changes:

- Obtain the other agent's existing-site health/rollback handoff.
- Confirm the resource preview, identities/role scopes, app/slot names and costs.
- Reference the existing shared P0v3 plan; do not redeclare or resize it.
- Leave the new parent production app unused/access-restricted.
- Keep the blog, article service, Cosmos and existing storage configuration unchanged.

Passing local tests is not proof of hosted deployment or public-artifact safety.
The deployment gate requires Azure to report the intended digest, the running
app to report the expected release, and an A/B/A rollback rehearsal in staging.
Retain the previous known-good image digest; do not rebuild a rollback release.
Recheck existing-site health and shared resource use afterward.

Future database work must preserve separate operator/runtime privileges and
private administration access. Future sign-in can use App Service authentication
for Microsoft/Google, but this checkpoint has no authorization to enable writes
or expose real data. Once authentication/protected data exists, these earlier
unauthenticated images must not be used as rollback targets.
