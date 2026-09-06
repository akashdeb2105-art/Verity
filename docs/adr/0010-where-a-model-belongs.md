# ADR-0010: A model proposes; deterministic code decides

**Status:** accepted · **Date:** 2026-09-06 · **Amends:** ADR-0009

## Context

ADR-0009 recorded that the compiler proposes contracts deterministically. That
was correct about the *core* and wrong about the *whole*: it removed the model
layer entirely rather than making it optional, and it did so partly because the
build environment had no API key. Letting an environment constraint decide an
architecture is a bad reason, and the omission was raised as such.

There are two genuinely different questions, and conflating them is what caused
the mistake:

1. **Should a model decide whether a workflow passed?** No, permanently.
2. **Should a model help write the contract?** Yes.

## Decision

**A model proposes; deterministic code decides.**

### Where a model may not go

The verifier. Verification stays deterministic, and that is now enforced rather
than intended: an `import-linter` contract forbids `verity_verifier` from
importing `verity_ai` or any provider SDK, and it was checked by deliberately
breaking it.

Three reasons, in order of weight. A verdict that is not reproducible is not
evidence. A model judging a document can be argued with by the document it is
judging, which is the whole prompt-injection problem restated. And a
verification that costs money per run cannot be run every night, which removes
the product's main recurring value.

### Where a model does belong

Authoring. `verity_ai` is a provider-agnostic layer — OpenRouter, Google AI
Studio, any OpenAI-compatible endpoint, and local Ollama — used by the compiler
to suggest checks nobody demonstrated and to write clearer explanations. "No
payment exists for this invoice" is obvious to a reader and invisible in a
recording of a successful run.

### The rule that makes it safe

Nothing a model returns is trusted. Every suggested expression must parse in
the assertion language, may use only functions the language defines, may
reference only facts the recording actually established, and must not restate a
check already derived. Anything failing is discarded and counted, and the count
is shown.

Accepted suggestions are written into the contract **commented out**, under a
heading saying they were not observed. Accepting one is a person deleting a
`#`. A suggestion is not an observation, and the file format does not let the
two look alike.

The recording is untrusted input — page text can carry an instruction aimed at
whatever reads it next — so it is delimited and labelled as data. Because
validation happens afterwards in code the model cannot influence, the worst a
successful injection achieves is a discarded suggestion. That is asserted for
four hostile suggestions, including one that would always pass and one that
tries to widen an existing tolerance to `999999`.

## Consequences

Verity works with no model configured; enrichment only ever adds to a result
that already exists, so a missing key or an unreachable endpoint costs
suggestions and nothing else.

Tests never call a paid endpoint. Provider request shapes are asserted against
mock transports, and the end-to-end path replays a recorded cassette — a suite
that needs a live key cannot run on a fork, cannot run offline, bills somebody
on every CI run, and is not deterministic.

The set of legal function names moved into `verity_schema.expr`, beside the
parser, because both the compiler that writes contracts and the verifier that
evaluates them need it and neither may import the other. The evaluator asserts
at import that its implementations match the declared vocabulary exactly.
