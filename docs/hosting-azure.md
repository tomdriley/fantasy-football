# Azure staging: hosting checkpoint 1

**Code preparation only. Nothing here authorizes a publication, GitHub setting
change, identity/RBAC change, Azure apply, deployment, or slot swap.** Obtain
explicit owner approval for each external gate below. The repository remains
private. The [foundation app](hosting.md) serves only harmless synthetic probes;
there is no database, authentication, real advisor, worker, or frontend migration.

## Fixed boundary

- Subscription: `b9ee5d35-c096-4772-8a56-0529054b4dcf`.
- Resource group: `WebResourceGroup2`; region: `eastus`.
- **Existing** shared plan: `ASP-WRG3-2` (reported P0v3), resource ID
  `/subscriptions/b9ee5d35-c096-4772-8a56-0529054b4dcf/resourceGroups/WebResourceGroup2/providers/Microsoft.Web/serverfarms/ASP-WRG3-2`.
  The templates reference it; they neither create nor resize it.
- One newly approved fantasy parent, disabled with public access denied and no
  image, and exactly one `stage` slot. Never supply a blog/existing app name.
  The empty parent and slot use the same Linux kind. Azure normalizes
  an image-less parent's kind to `app,linux` and rejects a mismatched
  `app,linux,container` slot. The slot's container is selected by `linuxFxVersion`.
- Stage uses HTTPS/TLS 1.2+, port 8080, the reviewed public GHCR digest, no registry
  credentials, no persistent App Service storage, and no runtime managed identity.
  FTP/basic publishing credentials are disabled; SCM ingress is denied.
- `FFOPT_HOSTING_ALLOWED_HOSTS` comes from the slot's actual `defaultHostName`,
  not an assumed `<app>-stage.azurewebsites.net` formula. Its settings are applied
  after slot creation; startup fails closed until that setting exists.
- No built-in sign-in is enabled at this checkpoint. Public synthetic responses
  are intentional, not proof of authentication. The parent stays unused.
- No VNet, database, Key Vault, production image, website routing, shared-plan
  change, or settings cloned from another app.

## Local and unprivileged checks

`hosting-ci.yml` runs only on relevant pull requests or manual dispatch. It has
`contents: read`, no OIDC, no package publication, and no Azure login. It runs the
existing hosting unittest selector, compiles both Bicep files, and builds/smokes
the non-root Linux/amd64 container.

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_hosting*.py'
bash -n infra/azure/check-image.sh infra/azure/deploy-stage.sh
# Local compilation only; install the Bicep component if it is missing.
az bicep install --version v0.47.16
az bicep build --file infra/azure/main.bicep --stdout > /dev/null
az bicep build --file infra/azure/bootstrap.bicep --stdout > /dev/null
```

Action pins were resolved from upstream release metadata during preparation:
checkout v7.0.1, setup-python v7.0.0, upload-artifact v7.0.1, and Azure/login v3.1.0. Hosted
`ubuntu-24.04` runners supply Docker and Azure CLI; no self-hosted runner is used.
Re-review upstream changes before updating their full commit pins.

## Gate 1: review and authorize the narrow package

Review the exact committed foundation source, Dockerfile, `.dockerignore`,
dependency locks, release workflows, and all image layers/config/history/labels.
Have the security specialist review the publication/deployment boundary.
Approval to publish this image is **not** approval to publish the private repo,
its history, `ffopt`, Yahoo material, real data, or the future hosted advisor.

Before authorizing the workflow, build a private local candidate of the approved
commit using the same command as CI. Inspect it locally, without uploading its
archive or inspection output anywhere:

```sh
RELEASE='<reviewed-full-lowercase-40-character-commit>'
IMAGE='ghcr.io/tomdriley/fantasy-football-hosting'
docker build --platform linux/amd64 --provenance=false --sbom=false \
  -f hosting/Dockerfile --build-arg "RELEASE=$RELEASE" \
  -t "$IMAGE:deployment-$RELEASE" .
bash infra/azure/check-image.sh "$IMAGE:deployment-$RELEASE" "$RELEASE"
docker image inspect "$IMAGE:deployment-$RELEASE"
docker history --no-trunc "$IMAGE:deployment-$RELEASE"
mkdir -p infra/azure/.image-review
docker save "$IMAGE:deployment-$RELEASE" -o infra/azure/.image-review/image.tar
tar -tf infra/azure/.image-review/image.tar
```

Review the manifest/config and **each referenced layer**, not just the final
filesystem (deleted files still disclose contents). Use archive tooling without
extracting untrusted paths onto the host. Confirm that `/app` contains only the
explicit hosting files and dependency manifests; review base/dependency layers
too. Inspect environment, build history, OCI labels and any auxiliary manifests
for private URLs, credentials, build arguments or unintended content. Delete
`infra/azure/.image-review` afterward; do not commit it. Do not print credentials
or paste unreviewed archive contents into issue comments.

The workflow deliberately disables provenance/SBOM attestations and uses no
source/repository label or remote cache. Its ordinary config/history/layers are
still published and require review. A prior local candidate is not necessarily
byte-identical to the workflow build; the workflow's push is the authoritative
artifact. Inspect that exact digest again **before making the package public**.
If the package is already public, its push is immediately public: authorize that
fact and every input/layer source beforehand, or stop and use a separately
reviewed private-candidate process. Automated smoke checks are not a disclosure
audit. Never broaden the context allowlist without renewed approval.

After approval, the owner may dispatch **Hosting foundation stage** from `main`
with `operation=build-publish`, `expected_release=<main's exact reviewed SHA>`,
and `confirmation=publish-reviewed-foundation`. The SHA must equal the selected
workflow commit; if `main` advanced, stop and review the new revision. This job
has package-write rights but no Azure OIDC. It builds once, tests that image,
publishes `ghcr.io/tomdriley/fantasy-football-hosting:deployment-<SHA>`, and records
the resulting `sha256:...` digest in the run summary. No deployment occurs.
The exact pushed image is also retained for three days as a private Actions
artifact named `hosting-image-review-<SHA>`, alongside its reference and release.
Download that artifact for the layer review before making the package public.
This avoids requiring a personal registry token to inspect the private candidate.
An existing readable release tag is refused; GHCR does not enforce immutable
tags, so retain and deploy the digest, not the tag.

GHCR package visibility is independent of repository visibility. A first push
can create a private package. Inspect the actual package/artifact, separately
approve its public visibility, and verify an **anonymous** pull of that digest.
If a package already exists, separately verify that its Actions access grants
this repository publication rights; do not compensate with a broad PAT.
Do not change repository visibility or insert a registry password into Azure.
The digest-deploy job performs an anonymous pull with an empty Docker config;
a private or wrong package fails before Azure login.

## Gate 2: preview resources, costs, and operator privileges

Obtain the website agent's post-upgrade blog/API/image health and rollback
handoff. Confirm the shared plan is still the expected East US Linux P0v3 plan,
and that `stage` capacity is available. Inspect current subscription spend and
projected shared CPU/memory usage against the **USD150/month total budget**.
No new compute plan is requested, but every running slot consumes shared plan
resources; do not assume zero operating impact or enable unbounded logging.
An unexpected tier/region/OS/capacity or other resource diff is a stop condition,
not authorization to resize the plan.

Confirm a globally available, new fantasy-only app name, the reviewed public
digest, and the exact resource scope. Bootstrap is a separate privileged
operator operation; the routine stage identity cannot provision resources,
assign roles, or apply Bicep. Do not give it subscription Contributor.

The following commands are a **runbook, not executed changes**. Replace the
placeholders only after approval; do not paste them blindly:

```sh
SUBSCRIPTION='b9ee5d35-c096-4772-8a56-0529054b4dcf'
GROUP='WebResourceGroup2'
APP='<approved-new-fantasy-parent>'
DIGEST='sha256:<approved-64-lowercase-hex>'

az deployment group what-if --subscription "$SUBSCRIPTION" \
  --resource-group "$GROUP" --name hosting-foundation \
  --mode Incremental --template-file infra/azure/main.bicep \
  --parameters appName="$APP" imageDigest="$DIGEST"
```

Inspect every change: only the new parent/one stage slot and their scoped
settings/publishing policies are expected. `existing` plan references are not a
plan deployment. `what-if` may report runtime-reference uncertainty for the
generated hostname; inspect the resulting slot setting after apply. Bicep
compilation alone is not a cloud preview. Never use Complete mode.

Only after approving the preview and cost/resource/identity boundaries:

```sh
az deployment group create --subscription "$SUBSCRIPTION" \
  --resource-group "$GROUP" --name hosting-foundation \
  --mode Incremental --template-file infra/azure/main.bicep \
  --parameters appName="$APP" imageDigest="$DIGEST" \
  --query properties.outputs --output json

az deployment group what-if --subscription "$SUBSCRIPTION" \
  --resource-group "$GROUP" --name hosting-stage-identity \
  --mode Incremental --template-file infra/azure/bootstrap.bicep \
  --parameters appName="$APP"
# Separate identity/RBAC approval, then:
az deployment group create --subscription "$SUBSCRIPTION" \
  --resource-group "$GROUP" --name hosting-stage-identity \
  --mode Incremental --template-file infra/azure/bootstrap.bicep \
  --parameters appName="$APP" --query properties.outputs --output json
```

The first apply already exposes the harmless stage image, so its approval must
include that exposure. Outputs contain only resource IDs/origin and identity
IDs, not authentication secrets. Never use app-setting or publishing-profile
dumps. For subsequent IaC runs, pass the **currently approved** digest; stale
parameters can unintentionally restore an earlier image. Routine releases
update only stage's image configuration, not the whole template.

### OIDC and exact scope

The optional bootstrap creates one user-assigned deployment identity, one
GitHub federation, a custom role definition, and a role assignment **at**
`.../sites/<approved-app>/slots/stage`. Its only management actions are:

- `Microsoft.Web/sites/slots/read`
- `Microsoft.Web/sites/slots/config/read`
- `Microsoft.Web/sites/slots/config/write`
- `Microsoft.Web/sites/slots/restart/action`

Config-write cannot be restricted to just `linuxFxVersion` by Azure RBAC: the
identity can modify other configuration within this slot. It has no parent,
plan, swap, publishing-profile/list-secrets, resource creation, or RBAC rights.
The role's resource-group `assignableScopes` specifies where it *may be assigned*;
it is not a resource-group permission grant.

The script uses direct slot REST paths to avoid action/CLI ancestor discovery.
These exact rights and Azure/login's subscription discovery have **not been
proved against the live target**. If Azure requires an ancestor read, stop,
identify the denied action, and obtain approval for only that read on the exact
parent (or other necessary ancestor). Do not fall back to parent/RG/subscription
Contributor or broad Reader. Record the verified effective rights before
declaring checkpoint 1 accepted.

With separate approval, set these repository **variables**, not credentials:
`FFOPT_AZURE_APP_NAME`, `FFOPT_AZURE_STAGE_CLIENT_ID`, `FFOPT_AZURE_TENANT_ID`.
Use the bootstrap's client/tenant ID outputs. No publish profile, client secret,
or GHCR PAT is required.

The federation subject is
`repo:tomdriley/fantasy-football:ref:refs/heads/main`, audience
`api://AzureADTokenExchange`. It trusts eligible OIDC jobs on `main`, not just
this filename. Restrict write access to trusted maintainers and review workflow
changes. The workflow also requires both initiating and rerunning actors to be
`tomdriley`, the exact repository, and `refs/heads/main`.

No GitHub Environment or paid private-repository required-reviewer feature is
assumed. Owner dispatch of a reviewed `main` revision, with an explicit operation
confirmation, is the initial mechanism. This mechanism does **not** supply the
external approvals. If environment protection is later available and approved,
change the workflow and federation subject together and verify enforcement;
merely naming an environment is not an approval control.

## Gate 3: deploy digest, verify, and rehearse A/B/A

After deployment approval, dispatch the same workflow from approved `main`:

- `operation=digest-deploy`
- `target_app=<the exact FFOPT_AZURE_APP_NAME value>`
- `expected_release=<reviewed full SHA associated with the chosen digest>`
- `image_digest=sha256:<approved digest>`
- `confirmation=deploy-reviewed-digest`

Deployment never builds/publishes. It anonymously pulls the fixed package,
checks the baked revision/non-root architecture and local marker, then uses
Azure OIDC to update **`<target_app>/slots/stage` only**. It reads the actual Azure
hostname, records the preceding digest, PATCHes only `linuxFxVersion`, restarts
stage, and verifies both Azure's configured digest and the HTTPS release marker.
There is no production job, swap, automatic main release, or automatic rollback.
Manual operations are serialized, including reruns and rollback.

Keep a durable release ledger outside public artifacts: approved source SHA,
digest, run link, target/actual hostname, sanitized config differences, logs,
effective permissions, and probe results. Do not delete referenced A/B package
versions. The full source SHA is an image marker, not cryptographic proof of
provenance; review the association before accepting an input pair.

1. Publish/approve A, deploy its digest, and verify A's exact status/page.
2. Publish/approve a distinct foundation commit B, deploy its digest, verify B.
3. Dispatch **digest-deploy** with A's original digest **and A's SHA**. Do not
   rebuild A or use a mutable tag. Verify the visible release returns to A.
4. Inspect sanitized startup logs using the operator path (SCM remains denied);
   verify the parent stays disabled, HTTPS/health/readiness work, and no auth,
   real data or domain routes have appeared.
5. Recheck the existing blog/API/image routes and shared CPU/memory headroom
   with their owner. Save evidence before accepting checkpoint 1.

A failed identity, image pull, Azure update, or marker check fails the job. It
does not silently switch images or broaden permissions. Inspect the failure and
explicitly dispatch an approved rollback pair. For local smoke only, HTTP is
permitted on loopback; hosted verification always uses HTTPS without redirects.
Once later checkpoints introduce protected data/authentication, these old
unauthenticated images cease to be eligible rollback targets.
