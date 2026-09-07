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

from .audit import AuditLog
from .audit import verify as verify_audit
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
    risk: str = ""
    """Effective risk level, after the policy declined to take the graph's word for it."""

    requirement: str = ""
    """What the policy required of this step: ALLOW, REQUIRE_APPROVAL or FORBID."""

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

    halted_by: str = ""
    """Which control stopped it: policy, gate, approval, kill switch or budget.

    A run can be stopped for several different reasons and they are not
    interchangeable. "Verification failed" and "nobody approved this" call for
    different actions from whoever reads the report.
    """

    halt_reason: str = ""
    """The stop, in a sentence, from whichever control did the stopping."""

    policy_name: str = ""

    not_performed: list[str] = field(default_factory=list)
    """Consequential actions that were never carried out, described in words."""

    inputs: dict[str, str] = field(default_factory=dict)
    model_calls: int = 0
    cost_usd: float = 0.0
    audit: AuditLog = field(default_factory=AuditLog)
    """Every decision in the run, chained. See :mod:`verity_runtime.audit` for
    what a hash chain does and does not prove."""

    @property
    def runtime_said(self) -> str:
        """What an ordinary agent would have reported.

        Every step it attempted succeeded, so an executor with no verifier
        would say the task is done -- and would be wrong. This field exists to
        be printed next to the next one.

        A run the policy refused before anything was attempted says NOT
        STARTED rather than DONE. Claiming an agent reported success for work
        it never began would be the same kind of lie the rest of this file
        exists to prevent, just told in our favour.
        """
        if any(s.status == "error" for s in self.steps):
            return "ERROR"
        if not any(s.status == "ok" for s in self.steps):
            return "NOT STARTED"
        return "DONE"

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

    @property
    def audit_head(self) -> str:
        """The value an external anchor would record to detect a later rewrite."""
        return self.audit.head

    @property
    def audit_intact(self) -> bool:
        return verify_audit(self.audit).intact

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph": self.graph_name,
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "outcome": self.outcome.value,
            "runtime_said": self.runtime_said,
            "verifier_says": self.verifier_says,
            "halted_at": self.halted_at,
            "halted_by": self.halted_by,
            "halt_reason": self.halt_reason,
            "policy": self.policy_name,
            "not_performed": list(self.not_performed),
            "node_sequence": self.node_sequence,
            "writes_performed": [s.node_id for s in self.writes_performed],
            "budgets": {"model_calls": self.model_calls, "cost_usd": self.cost_usd},
            "audit": {"entries": len(self.audit), "head": self.audit_head,
                      "intact": self.audit_intact},
            "steps": [
                {
                    "index": s.index, "node": s.node_id, "action": s.action,
                    "status": s.status, "consequential": s.consequential,
                    "performed_write": s.performed_write,
                    "write_digest": s.write_digest, "error": s.error,
                    "risk": s.risk, "requirement": s.requirement,
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
