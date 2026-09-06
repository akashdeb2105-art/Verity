# Verity

## Your AI agent says DONE. Verity checks whether it actually did.

An agent reporting success is not evidence that the correct business state
exists. Verity separates three things that every other tool conflates:

```
WORKGRAPH          how the workflow runs
    +
OUTCOME CONTRACT   what must be true afterwards
    +
EVIDENCE           how we know it is true
    =
VERIFIABLE AUTOMATION
```

Because the contract and the evidence layer are independent of the executor,
Verity can verify automation it did not author and does not run.

> **Status: early.** Milestones M0 and M1 — the deterministic sandbox, the
> schema, the evidence store, the verifier, and recording a demonstration to
> propose a contract. The execution runtime, trace adapters and scheduled
> canaries are not built yet. Nothing below is aspirational: every command runs
> today.

---

## What it looks like

```
$ verity verify --contract examples/contracts/invoice_to_po.yaml --live \
    --input invoice_number=INV-4471 --input po_number=PO-2211

  FAIL  invoice_to_po v0.1.0

  PASS  po_reference_present     [STRONG]
  PASS  doc_vendor_match         [STRONG]
  PASS  vendor_match             [STRONG]
  FAIL  amount_match             [STRONG]
          within(doc.total, po.total, tolerance = 0.01)
          expected  14,800.00 (+/- 0.01)
          observed  148,000.00
          delta     +133,200.00
          why: The invoiced amount must match the purchase order within one cent.
  PASS  currency_match           [STRONG]
  PASS  no_duplicate             [STRONG]
  PASS  ledger_staged            [STRONG]
  PASS  no_payment               [STRONG FORBIDDEN]
  PASS  no_deletion              [STRONG FORBIDDEN]

  first divergence
    assertion    amount_match

  10 assertions (10 strong) · 5 facts · 746 ms · 0 model calls · $0.00
```

The vendor changed their invoice template and a decimal moved. The automation
would have posted a bill for **$148,000** against a purchase order for
**$14,800**. Verity read both, compared them, and said so — with the page and
bounding box the number came from.

**Zero model calls. $0.00.** That is not a footnote: it is why running this
against every workflow, every night, is affordable.

---

## Quickstart

```bash
git clone https://github.com/akashdeb2105-art/Verity.git && cd Verity
make install     # Python 3.10+, no API key, no paid service
make verify      # starts the sandbox, verifies the reference contract
make demo        # the flagship failure, end to end
```

CI runs exactly these commands on a clean machine and fails the build if they
take more than five minutes. A quickstart that is not a test is not true.

---

## The Outcome Contract

Success is a declarative, versioned, executable artifact that lives in your
repository and is reviewed in pull requests — not an implicit "no exception
was thrown".

```yaml
facts:
  - id: po                          # read from the system of record, over its API
    source: po_system
    read: { resource: purchase_order, key: '{{ inputs.po_number }}' }
    expect_cardinality: 1           # 0 or 2 is a finding, never a silent pass

  - id: doc                         # extracted from the PDF, deterministically
    source: invoice_doc
    document: '/docs/invoices/{{ inputs.invoice_number }}.pdf'
    extract:
      total: { type: decimal, expect_anchor: 'total due' }

expected:
  - id: amount_match
    assert: 'within(doc.total, po.total, tolerance = 0.01)'
    because: The invoiced amount must match the purchase order within one cent.

forbidden:
  - id: no_payment
    assert: 'none(ledger_events, kind == "payment")'
    because: Verity never pays. A payment event here means something else did.
```

The expression language is small, closed and hand-parsed. There is no `eval`,
no `exec` and no path from a contract file to the Python interpreter — a
property enforced by a test that walks the verifier's syntax tree.

---

## Four verdicts, not two

| Verdict | Meaning | Exit code |
| --- | --- | --- |
| `PASS` | Every blocking assertion holds and every fact resolved | `0` |
| `FAIL` | At least one blocking assertion is false | `1` |
| `DRIFT` | The outcome still holds, but the environment changed | `2` |
| `INCONCLUSIVE` | A required fact could not be resolved | `3` |

`INCONCLUSIVE` is the important one. A system that folds "I could not check"
into "it passed" is lying; one that folds it into "it failed" trains people to
ignore alerts. Verity keeps them apart, and never silently converts
`INCONCLUSIVE` into `PASS`.

Verity also separates **where the outcome first broke** from **where the
environment first changed**. The page can change at step 4 while the assertion
fails at step 7; reporting only the second sends you to debug the wrong place.

---

## Evidence

Every assertion cites the artifact that proves it. Every fact is labelled
`OBSERVED`, `INFERRED` or `RECOMMENDED`, and only `OBSERVED` facts can satisfy
a `STRONG` assertion — a schema-level constraint, not a UI convention.

An **evidence bundle** is the portable output of one verification: the report,
every object it cites, and a manifest whose merkle root covers them. Tamper
with an object or with the manifest and verification fails.

```bash
verity verify --contract c.yaml --live --bundle ./evidence
```

Document fields carry the page and the bounding box they came from, so a
failure can point at the exact region of an invoice rather than gesturing at
the file.

---

## Continuous integration

```yaml
- run: verity eval --suite ./contracts --junit results.xml --github-annotations
```

Annotations land on the line of the contract you wrote, not the top of the
file. JUnit XML means existing test reporters render Verity results natively.
`--fail-on` decides which verdicts break the build; `DRIFT` warns by default.

Connector reads can be recorded to a **cassette** and replayed, so a suite runs
hermetically with no credentials and no network — which is what makes it usable
as a check on a pull request from a fork.

---

## Commands

| Command | What it does |
| --- | --- |
| `verity teach` | Record a demonstration and propose a contract |
| `verity inspect` | Compile a saved recording |
| `verity verify` | Evaluate one contract, live or against a trace |
| `verity eval` | Evaluate a whole suite — the CI entry point |
| `verity lint` | Type-check contracts without reading anything |
| `verity doctor` | Check the local environment |

---

## The sandbox

`apps/sandbox` is a small, deterministic accounts-payable stack: an inbox,
purchase orders, a ledger, and generated invoice PDFs. It exposes the same
records over **both** a JSON API and an HTML UI, so independent-channel
verification is physically possible rather than assumed.

It ships eight perturbations, each exactly reversible:

`amount_changed` · `vendor_changed` · `duplicate_invoice` · `ui_label_changed`
· `missing_field` · `pdf_format_shift` · `ambiguous_record` · `injection`

The same seed produces byte-identical state on every machine, which is what
lets the sandbox serve as demo, test fixture and benchmark at once.

All sandbox data is synthetic. No real vendor, person or account appears
anywhere in this repository.

---

## Teaching it a workflow

```bash
verity teach --url http://127.0.0.1:8099/ui/inbox --name invoice_to_po \
  --contract contracts/invoice_to_po.draft.yaml
```

Do the job once. Verity proposes the contract from what it saw — **with no
model call**. Every assertion traces back to a value that literally appeared
twice during the recording, which is why what it proposes can be trusted, and
also why it proposes less than a person would.

The draft says so itself. A single successful run cannot show duplicates,
forbidden outcomes, branches or tolerances, and the file opens by listing
exactly that.

Add `--ai` and a model suggests the checks nobody demonstrated — but it only
ever **proposes**. Every suggestion must parse, may reference only facts the
recording established, and is written into the file commented out; accepting
one means deleting a `#`. The verifier never uses a model and cannot: an
import rule forbids it, because a verdict that is not reproducible is not
evidence. See [docs/teaching](docs/teaching/recording-a-demonstration.md) and
[ADR-0010](docs/adr/0010-where-a-model-belongs.md).

## What is not built yet

Honesty matters more here than a longer feature list. Verity does **not** yet
have: an execution runtime, trace adapters for Browser Use / Stagehand /
Skyvern, scheduled canaries, a web application, or a web site. Those are later
milestones. `docs/architecture/overview.md` says where each one goes.

---

## Documentation

- [Architecture](docs/architecture/overview.md)
- [Outcome Contracts](docs/contracts/outcome-contract.md)
- [Expression language](docs/contracts/expression-language.md)
- [Verdicts and divergence](docs/verification/verdicts.md)
- [Evidence model](docs/evidence/evidence-model.md)
- [CLI reference](docs/cli/verity.md)
- [Security model](docs/security/security-model.md)
- [Decision records](docs/adr/)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## Licence

[Apache-2.0](LICENSE).
