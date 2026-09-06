# Benchmark

**Not built yet.** The benchmark is milestone M3. This directory holds its
shape so that the plan is visible rather than implied.

## What it will be

Ten workflows, each run under seven perturbations, scored against recorded
ground truth:

`relabel` · `dom-restructure` · `field-reorder` · `missing-field` ·
`pdf-format-shift` · `ambiguous-record` · `injection`

| Metric | Gate |
| --- | --- |
| Outcome verification accuracy | ≥ 0.90 |
| First-failure localization | ≥ 0.85 |
| Evidence correctness | ≥ 0.95 |
| False-halt rate | ≤ 0.10 |
| **Unsafe action rate** | **0 — release blocker** |
| Deterministic replay consistency | 100% |

## Rules

- Published numbers must reproduce from `make bench` in this repository, or
  they are not published.
- Per-perturbation breakdowns, never a single headline number.
- Failure traces are published alongside successes.

## What exists today

The ground truth already exists and is already scored — for one workflow, in
`tests/integration/test_verification.py`, against the eight perturbations the
sandbox ships. Every one currently produces its expected verdict, localized to
the expected assertion or fact. The benchmark generalises that to ten
workflows and reports it as data rather than as a test result.
