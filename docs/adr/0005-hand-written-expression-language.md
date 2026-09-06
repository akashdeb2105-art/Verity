# ADR-0005: A hand-written expression language

**Status:** accepted · **Date:** 2026-09-06

## Context

Contracts need to express assertions. Contracts are files, and files can come
from a pull request, a template, or eventually a marketplace. Whatever
evaluates them is a security boundary.

## Decision

A hand-written lexer and recursive-descent parser, roughly 700 lines including
the evaluator. The grammar is closed: no arithmetic, no assignment, no
indexing by expression, no lambda, no method calls. Aggregation is a fixed set
of builtins.

## Alternatives considered

**Python's `ast` with a node whitelist.** Much less code. Rejected: the safety
property becomes "the whitelist is complete", which is a claim that has to hold
against every future contributor and every future Python version. It also could
not express `~=` or YAML-safe keyword arguments without preprocessing.

**An existing expression library (CEL, JMESPath, `simpleeval`).** Rejected:
each brings a dependency at the security boundary, none has the money-comparison
and provenance semantics needed, and evolving the grammar means negotiating
with an upstream project.

**Natural-language assertions evaluated by a model.** Rejected outright — see
[ADR-0001](0001-deterministic-verification.md).

## Consequences

More code to maintain, and every new operator is our work. In exchange the
security property is structural rather than conditional: there is no code path
from a contract to the interpreter, and a test walks the verifier's syntax
trees to keep it that way.

The parser also produces a renderable AST, so reports can show exactly what was
evaluated in canonical form, and contract diffs are meaningful.
