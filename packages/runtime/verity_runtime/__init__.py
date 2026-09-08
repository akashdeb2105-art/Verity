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
from .audit import GENESIS, AuditEntry, AuditLog, AuditVerification
from .audit import verify as verify_audit
from .control import Budget, FileKillSwitch, KillSwitch, NeverPulled, Stoppable
from .execute import (
    ExecutionError,
    PendingWrite,
    RunOptions,
    execute,
    interpolate_inputs,
    pending_writes,
)
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
    TIER_BROWSER,
    TIER_RECORDED,
    AlwaysPassGate,
    BrowserDriver,
    BrowserObservation,
    ClosedGate,
    GateResult,
    GateVerdict,
    RecordedNoOpDriver,
    VerificationGate,
)
from .report import RunOutcome, RunReport, StepResult
from .runrecord import (
    RunDiff,
    RunDifference,
    RunRecord,
    diff_runs,
    read_run_record,
    record_from_report,
    write_run_record,
)

__all__ = [
    "AMOUNT_FIELDS",
    "CONSEQUENTIAL",
    "GENESIS",
    "TIER_BROWSER",
    "TIER_RECORDED",
    "VERB_FLOOR",
    "AlwaysPassGate",
    "Approval",
    "ApprovalCheck",
    "ApprovalStore",
    "Assessment",
    "AuditEntry",
    "AuditLog",
    "AuditVerification",
    "BrowserDriver",
    "BrowserObservation",
    "Budget",
    "ClosedGate",
    "Decision",
    "ExecutionError",
    "FileKillSwitch",
    "GateResult",
    "GateVerdict",
    "InMemoryApprovalStore",
    "KillSwitch",
    "NeverPulled",
    "NoApprovals",
    "PendingWrite",
    "Plan",
    "PlanError",
    "Policy",
    "PolicyError",
    "RecordedNoOpDriver",
    "Requirement",
    "RunDiff",
    "RunDifference",
    "RunOptions",
    "RunOutcome",
    "RunRecord",
    "RunReport",
    "Signal",
    "Step",
    "StepResult",
    "Stoppable",
    "VerificationGate",
    "check_approval",
    "classify",
    "diff_runs",
    "execute",
    "interpolate_inputs",
    "is_consequential",
    "pending_writes",
    "plan",
    "read_run_record",
    "record_from_report",
    "verify_audit",
    "write_run_record",
]
