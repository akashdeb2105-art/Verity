"""The raw capture event model.

A capture session is a list of these. They are deliberately *raw*: one event
per thing the person actually did, with no interpretation. Turning them into
semantic steps is the compiler's job, and keeping the two apart means a
recording made today still compiles correctly after the compiler improves.

Nothing here imports a browser. The browser driver produces these; the tests
build them by hand. That split is what lets the whole compiler be proven
without a browser.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from verity_schema import SCHEMA_VERSION


class RawEventKind(str, Enum):
    """What physically happened. Not what it meant."""

    NAVIGATE = "navigate"
    CLICK = "click"
    INPUT = "input"
    SELECT = "select"
    SUBMIT = "submit"
    KEY = "key"
    DOWNLOAD = "download"
    OPEN_DOCUMENT = "open_document"
    NETWORK = "network"
    """An API response the page received. Recorded because it is often the
    cleanest evidence of what a screen actually showed."""

    SESSION_START = "session_start"
    SESSION_END = "session_end"


class ElementRef(BaseModel):
    """How to find an element again, best hint first.

    Recorded selectors are the *last* resort, not the first: a role and an
    accessible name survive a redesign that a CSS path does not.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str | None = None
    name: str | None = None
    testid: str | None = None
    label: str | None = None
    tag: str | None = None
    input_type: str | None = None
    css: list[str] = Field(default_factory=list)
    xpath: str | None = None
    text: str | None = None
    href: str | None = None
    """Where a link pointed, resolved absolute. Kept so that a document the
    person opened can be fetched and stored with the recording rather than
    left as a filename nobody can read."""

    @property
    def best_hint(self) -> str:
        for candidate in (
            f"testid={self.testid}" if self.testid else None,
            f"{self.role}[name={self.name!r}]" if self.role and self.name else None,
            f"label={self.label!r}" if self.label else None,
            self.css[0] if self.css else None,
            self.xpath,
        ):
            if candidate:
                return candidate
        return self.tag or "unknown"


class RawEvent(BaseModel):
    """One recorded interaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int
    kind: RawEventKind
    ts: datetime | None = None
    url: str | None = None
    title: str | None = None
    element: ElementRef | None = None

    value: str | None = None
    """What the person typed or chose. Redacted at source when sensitive; the
    raw value never reaches this field for a password or a secret."""

    value_redacted: bool = False

    visible_text: dict[str, str] = Field(default_factory=dict)
    """Values the screen was showing, keyed by a stable identifier. This is the
    raw material for spotting that the person was comparing two numbers."""

    dom_hash: str | None = None
    """Structural hash of the page: tags and roles, text stripped. Cheap, and it
    is what later tells us the page changed without a model in the loop."""

    document_ref: str | None = None
    network: dict[str, Any] = Field(default_factory=dict)
    screenshot_ref: str | None = None
    note: str = ""


class SessionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    start_url: str = ""
    browser: str = ""
    capture_version: str = "1.0"
    redaction_applied: bool = True
    """Always true. Redaction is not optional and cannot be switched off."""


#: What a link has to look like for the recorder to treat it as a document
#: worth keeping. Shared with the compiler, which must agree about what
#: counts as opening a document.
DOCUMENT_URL = re.compile(r"\.(pdf|docx?|xlsx?|csv|png|jpe?g|tiff?)(\?|$)", re.I)


class Attachment(BaseModel):
    """A document the person opened, stored with the recording.

    Content-addressed. A recording either carries the bytes that were read or
    it does not, and the compiler can tell which -- so a contract never claims
    to have checked a document that was never fetched.

    This is the independent channel that makes the whole product work: the
    invoice is not the ERP screen, so comparing them is real evidence rather
    than a system agreeing with itself.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str
    url: str
    media_type: str = ""
    byte_count: int = 0
    filename: str = ""
    source_event: int | None = None
    #: Set when the fetch failed. The recording says so instead of pretending.
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.byte_count > 0


class CaptureSession(BaseModel):
    """A complete recording. This is what `verity teach` writes to disk."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    api_version: str = Field(default=SCHEMA_VERSION, alias="apiVersion")
    kind: str = "CaptureSession"
    metadata: SessionMetadata
    events: list[RawEvent] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)

    @property
    def interactions(self) -> list[RawEvent]:
        """Events the person caused, excluding bookkeeping."""
        skip = {RawEventKind.SESSION_START, RawEventKind.SESSION_END, RawEventKind.NETWORK}
        return [e for e in self.events if e.kind not in skip]

    def fingerprint(self) -> str:
        """Stable content hash, so a recording can be compared across runs."""
        import hashlib
        import json

        payload = json.dumps(
            self.model_dump(mode="json", by_alias=True, exclude={"metadata"}),
            sort_keys=True, separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
