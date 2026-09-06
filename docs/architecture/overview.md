# Architecture

## The shape of the system

```
  contract.yaml ──┐
                  │      ┌──────────────────────────────────────┐
  trace.json ─────┼─────▶│              VERIFIER                │──▶ VerificationReport
  (any runtime)   │      │  (pure · deterministic · no models)  │    ├ verdict
                  │      │                                      │    ├ per-assertion results
  live connectors ┘      │  1. parse + typecheck                │    ├ evidence bundle
                         │  2. bind inputs                      │    ├ first divergence
                         │  3. resolve facts ──▶ evidence store │    └ exit code
                         │  4. evaluate expressions             │
                         │  5. localize divergence              │
                         │  6. emit report                      │
                         └──────────────────────────────────────┘
```

## The boundary that matters

`verity_verifier` imports the schema, the evidence store, the connectors and
the document extractor — and nothing that executes a workflow. There is no
import path from the verifier to a browser, an agent or a model client.

This is enforced by `import-linter` (`.importlinter`, run by `make imports`
and in CI), and the contract names `verity_runtime`, `verity_capture` and
`verity_compiler` even though none of them exists yet. Declaring the boundary
before the packages are written is the point: it cannot erode one convenient
import at a time.

```
verity_cli
    ↓
verity_verifier
    ↓
verity_connectors | verity_extract | verity_evidence
    ↓
verity_schema          (a leaf: imports nothing else in the project)

verity_sandbox         (a fixture: imports no Verity logic at all)
```

## Packages

| Package | Responsibility |
| --- | --- |
| `verity_schema` | The three artifacts, the trace, the report. The format others adopt. |
| `verity_evidence` | Content-addressed storage, redaction, merkle-rooted bundles. |
| `verity_connectors` | Read-only access to systems of record, plus cassette record/replay. |
| `verity_extract` | Deterministic document extraction with provenance. |
| `verity_verifier` | Parsing, type checking, fact resolution, evaluation, localization. |
| `verity_cli` | The `verity` command. No business logic. |
| `verity_sandbox` | A deterministic accounts-payable fixture. |

One installable distribution, seven top-level modules. Distribution packaging
is not an architectural boundary; the import graph is. See
[ADR-0006](../adr/0006-one-distribution-many-modules.md).

## Determinism

A verification is a pure function of the facts it resolves. No model call, no
clock read that affects a result, no dynamic dispatch onto arbitrary objects.
The determinism suite runs ten identical verifications and asserts the reports
are identical after removing wall-clock timings.

This is what makes the rest possible: regression tests that mean something, a
verdict you can reproduce three weeks later, and canary runs that cost `$0.00`
per execution.

## What is not built yet

| Component | Milestone | Where it goes |
| --- | --- | --- |
| Capture (`verity_capture`) | M1 | Controlled Chromium session, CDP, redaction at source |
| Compiler (`verity_compiler`) | M1 | Raw events → WorkGraph → proposed Outcome Contract |
| Trace adapters (`verity_adapters`) | M1 | Playwright · Browser Use · Stagehand · Skyvern → `verity-trace/v1` |
| Runtime (`verity_runtime`) | M2 | Tiered execution, risk policy, approval, the single side-effect path |
| Canary + scheduler | M3 | Postgres job table, notifications |
| Studio (`apps/web`) | M4 | Five destinations, contract-first Studio |

M0 has no database. The verifier is pure, the evidence store is filesystem
content-addressed, and the sandbox is a fixture that owns its own state.
Postgres arrives with runs, audit and jobs — not before.
