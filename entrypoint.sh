#!/usr/bin/env bash
# Picks the mode of the image: `api` serves the producer endpoint, `listener` consumes the queue.
set -euo pipefail

SERVICE_CMD="${1:-api}"
shift || true

# gunicorn settings of the api mode
API_BIND="${API_BIND:-0.0.0.0:5000}"
API_WORKERS="${API_WORKERS:-2}"
API_TIMEOUT="${API_TIMEOUT:-60}"

case "$SERVICE_CMD" in
  api)
    exec gunicorn \
      --bind "$API_BIND" \
      --workers "$API_WORKERS" \
      --timeout "$API_TIMEOUT" \
      --access-logfile - \
      src.api.main:app "$@"
    ;;
  listener)
    exec python -m src.worker.main "$@"
    ;;
  *)
    echo "usage: $0 {api|listener}" >&2
    exit 64
    ;;
esac
