#!/usr/bin/env bash
set -euo pipefail

image=${1:?image reference required}
release=${2:?expected release required}
phase=${3-deployment}
fail() { printf '%s\n' "$1" >&2; exit 1; }
[[ "$phase" == deployment || "$phase" == database-readonly || "$phase" == authentication-only ]] \
  || fail 'Invalid expected hosting phase.'
[[ "$release" =~ ^[0-9a-f]{40}$ ]] || fail 'Expected release must be a lowercase full commit SHA.'
[[ "$(docker image inspect "$image" --format '{{.Config.User}}')" == '10001:10001' ]] \
  || fail 'Image must run as the expected non-root user.'
[[ "$(docker image inspect "$image" --format '{{.Architecture}}')" == 'amd64' ]] \
  || fail 'Image must target amd64.'
[[ "$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" == "$release" ]] \
  || fail 'Image revision label does not match the expected release.'
if [[ "$phase" == authentication-only ]]; then
  [[ "$(docker image inspect "$image" --format '{{index .Config.Labels "io.ffopt.hosting.authentication"}}')" == google-allowlist-v1 ]] \
    || fail 'Legacy images cannot be deployed to the authentication checkpoint.'
fi

container="hosting-probe-${GITHUB_RUN_ID:-local}-$$"
container_id=''
cleanup() {
  if [[ -n "$container_id" ]]; then
    docker rm --force "$container_id" > /dev/null \
      || printf 'Could not remove test container %s.\n' "$container_id" >&2
  fi
}
trap cleanup EXIT
container_id=$(docker run --detach --name "$container" \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --publish 127.0.0.1::8080 \
  --env FFOPT_HOSTING_ENVIRONMENT=local "$image")
address=$(docker port "$container_id" 8080/tcp)
[[ "$address" =~ ^127\.0\.0\.1:[0-9]+$ ]] || fail 'Test container is not bound to loopback.'
for attempt in {1..30}; do
  if timeout 20s python3 scripts/check_hosted_app.py "http://$address" \
    --allow-http --expected-environment local --expected-release "$release"; then
    exit 0
  fi
  sleep 2
done
docker logs --tail 40 "$container_id"
echo 'Container smoke check failed.' >&2
exit 1
