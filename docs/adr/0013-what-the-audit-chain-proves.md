# ADR-0013: What the audit chain proves, and what it does not

**Status:** accepted · **Date:** 2026-09-07

## Context

Verity's claim is evidential: not "the agent said it worked" but "here is what
was checked, and here is what was refused". A record that can be edited
afterwards does not support that claim — it is a story about the run rather
than a record of it.

The temptation with audit logs is to describe them in language they do not
earn. "Tamper-proof" and "immutable" get used for files sitting on a disk the
writer owns. Anyone who has to reason about the guarantee later then reasons
about the wrong one.

## Decision

**Every decision in a run is an entry, and every entry carries the hash of the
one before it.**

The chain covers the run starting, each node's classification, the verification
verdict, each approval check, each step and what it wrote, any halt with the
control that caused it, and the run finishing. `verify()` recomputes each
entry's hash from its own contents and checks it against its parent's, and
reports the first index where the chain stops holding — because "something was
changed" is not actionable and "entry 4 no longer matches its contents" is.

That detects an edited entry, an entry removed from the middle, and two
entries reordered. There are tests for all three, performing the modification
the way someone with file access would.

**The limit is written into the module, and into a test.**

Truncating the log at the end is not detected. Cutting the tail off a chain
leaves nothing behind to disagree with, so the remainder verifies perfectly.
Nor is a full rewrite: someone who can rewrite the file can recompute every
hash and produce a chain that verifies.

Both need an anchor outside the file — a copy somewhere the writer cannot
reach, a signature, or the head length recorded elsewhere. Verity has no such
anchor today. Rather than leave that as a gap somebody discovers later, there
is a test named `test_truncating_the_end_is_not_detected_and_that_is_stated`,
which will keep saying so until the situation changes.

**The head is published on every report anyway.**

`RunReport.audit_head` is the single value an external anchor would need to
record. It is emitted now, before there is anywhere to put it, so that when
there is somewhere, the runs made before then are not a separate migration
problem.

## Consequences

A halted run is as fully recorded as a successful one — more usefully, since
the runs worth auditing are the ones that were stopped. `report.audit` survives
a round trip through JSONL and still verifies, which is what makes it evidence
that can leave the process.

The honest description of this control is: it detects accident and casual
tampering, and it does not detect a determined rewrite. That sentence is in
`audit.py`, in `SECURITY.md`, and here. It should be the sentence anyone
repeats about it.
