# Verdicts and divergence

## Four verdicts

| Verdict | Meaning | Exit | CI default |
| --- | --- | --- | --- |
| `PASS` | Every blocking assertion holds; every fact resolved | `0` | green |
| `FAIL` | At least one blocking assertion is false | `1` | red |
| `DRIFT` | The outcome still holds, but the environment changed | `2` | warning |
| `INCONCLUSIVE` | A required fact could not be resolved | `3` | red |

`--fail-on` decides which verdicts break a build. The default is
`fail,inconclusive`, so drift warns rather than blocking; a team that wants
drift to break the build passes `--fail-on fail,drift,inconclusive`.

## Why `INCONCLUSIVE` is separate

This is the most important design decision in the verifier.

A system that folds "I could not check" into "it passed" is lying. One that
folds it into "it failed" trains its users to ignore alerts, which is how a
monitoring system dies. Keeping them apart is what makes a false-halt rate
manageable and what makes a green result mean something.

`INCONCLUSIVE` arises when a connector is unreachable, a document cannot be
read, a read returns an unexpected number of records, or an assertion could not
be evaluated because a fact it needs is missing. In every case the report names
the fact and the reason.

**Verity never converts `INCONCLUSIVE` into `PASS`.**

## How a verdict is decided

In this order, deliberately:

1. Any blocking assertion false → `FAIL`.
2. Any fact unresolved → `INCONCLUSIVE`.
3. Any assertion not evaluated → `INCONCLUSIVE`.
4. Any environment change, or any non-blocking assertion false → `DRIFT`.
5. Otherwise → `PASS`.

An assertion that could not be evaluated is rendered as `----` / *not
evaluated*. Never as passing. Never as failing.

## Two divergences, not one

```
first_assertion_failure    where the business outcome first stopped being true
first_environment_change   where the world first stopped matching what it was
```

They are frequently different. The page structure can change at step 4 while
the assertion fails at step 7; reporting only the second sends a person to
debug the wrong step.

Environment changes are detected from three sources, all deterministic:

- **Document template drift** — a field found under a different label anchor
  than the contract expects. The value may still be correct; the template moved.
- **DOM structure** — normalised structural page hashes compared step by step
  against a golden trace.
- **UI surface** — a `WEAK` assertion no longer holding while every `STRONG`
  one does.

## Reading a report

```
  FAIL  invoice_to_po v0.1.0
  runtime said: success   verifier says: FAIL  <- disagreement
```

The second line is the product in six words. `status_reported` is captured
from the trace and **never** used in evaluation — it exists so the report can
contradict it.

```
  FAIL  amount_match             [STRONG]
          within(doc.total, po.total, tolerance = 0.01)
          expected  14,800.00 (+/- 0.01)
          observed  148,000.00
          delta     +133,200.00
```

The delta is the number a person reacts to. `+133,200.00` says more than
`amount_match: false`.

Badges after each assertion carry `STRONG`/`WEAK`, `WARNING` for non-blocking
severity, `FORBIDDEN` for negative-space assertions, and `SAME-CHANNEL` when a
value was read back through the surface that wrote it.
