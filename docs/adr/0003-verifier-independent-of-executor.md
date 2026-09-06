# ADR-0003: The verifier must not depend on any executor

**Status:** accepted · **Date:** 2026-09-06

## Context

Runtime independence is the product's differentiator. Differentiators stated
only in documentation decay: one convenient import at a time, the verifier
acquires a dependency on the browser runtime, and a year later "portable" is
marketing rather than fact.

## Decision

`verity_verifier` may import `verity_schema`, `verity_evidence`,
`verity_connectors` and `verity_extract`. It may not import `verity_runtime`,
`verity_capture`, `verity_compiler`, `verity_cli` or `verity_sandbox`,
directly or transitively.

Enforced by an `import-linter` contract in CI, which names the executor
packages **before any of them exists**. `include_external_packages = True`
makes the contract meaningful against packages that are not yet written.

Four contracts are enforced: verifier independence, schema-is-a-leaf, package
layering, and the sandbox importing no Verity logic.

## Alternatives considered

**A documented convention.** Rejected — this is exactly the kind of rule that
is followed until the first inconvenient Friday.

**Separate repositories.** Rejected as premature for one engineer; it buys the
same guarantee at a much higher coordination cost.

## Consequences

Some duplication is accepted rather than reaching across the boundary. The
verifier reads traces through a portable format rather than calling into a
runtime, which is more work and is the point.
