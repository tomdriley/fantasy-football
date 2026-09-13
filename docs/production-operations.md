# West US 3 production operations

## Scope and source of truth

The retained resource group is **ff-westus3-pilot**, location **westus3**. “Pilot”
in resource names is historical, not permission to delete. Production tags are
`purpose=production environment=production retention=production owner=tomdriley`;
slots use `environment=stage`. There is no 48-hour cleanup policy.

`infra/azure/production/` imports the original PostgreSQL, fantasy app and vault
Bicep and translates the blog canary's Python-generated ARM into repo-owned
Bicep. It records the migrated configuration, not the unrelated stage bootstrap
in `infra/azure/main.bicep`. Existing numeric repository-ID OIDC bootstrap and
deployment workflows remain authoritative for CI identity.

| File | Resources / deployment prerequisites |
| --- | --- |
| `postgres.bicep` | `ff-w3-pilot-vnet`, its four subnets, private PostgreSQL DNS, `thomasriley-ff-w3-pg` (17, B1ms, 32 GB), `hosting_stage`; requires existing admin password |
| `vault.bicep` | `tr-ff-w3-kv-fcbc`, RBAC and original operator secret-officer assignment; requires tenant and original operator ID |
| `blog-data.bicep` | Single P0v3 `ff-w3-pilot-plan`, serverless Mongo `tr-blog-w3-fcbc`, private endpoint/DNS, `blog-site`, `trblogw3fcbc` with public `img`/`synthetic` and private `migration-backups` |
| `mongo-reader.bicep` | `blog-site.ArticleFindOnly` and `production_reader`, find-only on `blog-site.articles2`; requires preserved reader password |
| `blog-apps.bicep` | Blog/article parents and synthetic stage slots, digest images and secret-scoped reader grant |
| `fantasy-app.bicep` | Disabled, publicly inaccessible fantasy parent and read-only database stage; requires fantasy image digest |
| `tls.bicep` | Three managed certificates and SNI bindings; requires public DNS and existing verified hostname bindings |
| `monitoring.bicep` | Five alerts and subscription Owner-role action group |
| `budget.bicep` | Subscription-scoped budget, not resource-group scoped |

All three apps retain `thomasriley-{blog,article,fantasy}-w3-pilot` names.
Blog production calls article production; article reads `blog-site/articles2`
using the Key Vault `blog-production-mongo-reader` connection URI. It must never
use the Cosmos account key/admin seed identity. Stage is deliberately different:
`ARTICLE_DATA_MODE=synthetic`, blog-canary content and one synthetic article,
with stage blog calling stage article. Never slot-swap this synthetic configuration
into production. Fantasy's parent is intentionally stopped/Disabled, not an outage.

## Safe changes and reconstruction

These templates are **not a safe destructive full redeploy**, nor backups. There
is intentionally no “deploy everything” wrapper. Incremental deployments can still
replace app settings, subnet definitions, passwords, grants or certificates.
Do not use Complete mode, delete the group, or change names to remove “pilot”.

1. Authenticate with approved OIDC/operator identity; verify subscription and
   resource group. Use an installed Azure CLI and Bicep (`az bicep version`).
2. Export a private inventory: resource IDs, subnet prefixes/delegations, app/slot
   config, identities, sticky settings, role assignment UUIDs, DNS/host bindings,
   certificate metadata, database users/indexes, and running image digests.
   Settings exports can contain secrets: encrypted restricted storage only.
3. Preserve existing PostgreSQL administrator and Mongo reader passwords. Pass
   secure parameters through an ARM Key Vault reference parameter file, never
   shell literals, logged `--debug` output, committed files or Bicep defaults.
   The existing reader KV value is a **connection URI**, not a bare password:
   a trusted private runner must decode its password into a separate protected
   secret if using `mongo-reader.bicep`. Do not blindly pass the URI as password.
   Preserve/recreate `pg-reader-password` and `blog-production-mongo-reader`
   consistently with their database users. These templates do not populate secrets.
4. Obtain the current `blog-production-mongo-reader` secret-scoped Key Vault
   Secrets User assignment UUID with `az role assignment list --scope "$SECRET_ID"
   --all`, matching the production article principal. Supply its name as
   `readerRoleAssignmentName`; do not create a second assignment with another UUID.
   A recreated app gets a new principal: remove/recreate only its obsolete grant
   after review. Recheck all secret references after identity recreation.
5. Compile and run a separate what-if for **each** intended change. Example:

   ```sh
   az bicep build --file infra/azure/production/blog-data.bicep --stdout >/dev/null
   az deployment group what-if --resource-group ff-westus3-pilot \
     --template-file infra/azure/production/blog-data.bicep --mode Incremental
   # Only after reviewing the complete diff:
   az deployment group create --resource-group ff-westus3-pilot \
     --template-file infra/azure/production/blog-data.bicep --mode Incremental
   az deployment sub what-if --location westus3 \
     --template-file infra/azure/production/budget.bicep
   ```

   Supply required parameters with a protected `--parameters @...` file. What-if
   is not proof of data preservation. Stop for unexpected deletes/replacements,
   SKU changes, access changes, app-setting removals or identity/role churn.
6. For reconstruction: network/PostgreSQL and vault first, blog data/plan next,
   restore datasets and users/secrets, then apps and grants, verified DNS/hostnames,
   TLS, monitoring and budget. Do not route production traffic until verification.
   Preserve existing collection indexes rather than letting ARM overwrite them.
   PostgreSQL reader grants/data are restored using the database recovery steps.
   Inspect the original four-subnet VNet before applying: a PUT must not omit
   any subsequently added subnet. No temporary egress/seed jobs are deployed here.

Container references in `blog-apps.bicep` are observed immutable production and
synthetic stage digests. Retain those artifacts in a controlled registry/archive;
reconstruction fails if GHCR artifacts disappear. Validate new refs have
`@sha256:` plus 64 hexadecimal characters; never substitute `latest`. Promote
reviewed digests through existing workflows, verify health/content and keep the
previous digest for rollback. Record the deployed fantasy stage digest with
`az webapp config show --resource-group ff-westus3-pilot
--name thomasriley-fantasy-w3-pilot --slot stage --query linuxFxVersion`.
Do not restart its parent.

## Recovery artifacts and drill

Migration evidence (2026-09-13): both Mongo collections, `articles` and `articles2`,
had **12 documents each**, indexes were restored and reader writes were rejected.
Blob backup covered **80 objects, 100,950,531 bytes**, with SHA-256 checksums.
This is a historical drill, not an ongoing backup schedule or current row count.
The private `backup-manifest.json` records `mongoSha256`, collection counts,
per-blob container/name/hash/bytes/source ETag/metadata/tags/content settings and
`mongoBackupBlob`. `mongo-import-result.json` records exit status and verification
logs. Do not publish either file or database payloads in GitHub.

The migration Mongo artifact is canonical Extended JSON (documents and index
definitions), **not** a `mongodump` archive. Its private object is:
`migration-backups/2026-09-13/mongo-4fe4282077c816fec8e303bd65e8cd020ebcf48f2ba909b1fc0d74bc2dc96759.ejson`
in `trblogw3fcbc`. Obtain it using Azure Storage data-plane RBAC, for example
`az storage blob download --auth-mode login --account-name trblogw3fcbc
--container-name migration-backups --name "$OBJECT" --file "$BACKUP_DIR/mongo.ejson"`.
Use a restricted, encrypted persistent `$BACKUP_DIR` outside version control.
The original manifest, blob backup tree and restore verification report must be
obtained from the operator's private migration backup custody and copied into
durable restricted off-machine backup storage. Do not assume those local files
or their original session paths will survive, or that all 80 blobs are mirrored
in the private container. Verify availability/checksums before source deletion.

Portable procedure for subsequent backups/restores:

1. Run from an authorized private data-plane runner joined to the VNet, with
   private DNS resolving PostgreSQL and Mongo endpoints. A hosted CI runner
   with ARM access alone cannot reach these databases. Use TLS verification.
   Separate short-lived backup/restore operator credentials from application
   find-only credentials; never elevate the app reader to take/restore backups.
2. Quiesce writers or document snapshot consistency. For Mongo, use compatible
   database tools to `mongodump` both collections plus metadata/indexes into
   an encrypted archive, then hash it and record tool versions, timestamps,
   namespaces, counts and indexes in a new manifest. Alternatively explicitly
   export canonical EJSON and `listIndexes` per collection. Do not mix formats.
   Recover the migration EJSON using a BSON-aware EJSON parser, insert each
   collection's documents and recreate compatible recorded indexes separately;
   plain JSON parsing loses BSON types. Native dumps use `mongorestore` instead.
   The migration bundle has `database` and `collections`, with `documents` and
   `indexes` under each collection. Preserve `_id`; let Mongo create `_id_`,
   recreate other index keys/names and supported `unique`, `sparse`,
   `expireAfterSeconds` options. This original backup predates the asset-host
   rewrite: transform string values from `thomasrileyca.blob.core.windows.net`
   to `trblogw3fcbc.blob.core.windows.net` through EJSON serialization/
   deserialization so BSON types survive. Verify the original artifact hash
   **before** transformation and compare restored documents to the explicitly
   transformed expectation. Never overwrite unexpected destination documents.
3. For blobs, enumerate containers/blobs and record access level, metadata,
   content settings, tags, ETags and version IDs. Download bytes with Azure SDK
   or AzCopy and calculate/verify SHA-256 for every object. Save the manifest
   alongside payloads in encrypted restricted storage and a separate protected
   account/offline copy. Never make backups public or embed long-lived SAS URLs.
4. For PostgreSQL, take a consistent `pg_dump --format=custom` per database and
   a protected roles/grants inventory (`pg_dumpall --globals-only` where permitted).
   Password hashes and SQL dumps are secrets. Restore into an isolated private
   server with compatible extensions/version using `pg_restore`; recreate the
   least-privilege `ffopt_stage_reader` grants, and test TLS/read-only access.
5. Restore Mongo into isolated namespaces/account, then verify both counts,
   indexes and sampled BSON values. Restore blobs and metadata with public
   access disabled initially; verify all hashes/bytes before exposing intended
   image containers. Test production reader `find` on `articles2` succeeds and
   writes/other collections fail. Test fantasy reader cannot write. Run public
   HTTP/content/image and TLS checks before switching configuration/DNS.
6. Preserve the previous app digests, database target and DNS values for rollback.
   Retain source until verified backups and restore tests are durable. Rehearse
   quarterly and after material schema changes; record measured recovery time
   and recovery point, rather than promising unmeasured RTO/RPO.

Service recovery configuration:

* Blob versioning plus blob **and container** soft-delete: **30 days**. Use the
  saved version IDs to copy a previous version to current, or undelete within the
  retention window. Container soft-delete does not replace an independent backup.
* Cosmos periodic backup: every **4 hours**, **24-hour** retention, **Geo**
  redundancy. Periodic backup restoration requires Azure's supported restore/
  support process into a new account; do not assume continuous point-in-time
  restore is enabled. Rebuild private endpoint/DNS and reader access afterward.
* PostgreSQL: **7 days** backup retention, no geo-redundancy, no HA. Restore to a
  new private server at an available restore point, validate, then deliberately
  update the app target. Neither retention setting protects against every
  regional/account-loss scenario.

## Monitoring and TLS

Five severity-2 auto-mitigating metric alerts evaluate every **5 minutes** across
**15 minutes**: plan average CPU >80%, memory >85%, blog and article each total
HTTP 5xx >5, and PostgreSQL average storage >80%. Action group
`production-subscription-owners` uses the ARM **Owner** role receiver, not a
hard-coded email. Confirm eligible human subscription Owners have working email,
send an action-group test and record receipt; creating a rule proves no delivery.

On alert: correlate deployments and app/container logs first; check CPU/memory
and DB connections/storage, private DNS, reader KV reference status and upstream
article errors. Roll back a recent digest if justified. Do not automatically
scale up or start fantasy production. The five rules are not synthetic uptime,
certificate-expiry, backup-success or Cosmos-capacity alerts. Check public pages,
article responses, image hashes, TLS renewal and backup freshness separately.

Managed certificates **thomasriley-ca-managed**, **www-thomasriley-ca-managed**,
and **article-service-managed** are actually serving their respective domains
`thomasriley.ca`, `www.thomasriley.ca`, `article-service.thomasriley.ca`; observed
expiry **2027-03-13**. The bridge certificate and private key artifacts were
removed. There is no bridge PFX to recover or commit. Before `tls.bicep` on a
new app, establish domain ownership DNS and verified hostname bindings using
App Service custom-domain setup. Then issue managed certificates and bind SNI.
Preserve verification/routing DNS for renewal, verify the **served** certificate
from outside Azure, and investigate renewal well before expiry. Merely seeing
a managed-certificate resource is not evidence that it is bound or serving.

## Cost controls

Subscription budget `production-monthly-150` is monthly **150 in the subscription's
billing currency (not verified as USD)**, with Owner-role notifications at 80%
and 100% actual and 100% forecast. It covers other resource groups too, expires
2031-09-01 and is **not a spending cap** or automatic shutdown. On reconstruction,
choose a valid first-of-month start date; do not blindly reuse an expired period.

Private retail-rate audit estimated fixed target core **USD 72.665/month** and
target baseline **USD 80.965/month plus USD 0–0.50 metric alerts**, before variable
usage and other resource groups. Old source infrastructure adds roughly
**USD 57.075/month until deleted**. These are estimates, not billed actuals or
a guarantee of subscription cost below 150. Observed subscription actuals were
about **429 in an unconfirmed currency**. Check Cost Management currency, scope,
time window, accumulated charges and forecast; distinguish historical spend
from future run rate. Budget alerts can lag.

Review costs weekly and after migration/deletion: plan capacity, PostgreSQL disk/
backup, Cosmos requests/storage, private endpoint hours/data, blob versions and
transactions, egress, logs, alerts and unrelated groups. Do not delete a source
solely for savings: first prove traffic/dependencies moved, backups are durable
and recovery works. Record resource-specific deletion evidence and verify later
billing rather than claiming immediate zero charges.
