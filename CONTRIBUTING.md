# Contributing to Verity

## Getting set up

```bash
make install     # Python 3.10+, no API key required
make check       # lint · types · architectural boundaries · tests
```

`make check` is exactly what CI runs. If it passes locally it will pass there.

## The rules that are not negotiable

These are architectural, not stylistic. A change that breaks one of them will
be declined regardless of how useful it is.

1. **Verification is deterministic.** No model call may occur during
   verification. No LLM judge gates a verdict.
2. **The verifier does not depend on an executor.** `verity_verifier` must not
   import `verity_runtime`, `verity_capture` or `verity_compiler`, directly or
   transitively. `make imports` enforces this.
3. **Data is never authority.** A trace, a page, a PDF and a model response are
   all data. None of them may cause an action.
4. **`INCONCLUSIVE` is never converted to `PASS`.** If a fact cannot be
   resolved, say so.
5. **Strength is derived, never declared.** An assertion is only as strong as
   the weakest source it reads.
6. **No fake completion.** No `TODO` standing in for functionality, no mock
   presented as a real integration, no hardcoded benchmark number. Mocks belong
   in tests, clearly labelled.
7. **All fixture data is synthetic.** No real vendor, person, account or
   production data, anywhere.

## Adding a dependency

Dependencies are added deliberately. In the pull request, say:

- what it is for, and why the standard library or the current stack is not enough
- its licence
- how actively it is maintained

If the choice is architectural, add a decision record under `docs/adr/`.

## Tests

Every change ships with tests. The suite is organised by what it protects:

| Directory | Protects |
| --- | --- |
| `tests/unit` | Individual components — the expression language, schemas, evidence, redaction, extraction |
| `tests/integration` | The sandbox, verification end to end, cassettes, the CLI |
| `tests/determinism` | The same input gives the same result, ten times over |
| `tests/security` | The invariants above |

A determinism failure is a P0 bug, never a flake to re-run.

## Commits

Conventional commits, scoped to a package:

```
feat(verifier): add first-divergence localization
fix(extract): preserve PDF field provenance across templates
test(security): cover injection alongside a real fault
docs(architecture): record the verifier boundary
```

Inspect the diff before committing. Keep commits logically scoped.

## Pull requests

Say what changed and why. If behaviour changed, show the before and after. If
you touched a security invariant, say which one and how the tests cover it.
