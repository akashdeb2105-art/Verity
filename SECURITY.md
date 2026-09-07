# Security policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub security advisories](https://github.com/akashdeb2105-art/Verity/security/advisories/new)
rather than opening a public issue.

We aim to acknowledge a report within 5 working days and to publish a fix or a
mitigation within 90 days of acknowledgement. If a report affects a downstream
user before then, we will coordinate disclosure with you.

## The governing principle

> **A trace is data. A page is data. A PDF is data. Model output is data.
> None of them is authority.**

Only an authorised WorkGraph node, executed under policy, may cause a
consequential side effect. In this milestone that is trivially true, because
Verity has no side-effect path at all: the verifier and its connectors are
read-only. When the runtime arrives, there will be exactly one code path that
can act, and it will take a compiled node id plus a policy decision as
arguments — reachable from neither parsed page content nor model output.

## What is enforced today, and how

| Invariant | How it is enforced | Test |
| --- | --- | --- |
| Verification is deterministic; zero model calls | No provider SDK is a dependency; `model_calls` budget must be `0` and is validated at load time | `tests/security/test_invariants.py` |
| No dynamic code execution | The expression language is hand-tokenised and hand-parsed; a test walks the verifier's AST for calls to `eval`, `exec`, `compile`, `__import__` | `tests/security/test_invariants.py` |
| A contract cannot construct Python objects | `yaml.safe_load` only | `tests/security/test_invariants.py` |
| Adversarial text in a document or page changes nothing | Injection perturbation, verified alone and alongside a real fault | `tests/security/test_invariants.py` |
| Secrets are never persisted | Redaction runs before any evidence is written, fails closed on unknown fields | `tests/unit/test_redaction.py` |
| A recording never carries a credential | Redaction at the point of capture: inside the page, then on content. 30 adversarial field shapes | `tests/unit/test_capture_redaction.py` |
| Redaction does not destroy business data | 15 ordinary field names asserted to survive | `tests/unit/test_capture_redaction.py` |
| Nothing consequential happens unverified | The runtime consults a gate before the first world-changing step; only `PASS` continues | `tests/unit/test_runtime.py`, parametrised over all four verdicts |
| A missing decision is not permission | The default gate refuses everything; `INCONCLUSIVE` halts | `tests/unit/test_runtime.py` |
| A dry run cannot write | Interception at the connector boundary; sandbox state hash asserted unchanged | `tests/integration/test_run_loop.py` |
| The executor can never reach a verifier or a model | `import-linter`, plus a fresh-interpreter probe | `tests/security/test_invariants.py` |
| Verification can never reach a write | Reading and writing are separate protocols; `HttpJsonConnector` has no `write` | `tests/security/test_invariants.py` |
| The verifier can never reach a model | `import-linter` forbids `verity_verifier` from importing `verity_ai` or any provider SDK | CI, and `tests/security` |
| A model cannot get a check into a contract | Every suggestion is parsed and validated afterwards; four hostile suggestions asserted to be discarded | `tests/unit/test_ai_enrichment.py` |
| A model suggestion is never enforced silently | Suggestions are written commented out | `tests/integration/test_ai_loop.py` |
| The compiler's core makes no model call | No provider SDK reachable; checked in a fresh interpreter | `tests/security/test_invariants.py` |
| Evidence cannot be altered undetected | Content addressing plus a merkle manifest; tampering with either fails | `tests/unit/test_evidence.py` |
| The verifier cannot reach an executor | `import-linter` contract, declared before the executor packages exist | CI |
| "I could not check" never becomes "it passed" | `INCONCLUSIVE` is a distinct verdict | `tests/integration/test_verification.py` |
| The repository carries no credential-shaped literal | Every tracked file is scanned for token, key and JWT shapes | `tests/security/test_repository_hygiene.py` |
| No `.env`, key or profile file is tracked | Name and suffix check over tracked files | `tests/security/test_repository_hygiene.py` |

## Scope

In scope: the verifier, the expression language, the contract loader, the
evidence store, the redaction pipeline, the connectors and the CLI.

The sandbox (`apps/sandbox`) is a test fixture. It is deliberately permissive,
serves synthetic data only, and is not intended to be exposed to a network. It
binds to `127.0.0.1` by default; please do not report its openness as a
vulnerability, but do report anything that lets sandbox content influence a
verdict.

## Data handling

Verity stores evidence on the local filesystem. Redaction runs before anything
is written. No telemetry is collected and nothing is sent anywhere: the only
network calls Verity makes are to the connectors a contract explicitly binds.
