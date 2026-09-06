#!/usr/bin/env bash
# Run a command with the AP sandbox up, then shut it down again.
#
#   scripts/with-sandbox.sh verity verify --contract examples/contracts/invoice_to_po.yaml --live
#
# Used by the Makefile and by CI so no test depends on a server someone
# remembered to start by hand.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${VERITY_SANDBOX_PORT:-8099}"
HOST="${VERITY_SANDBOX_HOST:-127.0.0.1}"
LOG="${VERITY_SANDBOX_LOG:-$(mktemp -t verity-sandbox.XXXXXX.log)}"

export PYTHONPATH="$REPO_ROOT/packages/schema:$REPO_ROOT/packages/evidence:$REPO_ROOT/packages/connectors:$REPO_ROOT/packages/extract:$REPO_ROOT/packages/verifier:$REPO_ROOT/packages/cli:$REPO_ROOT/apps/sandbox${PYTHONPATH:+:$PYTHONPATH}"
export VERITY_SANDBOX_URL="http://$HOST:$PORT"

python3 -m uvicorn verity_sandbox.app:app --host "$HOST" --port "$PORT" --log-level warning >"$LOG" 2>&1 &
SANDBOX_PID=$!

cleanup() {
  kill "$SANDBOX_PID" 2>/dev/null || true
  wait "$SANDBOX_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 60); do
  if curl -fsS "$VERITY_SANDBOX_URL/api/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SANDBOX_PID" 2>/dev/null; then
    echo "sandbox failed to start; log follows:" >&2
    cat "$LOG" >&2
    exit 1
  fi
  sleep 0.25
done

if ! curl -fsS "$VERITY_SANDBOX_URL/api/health" >/dev/null 2>&1; then
  echo "sandbox did not become healthy within 15s; log follows:" >&2
  cat "$LOG" >&2
  exit 1
fi

"$@"
