## What changed

<!-- One or two sentences. What does this do that the repository could not do before? -->

## Why

<!-- The problem, not the solution. If it fixes an issue, link it. -->

## How to verify

<!-- The exact commands a reviewer should run, and what they should see. -->

```bash
make check
```

## Invariants

These are architectural, not stylistic. Tick every line, or explain below why it
does not apply.

- [ ] Verification stays deterministic — no model call during verification
- [ ] `verity_verifier` still imports no executor, capture or compiler package
- [ ] No trace, page, PDF or model output is treated as authority
- [ ] `INCONCLUSIVE` is never converted to `PASS`
- [ ] Assertion strength is derived, never declared
- [ ] No `TODO` standing in for functionality, no mock presented as a real integration
- [ ] All fixture data is synthetic

## Checks

- [ ] `make check` passes locally (lint · types · boundaries · tests)
- [ ] Tests accompany the change
- [ ] `CHANGELOG.md` updated under *Unreleased*
- [ ] A decision record added under `docs/adr/` if the choice is architectural
- [ ] A new dependency is justified in the description (purpose, licence, maintenance)
