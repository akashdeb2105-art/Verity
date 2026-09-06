"""Evidence: how we know a fact is true.

Evidence records are immutable and content-addressed. An evidence *bundle* is
the portable output of one verification -- the report plus every artifact it
cites plus a manifest with a merkle root -- and it is what a human, an auditor,
or a future you actually needs three weeks later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import SCHEMA_VERSION, EvidenceKind, Label


class Provenance(BaseModel):
    """Where a value physically came from. Present on every document field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_ref: str
    """Connector+resource, or document sha256."""

    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    """(x0, top, x1, bottom) in PDF points, when the layout engine provides it.
    This is what lets a failure highlight the exact region of an invoice."""

    field: str | None = None
    method: str = ""
    """How the value was obtained, e.g. ``pdfplumber:label-proximity``."""

    confidence: float = 1.0


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    """Content address: ``ev_<first 16 hex of sha256>``."""

    kind: EvidenceKind
    label: Label
    sha256: str
    produced_by: str
    """Which resolver produced it, e.g. ``connector:po_system``."""

    produced_at: datetime
    provenance: Provenance | None = None
    redaction_applied: bool = False
    summary: str = ""
    """Short human-readable description. Never the full payload."""

    payload_ref: str | None = None
    """Relative path inside the evidence store. None for inline-only records."""


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    sha256: str
    bytes: int
    path: str


class EvidenceManifest(BaseModel):
    """Integrity information for a bundle. A tampered bundle fails verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_version: str = Field(default=SCHEMA_VERSION, alias="apiVersion")
    kind: str = "EvidenceManifest"
    created_at: datetime
    entries: list[ManifestEntry] = Field(default_factory=list)
    merkle_root: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
