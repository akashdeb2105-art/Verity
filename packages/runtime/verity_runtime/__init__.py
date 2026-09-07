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

from .execute import ExecutionError, RunOptions, execute
from .plan import CONSEQUENTIAL, Plan, PlanError, Step, is_consequential, plan
from .ports import (
    AlwaysPassGate,
    ClosedGate,
    GateResult,
    GateVerdict,
    VerificationGate,
)
from .report import RunOutcome, RunReport, StepResult

__all__ = [
    "CONSEQUENTIAL",
    "AlwaysPassGate",
    "ClosedGate",
    "ExecutionError",
    "GateResult",
    "GateVerdict",
    "Plan",
    "PlanError",
    "RunOptions",
    "RunOutcome",
    "RunReport",
    "Step",
    "StepResult",
    "VerificationGate",
    "execute",
    "is_consequential",
    "plan",
]
