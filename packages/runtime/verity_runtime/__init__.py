"""Executing a WorkGraph, and stopping before it does something wrong.

This package has no import edge to the verifier, in either direction. The
portability claim -- that a contract can check a run produced by somebody
else's agent -- only holds if verification is independent of execution, and a
rule that is merely intended is a rule that erodes. ``import-linter`` enforces
it.

The runtime still has to consult a verifier before a consequential write, so
it declares the shape of the thing it consults (:mod:`verity_runtime.ports`)
and never names one. The CLI is the only place the two meet.
"""

from .approval import (
    Approval,
    ApprovalCheck,
    ApprovalStore,
    InMemoryApprovalStore,
    NoApprovals,
    check_approval,
)
from .execute import ExecutionError, PendingWrite, RunOptions, execute, pending_writes
from .plan import CONSEQUENTIAL, Plan, PlanError, Step, is_consequential, plan
from .policy import (
    AMOUNT_FIELDS,
    VERB_FLOOR,
    Assessment,
    Decision,
    Policy,
    PolicyError,
    Requirement,
    Signal,
    classify,
)
from .ports import (
    AlwaysPassGate,
    ClosedGate,
    GateResult,
    GateVerdict,
    VerificationGate,
)
from .report import RunOutcome, RunReport, StepResult

__all__ = [
    "AMOUNT_FIELDS",
    "CONSEQUENTIAL",
    "VERB_FLOOR",
    "AlwaysPassGate",
    "Approval",
    "ApprovalCheck",
    "ApprovalStore",
    "Assessment",
    "ClosedGate",
    "Decision",
    "ExecutionError",
    "GateResult",
    "GateVerdict",
    "InMemoryApprovalStore",
    "NoApprovals",
    "PendingWrite",
    "Plan",
    "PlanError",
    "Policy",
    "PolicyError",
    "Requirement",
    "RunOptions",
    "RunOutcome",
    "RunReport",
    "Signal",
    "Step",
    "StepResult",
    "VerificationGate",
    "check_approval",
    "classify",
    "execute",
    "is_consequential",
    "pending_writes",
    "plan",
]
