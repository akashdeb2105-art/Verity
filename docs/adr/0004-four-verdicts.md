# ADR-0004: Four verdicts, not two

**Status:** accepted · **Date:** 2026-09-06

## Context

Every test framework returns pass or fail. Verification against live systems
has two failure modes a binary result cannot distinguish: *the outcome is
wrong*, and *I could not determine the outcome*.

There is a third case worth separating: the outcome is right but the
environment moved. That is the early warning — the moment before a workflow
breaks — and folding it into `PASS` throws away the most valuable signal the
system produces.

## Decision

`PASS` · `FAIL` · `DRIFT` · `INCONCLUSIVE`, with exit codes `0` · `1` · `2` ·
`3`. `--fail-on` decides which break a build; the default is
`fail,inconclusive`, so drift warns.

**`INCONCLUSIVE` is never silently converted to `PASS`.**

## Alternatives considered

**Binary pass/fail.** Rejected: a connector outage would either be reported as
success — a lie — or as failure, which trains people to ignore alerts and kills
the monitoring system.

**A numeric confidence score.** Rejected: it invites a threshold nobody can
justify, and it hides which of the four situations actually occurred.

## Consequences

Consumers must handle four cases. Exit codes carry more information than a
shell's `if` naturally expresses, so `--fail-on` exists to collapse them for CI
without losing the distinction in the report.

An assertion that could not be evaluated renders as *not evaluated* — never as
passing, never as failing.
