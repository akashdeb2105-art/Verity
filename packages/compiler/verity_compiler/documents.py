"""Reading the documents a recording carried, so they can be checked.

A recording that only says "they opened INV-4471.pdf" supports no check at
all about what the invoice said -- and the invoice is the whole point. It is
the one source in an accounts-payable workflow that the ERP did not write, so
comparing it to the ERP is real evidence rather than a system agreeing with
itself.

Extraction here is deterministic: label proximity and layout, no model. That
matters because a fact only a model can produce is INFERRED, and an INFERRED
fact cannot satisfy a STRONG assertion. A model may later suggest *which*
comparisons are worth making; it never supplies the numbers being compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verity_capture import Attachment, CaptureSession, read_attachment

#: The fact root documents are read into. Matches the reference contract, so a
#: compiled draft and a hand-written one talk about the same things.
DOCUMENT_SYSTEM = "doc"

#: What to look for when nobody has said yet what the document is for.
#: Everything is optional: teaching must report what a document happens to
#: contain, not fail because a field a business form usually has is absent.
TEACHING_SPEC: dict[str, dict[str, Any]] = {
    "total": {"type": "decimal", "required": False},
    "number": {"type": "string", "required": False},
    "vendor": {"type": "string", "required": False},
    "po_ref": {"type": "string", "required": False},
    "date": {"type": "date", "required": False},
}


@dataclass
class DocumentRead:
    """What one attachment yielded, including when it yielded nothing."""

    attachment: Attachment
    values: dict[str, str] = field(default_factory=dict)
    sha256: str = ""
    page_count: int = 0
    missing: tuple[str, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.values)


def read_documents(
    session: CaptureSession, session_path: str | Path | None
) -> dict[int, DocumentRead]:
    """Extract every document the recording carried, keyed by source event.

    Never raises. A document that cannot be read produces a DocumentRead with
    an error, because a draft that quietly omits the invoice checks is worse
    than one that says why it has none.
    """
    out: dict[int, DocumentRead] = {}
    for attachment in session.attachments:
        if attachment.source_event is None:
            continue
        out[attachment.source_event] = _read_one(attachment, session_path)
    return out


def _read_one(attachment: Attachment, session_path: str | Path | None) -> DocumentRead:
    if attachment.error:
        return DocumentRead(attachment=attachment, error=attachment.error)
    if session_path is None:
        return DocumentRead(
            attachment=attachment,
            error="the recording was not read from a file, so its documents are not on disk",
        )

    body = read_attachment(session_path, attachment)
    if body is None:
        return DocumentRead(
            attachment=attachment,
            error="the stored document is missing or does not match its recorded hash",
        )

    suffix = Path(attachment.filename).suffix.lower()
    if suffix != ".pdf":
        return DocumentRead(
            attachment=attachment,
            error=f"no deterministic extractor for {suffix or 'this file type'}",
        )

    try:
        from verity_extract import PdfExtractor
    except ImportError:
        return DocumentRead(
            attachment=attachment,
            error="PDF extraction is not installed (pip install 'verity[extract]')",
        )

    try:
        result = PdfExtractor().extract(
            body, TEACHING_SPEC, uri=attachment.url, document_sha256=attachment.sha256
        )
    except Exception as exc:  # a broken PDF must not end a compilation
        return DocumentRead(
            attachment=attachment, error=f"{type(exc).__name__}: {exc}".strip()[:200]
        )

    return DocumentRead(
        attachment=attachment,
        values={name: _as_text(value) for name, value in result.values().items()},
        sha256=result.document_sha256,
        page_count=result.page_count,
        missing=tuple(result.missing),
    )


def _as_text(value: Any) -> str:
    """Documents join the same value index as screen text, so they match it.

    A total read from a PDF as Decimal('14800.00') and the same total read off
    a page as '14,800.00' are the same observation, and the index that spots a
    value seen twice compares strings.
    """
    from decimal import Decimal

    if isinstance(value, Decimal):
        return f"{value:f}"
    return str(value)
