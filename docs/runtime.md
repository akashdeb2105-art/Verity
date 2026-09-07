# Running a workflow

`verity verify` answers "did this go right?" after the fact. `verity run`
does the work *and* refuses to finish it when the answer is no.

```bash
# See what it would do. Writes nothing, ever.
verity dry-run examples/workgraphs/invoice_to_po.yaml \
  --contract examples/contracts/invoice_to_po.yaml \
  --input invoice_number=INV-4471 --input po_number=PO-2211

# Do it. Consequential steps happen only if verification passes first,
# and only if someone approved that exact payload.
verity run examples/workgraphs/invoice_to_po.yaml \
  --contract examples/contracts/invoice_to_po.yaml \
  --input invoice_number=INV-4471 --input po_number=PO-2211 \
  --run-id run_2026_09_07 --approvals approvals.json
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

## What has to be true before a write

Four separate things, in this order. Any one of them refusing stops the run.

| | Control | Refuses when |
|---|---|---|
| 1 | **Policy** | the step's assessed risk is one the policy forbids |
| 2 | **Kill switch / budget** | something asked the run to stop, or a limit was passed |
| 3 | **Verification** | the gate returned anything but `PASS` |
| 4 | **Approval** | no approval matches this run, this node and this payload |

They are separate on purpose. "The outcome is right" and "you may do it" are
different questions, and a system that only answers the first will eventually
do something correct that nobody wanted.

## Risk is assessed, not declared

A graph carries a `risk` field, but that is a claim made by whoever wrote the
graph. Verity reads the verb, the resource and the payload, and takes the
**higher** of declared and assessed — so a node marked `LOW` that writes
$250,000 is `CRITICAL`.

```
$ verity pending examples/workgraphs/invoice_to_po.yaml     --input invoice_number=INV-4471 --input po_number=PO-2211 --run-id run_demo

  create_bill  HIGH  REQUIRE_APPROVAL
    ledger.bill(amount='14800.00', currency='USD', number='INV-4471',
                ref='INV-4471', status='DRAFT', vendor='Acme Supplies')
    HIGH (verb: CREATE_RECORD changes a system of record;
          amount_high: amount=14800.00 at or above 10000)
    the graph declared MEDIUM; assessed higher
    sha256:09175befac3289fdec756cedf048c7b2af5fddb3ffa2f544e1b589f134ea79e1
```

An amount that cannot be read escalates rather than counting as zero. An
unresolved `{{ inputs.amount }}` has no value yet, and reading that as "small"
would make every templated payment look harmless.

Default policy, changeable with `--policy`:

| Risk | Requirement |
|---|---|
| `LOW` | proceed once verification passes |
| `MEDIUM` | a person must approve this payload |
| `HIGH` | a person must approve this payload |
| `CRITICAL` | not available to an automated run at all |

A level the policy does not mention is **forbidden**, not allowed. A policy
file with an unknown key is rejected rather than partly applied. A run
containing a forbidden action does not begin.

## Approval is bound to one payload

An approval names the run, the node, and a digest of exactly what would be
written:

```json
[{"run_id": "run_2026_09_07",
  "node_id": "create_bill",
  "digest": "sha256:09175befac3289fdec756cedf048c7b2af5fddb3ffa2f544e1b589f134ea79e1",
  "approver": "controller@example.com"}]
```

Change the amount after that is recorded and the digest changes with it, so
the approval no longer describes the write and the run stops:

```
  Halted before create_bill.
    approval: ledger.bill(amount='48000.00', ...) requires approval:
      the payload changed after it was approved:
      controller@example.com approved sha256:09175bef…, this run would write sha256:938d3419…
```

Note what the verifier said in that run: `PASS`. The contract was satisfied.
Approving $14,800 still did not authorise $48,000. Use `--run-id` so the
approval can be prepared before the run, and `verity pending --json` to
generate the records.

## Stopping a run that has started

`Budget` limits steps, wall-clock seconds and writes. A `KillSwitch` is asked
between steps — `FileKillSwitch` stops the run when a file appears, which is
deliberately the crudest possible mechanism: a control that depends on the
healthy operation of the thing it is meant to stop is not a control.

Both are checked **between** steps, so a stop takes at most one step. Nothing
is interrupted mid-call, because a run that is stopped must still know what it
did.

## The audit record

Every decision in a run is an entry — classification, verdict, approval check,
each step, the halt, the finish — and each entry carries the hash of the one
before it. `report.audit_head` is the value an external anchor would record.

**What it proves:** an edited entry, an entry removed from the middle, or two
entries reordered are all detected, and the report names the entry where the
chain broke.

**What it does not prove:** truncation at the end is not detected — cutting
the tail off a chain leaves nothing behind to disagree with. Neither is a full
rewrite by someone who can recompute every hash. Both need an anchor outside
the file, and Verity does not have one yet. See
[ADR-0013](adr/0013-what-the-audit-chain-proves.md).

## Not yet built

- **Signed approvals.** The binding proves the approval and the payload agree,
  not that a particular person authorised it. Anyone who can write the
  approvals file can write an approval. Identity and signatures come with
  Studio in M4.
- **An anchor for the audit head**, without which truncation is undetectable.
- **The Tier-2 browser executor** and run replay.
- **Extraction robustness.** Four of the fifteen injection payloads disturb
  the invoice PDF's layout enough that vendor extraction misses and reports
  `FAIL` on an invoice whose vendor is unchanged. Safe, but wrong; named and
  pinned in `tests/security/test_injection_suite.py`.

Until the first two exist, `verity run` should be pointed at a sandbox, not at
a system you care about.
