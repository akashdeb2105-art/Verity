# Outcome Contracts

An Outcome Contract states **what must be true after a workflow runs**. It is a
first-class artifact, versioned independently of any WorkGraph, and it can be
evaluated with no WorkGraph at all — which is what lets Verity verify
automation it neither authored nor ran.

Contracts live in your repository, in git, and are reviewed in pull requests.
A contract in a database is configuration; a contract in git is a
specification with a review history.

## Anatomy

```yaml
apiVersion: verity/v1
kind: OutcomeContract

metadata:
  name: invoice_to_po      # lower_snake_case
  version: 0.1.0           # semver, independent of any workflow version
  risk: MEDIUM             # the highest risk this contract governs

inputs:                    # what the contract needs in order to be evaluated
  invoice_number: { type: string, required: true }

sources:                   # roles, not connector instances
  po_system: { kind: connector, capability: purchase_orders }
  invoice_doc: { kind: document, format: pdf }

facts:                     # one read per fact, in declaration order
  - id: po
    source: po_system
    read: { resource: purchase_order, key: '{{ inputs.po_number }}' }
    expect_cardinality: 1

expected:                  # what must hold
  - id: amount_match
    assert: 'within(doc.total, po.total, tolerance = 0.01)'
    because: The invoiced amount must match the purchase order within one cent.

forbidden:                 # what must not have happened
  - id: no_payment
    assert: 'none(ledger_events, kind == "payment")'

budgets:
  model_calls: 0           # enforced, not advisory

on_failure:
  halt: true
  require_human: true
```

## Sources are roles

A source names a *role* — "somewhere I can read purchase orders" — not a
particular system. Bindings from role to connector are supplied at
verification time. That indirection is why the same contract file runs
unchanged against a sandbox, a staging system and production.

## Facts resolve first, and in order

Facts are read before any assertion is evaluated, in the order they are
declared, so a later fact may reference an earlier one:

```yaml
- id: bill
  source: ledger
  read: { resource: bill, query: { vendor: '{{ doc.vendor }}' } }
```

`{{ … }}` is expanded with the same parser the assertions use, so there is
exactly one way to read a value in a contract and no second mini-language.

**`expect_cardinality`** turns an ambiguous read into a finding. If a contract
expects one purchase order and finds two, that is `INCONCLUSIVE` — not a crash,
and never a silent pass on whichever record happened to come first.

## Strength is derived, never declared

```
STRONG   every referenced fact is OBSERVED from a connector or a document
WEAK     at least one referenced fact comes from a trace, DOM or screenshot
INVALID  references an INFERRED fact with no chain to an OBSERVED root
```

You may write `strength:` in the file for readability, but the typechecker
recomputes it and rejects a mismatch. An assertion is only as strong as the
weakest source it touches.

A contract whose risk is `MEDIUM` or higher and which has no `STRONG` blocking
assertion cannot verify business state, and Verity says so in a warning rather
than reporting a confident `PASS`.

## Independent channels

```yaml
- id: ledger_staged
  assert: 'bill.status == "DRAFT"'
  channel: independent
```

Reading a value back through the same surface that wrote it is
self-confirmation, and it is the most common way verification quietly becomes
theatre. Verity records the channel each fact was read through and reports
`INDEPENDENT`, `SAME` or `UNKNOWN` rather than assuming.

## Forbidden

The `forbidden` block is negative space: what must *not* have happened. Entries
are written as negative assertions that must hold, so they evaluate exactly
like `expected` entries and are only labelled differently in reports. They are
frequently more valuable than the positive checks.

## Style

- Single-quote assertion expressions. A bare `: ` inside a YAML scalar starts a
  mapping.
- Use `=` for keyword arguments — `tolerance = 0.01`. Both `=` and `:` parse,
  but `=` is the YAML-safe form.
- Write `because:` in the words of whoever owns the process. It is what makes a
  failed assertion legible to someone who did not write it.
