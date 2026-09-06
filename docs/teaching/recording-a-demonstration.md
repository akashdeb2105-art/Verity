# Recording a demonstration

`verity teach` opens a browser, watches someone do a job once, and proposes a
WorkGraph and an Outcome Contract from what it saw.

```bash
verity teach --url http://127.0.0.1:8099/ui/inbox \
  --name invoice_to_po \
  --allow-domain 127.0.0.1 \
  --out invoice.session.json \
  --contract contracts/invoice_to_po.draft.yaml
```

Do the task. Press Enter when finished.

To compile a recording again later, without recording anything new:

```bash
verity inspect invoice.session.json --contract draft.yaml --graph graph.json
```

## The core calls no model

Every step, variable and assertion is derived from a value that literally
appeared in the recording. A wrong proposed assertion is worse than a missing
one, because a person may trust it — so proposing only what was observed keeps
precision high by construction, and teaching works with no API key at all.

## Adding a model with `--ai`

```bash
verity teach --url ... --contract draft.yaml --ai
```

A model then suggests checks nobody demonstrated and writes clearer
explanations. "No payment exists for this invoice" is obvious to a reader and
invisible in a recording of a successful run.

Nothing it returns is trusted. Every suggestion must parse, may use only
functions the language defines, may reference only facts the recording
established, and must not restate a check already derived. Anything else is
discarded and the count is shown:

```
2 suggested, 1 discarded, $0.0000
  suggests   vendor_is_not_blank  (expected)
  suggests   bill_is_not_already_paid  (forbidden)
  discarded  totally_made_up: references approvals, which the recording never established
```

Suggestions are written into the contract **commented out**. Accepting one
means deleting a `#`. A suggestion is not an observation, and the file does not
let the two look alike.

Configure a provider in `.env` (which is gitignored and must never be
committed):

```
VERITY_LLM_PROVIDER=openrouter     # or gemini, openai, ollama
OPENROUTER_API_KEY=...
```

The verifier never uses a model, and cannot: an `import-linter` contract
forbids it. A verdict that is not reproducible is not evidence, a model judging
a document can be argued with by that document, and verification that costs
money per run cannot run every night. See ADR-0010.

## What it can work out, and how

| It proposes | Because |
| --- | --- |
| An **input** | An identifier appeared in a page address *and* in the page text, so it selects which record the job is about. |
| A **comparison** | The same value appeared in two different systems. The person was checking a match — that is an observation, not a guess about intent. |
| An **end state** | The person clicked on a short state word to look at it, and it appeared nowhere else. |

A number that was read is never turned into a constant. Asserting
`amount == 14800` would pass today and be wrong tomorrow; a value that varies
is an input or a comparison, never a fixed expectation.

Clicking matters. A value merely present on screen is weaker evidence than one
the person opened the page to look at, and the draft marks the difference.

## What one recording cannot show

The draft says all of this at the top of the file, because the dangerous
failure is not a bad proposal — it is a person assuming the draft is complete.

- **Duplicate checks.** A single successful run never shows what a duplicate
  looks like.
- **Forbidden outcomes.** A payment that must *not* happen cannot be observed
  in a run where it did not happen. This is often the most valuable part of a
  contract, and it is always left empty for you to write.
- **Branches.** One recording is one path. No alternative is invented, and the
  graph records `branches_observed: 0` rather than guessing.
- **Tolerances.** An exact match was demonstrated. Any allowance is your
  judgement.
- **Document contents.** If a PDF was opened, Verity records that it happened
  but not what was inside. The draft carries a TODO naming the document.

## Redaction

Redaction runs at the point of capture and cannot be switched off. A password
is never placed into a recording at all — there is no moment when one exists in
memory as part of a session, and no later sanitising step that could be
forgotten.

Two layers:

1. **The field.** Input type, `autocomplete`, name, id, label, placeholder and
   `aria-label` are inspected. An unrecognised `<input>` type is treated as
   sensitive, because that is exactly where a new credential widget appears.
2. **The content.** Whatever survives is run through the evidence redactor,
   which catches a token pasted into an ordinary text box.

The first check also happens inside the page, so a password field's value never
travels from the browser to Verity at all.

Over-redaction is treated as a failure too. A recording with the invoice number
stripped out is useless, and a redaction notice that fires on
`passenger_count` teaches people to ignore it. Thirty adversarial field shapes
and fifteen ordinary business fields are covered by tests.

## Reviewing the draft

```bash
verity lint contracts/invoice_to_po.draft.yaml
```

Then read it. Every assertion carries a `because:` line saying which observation
produced it, so you can judge it without replaying the recording.

Expect to make at least one edit: the `capability:` on each source is guessed
from the page address, and only you know what your connector actually provides.

## Where the browser runs

Recording needs Playwright and Chromium:

```bash
pip install playwright && playwright install chromium
```

Only the recorder needs them. The event model, the redaction layer, the reader
and the entire compiler work with no browser installed, which is why the
compiler's tests run anywhere.
