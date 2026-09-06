# ADR-0008: Dependency policy

**Status:** accepted · **Date:** 2026-09-06

## Context

Dependency sprawl is how a small tool becomes unauditable, and this one sits on
a security boundary.

## Decision

Every dependency must be load-bearing, and its justification recorded.

### Runtime

| Package | Licence | Why |
| --- | --- | --- |
| `pydantic` | MIT | Typed artifact models with validation. The schema is the product; hand-rolling it would be worse and larger. |
| `PyYAML` | MIT | Contracts are git-friendly YAML. Used via `safe_load` only. |
| `httpx` | BSD-3 | Connector transport, with a client that can be injected for hermetic tests. |

### Optional

| Package | Extra | Licence | Why |
| --- | --- | --- | --- |
| `fastapi` / `uvicorn` | `sandbox` | MIT / BSD-3 | The fixture's API and UI. |
| `jinja2` | `sandbox` | BSD-3 | The sandbox's HTML surface — the independent channel. |
| `reportlab` | `sandbox` | BSD-3 | Byte-deterministic invoice PDFs via `invariant=1`. |
| `pdfplumber` | `extract` | MIT | Text and per-word bounding boxes, which the provenance model requires. |

**No LLM SDK appears anywhere**, by design. See
[ADR-0001](0001-deterministic-verification.md).

### Deliberately not used

- **A CLI framework** (`typer`, `click`) — `argparse` is in the standard
  library and does everything the CLI needs.
- **An expression library** — see [ADR-0005](0005-hand-written-expression-language.md).
- **An agent framework** (LangGraph, CrewAI) — the WorkGraph is the execution
  representation; a second graph abstraction would be duplicated state.
- **A vector store, chunker or reranker** — extraction is deterministic and
  rule-based. There is no RAG here.

## Before adding one

State what it is for and why the standard library or the current stack is not
enough; its licence; and how actively it is maintained. If the choice is
architectural, add a decision record.

## Consequences

More code we own — a CLI, an expression language, a merkle manifest. That is
the intended trade: this repository should be auditable by one person in an
afternoon.
