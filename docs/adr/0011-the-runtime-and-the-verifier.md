# ADR-0011: The runtime and the verifier do not know about each other

**Status:** accepted · **Date:** 2026-09-07

## Context

Verity's portability claim is that an Outcome Contract can check a run
produced by *someone else's* agent — a Playwright script, a Browser Use
agent, a bespoke loop. That claim is only true if verification has no
dependency on execution. An import edge from the verifier to an executor
would make the two ship as one thing, and the claim would quietly stop being
true long before anyone noticed.

The reverse edge is subtler and easier to justify away. The runtime *must*
consult a verifier: the whole product is that it stops before a consequential
write when the outcome is wrong. The obvious implementation is
`from verity_verifier import verify` inside the executor, and it would work.

It would also mean the executor could not be used by anyone who had their own
verification, the verifier could not be released or versioned separately, and
"runtime-agnostic" would be a marketing line rather than a property.

## Decision

**No import edge in either direction.**

The runtime declares the *shape* of the thing it consults, in
`verity_runtime.ports`: a `VerificationGate` protocol returning a
`GateResult` with a `GateVerdict`. It never names a verifier. The verdicts
mirror the verifier's by value, not by import — four strings that both sides
happen to agree on.

The CLI composes them. `verity_cli.run.ContractGate` holds a checked
contract, calls the real verifier, and translates the report into the
runtime's vocabulary. That module is the only place in the codebase where the
two appear together, and it is deliberately small enough to read in one
sitting.

Two `import-linter` contracts enforce this: `verity_runtime` and
`verity_verifier` may not import each other, and `verity_runtime` may not
import a model provider. Both were checked by deliberately breaking them and
watching them fail — a contract nobody has seen go red is a contract nobody
has tested.

## The gate's rules

**Only `PASS` opens it.** `FAIL` halts. `DRIFT` halts. `INCONCLUSIVE` halts.

That last one is the decision worth defending. "I could not check" is not
permission. A system that treats an unresolvable fact as a pass is lying
about what it knows, and the failure mode is silent: the day a connector
breaks is the day every write goes through unverified. This is not
hypothetical — it happened during development, when the sandbox died
mid-test, the connector failed, and the run halted instead of writing.

**A runtime with no gate configured is closed, not open.** The default is
`ClosedGate`, which refuses everything. The safe direction for a missing
decision is to stop, because the alternative is that the first misconfigured
deployment writes.

**The gate is consulted once, before the first consequential step** — not per
step. It answers a question about the outcome of the run. Asking repeatedly
would invite a caller to treat a later `PASS` as overturning an earlier
`FAIL`.

## Consequences

The executor is independently useful: a team with their own verification can
use it by supplying a gate, and the type system tells them exactly what to
supply.

Execution is deterministic and free. Ten replays of the same graph produce an
identical node sequence at zero cost, which is what makes a nightly canary
affordable and a diff between two runs meaningful. Ordering ties are broken
by edge priority then node id, because a runtime that picks a different valid
order each time cannot be replayed.

Reading and writing became separate protocols on separate classes rather than
two methods on one, so that no refactoring inside verification can reach a
write. `HttpJsonConnector` has no `write` attribute at all, and a test
asserts it.

The cost is one indirection and one adapter class. That is the whole price of
a boundary the product's main claim rests on.
