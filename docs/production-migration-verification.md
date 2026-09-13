# Production migration verification — 2026-09-13

The public website and article API run in `ff-westus3-pilot` (West US 3).
Resource names containing “pilot” are retained production names, not an expiry
policy. See [production operations](production-operations.md) for infrastructure,
recovery, deployment and monitoring procedures.

## Verified outcomes

- `thomasriley.ca`, `www.thomasriley.ca`, and
  `article-service.thomasriley.ca` serve their separate Azure-managed certificates,
  expiring 2027-03-13. Certificate fingerprints were checked against the
  certificates actually served over trusted public TLS, before and after deleting
  the temporary bridge certificate. Its private keys/account artifacts were
  removed.
- Forty public checks between 03:52:34 and 04:33:04 UTC passed for all three
  domains. Every check verified managed TLS, successful HTTP responses, and
  twelve API articles. The last check was after the old DNS TTL expired.
- The full public parity check passed for twelve article details, fourteen
  rendered pages, and referenced regional assets.
- Both Mongo collections were restored from BSON Extended JSON, twelve documents
  each. The private non-root restore job checked document read-back, index key
  patterns and denial of writes by the runtime reader. This is not a claim that
  every possible index option was exercised. Production reads only
  `blog-site/articles2`; the original `articles` collection is archival.
- All eighty image blobs (100,950,531 bytes) and their private archived copies
  passed fresh size/hash checks; metadata and source inventory matched. The
  archived Mongo export and manifest also passed fresh integrity checks.
  Backups remain private and outside Git.
- The archival `articles` collection contains sixteen already-broken WordPress
  image paths. These are not served by the active collection and were not
  silently rewritten. Recovery of `articles2` must apply the documented old-to-new
  Blob hostname transformation.
- Production Mongo uses a collection-scoped find-only reader through Key Vault.
  Website stages expose one synthetic article; production exposes twelve real
  articles. Production promotion changes only the verified component image,
  never swaps synthetic stage settings into production.
- The fantasy stage serves the verified hosting release
  `d5fdb8625273bd69974454582fc83b3c819336e5`, image digest
  `sha256:2e306ca539d7e4eb1a5dbcea9c1aac47bdc937d7020e6eae459aecd5b01df782`.
  PostgreSQL remains private, B1ms/32 GB and runtime read-only. The fantasy parent
  is intentionally Disabled.
- Five scoped deployment identities were checked. Both obsolete website
  publish-profile secrets were removed.

## Repository changes and checks

| Repository | Commit | Change |
| --- | --- | --- |
| fantasy-football | `56922390e78e8f7a219294223af5a767b6de1b61` | West US 3 stage deployment, scoped OIDC and deployment checks |
| fantasy-football | `769c6158ea50ba094f0e1a38da3e0a9aa373da64` | Nine production Bicep templates and recovery/operations runbook |
| thomasriley-ca | `2c05dd2d5624e8abd2b693043dfac008a11a594f` | Scoped OIDC deployment and separate stage slots |
| thomasriley-ca | `7c33e95fb718a8cadab861be02396b886bcef258` | Synthetic article rendering verification |
| thomasriley-ca | `b220ab6f2b65c3e50a83768846d5f06cd410f704` | Immutable component artifacts required for promotion |
| thomasriley-ca | `a66e9dd8bd331caf8fa48d96bfd661fca6468bbf` | Reject synthetic data during production verification |
| thomasriley-ca | `7b3af0529b9cfc9864d61843f41504afaccfeeb8` | Reuse the Mongo pool across concurrent requests |
| thomasriley-ca | `8b031974700cc29bbf37983b1529fa1d54b89352` | Real database health probe without closing the pool |

Local validation: 55 hosting tests passed on the final targeted rerun; website
lint/compilation and eight HTTP guard cases passed. All nine production Bicep templates compiled
without warnings; secure-parameter, alert and budget assertions passed.
The added dependency-free database regression test covers concurrent reads,
health checks, query failures and database outages without connecting to a database.

Independent reviews covered workflow/OIDC/deployment security and the new
production templates' correctness:

| Review | Severity | Confidence | Finding |
| --- | --- | --- | --- |
| Workflow, OIDC and deployment security | None reported | Not applicable | No vulnerabilities identified in the reviewed changes |
| Production infrastructure correctness | None reported | Not applicable | No significant issues identified |
| Mongo pool follow-up correctness | Medium, resolved | High | A retained client's connection state was not a real health probe; fixed with an awaited ping and an outage regression test |

These are scoped reviews, not a guarantee that the system has no defects.

## Successful deployment gates

The hosted-runner queue subsequently cleared. These actual OIDC workflows
completed successfully; no environment approval or provenance gate was bypassed:

- Fantasy stage: run `34737148503`; CI: `34737149376`.
- Latest website component builds: `34738944112` and `34738944167`.
- Website lint and database regression: `34738943852`.
- Explicit article production promotion: `34738982968`, digest
  `sha256:e482720787d7cc86225917164d663b70d543597ddf937df06fd6285caf454567`.
- Explicit website production promotion: `34739075365`, digest
  `sha256:1ae75f0207119c23eecd3ab4f8a7ce395783652d7b83e58935d37810e80959f7`.

Production promotion consumed each successful component run's immutable
verification artifact and left environment settings unchanged. Full public
parity and managed TLS were checked again after promotion. A modest concurrent
read test exposed HTTP 500s hidden by sequential checks: each request closed the
shared Mongo pool. After the surgical pool-lifetime correction and reviewed
health-probe fix, forty live concurrent requests (thirty article-list reads and ten
real database pings, four workers) all returned HTTP 200; full parity also passed.
Original runtime retirement was gated on verified backups, production acceptance
and DNS TTL expiry; the TTL gate alone was insufficient.

Direct main-branch publishing used the existing maintainer bypass permission:
GitHub reported bypasses of PR/required-status branch rules. Actual lint, stage
and explicit production workflow checks subsequently ran successfully; production
artifact verification and OIDC environment gates were not bypassed.

## Completed, scoped cleanup

After fresh checks across 65 resources and twelve apps/slots found no consumers
and no MySQL server, these obsolete failed-attempt resources were removed between
04:41 and 04:42 UTC, after the TTL gate:

- `thomasriley-ff.mysql.database.azure.com/mysql-vnet` private DNS link.
- `thomasriley-ff.mysql.database.azure.com` private DNS zone.
- `thomasriley-ff-data-vnet`.
- `tr-ff-stage-kv-9c83`, soft-deleted without purging. Its only secrets were the
  two approved, unused MySQL passwords.
- `blog-production-backup-url` in the target vault, soft-deleted after verifying
  the successful import and absence of import jobs or app consumers.

Absence/soft-deletion was verified individually. At that orphan-cleanup checkpoint,
the original runtime resources and both canary secrets were verified retained.
The credential-bearing completed
import deployment template was removed from local migration artifacts; reusable
restore code, data backups and successful restore evidence remain.

Source runtime retirement completed at **05:09:51 UTC**:

- Deleted `thomasriley-ca`, `article-service`, `thomasriley-fantasy-football`
  and each `stage` slot.
- Verified `ASP-WRG3-2` absent after deletion of its last app.
- Deleted `tomriley-blog-db` only after fresh document/index backup checks.
- Deleted `thomasriley-fantasy-football-stage-deployer` and the unused custom role
  `384903d5-7cfc-52c7-8b38-56ff24fb49f3`; its old slot role assignment was already
  absent after slot deletion.
- Deleted the three unbound source certificates:
  `thomasriley.ca-thomasriley-ca`,
  `www.thomasriley.ca-thomasriley-ca`, and
  `article-service.thomasriley.ca-article-service`.

Every retirement was verified absent. **Only `thomasrileyca` storage remains in
WebResourceGroup2**. The group itself was not deleted. Unrelated resource inventory
was preserved, and private recovery configuration was retained outside Git.
After retirement, the full public content check, all three served managed
certificates and the fantasy private-database hosting check passed again; the
fantasy parent remains Disabled.

## Recovery, monitoring and cost

Blob versioning is enabled; deleted blobs and containers have thirty-day
soft-delete retention. Cosmos periodic backups run every four hours with twenty-four-hour
retention and Geo redundancy; PostgreSQL PITR retention is seven days.
Five operational metric alerts notify subscription Owner-role recipients.
A subscription-wide monthly budget of **USD 150** notifies
Owners at 80%/100% actual and 100% forecast. It is not a spending cap.

Configured USD retail estimates at 730 hours/month:

- Core plan/PostgreSQL/storage: **$72.665/month**.
- Target fixed costs including the Cosmos private endpoint and two private DNS
  zones: **$80.965/month**, plus approximately $0–$0.50 for metric alerts.
- Retiring the original source plan and orphaned DNS zone avoids approximately
  **$57.075/month** of fixed retail costs, plus former Cosmos usage. The retained
  legacy Blob account remains usage-billed.

Initial Cost Management queries returned HTTP 429. A later successful
subscription-wide month-to-date query verified USD billing and **$28.743508**
reported total: Ingrecog **$3.243520**, WebResourceGroup2 **$25.499988**. The target
group and NetworkWatcher did not appear in the returned rows; that does not prove
zero cost, because charges may lag. Historical-query access remained unavailable.
Taxes, final monthly usage and future budget compliance are **not verified**.
Do not describe the entire subscription as below 150 on fixed estimates alone.

The old public Blob account is retained because external references cannot be
ruled out. All unrelated Ingrecog/NetworkWatcher resources, backup artifacts and
operator credentials remain untouched. Unrelated README/Yahoo work was not
included in migration commits.
