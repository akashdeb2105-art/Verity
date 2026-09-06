"""Locating where things first went wrong.

Two questions, deliberately answered separately:

* **First assertion failure** -- where the business outcome first stopped being
  true.
* **First environment change** -- where the world the workflow ran in first
  stopped matching what it ran in before.

They are frequently different steps. The DOM can change at step 4 while the
assertion fails at step 7, and reporting only the second sends a person to
debug the wrong place.
"""

from __future__ import annotations

from dataclasses import dataclass

from verity_schema import AssertionResult, Divergence, FactResult, Trace


@dataclass(frozen=True)
class EnvironmentChange:
    kind: str
    where: str
    step: int | None = None
    detail: str = ""


def detect_environment_changes(
    trace: Trace | None,
    baseline: Trace | None,
    drift_notes: list[str],
    weak_failures: list[AssertionResult],
) -> list[EnvironmentChange]:
    """Everything that suggests the environment moved, earliest first."""
    changes: list[EnvironmentChange] = []

    if trace is not None and baseline is not None:
        changes.extend(_dom_hash_changes(trace, baseline))

    changes.extend(
        EnvironmentChange(kind="document_template", where=note.split(".")[0], detail=note)
        for note in drift_notes
    )

    changes.extend(
        EnvironmentChange(
            kind="ui_surface", where=result.id,
            detail=f"weak assertion '{result.id}' no longer holds while the business "
                   "outcome is unchanged",
        )
        for result in weak_failures
    )

    return changes


def _dom_hash_changes(trace: Trace, baseline: Trace) -> list[EnvironmentChange]:
    """Compare structural page hashes step by step against a golden run."""
    current = dict(trace.dom_hash_sequence())
    previous = dict(baseline.dom_hash_sequence())
    changes: list[EnvironmentChange] = []
    for seq in sorted(set(current) & set(previous)):
        if current[seq] != previous[seq]:
            step = next((s for s in trace.steps if s.seq == seq), None)
            changes.append(
                EnvironmentChange(
                    kind="dom_structure",
                    where=(step.url or f"step {seq}") if step else f"step {seq}",
                    step=seq,
                    detail=f"page structure at step {seq} differs from the golden run",
                )
            )
    return changes


def localize(
    assertions: list[AssertionResult],
    facts: list[FactResult],
    changes: list[EnvironmentChange],
) -> Divergence:
    """Build the divergence report from ordered results."""
    first_failure: str | None = None
    first_index: int | None = None

    for index, result in enumerate(assertions):
        if result.passed is False:
            first_failure = result.id
            first_index = index
            break

    # An unresolved fact precedes every assertion that reads it: facts resolve
    # first, so an unresolvable fact is the earliest thing that went wrong.
    unresolved = next((f for f in facts if not f.resolved), None)

    first_change = changes[0] if changes else None

    explanation = _explain(first_failure, unresolved, first_change)

    return Divergence(
        first_assertion_failure=first_failure,
        first_assertion_index=first_index,
        first_environment_change=first_change.where if first_change else None,
        first_environment_step=first_change.step if first_change else None,
        environment_change_kind=first_change.kind if first_change else None,
        explanation=explanation,
    )


def _explain(
    first_failure: str | None,
    unresolved: FactResult | None,
    change: EnvironmentChange | None,
) -> str:
    if unresolved is not None:
        return (
            f"fact '{unresolved.id}' could not be resolved ({unresolved.error}), so the "
            "assertions that read it were not evaluated"
        )
    if first_failure and change:
        return (
            f"'{first_failure}' is the first failing assertion, but the environment had "
            f"already changed ({change.kind} at {change.where}) -- start there"
        )
    if first_failure:
        return f"'{first_failure}' is the first assertion that does not hold"
    if change:
        return (
            f"every assertion holds, but the environment changed "
            f"({change.kind} at {change.where})"
        )
    return "no divergence detected"
