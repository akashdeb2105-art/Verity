# ADR-0007: No database in M0

**Status:** accepted · **Date:** 2026-09-06

## Context

The architecture specifies PostgreSQL, consistently, with no second database
mode. M0 contains the sandbox, the schema, the evidence store and the verifier.

## Decision

M0 ships with no database.

- The verifier is a pure function of the facts it resolves.
- The evidence store is content-addressed on the filesystem.
- The sandbox is a fixture that owns its own in-process state, rebuilt
  deterministically from a seed.

PostgreSQL arrives with the components that genuinely need durable shared
state: runs, the audit log and the job queue.

## Alternatives considered

**Stand up Postgres now for future use.** Rejected: an unused dependency in the
quickstart, and a service a contributor must run before they can execute a
single test.

**SQLite for the sandbox.** Rejected as the "second database mode" the
architecture explicitly forbids — and it would buy nothing, since the sandbox
must be rebuildable from a seed anyway.

## Consequences

This is a deferral, not a reversal. When durable state arrives it is Postgres,
in Docker Compose, once. The sandbox having no database is not an exception to
the rule: a test fixture is not the product's persistence layer.

Docker Compose is also deferred until there is a multi-service stack to
compose, rather than shipping an unverified compose file.
