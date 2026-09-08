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

## Tier 2: driving a browser

By default a read step is a recorded no-op: `NAVIGATE`, `CLICK`, `TYPE`,
`SELECT` and `EXTRACT` record that the step was reached and nothing happens.
`--browser` carries them out for real against a live UI.

```bash
verity run examples/workgraphs/invoice_to_po_browser.yaml \
  --contract examples/contracts/invoice_to_po.yaml --browser \
  --sandbox http://127.0.0.1:8099 \
  --input invoice_number=INV-4471 --input po_number=PO-2211 \
  --run-id run_browser --approvals approvals.json
```

```
  invoice_to_po_browser  run_browser
  tier 2: drove a browser, 7 page steps

  ok   1  NAVIGATE      Open the invoices list
  ok   2  TYPE          Type the invoice number into the filter
  ok   3  SELECT        Filter by invoice number
  ok   4  CLICK         Click Find
  ok   5  EXTRACT       Read the invoice row
  ok   6  NAVIGATE      Open the purchase order
  ok   7  EXTRACT       Read the purchase order
  ok   8  CREATE_RECORD Create the draft bill  wrote

  runtime said  DONE      verifier says  PASS
```

Three properties hold whether or not a browser was driven:

- **The runtime imports no browser.** It walks the read steps through a
  `BrowserDriver` protocol (`verity_runtime/ports.py`); the Playwright-backed
  implementation lives in `verity_browser` and the CLI composes the two, the
  same shape as the verification gate. An import-linter contract enforces it.
- **A browser observation is not a fact.** What a page showed is recorded on
  the run's trace and used by replay. It is never handed to the verifier,
  which reads the world through its own connectors. Point the same contract at
  the same sandbox with the altered invoice and a Tier-2 run halts before
  `create_bill` with `verifier says FAIL`, exactly as the no-op run does.
- **The consequential write is still connector-gated.** `create_bill` goes
  through the `ledger` connector, behind policy, verification and approval —
  the browser is not in that path. What a browser *can* do, and what M2c does
  not yet restrict, is submit an HTML form: a browser-native POST that bypasses
  that gate entirely. The shipped browser graph only drives a read-only page,
  and a test asserts the sandbox state hash does not move; a graph that pointed
  `CLICK` at a mutating form would not be stopped. See `SECURITY.md`, "Gaps in
  enforcement".

A driven read that cannot observe what it claimed to — a navigation that times
out, an `EXTRACT` whose target is not on the page — halts the run before the
next consequential step, `halted by: observation`. "I could not look" is not
permission to write, any more than `INCONCLUSIVE` is.

`--browser` needs Chromium: `pip install ".[browser]" && playwright install chromium`.

## Replay and diff

Every `verity run` and `verity dry-run` writes a run record to
`.verity/runs/<run_id>/` (`--no-record` opts out; `--runs-dir` moves it). It is
five JSON files — the plan, the report, the trace, the audit chain, and a
`meta.json` with enough to re-run. There is no database.

```bash
verity replay run_browser --sandbox http://127.0.0.1:8099
```

Replay re-executes the recorded graph with the recorded inputs, **always as a
dry run** — replay is a comparison, not a repetition, and it writes nothing
whatever the recorded run did — and prints how the two runs differ.

```
  baseline run_browser (browser)
  replay   run_browser_replay (browser)

  identical: no difference between the two runs
```

A **difference** is one of: `path` (a different step sequence), `step-status`,
`structural` (a step's page structure changed — browser runs only), `extract`
(a step read a different value), `write` (a different payload digest),
`verdict`, `outcome`. Timing, run ids and audit hashes are **never** a
difference — a canary that fired on those would be noise.

`structural` uses `dom_hash`, a fingerprint of the page's tag-and-role
skeleton — two 32-bit passes (djb2 and FNV-1a) over a pre-order tag+role
stream, joined into 64 bits (`dom1:…`). It ignores renamed classes, reflowed
whitespace, reordered attributes and changed text; it moves when an element is
added, removed, renamed or reordered. Add a second `PO-2211` row to the sandbox
and replay reports it:

```
  structural read_purchase_order: the page structure changed underneath this step
      baseline  dom1:8aa3b5483eb309c0
      replay    dom1:a88295042f7d08ee
  verdict: verification concluded differently
      baseline  PASS
      replay    INCONCLUSIVE
```

**A tier boundary is not drift.** A browser run replayed with `--no-browser`
reports one `tier` difference, is marked *not comparable*, and is never called
identical — "I read the page" and "I recorded that I would have" are different
claims:

```
  not comparable: the replay did not drive a browser, the baseline did
```

`verity replay` exits `0` when the two runs are identical and `1` otherwise.

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
- **Retry.** A Tier-2 read step is a single attempt honouring `timeout_ms`;
  `RetryPolicy` on a node is not yet applied.
- **Cross-environment replay.** `verity replay` re-runs against the sandbox URL
  the record names. It cannot replay a staging run against production.
- **A second drift signal.** `dom_hash` is a pre-order tag+role stream with no
  close markers, so it cannot see an element that is re-nested without changing
  the order elements are first visited in. Every reorder and every reparent
  that changes visit order is still caught. Stated in `verity_browser/domhash.py`
  and pinned in `tests/browser/test_domhash.py`.
- **Extraction robustness.** Four of the fifteen injection payloads disturb
  the invoice PDF's layout enough that vendor extraction misses and reports
  `FAIL` on an invoice whose vendor is unchanged. Safe, but wrong; named and
  pinned in `tests/security/test_injection_suite.py`.
- **One security test is unverified on Python 3.14 + Windows.**
  `test_verification_opens_no_network_sockets` patches `socket.socket`, which
  deadlocks Starlette's `TestClient` on that interpreter/OS pair (a harness
  bug, not this code). It is skipped there and runs on CI's Python 3.10 and
  3.12. See `SECURITY.md`.

Until signed approvals and an audit anchor exist, `verity run` should be
pointed at a sandbox, not at a system you care about.
