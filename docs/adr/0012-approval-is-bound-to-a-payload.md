# ADR-0012: A declaration is not evidence, and an approval is not a boolean

**Status:** accepted · **Date:** 2026-09-07

## Context

M2a gave the runtime one control: nothing consequential happens unless
verification passes. That is necessary and it is not sufficient, for two
reasons that turn out to be the same reason.

The first is that a WorkGraph carries a `risk` field. Whoever wrote the graph
put it there — a planner, a compiler, a person editing YAML, possibly a model
that was shown a document containing instructions. If the runtime reads that
field and acts on it, then anything that can influence the graph can set its
own risk level, and the control is decorative. A wire transfer labelled `LOW`
would sail through.

The second is what "approved" normally means in these systems. A person is
shown a screen, clicks yes, and a boolean is set. But between the screen and
the write there is a gap — a re-plan, a retry with different inputs, a
tampered file, an injected instruction — and nothing in the boolean says
which payload it was about. Afterwards every part of the system will
truthfully report that a human approved it. That is the worst possible
failure: not an unapproved action, but an unapproved action carrying a
genuine approval.

Both are the same mistake. Something said "this is fine", and the system
believed the statement instead of checking the fact.

## Decision

**Risk is assessed from what a step would do, and a declaration cannot lower
the result.**

`verity_runtime.policy.classify` reads the verb, the resource and the payload.
The effective level is `max(declared, assessed)`. A node marked `LOW` that
writes `250000` is `CRITICAL`; a node marked `CRITICAL` that writes `1.00`
stays `CRITICAL`, because trusting a declaration in the cautious direction
costs nothing. `SEND_MESSAGE` outranks the record verbs, since a wrongly
created bill can be deleted and a sent message has been read.

An amount that cannot be parsed escalates rather than counting as zero. An
unresolved `{{ inputs.amount }}` has no value at classification time, and
reading that as "small" would classify every templated payment as harmless —
the same error as treating `INCONCLUSIVE` as a pass, in a different costume.

**Policy is configuration, and a level it does not mention is forbidden.**

`Policy` maps a risk level to `ALLOW`, `REQUIRE_APPROVAL` or `FORBID`.
`requirement_for` returns `FORBID` for anything unlisted, and
`Policy.from_mapping` rejects unknown keys rather than ignoring them: a typo
in a policy file that silently fell back to a default would be the quietest
possible way to turn a control off. A run containing a forbidden action does
not begin, rather than running its read steps and stopping on arrival —
"we never started" is a stronger claim than "we halted in time".

**An approval is a claim about `(run_id, node_id, payload_digest)`.**

The digest is `sha256` over the resource and every payload field. An approval
of a $14,800 bill does not authorise a $148,000 one, because those produce
different digests and there is no approval for the second. Refusals name
their own cause: "no approval on file" and "the payload changed after it was
approved" are very different situations for the person reading the report, and
collapsing them is how a tampered run gets mistaken for a forgotten one.

Three consequences of that shape are load-bearing:

- **The match is made in the runtime, not in the store.** A store's only job
  is to hand over the rows it holds. A store written carelessly — or written
  by somebody else — cannot grant anything, and a test passes a deliberately
  sloppy store to prove it.
- **`pending_writes()` builds the intent with the same function the executor
  uses.** An approval screen that displays anything other than the intent
  whose digest is being signed is theatre. Two code paths that each construct
  "the payload" is exactly how a system approves one thing and sends another.
- **The digest is taken at the write, not predicted earlier.** Binding to
  anything other than the bytes that will be sent leaves a gap, and the gap
  is the whole attack.

## Consequences

Approval is now usable without a UI: `verity pending` prints each write, its
assessed risk, why it was assessed that way, and the digest to record.
`verity run --approvals` reads them back. The same three fields will drive
Studio's approval bar in M4 without the model changing.

The defaults are inconvenient on purpose. `MEDIUM` requires approval, which
means every write in the example graph requires approval, which means the
test suite had to grant them explicitly. A policy that let an unattended run
perform its most dangerous action without asking anyone would not be a policy.

Verified end to end against the running sandbox: with the contract passing and
no approval on file, no bill is created; approved at the exact payload, it is;
altered to $48,000 afterwards, the write is refused by digest while the
verifier still says `PASS`. At $148,000 the policy refuses it before the run
starts, which is the two controls doing different jobs rather than one doing
it twice.

## What this does not do

Approvals are not signed. Anyone who can write the approvals file can write an
approval, and the binding only proves that the approval and the payload agree
— not that a particular person authorised it. Signature verification, and an
identity to check it against, belong with the Studio work in M4. Until then
the approvals file is exactly as trustworthy as the filesystem it sits on, and
`docs/runtime.md` says so.
