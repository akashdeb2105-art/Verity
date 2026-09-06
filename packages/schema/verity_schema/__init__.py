"""Verity artifact schemas.

Three separate artifacts, deliberately not collapsed into one object:

* :mod:`verity_schema.workgraph` -- how a workflow runs
* :mod:`verity_schema.contract`  -- what must be true afterwards
* :mod:`verity_schema.evidence`  -- how we know it is true

plus :mod:`verity_schema.trace` (a portable, vendor-neutral execution record)
and :mod:`verity_schema.report` (the result of a verification).

This package is a leaf: it imports nothing else from Verity, so any consumer
can adopt the format without inheriting a dependency on the verifier.
"""

from . import expr
from ._version import __version__, get_version
from .common import (
    SCHEMA_VERSION,
    Channel,
    EvidenceKind,
    Label,
    RiskLevel,
    Severity,
    SourceKind,
    Strength,
    Verdict,
    worst,
)
from .contract import (
    Assertion,
    Budgets,
    ContractMetadata,
    ExtractFieldSpec,
    FactSpec,
    FailurePolicy,
    InputSpec,
    OutcomeContract,
    ReadSpec,
    SourceSpec,
)
from .evidence import EvidenceManifest, EvidenceRecord, ManifestEntry, Provenance
from .report import (
    AssertionResult,
    BudgetReport,
    Divergence,
    FactResult,
    VerificationReport,
)
from .trace import RuntimeInfo, Trace, TraceArtifact, TraceStep, TraceTarget
from .workgraph import Edge, Node, RetryPolicy, VariableSpec, WorkGraph

__all__ = [
    "SCHEMA_VERSION",
    "Assertion",
    "AssertionResult",
    "BudgetReport",
    "Budgets",
    "Channel",
    "ContractMetadata",
    "Divergence",
    "Edge",
    "EvidenceKind",
    "EvidenceManifest",
    "EvidenceRecord",
    "ExtractFieldSpec",
    "FactResult",
    "FactSpec",
    "FailurePolicy",
    "InputSpec",
    "Label",
    "ManifestEntry",
    "Node",
    "OutcomeContract",
    "Provenance",
    "ReadSpec",
    "RetryPolicy",
    "RiskLevel",
    "RuntimeInfo",
    "Severity",
    "SourceKind",
    "SourceSpec",
    "Strength",
    "Trace",
    "TraceArtifact",
    "TraceStep",
    "TraceTarget",
    "VariableSpec",
    "Verdict",
    "VerificationReport",
    "WorkGraph",
    "__version__",
    "expr",
    "get_version",
    "worst",
]
