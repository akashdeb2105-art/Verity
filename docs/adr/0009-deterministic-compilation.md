# ADR-0009: The compiler proposes deterministically

**Status:** accepted · **Date:** 2026-09-06

## Context

The approved plan for M1 was: record a demonstration, send it to a model, and
have the model write the contract. The risk was recorded at the time — *a wrong
proposed assertion is worse than a missing one, because a person may trust it*
— with a fallback of shipping scaffolding if quality was poor.

Building it made a third option obvious. Most of the work needs no model at
all, because the demonstration already contains the answer:

- A value typed into a search box that also appears in a document is a variable.
  That is string matching.
- The same number on the purchase-order screen and the ledger screen means the
  person was comparing them. That is an observation about what was on screen,
  not an inference about intent.
- A short state word the person clicked on, appearing nowhere else, is the end
  state that was demonstrated.

## Decision

The compiler is deterministic. Every step, variable and assertion it proposes
is traceable to a value that literally appeared in the recording. No model is
called, and none is required.

A model may later improve the *wording* of a proposal — names, descriptions.
It does not decide what is checked.

Two consequences follow, and both are wanted:

- **Precision is high by construction.** The compiler cannot invent an
  assertion, because it can only report a relationship it saw twice.
- **Recall is visibly low, and the draft says so.** Duplicates, forbidden
  outcomes, branches, tolerances and unread documents are each named at the top
  of the generated file as things one demonstration cannot establish.

## Alternatives considered

**Model-written contracts, as originally planned.** Rejected. Higher recall,
but every extra assertion is one a person must disprove rather than confirm,
and the failure is silent: a plausible-looking assertion that checks the wrong
thing still goes green. It also could not have been tested honestly in an
environment with no API key.

**Model-assisted, with deterministic proposals as a floor.** Still open, and
the design leaves room for it. Not needed yet: on the reference workflow the
deterministic path proposes three assertions, all correct, all STRONG.

**Inferring branches.** Rejected outright. One recording shows one path.
Inventing an alternative invents a fact.

## Consequences

A draft is a starting point, never a finished contract, and the tooling is
built to say so — the CLI ends with "This is a draft", the file opens with what
it cannot know, and an unread document produces a TODO naming the file.

The honest negative is asserted in the test suite: the proposed contract does
**not** catch the flagship invoice-total error, because the person opened the
PDF and Verity could not read inside it. A companion test shows the completed
reference contract catching it. Both are locked in so the gap cannot close
quietly or widen unnoticed.
