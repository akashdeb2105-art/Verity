"""The WorkGraph: how a workflow executes.

Defined in M0 so the format is stable and the three artifacts stay visibly
separate, but not yet produced by anything -- the compiler and the runtime
arrive in later milestones. Nothing in the verifier imports this module, and
import-linter enforces that the reverse is also true.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import SCHEMA_VERSION, Label, RiskLevel

ActionType = Literal[
    "NAVIGATE", "CLICK", "TYPE", "SELECT", "EXTRACT", "SEARCH", "COMPARE",
    "TRANSFORM", "CALL_API", "CREATE_RECORD", "UPDATE_RECORD", "SEND_MESSAGE",
    "VERIFY", "ASK_HUMAN",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class VariableSpec(_Frozen):
    name: str
    type: str
    source_kind: Literal["dom", "pdf", "api", "const", "input"]
    source_ref: str = ""
    label: Label = Label.INFERRED
    confidence: float = 1.0
    required: bool = True


class RetryPolicy(_Frozen):
    max: int = 0
    backoff_ms: int = 500


class WriteSpec(_Frozen):
    """Where a consequential node writes, and exactly what it sends.

    Typed rather than a free dictionary, for one reason: this is the object a
    person approves. An approval is later bound to a hash of the resolved
    payload, so a payload that could contain anything would make the approval
    meaningless -- you cannot consent to a shape you cannot see.

    ``payload`` values may interpolate ``{{ inputs.name }}`` and nothing else.
    """

    connector: str
    resource: str
    payload: dict[str, str] = Field(default_factory=dict)


class Node(_Frozen):
    id: str
    type: ActionType
    label: str = ""
    intent: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    postcondition_refs: list[str] = Field(default_factory=list)
    """Assertion ids in the associated Outcome Contract. The graph *references*
    the contract; it does not contain it."""

    evidence_refs: list[str] = Field(default_factory=list)
    execution_strategies: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    risk: RiskLevel = RiskLevel.LOW
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_ms: int = 15_000
    approval_required: bool = False
    write: WriteSpec | None = None
    """Set on nodes that change the world. Absent on every node that only reads,
    which is what lets a runtime tell the two apart without guessing."""


class Edge(_Frozen):
    from_: str = Field(alias="from")
    to: str
    condition: str | None = None
    priority: int = 0
    confidence: float = 1.0
    exception_of: str | None = None
    """Set when this branch exists because of a learned exception, so the graph
    can say *this path exists because of incident N*."""


class WorkGraph(_Frozen):
    api_version: str = Field(default=SCHEMA_VERSION, alias="apiVersion")
    kind: str = "WorkGraph"
    name: str
    version: str = "0.1.0"
    contract_ref: str | None = None
    variables: list[VariableSpec] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
