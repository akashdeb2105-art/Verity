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
| Risk cannot be lowered by declaring it | Classification reads the verb, resource and payload; the effective level is `max(declared, assessed)` | `tests/unit/test_policy.py::test_a_graph_cannot_declare_its_way_out_of_risk` |
| An unreadable amount escalates rather than counting as zero | An unparseable amount field emits a `HIGH` signal | `tests/unit/test_policy.py::test_an_unreadable_amount_escalates_rather_than_counting_as_zero` |
| A policy that has not decided has not granted permission | `requirement_for` returns `FORBID` for any unlisted level; unknown keys in a policy file are rejected | `tests/unit/test_policy.py::test_a_gap_in_a_policy_forbids_rather_than_allows` |
| A forbidden action stops the run before it starts | Every consequential step is classified in pre-flight, before any step runs | `tests/unit/test_approval.py::test_a_forbidden_action_stops_the_run_before_it_starts` |
| An approval authorises one payload, in one run | Approval is matched on `(run_id, node_id, payload_digest)`; the digest covers resource and every field | `tests/unit/test_approval.py::test_an_approved_amount_cannot_be_replayed_at_a_different_amount` |
| An approval store cannot grant anything on its own | The match is made in `check_approval`, not in the store; a deliberately sloppy store is passed in a test | `tests/unit/test_approval.py::test_a_careless_store_cannot_grant_anything` |
| What an approver is shown is what is checked | `pending_writes` builds the intent with the same function the executor uses | `tests/unit/test_approval.py::test_what_an_approver_is_shown_is_what_is_checked` |
| A run with no approvals configured writes nothing | `NoApprovals` is the default store | `tests/unit/test_approval.py::test_a_run_with_no_approvals_configured_writes_nothing` |
| A run can be stopped, and stops within one step | Kill switch and budgets are checked between steps | `tests/unit/test_audit.py::test_a_kill_switch_pulled_mid_run_stops_the_rest_of_it` |
| An edited, removed or reordered audit entry is detected | Each entry carries the hash of the previous one; `verify` names the first index that fails | `tests/unit/test_audit.py::test_editing_an_entry_is_detected` |
| Audit truncation at the end is **not** detected | Stated, not enforced -- it needs an anchor outside the file, which does not exist yet | `tests/unit/test_audit.py::test_truncating_the_end_is_not_detected_and_that_is_stated` |
| No injection payload produces an unapproved write | Fifteen payloads in free text; the executor holds no model, so text is never instruction | `tests/security/test_injection_suite.py` |
| No injection payload changes what would be written | Payloads are built only from the run's declared inputs and the graph | `tests/security/test_injection_suite.py::test_no_payload_changes_what_would_be_written` |
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
| Verification opens no raw sockets | `socket.socket` is patched to raise; verification still returns `PASS` through its bound connectors | `tests/security/test_invariants.py::test_verification_opens_no_network_sockets` (see gap note below) |
| The runtime cannot reach a browser | `import-linter` forbids `verity_runtime` from importing `verity_browser` or `playwright`; it walks read steps through a `BrowserDriver` protocol | CI (`.importlinter`, contract `runtime-does-not-drive-a-browser`) |
| A browser observation is never a fact the gate uses | The verifier reads through its own connectors; a Tier-2 run against the altered invoice still halts with the verifier's own `FAIL` | `tests/integration/test_browser_executor.py::test_the_altered_invoice_halts_before_the_bill_even_with_a_browser` |
| A `CLICK` is never the path to a system of record | The browser drives read-only surfaces; the full `TYPE`/`SELECT`/`CLICK`/`EXTRACT` sequence leaves the sandbox state hash byte-identical | `tests/integration/test_browser_executor.py::test_the_invoices_filter_form_is_read_only` |
| A driven read that did not observe halts before a write | A failed browser read with a consequential step ahead halts, `halted_by = "observation"` | `tests/unit/test_runtime_browser.py::test_a_failed_read_halts_before_a_consequential_step` |
| Replay never writes | `verity replay` is forced to `DRY_RUN`; the sandbox bill count is unchanged after a replay of a run that wrote | `tests/integration/test_browser_executor.py::test_verity_run_then_replay_through_the_cli` |
| A cross-tier replay is refused, not diffed | A browser run replayed without a browser yields one `tier` difference, `comparable = False`, never identical | `tests/unit/test_runrecord.py::test_a_replay_across_the_tier_boundary_is_refused_not_diffed` |
| The structural page hash has one definition | `verity_capture` splices in `verity_browser.domhash.DOM_HASH_JS`; a live page and the offline string hash identically | `tests/browser/test_domhash.py::test_the_js_and_python_definitions_are_the_same_source_of_truth`, `tests/integration/test_browser_executor.py::test_the_shared_dom_hash_matches_between_a_live_page_and_the_offline_string` |
| The structural hash is blind to cosmetics, not to structure | Renamed class, whitespace, attribute order and text do not move it; add / remove / rename / reorder does. Pure nesting depth is a stated blind spot | `tests/browser/test_domhash.py` |
| The repository carries no credential-shaped literal | Every tracked file is scanned for token, key and JWT shapes | `tests/security/test_repository_hygiene.py` |
| No `.env`, key or profile file is tracked | Name and suffix check over tracked files | `tests/security/test_repository_hygiene.py` |

## Gaps in enforcement

- **Browser-driven `CLICK` / `TYPE` / `SELECT` are not restricted to read-only
  surfaces.** The write-gating guarantee — every consequential write passes
  policy, verification, approval and the audit chain — covers the
  *connector-mediated* write path. A Playwright `CLICK` that submits an HTML
  form is a separate, browser-native channel: a real POST that never touches
  `WriteGuard`, policy, approval or the audit chain. Nothing in the runtime
  stops a graph pointing `CLICK` at a mutating form, so for a browser-driven
  run the guarantee currently depends on graph authors targeting only
  non-mutating pages. The one shipped browser graph
  (`examples/workgraphs/invoice_to_po_browser.yaml`) drives a GET-only surface,
  asserted by `tests/integration/test_browser_executor.py::test_the_invoices_filter_form_is_read_only`.
  Closing this needs a read-only-surface declaration on the graph plus
  plan-time enforcement, and it must land before any graph beyond that example
  is allowed to drive a browser. Noted in
  `packages/runtime/verity_runtime/plan.py` beside the `CONSEQUENTIAL` set.
- **`test_verification_opens_no_network_sockets` does not run on Python 3.14 +
  Windows.** Patching `socket.socket` there deadlocks Starlette's `TestClient`
  portal — a harness bug, reproducible with every Verity change reverted, not a
  regression in this code. It is skipped only on that interpreter/OS pair, with
  the reason in the skip message. CI runs Python 3.10 and 3.12, where the
  invariant is enforced normally. Revisit if 3.14 becomes a CI target or the
  `TestClient` behaviour is fixed upstream.

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
