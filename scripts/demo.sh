#!/usr/bin/env bash
# The flagship demonstration, end to end, against the real sandbox.
#
#   make demo
#
# Nothing here is staged: every number printed is read from the sandbox by the
# same verifier the CLI and CI use.
set -euo pipefail

SANDBOX="${VERITY_SANDBOX_URL:-http://127.0.0.1:8099}"
CONTRACT="examples/contracts/invoice_to_po.yaml"
ARGS=(--contract "$CONTRACT" --live --input invoice_number=INV-4471 --input po_number=PO-2211)

rule() { printf '\n\033[2m%s\033[0m\n' "----------------------------------------------------------------------"; }

rule
echo "  1. A clean run. The invoice matches its purchase order."
python3 -m verity_cli.main verify "${ARGS[@]}" || true

rule
echo "  2. The vendor changes their PDF template and a decimal moves."
echo "     Invoice now says 148,000.00. The purchase order still says 14,800.00."
curl -fsS -X POST "$SANDBOX/admin/perturb/amount_changed" >/dev/null
python3 -m verity_cli.main verify "${ARGS[@]}" --bundle .verity/bundles/demo || true

rule
echo "  3. Evidence bundle written to .verity/bundles/demo — report, cited"
echo "     evidence objects, and a manifest whose merkle root covers them."
ls -1 .verity/bundles/demo 2>/dev/null || true

curl -fsS -X POST "$SANDBOX/admin/unperturb/amount_changed" >/dev/null
rule
echo "  Sandbox restored."
