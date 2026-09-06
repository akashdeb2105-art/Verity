"""A small, portable trace format: ``verity-trace/v1``.

Deliberately not coupled to any vendor. Adapters translate a runtime's native
output into this shape; the verifier only ever sees this shape, which is what
makes it possible to verify a workflow executed by Playwright, Browser Use,
Stagehand, Skyvern, an agent SDK or hand-written code.

``status_reported`` is captured and never trusted. It is the field the product
exists to contradict.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import SCHEMA_VERSION


class TraceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str | None = None
    name: str | None = None
    testid: str | None = None
    selectors: list[str] = Field(default_factory=list)


class TraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int
    ts: datetime | None = None
    action: str
    url: str | None = None
    target: TraceTarget | None = None
    dom_hash: str | None = None
    """Normalized structural hash of the page: tag+role skeleton, text stripped.
    Cheap, and it is what makes environment-change detection possible with no
    model in the loop."""

    screenshot_ref: str | None = None
    result: str = "ok"
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class TraceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: str
    sha256: str | None = None
    path: str | None = None


class RuntimeInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str = ""
    adapter: str = ""


class Trace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    api_version: str = Field(default=SCHEMA_VERSION, alias="apiVersion")
    kind: str = "Trace"
    trace_version: str = "1.0"
    run_id: str
    runtime: RuntimeInfo
    started_at: datetime | None = None
    finished_at: datetime | None = None

    status_reported: str = "unknown"
    """What the runtime claimed. Recorded for the report's most important line:
    ``runtime said: success / verifier says: FAIL``. Never used in evaluation."""

    inputs: dict[str, Any] = Field(default_factory=dict)
    steps: list[TraceStep] = Field(default_factory=list)
    artifacts: list[TraceArtifact] = Field(default_factory=list)

    def dom_hash_sequence(self) -> list[tuple[int, str]]:
        return [(s.seq, s.dom_hash) for s in self.steps if s.dom_hash]
