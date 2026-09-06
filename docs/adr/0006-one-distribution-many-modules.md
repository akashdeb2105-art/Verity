# ADR-0006: One distribution, many modules

**Status:** accepted · **Date:** 2026-09-06

## Context

The design called for six or seven packages. The obvious reading is seven
installable Python distributions, each with its own `pyproject.toml`, version
and release process.

## Decision

One distribution (`verity`) exposing seven top-level modules, built by
`hatchling` from `packages/*/` and `apps/sandbox/`.

The architectural boundary is enforced by `import-linter` on the import graph,
not by distribution packaging.

## Alternatives considered

**Seven distributions.** Rejected for now: seven release processes and seven
version-compatibility matrices, maintained by one engineer, for zero present
benefit. Distribution packaging is not what makes a boundary real — the import
graph is, and that is checked in CI either way.

**A single flat package.** Rejected: it would erase the boundaries entirely.

## Consequences

`verity-schema` cannot yet be adopted on its own by someone who wants only the
format. That matters when the format is published for others to implement
against, and this decision should be revisited then — the module layout is
already shaped for the split.
