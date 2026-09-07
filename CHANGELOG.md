# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

Milestone **M1 — teach and compile**. Verity can now watch someone do a job
once and propose the contract itself.

**Capture** (`verity_capture`)
- A controlled Chromium teaching session with an ephemeral profile and an
  optional deny-by-default domain allowlist.
- Redaction at the point of capture. A password is never placed into a
  recording at all: the first check runs inside the page, so the value does not
  travel to Verity, and a second content pass catches a token pasted into an
  ordinary text box. Thirty adversarial field shapes are covered by tests, and
  so are fifteen ordinary business fields — over-redaction makes a recording
  useless and is treated as a failure too.
- Playwright is imported lazily, so the event model, the redaction layer and
  the session reader work on a machine with no browser.

**Compiler** (`verity_compiler`)
- Raw events to semantic steps, then to a WorkGraph and a proposed Outcome
  Contract — with **no model call**. Every proposal is traceable to a value
  that literally appeared in the recording. See ADR-0009.
- Inputs from identifiers that appear in both a page address and its text;
  comparisons from a value seen in two different systems; end states from a
  short state word the person deliberately clicked. A number that was read is
  never turned into a constant.
- The generated file opens with what a single demonstration cannot establish —
  duplicates, forbidden outcomes, branches, tolerances — and names any document
  that was opened but not read.

**Runtime** (`verity_runtime`)
- `verity run` and `verity dry-run` execute a WorkGraph and halt before a
  consequential write when verification does not pass. Only `PASS` continues:
  `INCONCLUSIVE` halts too, because "I could not check" is not permission.
- A runtime with no gate configured is closed, not open.
- No import edge between the runtime and the verifier in either direction;
  the CLI composes them. Two import-linter contracts enforce it. See
  ADR-0011.
- Deterministic and free: ten replays take the same path at zero cost, with
  no model provider importable from the package.
- Reading and writing are now separate connector protocols. Every write
  passes through a `WriteGuard`; in dry-run mode it records the intent and
  performs nothing, proven by the sandbox state hash.
- Exit codes: 0 completed, 1 failed, 2 halted.
- **Risk is assessed, not declared.** A graph's `risk` field is a claim by
  whoever wrote the graph; classification reads the verb, resource and payload
  and takes the higher of declared and assessed. A node marked `LOW` writing
  $250,000 is `CRITICAL`. An amount that cannot be read escalates rather than
  counting as zero. See ADR-0012.
- **Policy is configuration.** A risk level it does not mention is forbidden,
  not allowed; an unknown key in a policy file is rejected rather than ignored;
  and a run containing a forbidden action does not begin.
- **An approval is bound to `(run_id, node_id, payload_digest)`.** Approving a
  $14,800 bill cannot authorise a $148,000 one -- verified against the running
  sandbox, with the contract still returning `PASS`. Refusals distinguish "no
  approval on file" from "the payload changed after it was approved". The match
  is made in the runtime, so a carelessly written store cannot grant anything.
- `verity pending` shows what an approver has to be shown: the intent, the
  assessed risk with its reasons, and the digest that will be checked -- built
  by the same function the executor uses.
- **A kill switch and budgets**, checked between steps so a stop takes at most
  one step and nothing is interrupted mid-call. `FileKillSwitch` stops a run
  when a file appears.
- **A hash-chained audit record** of every decision. An edited, removed or
  reordered entry is detected and named. Truncation at the end is *not*, and
  that limit is stated in the module, in `SECURITY.md` and in a test. See
  ADR-0013.
- **Fifteen prompt-injection payloads**, grouped by what they attack -- forged
  approvals and digests, spoofed verdicts, redirected writes, policy disabling,
  zero-width and bidi hiding. None moves a payload digest, lowers an assessed
  risk, or produces an unapproved write.

### Known

- Four of the fifteen injection payloads disturb the invoice PDF's layout
  enough that anchored vendor extraction misses and reports `FAIL` on an
  invoice whose vendor is unchanged. The outcome is safe -- the run halts and
  writes nothing -- but it is a false positive, and it is named and pinned by
  a test rather than tuned away.
- Approvals are not signed. The binding proves the approval and the payload
  agree, not who authorised it.
- The audit head has nowhere external to be anchored, so truncation is
  undetectable.

**The document channel**
- A demonstration that opens a document now keeps it: fetched through the
  recording's own browser context, so a file behind a login is reachable, and
  stored content-addressed beside the session. Verified against its recorded
  hash when read back.
- The compiler extracts fields from it deterministically and proposes checks
  across it, which is what lets a generated contract catch an invoice that
  disagrees with its purchase order -- the case the ERP screens cannot show,
  because they all agree with each other.
- Reading the reference recording's invoice takes the proposed contract from
  3 assertions to 7.

**Model layer** (`verity_ai`)
- Provider-agnostic: OpenRouter, Google AI Studio, any OpenAI-compatible
  endpoint, and local Ollama. Budget ceilings on calls, input size and cost.
- Used by `verity teach --ai` to suggest checks nobody demonstrated and to
  write clearer explanations. Nothing it returns is trusted: a suggestion must
  parse, use only defined functions, reference only facts the recording
  established, and not restate an existing check. Anything else is discarded
  and counted.
- Suggestions are written **commented out**. Accepting one is a person deleting
  a `#`.
- A new import-linter contract forbids the verifier from importing `verity_ai`
  or any provider SDK. Verification stays deterministic and free per run.
- Tests never call a paid endpoint: request shapes are asserted against mock
  transports and the end-to-end path replays a recorded cassette.

**CLI**
- `verity teach` records a demonstration; `verity inspect` compiles a saved one.
  Both accept `--ai`.

**Boundaries**
- Two new import-linter contracts: the compiler and the verifier are siblings
  and neither may import the other, and capture may not import anything that
  judges what it recorded.

The whole loop is tested end to end against the sandbox: the auto-proposed
contract passes on a clean run and catches a duplicate bill. The honest gap is
asserted too — it does *not* catch the flagship invoice error, because the
person opened the PDF and Verity could not read inside it.

### Changed
- Dependency floors raised by Dependabot and verified against the upgraded
  toolchain: pytest 9, mypy 2.3, ruff 0.16, import-linter 2.15, plus httpx
  0.28.1, jinja2 3.1.6, uvicorn 0.52.4 and pdfplumber 0.11.10. The jinja2 and
  uvicorn floors carry security fixes.
- GitHub Actions pinned to `checkout@v7`, `setup-python@v7` and
  `upload-artifact@v7`.

- Dependency floors raised to pydantic 2.13.5, PyYAML 6.0.3, fastapi 0.141.1
  and reportlab 5.0.1. reportlab 5 is a major release; it was verified to
  produce byte-identical invoice PDFs before being accepted, since the document
  extraction tests read those bytes.

### Fixed
- Dependabot uses `versioning-strategy: increase-if-necessary`, so it no longer
  opens a pull request merely to raise a lower bound that the installed version
  already satisfies. Security updates are unaffected. Without this, every
  release of any dependency produced a pull request, and that volume of noise is
  how a real security update gets scrolled past.
- Dependabot no longer requests a `dependencies` label. The label does not
  exist in this repository, and naming a missing label made Dependabot warn on
  every pull request it opened.

### Added
- A repository hygiene guard (`tests/security/test_repository_hygiene.py`) that
  scans every tracked file for credential-shaped literals, tracked `.env` or key
  files, CRLF in shell scripts, and scripts missing the executable bit. Secret
  scanning matches the pattern rather than the validity, so a fake credential
  written as a literal blocks a push exactly as a real one would — and the fix
  at that point is a history rewrite rather than a commit.
- `.gitattributes` pinning LF line endings. This repository is edited from
  Windows, where a shell script that silently acquires CRLF fails on Linux with
  an error that blames the interpreter rather than the newline.
- PEP 561 `py.typed` markers on every module, so downstream consumers get our
  type information instead of falling back to `Any`.
- Contributor scaffolding: code of conduct, pull request template with an
  invariant checklist, structured issue forms, `CODEOWNERS`, Dependabot, an
  `.editorconfig`, and a pre-commit configuration running the fast half of
  `make check`.

### Changed
- Credential-shaped test fixtures in the redaction suite are assembled at
  runtime from fragments instead of being written as literals, so the suite can
  still exercise Slack, GitHub, OpenAI-style, JWT and PEM shapes without the
  repository carrying a string a scanner will reject.
- The version is declared once, in `pyproject.toml`, and read back from
  distribution metadata. Previously four components carried their own literal
  and could have disagreed after a release.

## [0.0.1] — 2026-09-06

Milestone **M0 — verifier foundation**. The first release that can prove or
disprove the product thesis: a contract, evidence, and a deterministic verdict.

### Added

**Schema** (`verity_schema`)
- `OutcomeContract`, `WorkGraph`, `Trace`, `EvidenceRecord` and
  `VerificationReport` as separate, independently versioned artifacts.
- Four verdicts — `PASS`, `FAIL`, `DRIFT`, `INCONCLUSIVE` — with stable exit codes.
- Epistemic labels (`OBSERVED` / `INFERRED` / `RECOMMENDED`) and assertion
  strength (`STRONG` / `WEAK` / `INVALID`) as first-class schema concepts.
- A `model_calls` budget that must be zero, validated at load time.

**Verifier** (`verity_verifier`)
- A hand-written lexer and recursive-descent parser for the assertion
  expression language. No `eval`, no `exec`, no path to the interpreter.
- Static type checking: undefined facts, unknown functions and malformed
  expressions are compile-time errors.
- Strength derivation from the sources an assertion actually reads, with a
  declared strength that disagrees treated as an error.
- Fact resolution across connectors, documents and traces, in declaration
  order, with templated references between facts.
- Independent-channel detection, so reading a value back through the surface
  that wrote it is recorded rather than assumed away.
- First-divergence localization that separates the first failing assertion
  from the first environment change.

**Evidence** (`verity_evidence`)
- Content-addressed, append-only storage with canonical JSON.
- Merkle-rooted manifests and portable bundles; tampering with an object or
  with the manifest fails verification.
- A fail-closed redaction pipeline that runs before anything is written.

**Extraction** (`verity_extract`)
- Deterministic PDF field extraction with page and bounding-box provenance,
  using label proximity and a layout-aware fallback. No vector search, no RAG.

**Connectors** (`verity_connectors`)
- A read-only connector protocol, an HTTP JSON connector, a document fetcher,
  and cassette record/replay for hermetic CI.

**Sandbox** (`verity_sandbox`)
- A deterministic accounts-payable fixture exposing the same records over both
  a JSON API and an HTML UI.
- Byte-identical invoice PDF generation in two templates.
- Eight exactly reversible perturbations with recorded ground truth.

**CLI** (`verity_cli`)
- `verity verify`, `verity eval`, `verity lint`, `verity doctor`.
- Human-readable output, JSON, JUnit XML, GitHub annotations anchored to the
  contract line, markdown summaries and evidence bundles.

**Repository**
- 213 tests across unit, integration, determinism and security suites.
- `import-linter` contracts enforcing that the verifier cannot reach an
  executor — declared before those packages exist.
- CI covering lint, strict typing, boundaries, tests, determinism, security,
  ground truth, and a timed five-minute quickstart.

### Not included

Capture, compilation, the execution runtime, trace adapters for third-party
runtimes, scheduled canaries, the web application and the web site. See
`docs/architecture/overview.md`.

[Unreleased]: https://github.com/akashdeb2105-art/Verity/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/akashdeb2105-art/Verity/releases/tag/v0.0.1
