# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
