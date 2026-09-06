# ADR-0002: The Outcome Contract is a first-class artifact

**Status:** accepted · **Date:** 2026-09-06

## Context

An earlier design carried postconditions as properties of WorkGraph nodes.
That made success definitions inseparable from the workflow that produced
them, and therefore inseparable from the runtime that executed it.

## Decision

Three separate artifacts, never collapsed:

```
WORKGRAPH         how the workflow runs
OUTCOME CONTRACT  what must be true afterwards
EVIDENCE          how we know it is true
```

The Outcome Contract is versioned independently, lives in the user's
repository as YAML, and can be evaluated with **no WorkGraph at all**.

## Alternatives considered

**Postconditions on nodes.** Simpler to author from a recording. Rejected: it
makes verification impossible for automation Verity did not author, which is
the entire adoption path.

**A single "workflow" object containing everything.** Rejected: it forces
adopting the recorder to get the verifier, and a change to how a workflow runs
would bump the version of what success means.

## Consequences

A developer with an existing Playwright script or Browser Use agent can adopt
`verity verify` without rewriting anything.

The cost is indirection: sources are roles, bound to connectors at
verification time, which is one more concept to learn. It is also what lets one
contract run unchanged against a sandbox, staging and production.
