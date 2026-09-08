# Verity — web creative direction

**Status:** approved direction, not yet built. Milestone **M5**, after M2c, M3
and M4.

> Reconstructed from the working notes of the session where this was agreed.
> The structure, the metaphor and the scene→milestone mapping are as decided.
> Prose and specifics were rebuilt rather than recovered, so treat the wording
> as a faithful restatement, not a transcript.

---

## The rule that governs this whole document

**A scene may not ship before the milestone that makes it true.**

The site's entire argument is that Verity refuses to report success it did not
establish. A marketing site that demonstrates a capability the engine does not
have would refute the product on its own homepage. This is not caution, it is
the thesis applied to ourselves.

Building this in weeks 1–6 is how the engine ends up fake: the site starts
making promises, and the engine gets bent to match the promises instead of the
other way round.

## The idea: The Ledger Line

One continuous horizontal line runs the length of the site. It is the ledger —
the running record of what a system claims and what is true.

The line is the site's spine and its argument at the same time. It stays calm
while the agent is reading. It **breaks** at the moment verification disagrees.
Everything after the break is on the other side of a decision.

Nothing else on the page moves the way the line moves. It is the only element
allowed to carry meaning through motion; everything else supports it.

## The register: an instrument under load

Not a SaaS landing page. Not a dashboard screenshot with a gradient behind it.
The visual language is **measurement equipment** — an oscilloscope, a seismograph,
a pressure gauge, a ledger ruled in ink.

What that means concretely:

- **Precision over friendliness.** Thin rules, exact alignment, real numbers.
  Nothing rounded and reassuring.
- **Monospace where a value is being asserted.** Amounts, digests, verdicts and
  exit codes are set in mono, because they are readings, not copy.
- **Restraint, then one moment of violence.** The site is quiet for six scenes
  so that the break in Scene 07 lands. If everything moves, nothing means
  anything.
- **Paper and ink, not glass and glow.** The ground is off-white, the ink is
  near-black, and the accent is used for exactly one thing: divergence.
- **No stock illustration, no 3D blobs, no floating UI cards at an angle.**

The feeling to aim for: a very good print annual report that happens to be
alive. Human-made, art-directed, deliberate. Every element should look like
someone chose it.

## The eleven scenes

| # | Scene | What it does | Needs |
|---|---|---|---|
| 01 | The claim | "Your AI agent says DONE." The line is flat and calm. | shipped |
| 02 | The gap | What DONE actually means: a report, not a fact. | shipped |
| 03 | The contract | An Outcome Contract on screen, readable, real YAML. | M0 |
| 04 | The check | Deterministic verification running. Zero model calls. | M1 |
| 05 | Four verdicts | PASS / FAIL / DRIFT / INCONCLUSIVE, and why four not two. | M1 |
| 06 | Teach it once | The recording becomes a contract. No model call in the proposal. | M1 |
| 07 | **HALT** | The agent says DONE; the bill does not exist. **The line breaks.** | **M2a/M2b** |
| 08 | The approval | $14,800 approved cannot become $148,000. Digest binding, visible. | M2b |
| 09 | Every night | The canary runs while nobody is watching. | M3 |
| 10 | The evidence | A bundle you can hand to an auditor. | M3 |
| 11 | Studio | Where a person corrects it, and the correction becomes a test. | M4 |

Scenes 01–08 are honest as of M2b. **09–11 are not, and must not be built
until M3 and M4 exist.** If the site ships before then, it ships at Scene 08
and says so — an honest short site beats a dishonest long one.

## Scene 07 is the whole site

Everything before it is setup. Everything after is consequence.

The moment: every step the runtime attempted succeeded. An ordinary agent would
report success and leave a payable behind. Two lines appear side by side —

```
runtime said  DONE          verifier says  FAIL
```

— and the ledger line breaks between them. The break is the only hard cut in
the site's motion. It should feel like something structural failing, not like a
transition.

Then, quietly, what did not happen:

```
Halted before create_bill.
  did not: CREATE_RECORD Create the draft bill
```

This is real output. It is not a mockup. Use the actual terminal output from a
real run against the sandbox, because the one thing this scene cannot survive
is being staged.

## Motion

Motion is argument, not decoration. Rules:

- **The line is the through-line.** Scroll drives its progress. It is
  continuous across route changes — the scene changes around it, it does not
  reload.
- **Scroll choreography, not scroll-jacking.** The user's scroll always does
  what they expect. Nothing is trapped.
- **One hard cut only** (Scene 07). Everything else eases.
- **Numbers count, they do not fade in.** A value that is being asserted should
  resolve like an instrument settling.
- **Respect `prefers-reduced-motion`.** The argument must survive with all
  motion off — if the site is incomprehensible without animation, the animation
  was carrying meaning the copy should have carried.
- Nothing should animate on a loop. Ambient movement is noise.

## Stack

**One Next.js 15 app** — App Router, server components, persistent-scene route
transitions. Chosen over Astro-plus-separate-Studio and a Vite/React SPA
because Studio (M4) and the marketing site share the scene shell, and the
ledger line has to survive navigation.

## What "done" means for M5

- Every scene traceable to a shipped capability, with a link to the doc or ADR
  that backs it.
- All copy claiming a behaviour is checked against `docs/` — no claim on the
  site that is not true in the repo.
- Real output from real runs. No fabricated terminal text, no invented numbers,
  no screenshots of things that do not exist.
- Accessible: keyboard navigable, `prefers-reduced-motion` honoured, contrast
  checked, the whole argument readable with CSS animation disabled.
- Fast enough that an instrument metaphor is not embarrassing.
