#!/usr/bin/env bash
set -euo pipefail

app=${1:?exact approved parent app required}
release=${2:?expected release required}
digest=${3:?approved image digest required}
phase=${4-deployment}
fail() { printf '%s\n' "$1" >&2; exit 1; }
[[ "$app" =~ ^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$ ]] || fail 'Invalid fantasy app name.'
[[ "$release" =~ ^[0-9a-f]{40}$ ]] || fail 'Expected release must be a lowercase full commit SHA.'
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'An immutable sha256 image digest is required.'
[[ "$phase" == deployment || "$phase" == database-readonly ]] || fail 'Invalid expected hosting phase.'
phase_args=()
if [[ "$phase" == database-readonly ]]; then
  phase_args=(--expected-phase "$phase")
fi

subscription=b9ee5d35-c096-4772-8a56-0529054b4dcf
slot="/subscriptions/$subscription/resourceGroups/WebResourceGroup2/providers/Microsoft.Web/sites/$app/slots/stage"
api='api-version=2024-11-01'
image="ghcr.io/tomdriley/fantasy-football-hosting@$digest"

# Direct slot REST calls avoid parent discovery and publishing-profile access.
host=$(az rest --method get --url "$slot?$api" --query properties.defaultHostName --output tsv)
[[ "$host" =~ ^[a-z0-9-]+(\.[a-z0-9-]+)*\.azurewebsites\.net$ ]] \
  || fail 'Azure did not return an expected App Service hostname.'
previous=$(az rest --method get --url "$slot/config/web?$api" --query properties.linuxFxVersion --output tsv)
if [[ "$previous" =~ ^DOCKER\|ghcr.io/tomdriley/fantasy-football-hosting@sha256:[0-9a-f]{64}$ ]]; then
  printf 'Previous stage image: %s\n' "${previous#DOCKER|}"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf 'Previous stage image: `%s`\n\n' "${previous#DOCKER|}" >> "$GITHUB_STEP_SUMMARY"
  fi
fi

az rest --method patch --url "$slot/config/web?$api" \
  --body "{\"properties\":{\"linuxFxVersion\":\"DOCKER|$image\"}}" --output none
az rest --method post --url "$slot/restart?$api" --output none
configured=$(az rest --method get --url "$slot/config/web?$api" --query properties.linuxFxVersion --output tsv)
[[ "$configured" == "DOCKER|$image" ]] || fail 'Stage image configuration did not match the requested digest.'

for attempt in {1..24}; do
  if timeout 20s python3 scripts/check_hosted_app.py "https://$host" \
    --expected-environment stage --expected-release "$release" "${phase_args[@]}"; then
    configured=$(az rest --method get --url "$slot/config/web?$api" --query properties.linuxFxVersion --output tsv)
    [[ "$configured" == "DOCKER|$image" ]] || fail 'Stage image changed during verification.'
    printf 'Verified stage release %s at https://%s/fantasy-football/\n' "$release" "$host"
    if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
      printf 'Verified stage: `%s`\n\nRelease: `%s`\n\nImage: `%s`\n' \
        "$app/slots/stage" "$release" "$image" >> "$GITHUB_STEP_SUMMARY"
    fi
    exit 0
  fi
  sleep 10
done
echo 'Stage verification failed. Inspect sanitized logs and explicitly restore an approved digest; no automatic rollback or swap.' >&2
exit 1
