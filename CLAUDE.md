# Verity — working context

Read this before doing anything. It is the standing brief for every session.

## What Verity is

An open-source, developer-first **verification layer** for AI-driven and
browser-driven workflows. The one-line thesis:

> Your AI agent says DONE. Verity checks whether it actually did.

The product is not an agent. It is the thing that refuses to believe one.
Every design decision follows from that: if Verity ever reports success it did
not establish, the product has no reason to exist.

## Build order — do not jump ahead

| | Milestone | State |
|---|---|---|
| M0 | schema, contracts, expression language, evidence, connectors, sandbox | **done** |
| M1 | verifier, compiler, teach/capture, AI boundary, CLI | **done** |
| M2a | runtime: plan, execute, gate, halt before a write | **done** |
| M2b | risk policy, approval binding, kill switch, audit chain, injection suite | **done** |
| **M2c** | **Tier-2 browser executor + run replay** | **next** |
| M3 | nightly canary, scheduler, notifications, benchmark harness, OTel | after |
| M4 | Studio — three-panel app, contract editor, run timeline, evidence viewer, approval bar, takeover, correction delta | after |
| M5 | marketing website (11-scene storyboard in `docs/web/CREATIVE_DIRECTION.md`) | last |

**The website is last, and that is deliberate.** Its own creative direction
document (`docs/web/CREATIVE_DIRECTION.md`) says building it early is how
the engine ends up fake. Scenes are only
honest once the milestone behind them exists. Do not start it early, and say so
if asked to.

## Non-negotiable rules

1. **Never `git push`.** Never commit without being asked. The user runs git
   themselves from PowerShell. You may stage nothing and push nothing.
2. **Never fake anything.** No mock implementations presented as real, no
   placeholder UI described as complete, no invented test results, no sample
   output passed off as a real run. If something is not built, write "not yet
   built" and list it.
3. **Never hardcode a secret.** Keys go in `.env`, which is gitignored and
   stays that way. `.env.example` names variables with no values.
4. **Synthetic demo data only.**
5. **Stop at each milestone** and wait for approval before the next.
6. Every commit message ends with:
   ```
   Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
   Claude-Session: https://claude.ai/code/session_01NaW6Ssisu4tLa1ixKZYfxn
   ```

## Definition of done for any change

All four must pass before you say a thing is finished:

```bash
ruff check .
mypy packages apps
lint-imports
pytest -q
```

Current baseline: **552 tests, 8/8 import contracts, 72 files typed clean.**
A change that lowers any of those numbers is not done.

Code alone is not done either. Anything that changes behaviour also needs:
an **ADR** in `docs/adr/` when it is a decision, updated `docs/`, a
`CHANGELOG.md` entry, and a row in the `SECURITY.md` invariant table if it is
a safety property — each citing the test that enforces it. A test named in
`SECURITY.md` that does not exist fails the suite by design.

## Architecture invariants — these are enforced, not aspirational

- **`verifier ⊥ runtime`.** No import edge in either direction. The runtime
  declares a `VerificationGate` protocol (`verity_runtime/ports.py`); the CLI
  (`verity_cli/run.py`) is the only place the two meet. Two `import-linter`
  contracts enforce it. See ADR-0011.
- **Verification is deterministic. Zero model calls.** A model is used only by
  `teach --ai` / `inspect --ai` to *suggest*. Deterministic code decides. An
  import rule stops the verifier and the runtime from reaching a provider.
- **Four verdicts**: PASS, FAIL, DRIFT, INCONCLUSIVE. Only PASS opens the gate.
  `INCONCLUSIVE` halts — "I could not check" is not permission. See ADR-0004.
- **A missing decision is not permission.** Defaults refuse: `ClosedGate`,
  `NoApprovals`, `WriteMode.DRY_RUN`, and a policy level that is unlisted is
  forbidden rather than allowed.
- **A declaration is not evidence.** A graph's `risk` field is a claim; risk is
  assessed from the verb, resource and payload, and the effective level is the
  higher of the two. See ADR-0012.
- **Approval binds `(run_id, node_id, payload_digest)`.** Approving $14,800
  cannot authorise $148,000. The match happens in the runtime, never in a
  store. See ADR-0012.
- **Reading and writing are separate connector protocols**, so verification
  cannot reach a write.

## Writing style for this codebase

Comments and docstrings explain **why**, not what. They name the failure the
code prevents. Test names are sentences about behaviour
(`test_an_approved_amount_cannot_be_replayed_at_a_different_amount`), not
`test_approval_2`. Commit messages explain the reasoning and the failure mode,
in prose, not bullet lists of files touched.

**State limits as loudly as guarantees.** `docs/runtime.md` has a "Not yet
built" section; `audit.py` documents that truncation is undetectable; the
injection suite names four payloads that produce a false positive rather than
tuning them away. Keep doing this. An invariant table that lists only wins is
marketing.

## Known gaps — real, and deliberately visible

- Approvals are **not signed**. The binding proves approval and payload agree,
  not who authorised it. Needs identity — M4.
- The audit head has **no external anchor**, so truncating the log at the end
  is undetectable. Stated in `audit.py`, `SECURITY.md`, and a test.
- **Extraction fragility**: four of fifteen injection payloads shift the
  invoice PDF's layout enough that anchored vendor extraction misses and
  reports FAIL on an unchanged vendor. Safe but wrong. Named and pinned in
  `tests/security/test_injection_suite.py`.
- Read steps in the runtime are **recorded no-ops**. Making them real is M2c.

## Environment notes

- Windows, PowerShell, venv at `.venv`. Activate before anything:
  `.\.venv\Scripts\Activate.ps1`
- Adding a new package under `packages/` needs `pip install -e ".[dev,sandbox,extract]"`
  **and** an entry in `pyproject.toml` under both the hatch `packages` list and
  pytest's `pythonpath`. Forgetting the second is a bug that hides behind an
  editable install and only appears on a bare clone.
- Sandbox: `make sandbox` (port 8099). `verity doctor` checks the environment;
  `verity doctor --probe-ai` makes one real model call.

## Where to look

`docs/adr/README.md` indexes 13 ADRs and is the fastest way to load the
reasoning. `docs/runtime.md` covers running and halting. `SECURITY.md` maps
every invariant to its test. `CHANGELOG.md` has the per-milestone history.
