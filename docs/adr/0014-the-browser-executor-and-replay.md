# ADR-0014: The browser executor is a port, and a run is replayable

**Status:** accepted · **Date:** 2026-09-08

## Context

Through M2b the runtime's read steps were recorded no-ops. `_perform_read`
returned a description of the step and nothing happened: the executor could
plan a NAVIGATE/CLICK/TYPE/SELECT/EXTRACT sequence, and halt before a write,
but it never actually drove anything. M2c makes those steps real against the
sandbox UI, and makes a finished run replayable so two runs can be diffed.

Three forces shaped the design.

**The runtime must not import a browser.** The portability claim — a contract
can check a run somebody else's agent produced — rests on the runtime being
independent of any particular executor (ADR-0011). A `from playwright...`
inside `verity_runtime` would make the browser a hard dependency of every run,
the same way `from verity_verifier import verify` would have coupled the
runtime to the verifier.

**A browser observation is not evidence.** The verifier resolves facts through
its own read connectors. If an EXTRACT step's text became a fact the gate
consulted, then a page — which is data, not authority (`SECURITY.md`) — would
be deciding whether a write is allowed. The trace the browser produces is for
a person and for replay; it never reaches the verifier.

**Replay must be usable as a canary.** M3 will run a nightly replay of the
reference workflow. If replay reported a difference every time a page loaded a
millisecond slower, or every time an audit hash changed (which is every run,
by construction), it would be turned off within a week.

## Decision

### The browser sits behind a `BrowserDriver` protocol

`verity_runtime.ports` declares `BrowserDriver` — `perform` once per read step,
`finish` for the trace, `close` to release the browser — and names no
implementation. The default is `RecordedNoOpDriver`, which reproduces the
pre-M2c behaviour exactly: a run with no `--browser` still costs nothing, still
replays byte-for-byte, and never launches a browser it was not asked to.

The Playwright-backed session lives in a new package, `verity_browser`, which
depends only on `verity_schema` and (lazily) `playwright`. It does not import
`verity_runtime`, so there is no cycle. `verity_cli.run` adapts one to the
other, exactly as `verity_cli.run.ContractGate` adapts the verifier to the
runtime's `VerificationGate`. That adapter is the only place `verity_browser`
and `verity_runtime` appear together.

A ninth import-linter contract, `runtime-does-not-drive-a-browser`, forbids
`verity_runtime` from importing `verity_browser` or `playwright`. It was broken
once on purpose — a bare `import verity_browser` in `execute.py` — and watched
go red before it was trusted.

### One definition of the structural page hash

`dom_hash` is a comparison primitive that a `DRIFT` verdict and a replay diff
both rest on. It is defined once, in `verity_browser.domhash`, and
`verity_capture._inject` splices that one string into its page script rather
than keeping a second copy. Two copies would drift, and the drift would
surface as a structural difference nobody could explain. `tests/browser/`
pins the sensitivity in both directions: a renamed class, reflowed
whitespace, reordered attributes and changed text do not move it; an added,
removed, renamed or reordered element does. Its one blind spot — pure nesting
depth, because the token stream has no close markers — is stated in the module
and in a test, not tuned away.

**Why it is not SHA-256, when everything else in this repo that hashes for
comparison (`payload_digest`, the audit chain) uses it.** The teaching
recorder computes this value *inside a DOM event handler*, which must not
stall and must not go async — and a browser page has no synchronous SHA-256
(`crypto.subtle.digest` returns a promise). The alternative, sending the raw
skeleton string to Python to be hashed there, would add up to ~20 KB to every
recorded event, and a session has hundreds. So the hash is two independent
non-cryptographic passes over the skeleton string — djb2 and FNV-1a, each 32
bits, both exact in a browser without `BigInt` — concatenated to 64 bits
(`dom1:<8 hex><8 hex>`).

**The collision math.** `dom_hash` is only ever compared *pairwise*: replay
checks baseline step *N* against replay step *N*; the verifier's trace-mode
`_dom_hash_changes` checks `current[seq]` against `previous[seq]`. Nothing
deduplicates pages by hash or tests set membership, so there is no birthday
bound in play. The failure is: two skeletons that really are structurally
different collide in *both* lanes, so a replay reports `identical` when the
page changed. For two arbitrary distinct strings that is `2**-32` per lane and
— djb2 being linear and FNV-1a not — close to `2**-64` for both. At ~10 page
steps a night that is one expected false "identical" every ~5 x 10**15 years,
and only if a real structural change also lands on that exact collision. A
collision here can only ever *hide* a change, never invent one, and a hidden
change that matters to the path, the verdict, the outcome, an extracted value
or a payload is still caught by one of the other six difference kinds. If a
synchronous 64-bit-plus hash becomes available in the page, or the recorder
stops needing an in-page value, moving to truncated SHA-256 is a clean
follow-up.

### A failed observation before a write halts the run

If a driven read cannot observe what it claimed to — a NAVIGATE that times
out, an EXTRACT whose target is not on the page — and a consequential step is
still ahead, the run halts before it, `halted_by = "observation"`. This is the
`INCONCLUSIVE` rule one step earlier: "I could not look" is no more permission
to write than "I could not check". A read that fails with nothing consequential
left is allowed to end the run as `FAILED` on its own terms. A recorded no-op
never triggers this, because it was never a real observation.

### Replay re-runs, always dry, and diffs a narrow set of facts

A finished `verity run` / `verity dry-run` writes a run record to
`.verity/runs/<run_id>/` — five JSON files, no database (ADR-0007).
`verity replay <run_id>` re-executes the same graph with the same inputs,
**forced to `DRY_RUN`** so nothing is written whatever the recorded run did,
and grants itself approval for the plan's digests so the consequential step is
*reached* and its gate verdict can be compared rather than the two runs always
diverging at an approval prompt.

A difference is one of: `path` (the step sequence changed), `step-status`,
`structural` (a step's `dom_hash` changed — browser tier only), `extract` (a
step read a different value), `write` (a different payload digest), `verdict`,
`outcome`. Never a difference: wall-clock, `duration_ms`, `run_id`, audit
entry hashes and timestamps.

**A tier boundary is not drift.** A run made with `--browser` observed pages;
one made without recorded that it *would* have. Diffing those as if a missing
`dom_hash` were a structural change would be this product's own failure aimed
inward. So a cross-tier replay returns a single `tier` difference, is marked
not comparable, and is never reported identical.

## Alternatives considered

**Put the driver protocol's result type in `verity_schema`, and have
`verity_browser` implement `BrowserDriver` directly.** Rejected: it would give
`verity_browser` a reason to track the runtime's vocabulary, and the CLI
adapter is where the two vocabularies already meet for the verifier. One more
adapter is a smaller price than one more coupling.

**Make replay re-run live, with the original approvals.** Rejected: a canary
that writes a bill every night is not a canary. Replay is a comparison, and a
comparison does not have side effects.

**Truncated SHA-256 for `dom_hash`, for consistency with the rest of the
repo.** Rejected for now: the recorder needs a *synchronous* hash in the page,
and there isn't one for SHA-256. Sending the skeleton string out to be hashed
in Python bloats every capture event. The two-lane 64-bit hash gets the
collision headroom the pairwise comparison actually needs; the consistency
cost is one prefix (`dom1:` not `sha256:`) and a paragraph of justification.

**Add close markers to `dom_hash` so nesting depth is visible.** Deferred: it
changes the value for every page and would need `verity_capture`'s recorded
sessions to be regenerated. The blind spot is narrow (a re-nest that keeps
visit order identical) and is now documented and tested. A second drift signal
is M3's problem.

## Consequences

- New package `verity_browser`; import-linter contracts go from 8 to 9; typed
  files from 72 to 77.
- `Node` gains an optional `browser: BrowserAction | None`. Additive — every
  existing graph still validates. Like `write`, it is a claim about how to
  act, not evidence.
- `RunReport` gains `executor_tier` and `trace`. `verity run --json` now
  includes both, and per-step `outputs`.
- The sandbox gains one read-only surface, `/ui/invoices`, with a real filter
  form, so CLICK/TYPE/SELECT have something to act on without the browser ever
  touching a system of record — a test drives the whole form and asserts the
  sandbox state hash does not move.
- `verity run` writes a run record by default (`--no-record` opts out);
  `verity replay` is new.

## Still not done

- **CLICK/TYPE/SELECT are not restricted to read-only surfaces.** The
  write-gating guarantee covers the connector-mediated path; a Tier-2 `CLICK`
  that submits an HTML form is a browser-native POST that never reaches
  `WriteGuard`, policy, approval or the audit chain. Held closed today only by
  the one shipped browser graph targeting a GET-only page. Closing it needs a
  read-only-surface declaration plus a plan-time check, and must land before
  any further graph may drive a browser. Tracked in `SECURITY.md` and beside
  `CONSEQUENTIAL` in `plan.py`.
- **Retry.** A driven step is a single attempt honouring `timeout_ms`;
  `RetryPolicy` is not yet applied.
- **Cross-environment replay.** Replay re-runs against the same sandbox URL the
  record names; it cannot replay a staging run against production.
- **A second drift signal.** `dom_hash` alone cannot see a pure re-nest.
