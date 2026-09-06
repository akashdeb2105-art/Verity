"""The Outcome Contract: what must be true after a workflow runs.

The Outcome Contract is a first-class, independently versioned artifact. It is
deliberately separate from the WorkGraph (how a workflow runs) and from
Evidence (how we know something is true). A contract can be evaluated with no
WorkGraph at all, which is what makes Verity able to verify automation it did
not author and does not run.

Contracts are YAML, live in the user's repository, and are reviewed in pull
requests like any other specification.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import SCHEMA_VERSION, RiskLevel, Severity, SourceKind, Strength


class _Strict(BaseModel):
    """Base for every contract model. Unknown keys are errors, not warnings.

    A typo in a contract must never silently produce a contract that passes
    for the wrong reason.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class ContractMetadata(_Strict):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = ""
    owner: str = ""
    risk: RiskLevel = RiskLevel.LOW


class InputSpec(_Strict):
    type: Literal["string", "decimal", "integer", "boolean", "datetime"]
    required: bool = True
    description: str = ""


class SourceSpec(_Strict):
    """A named *role* a fact can be read from -- not a connector instance.

    Bindings from role to concrete connector are supplied at verification time.
    That indirection is what keeps a contract portable between a sandbox, a
    staging system and production without editing the contract.
    """

    kind: SourceKind
    capability: str | None = None
    """For connector sources: the capability required, e.g. ``purchase_orders``."""

    format: str | None = None
    """For document sources: e.g. ``pdf``."""

    optional: bool = False

    @model_validator(mode="after")
    def _check_shape(self) -> SourceSpec:
        if self.kind is SourceKind.CONNECTOR and not self.capability:
            raise ValueError("connector sources require a 'capability'")
        if self.kind is SourceKind.DOCUMENT and not self.format:
            raise ValueError("document sources require a 'format'")
        return self


class ReadSpec(_Strict):
    resource: str
    key: str | None = None
    query: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _key_or_query(self) -> ReadSpec:
        if not self.key and not self.query:
            raise ValueError("a read needs either 'key' or 'query'")
        if self.key and self.query:
            raise ValueError("a read takes 'key' or 'query', not both")
        return self


class ExtractFieldSpec(_Strict):
    type: Literal["string", "decimal", "integer", "date"]
    hint: str = ""
    """Human-readable description of where the field lives. Never sent to a model
    at verification time -- extraction at verify time is deterministic only."""
    required: bool = True
    pattern: str | None = None
    expect_anchor: str | None = None
    """The label this value is expected to sit next to. If the extractor finds
    the value under a different anchor the document template has moved: the
    outcome may still be correct, but the environment changed, and that is
    reported as DRIFT rather than silently accepted."""


class FactSpec(_Strict):
    """One read, from one source. Facts resolve before assertions evaluate.

    Facts are OBSERVED by construction: each one produces an evidence record
    tying the value to the artifact it came from.
    """

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    source: str
    read: ReadSpec | None = None
    extract: dict[str, ExtractFieldSpec] = Field(default_factory=dict)
    select: list[str] = Field(default_factory=list)
    expect_cardinality: int | None = None
    """Expected number of matching records. A mismatch is a finding
    (``INCONCLUSIVE``), never a crash and never a silent pass."""

    document: str | None = None
    """For document sources: path or input reference to the file."""

    @model_validator(mode="after")
    def _read_or_extract(self) -> FactSpec:
        if not self.read and not self.extract:
            raise ValueError(f"fact '{self.id}' needs either 'read' or 'extract'")
        return self


class Assertion(_Strict):
    """A typed, deterministic claim about business state.

    ``strength`` may be written in the file for readability but is always
    recomputed by the typechecker from the facts the expression references.
    A declared strength that disagrees with the computed one is an error.
    """

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    assert_: str = Field(alias="assert", min_length=1)
    severity: Severity = Severity.BLOCKING
    strength: Strength | None = None
    channel: Literal["independent", "same"] | None = None
    because: str = ""
    """Why this must be true, in the words of whoever owns the process. This is
    what makes a failed assertion legible to someone who did not write it."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Budgets(_Strict):
    duration_ms: int = 120_000
    actions: int = 100
    model_calls: int = 0
    """Enforced, not advisory. Verification is deterministic; the only correct
    value in V1 is zero, and a non-zero value is rejected."""
    cost_usd: float = 0.0

    @field_validator("model_calls")
    @classmethod
    def _no_models(cls, v: int) -> int:
        if v != 0:
            raise ValueError(
                "verification is deterministic in V1: 'model_calls' must be 0"
            )
        return v


class FailurePolicy(_Strict):
    halt: bool = True
    require_human: bool = True
    notify: list[str] = Field(default_factory=list)


class OutcomeContract(_Strict):
    api_version: str = Field(default=SCHEMA_VERSION, alias="apiVersion")
    kind: Literal["OutcomeContract"] = "OutcomeContract"
    metadata: ContractMetadata
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    sources: dict[str, SourceSpec] = Field(default_factory=dict)
    facts: list[FactSpec] = Field(default_factory=list)
    expected: list[Assertion] = Field(default_factory=list)
    forbidden: list[Assertion] = Field(default_factory=list)
    budgets: Budgets = Field(default_factory=Budgets)
    on_failure: FailurePolicy = Field(default_factory=FailurePolicy)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _unique_ids(self) -> OutcomeContract:
        fact_ids = [f.id for f in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("duplicate fact ids")
        assertion_ids = [a.id for a in self.expected] + [a.id for a in self.forbidden]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("duplicate assertion ids")
        for fact in self.facts:
            if fact.source not in self.sources:
                raise ValueError(
                    f"fact '{fact.id}' references undefined source '{fact.source}'"
                )
        return self

    @property
    def all_assertions(self) -> list[Assertion]:
        return [*self.expected, *self.forbidden]

    def fact(self, fact_id: str) -> FactSpec | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def fingerprint(self) -> str:
        """Stable content hash. Two contracts with the same fingerprint are the
        same contract, regardless of key ordering or comment changes."""
        import hashlib
        import json

        payload: Any = self.model_dump(mode="json", by_alias=True)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
