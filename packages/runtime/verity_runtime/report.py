"""What a run produced, including the thing it was stopped from producing.

The shape of this report is the product's argument. ``runtime_said`` and
``verifier_says`` sit next to each other as separate fields, because the whole
point is that they can differ -- and when they do, the second one is the one
that counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .ports import GateResult, GateVerdict


class RunOutcome(str, Enum):
    """How a run ended."""

    COMPLETED = "COMPLETED"
    """Every step ran. Any consequential write was verified first."""

    HALTED = "HALTED"
    """Stopped deliberately, before doing something that could not be undone."""

    FAILED = "FAILED"
    """A step could not be carried out. Not a verdict about the business outcome."""

    @property
    def exit_code(self) -> int:
        return {"COMPLETED": 0, "HALTED": 2, "FAILED": 1}[self.value]


@dataclass(frozen=True)
class StepResult:
    """One step, and what it did or did not do."""

    index: int
    node_id: str
    action: str
    label: str = ""
    status: str = "ok"
    """ok | halted | skipped | error"""

    consequential: bool = False
    performed_write: bool = False
    write_digest: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: int = 0

    @property
    def reached(self) -> bool:
        return self.status in ("ok", "halted")


@dataclass
class RunReport:
    """The record of one execution.

    Not a log. A log says what happened; this says what was claimed, what was
    checked, and what was refused.
    """

    graph_name: str
    run_id: str
    dry_run: bool
    steps: list[StepResult] = field(default_factory=list)
    outcome: RunOutcome = RunOutcome.COMPLETED
    gate: GateResult | None = None
    halted_at: str = ""
    """The node the run stopped *before*. Empty when nothing was refused."""

    not_performed: list[str] = field(default_factory=list)
    """Consequential actions that were never carried out, described in words."""

    inputs: dict[str, str] = field(default_factory=dict)
    model_calls: int = 0
    cost_usd: float = 0.0

    @property
    def runtime_said(self) -> str:
        """What an ordinary agent would have reported.

        Every step it attempted succeeded, so an executor with no verifier
        would say the task is done -- and would be wrong. This field exists to
        be printed next to the next one.
        """
        return "DONE" if not any(s.status == "error" for s in self.steps) else "ERROR"

    @property
    def verifier_says(self) -> str:
        return str(self.gate.verdict.value) if self.gate else GateVerdict.INCONCLUSIVE.value

    @property
    def contradicted(self) -> bool:
        """True when the runtime would have claimed success and verification disagreed."""
        return self.runtime_said == "DONE" and self.verifier_says != GateVerdict.PASS.value

    @property
    def writes_performed(self) -> list[StepResult]:
        return [s for s in self.steps if s.performed_write]

    @property
    def node_sequence(self) -> list[str]:
        """The steps that were reached, in order. Two replays must match exactly."""
        return [s.node_id for s in self.steps if s.reached]

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph": self.graph_name,
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "outcome": self.outcome.value,
            "runtime_said": self.runtime_said,
            "verifier_says": self.verifier_says,
            "halted_at": self.halted_at,
            "not_performed": list(self.not_performed),
            "node_sequence": self.node_sequence,
            "writes_performed": [s.node_id for s in self.writes_performed],
            "budgets": {"model_calls": self.model_calls, "cost_usd": self.cost_usd},
            "steps": [
                {
                    "index": s.index, "node": s.node_id, "action": s.action,
                    "status": s.status, "consequential": s.consequential,
                    "performed_write": s.performed_write,
                    "write_digest": s.write_digest, "error": s.error,
                }
                for s in self.steps
            ],
            "gate": None if self.gate is None else {
                "verdict": self.gate.verdict.value,
                "reason": self.gate.reason,
                "failed_assertions": list(self.gate.failed_assertions),
                "first_divergence": self.gate.first_divergence,
                "evidence_ref": self.gate.evidence_ref,
            },
        }
