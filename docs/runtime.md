# Running a workflow

`verity verify` answers "did this go right?" after the fact. `verity run`
does the work *and* refuses to finish it when the answer is no.

```bash
# See what it would do. Writes nothing, ever.
verity dry-run examples/workgraphs/invoice_to_po.yaml \
  --contract examples/contracts/invoice_to_po.yaml \
  --input invoice_number=INV-4471 --input po_number=PO-2211

# Do it. Consequential steps happen only if verification passes first.
verity run examples/workgraphs/invoice_to_po.yaml \
  --contract examples/contracts/invoice_to_po.yaml \
  --input invoice_number=INV-4471 --input po_number=PO-2211
```

## What a halt looks like

With the invoice altered to $148,000 against a $14,800 purchase order:

```
  ok   1  NAVIGATE      Open the inbox
  ok   2  EXTRACT       Read the invoice document
  ok   3  EXTRACT       Read the purchase order
 HALT  4  CREATE_RECORD Create the draft bill  not written

  runtime said  DONE      verifier says  FAIL
  'amount_match' is the first assertion that does not hold

  Halted before create_bill.
    did not: CREATE_RECORD Create the draft bill
```

Every step the runtime attempted succeeded. That is the point: an executor
without a verifier would report `DONE` and leave a $148,000 payable behind.
The two claims are printed next to each other because the product exists in
the gap between them.

## The two kinds of step

A node either looks at the world or changes it. Only four verbs change it —
`CREATE_RECORD`, `UPDATE_RECORD`, `SEND_MESSAGE`, `CALL_API` — and each one
must carry a `write` spec saying where it writes and exactly what it sends:

```yaml
  - id: create_bill
    type: CREATE_RECORD
    write:
      connector: ledger
      resource: bill
      payload:
        ref: "{{ inputs.invoice_number }}"
        amount: "14800.00"
```

`payload` may interpolate `{{ inputs.name }}` and nothing else. It is not a
template engine on purpose: a payload that could reach for anything would
make an approval meaningless, because you cannot consent to a shape you
cannot see.

A consequential node without a `write` spec is an error, not a guess.

## The gate

Before the first consequential step — once, not per step — the contract is
verified. **Only `PASS` continues.**

| Verdict | What happens |
|---|---|
| `PASS` | The run proceeds and writes. |
| `FAIL` | Halt. Something is provably wrong. |
| `DRIFT` | Halt. The world changed underneath the workflow. |
| `INCONCLUSIVE` | Halt. *"I could not check" is not permission.* |

The last row is the one that matters. A system that treats an unresolvable
fact as a pass is lying about what it knows, and it fails silently: the day a
connector breaks is the day every write goes through unchecked.

A runtime with **no gate configured is closed**, not open. The safe direction
for a missing decision is to stop.

## Dry run

Dry run intercepts at the connector boundary, not by skipping steps. Every
step still runs; the write returns a result shaped exactly like a live one,
so nothing downstream branches on whether it was real — code that behaves
differently under a dry run is code the dry run has not tested.

The proof is the sandbox's own state hash, asserted byte-identical before and
after in `tests/integration/test_run_loop.py`.

## Exit codes

| Code | Outcome |
|---|---|
| 0 | `COMPLETED` — every step ran; any write was verified first |
| 1 | `FAILED` — a step could not be carried out |
| 2 | `HALTED` — stopped deliberately, before something irreversible |

A halt is distinguishable from a crash, which matters in CI.

## Determinism

Ten replays of the same graph produce an identical node sequence and cost
nothing. Planning is separate from execution and does no I/O, so the order a
run will take can be inspected before anything happens. Ordering ties are
broken by edge priority then node id — a runtime that picked a different
valid order each time could not be replayed, and a diff between two runs
would mean nothing.

**Zero model calls.** Not a budget, an absence: `verity_runtime` imports no
provider, and an import rule stops it from acquiring one. See
[ADR-0011](adr/0011-the-runtime-and-the-verifier.md) for why the runtime
cannot import the verifier either.

## Not yet built

Risk classification and policy, approval bound to
`(run_id, node_id, payload_hash)`, the kill switch, the hash-chained audit
log, and the Tier-2 browser executor. Until those exist, `verity run` should
be pointed at a sandbox, not at a system you care about.
