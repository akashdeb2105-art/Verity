"""Recording a teaching session, with secrets removed at the point of capture.

This package records what a person did. It never judges whether they were
right -- that is the verifier's job, and an import-linter contract keeps the
two apart.

Playwright is imported lazily, so the event model, the redaction layer and the
session reader all work on a machine with no browser installed.
"""

from .events import (
    DOCUMENT_URL,
    Attachment,
    CaptureSession,
    ElementRef,
    RawEvent,
    RawEventKind,
    SessionMetadata,
)
from .recorder import (
    BrowserRecorder,
    CaptureError,
    RecorderOptions,
    attachments_dir,
    read_attachment,
    read_session,
    write_session,
)
from .redaction import (
    RedactionReason,
    RedactionResult,
    classify_field,
    redact_value,
    redact_visible_text,
)

__all__ = [
    "DOCUMENT_URL", "Attachment", "BrowserRecorder", "CaptureError", "CaptureSession",
    "ElementRef", "RawEvent", "RawEventKind", "RecorderOptions", "RedactionReason",
    "RedactionResult", "SessionMetadata", "attachments_dir", "classify_field",
    "read_attachment", "read_session", "redact_value", "redact_visible_text",
    "write_session",
]
