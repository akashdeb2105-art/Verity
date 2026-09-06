# ADR-0001: Verification is deterministic — zero model calls

**Status:** accepted · **Date:** 2026-09-06

## Context

Verity's job is to say whether an automation actually produced the correct
business state. That claim is only worth something if it is reproducible.

There is also an economic argument. Continuous verification — running every
contract against every workflow, nightly — is only a product if a run is
nearly free. Per-step-LLM browser agents cost roughly \$0.02–0.30 per ten-step
task; at that price, running verification continuously is not affordable, and
the product's main recurring value disappears.

## Decision

No model call may occur during verification. Assertions are typed expressions
evaluated deterministically. There is no LLM judge, advisory or gating, in V1.

Enforcement is layered rather than documented:

- No model provider SDK is a dependency of this repository.
- A contract's `model_calls` budget must be `0`, validated when it loads.
- A test asserts no provider module is imported by the verifier.
- A test asserts no raw socket is opened during a verification.

Models may be used later at *compile* time, when a demonstration is turned
into a draft contract. That output is `INFERRED` and a human reviews it.

## Alternatives considered

**LLM-as-judge for outcome correctness.** Standard in the agent-evaluation
category. Rejected: a judge can be argued with by the content it is judging,
its verdicts are not reproducible, and it makes every verification cost money.

**A model as a fallback when deterministic extraction fails.** Rejected for
verification time. A fact only a model can produce is `INFERRED`, and an
`INFERRED` fact cannot satisfy a `STRONG` assertion — so the honest outcome is
`INCONCLUSIVE`, which is also the case a human then corrects.

## Consequences

Some things Verity cannot check. Free-text quality — "was this reply good?" —
is outside what a deterministic contract can express, which is part of why the
reference workflow is invoice-to-PO rather than customer support.

In exchange: a verdict reproduces exactly, a replay costs `$0.00`, and the
determinism suite can assert that ten identical runs produce identical reports.
