# CLI reference

```
verity verify   evaluate one Outcome Contract
verity eval     evaluate a whole suite -- the CI entry point
verity lint     type-check contracts without reading anything
verity doctor   check the local environment
```

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | `PASS`, or a verdict excluded from `--fail-on` |
| `1` | `FAIL` |
| `2` | `DRIFT` |
| `3` | `INCONCLUSIVE` |
| `4` | Usage or contract error — nothing was verified |

## `verity verify`

```bash
# against live sources
verity verify --contract contracts/invoice_to_po.yaml --live \
  --input invoice_number=INV-4471 --input po_number=PO-2211

# against a trace produced by any runtime
verity verify --contract contracts/invoice_to_po.yaml --trace run.json

# with a golden trace to compare the environment against
verity verify --contract c.yaml --trace run.json --baseline golden.json
```

Inputs from `--input` override inputs carried in the trace.

## `verity eval`

```bash
verity eval --suite ./contracts \
  --junit results.xml \
  --github-annotations \
  --bundle ./evidence \
  --fail-on fail,drift,inconclusive
```

Contracts are discovered in sorted order, so a suite runs the same way every
time.

## Output options

| Flag | Produces |
| --- | --- |
| `--json PATH` | The full report, machine-readable |
| `--junit PATH` | JUnit XML — existing CI reporters render it natively |
| `--github-annotations` | Workflow commands anchored to the failing contract line |
| `--summary PATH` | A markdown table for a pull-request comment |
| `--bundle DIR` | An evidence bundle with a merkle manifest |

## Hermetic runs

```bash
verity verify --contract c.yaml --live --cassette t.json --cassette-mode record
verity verify --contract c.yaml --live --cassette t.json --cassette-mode replay
```

Replay needs no credentials and no network, which is what makes a suite usable
as a check on a pull request from a fork.

## Environment

| Variable | Default |
| --- | --- |
| `VERITY_SANDBOX_URL` | `http://127.0.0.1:8099` |
| `VERITY_EVIDENCE_DIR` | `.verity/evidence` |
| `VERITY_CASSETTE_MODE` | `off` |
| `NO_COLOR` / `FORCE_COLOR` | Colour is an enhancement; every state is also a word |
