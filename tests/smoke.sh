#!/usr/bin/env bash
# Starts the built image in both modes and checks it answers. No broker is needed: the api serves
# its health check and contracts without one, and the listener only has to start.
#   tests/smoke.sh ghcr.io/owner/repo:tag
set -euo pipefail

SMOKE_IMAGE_TAG="${1:-${SMOKE_IMAGE_TAG:-event-service:dev}}"
SMOKE_PORT="${SMOKE_PORT:-5099}"
SMOKE_WAIT_SECONDS="${SMOKE_WAIT_SECONDS:-30}"
SMOKE_CONTAINER="event-service-smoke-$$"

say() { printf '==> %s\n' "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

cleanup() { docker rm -f "$SMOKE_CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

####
# api
####
say "Starting the api from $SMOKE_IMAGE_TAG"
docker run -d --rm \
  --name "$SMOKE_CONTAINER" \
  -p "$SMOKE_PORT:5000" \
  -e RABBITMQ_HOST=nowhere \
  "$SMOKE_IMAGE_TAG" api >/dev/null

# Wait for gunicorn
for _ in $(seq 1 "$SMOKE_WAIT_SECONDS"); do
  curl -sf "http://localhost:$SMOKE_PORT/" >/dev/null && break
  sleep 1
done

grep -q '"ok"' <<< "$(curl -sf "http://localhost:$SMOKE_PORT/")" || die "health check did not answer"
say "Health check ok"

grep -q '"openapi"' <<< "$(curl -sf "http://localhost:$SMOKE_PORT/openapi.json")" || die "openapi.json not served"
grep -q '"asyncapi"' <<< "$(curl -sf "http://localhost:$SMOKE_PORT/asyncapi.json")" || die "asyncapi.json not served"
grep -q '{' <<< "$(curl -sf "http://localhost:$SMOKE_PORT/contracts")" || die "contracts not served"
say "Contracts served"

# A malformed envelope is refused before the broker is involved
SMOKE_STATUS=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://localhost:$SMOKE_PORT/action" \
  -H 'Content-Type: application/json' \
  -d '{"data": {}}')
[ "$SMOKE_STATUS" = "400" ] || die "malformed envelope answered $SMOKE_STATUS, expected 400"
say "Malformed envelope refused"

docker rm -f "$SMOKE_CONTAINER" >/dev/null

####
# listener
####
say "Starting the listener"
docker run -d --rm \
  --name "$SMOKE_CONTAINER" \
  -e RABBITMQ_HOST=nowhere \
  "$SMOKE_IMAGE_TAG" listener >/dev/null

sleep 3

grep -q 'listener starting' <<< "$(docker logs "$SMOKE_CONTAINER" 2>&1)" || die "listener did not start"
say "Listener started"

docker rm -f "$SMOKE_CONTAINER" >/dev/null

####
# entrypoint and exporter
####
set +e
docker run --rm "$SMOKE_IMAGE_TAG" bogus >/dev/null 2>&1
SMOKE_EXIT_CODE=$?
set -e

[ "$SMOKE_EXIT_CODE" = "64" ] || die "unknown mode exited $SMOKE_EXIT_CODE, expected 64"
say "Unknown mode refused"

# capture first: grep -q would close the pipe on the first match and break docker's output
SMOKE_EXPORT_OUTPUT="$(docker run --rm --entrypoint python "$SMOKE_IMAGE_TAG" -m src.contracts /tmp/contracts)" \
  || die "contract export failed"

grep -q 'openapi.json' <<< "$SMOKE_EXPORT_OUTPUT" || die "contract export did not write openapi.json"
say "Contracts export"

say "PASS"
