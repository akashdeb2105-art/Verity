"""The verification report: the artifact everything else in the product renders."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import SCHEMA_VERSION, Channel, Label, Severity, Strength, Verdict


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class FactResult(_Frozen):
    id: str
    resolved: bool
    label: Label | None = None
    value: Any = None
    evidence_refs: list[str] = Field(default_factory=list)
    source: str = ""
    cardinality: int | None = None
    error: str | None = None
    """Why the fact could not be resolved. Present exactly when ``resolved`` is
    false, and it is what turns an unresolvable fact into an honest
    ``INCONCLUSIVE`` rather than a silent pass."""

    duration_ms: int = 0


class AssertionResult(_Frozen):
    id: str
    expression: str
    passed: bool | None
    """``None`` means it could not be evaluated -- an unresolved fact, not a
    failure. Rendered as 'not reached', never as passing and never as failing."""

    severity: Severity
    strength: Strength
    channel: Channel = Channel.UNKNOWN
    because: str = ""
    expected_repr: str | None = None
    observed_repr: str | None = None
    delta_repr: str | None = None
    """Human-readable difference, e.g. ``+133,200.00``. The number a person
    actually reacts to."""

    evidence_refs: list[str] = Field(default_factory=list)
    error: str | None = None
    is_forbidden: bool = False


class Divergence(_Frozen):
    """Where things first went wrong. Two different questions, both answered.

    The DOM can change at step 4 while the business assertion fails at step 7.
    Reporting only the second sends people to debug the wrong step.
    """

    first_assertion_failure: str | None = None
    first_assertion_index: int | None = None
    first_environment_change: str | None = None
    first_environment_step: int | None = None
    environment_change_kind: str | None = None
    explanation: str = ""


class BudgetReport(_Frozen):
    duration_ms: int = 0
    duration_budget_ms: int = 0
    model_calls: int = 0
    model_calls_budget: int = 0
    cost_usd: float = 0.0
    exceeded: list[str] = Field(default_factory=list)


class VerificationReport(_Frozen):
    api_version: str = Field(default=SCHEMA_VERSION, alias="apiVersion")
    kind: str = "VerificationReport"

    verdict: Verdict
    contract_name: str
    contract_version: str
    contract_fingerprint: str

    run_id: str | None = None
    runtime_name: str | None = None
    status_reported: str | None = None
    """What the runtime claimed, for the report's headline contradiction."""

    started_at: datetime
    finished_at: datetime

    inputs: dict[str, Any] = Field(default_factory=dict)
    facts: list[FactResult] = Field(default_factory=list)
    assertions: list[AssertionResult] = Field(default_factory=list)
    divergence: Divergence = Field(default_factory=Divergence)
    budgets: BudgetReport = Field(default_factory=BudgetReport)
    evidence_manifest_ref: str | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def contradicts_runtime(self) -> bool:
        """True when the runtime claimed success and we disagree. The product."""
        claimed = (self.status_reported or "").lower()
        return claimed in {"success", "ok", "done", "passed"} and not self.verdict.is_success

    @property
    def blocking_failures(self) -> list[AssertionResult]:
        return [
            a for a in self.assertions
            if a.passed is False and a.severity is Severity.BLOCKING
        ]

    @property
    def unresolved_facts(self) -> list[FactResult]:
        return [f for f in self.facts if not f.resolved]
