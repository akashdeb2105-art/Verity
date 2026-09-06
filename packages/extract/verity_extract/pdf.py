"""Deterministic PDF field extraction with provenance.

Deliberately small and rule-based: label proximity, regex anchors and layout
position. There is no vector store, no chunking, no reranking and no model in
this module -- extraction at verification time must be deterministic, because
a fact that only a model can produce is INFERRED, and an INFERRED fact cannot
satisfy a STRONG assertion.

Scanned or image-only PDFs are detected and reported rather than guessed at.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pdfplumber

from .types import ExtractedField, ExtractionError, ExtractionResult

#: Label synonyms per canonical field. Order matters: earlier wins.
DEFAULT_LABELS: dict[str, tuple[str, ...]] = {
    "total": ("total due", "amount payable", "amount due", "grand total", "total"),
    "number": ("invoice number", "invoice no", "invoice #", "ref"),
    "vendor": ("vendor", "supplier", "from"),
    "po_ref": ("purchase order", "order ref", "po number", "po"),
    "date": ("invoice date", "issued", "date"),
}

_MONEY = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?")
_CURRENCY_PREFIX = re.compile(r"^[A-Z]{3}\s+")
_IDENTIFIER = re.compile(r"\b[A-Z]{2,5}-\d{2,8}\b")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _to_decimal(text: str) -> Decimal | None:
    match = _MONEY.search(text.replace("$", ""))
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def _coerce(raw: str, field_type: str) -> Any:
    if field_type == "decimal":
        return _to_decimal(raw)
    if field_type == "integer":
        value = _to_decimal(raw)
        return int(value) if value is not None else None
    if field_type == "date":
        match = _DATE.search(raw)
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
        return None
    return raw.strip()


def _value_after_label(line: str, label: str) -> str:
    lowered = line.lower()
    index = lowered.find(label)
    if index < 0:
        return ""
    tail = line[index + len(label):]
    return tail.lstrip(" :\t").strip()


class PdfExtractor:
    """Extracts typed fields from a text-bearing PDF."""

    def __init__(self, labels: dict[str, tuple[str, ...]] | None = None) -> None:
        self.labels = labels or DEFAULT_LABELS

    def extract(
        self,
        data: bytes,
        spec: dict[str, dict[str, Any]],
        *,
        uri: str = "",
        document_sha256: str = "",
    ) -> ExtractionResult:
        """Extract the fields named in ``spec``.

        ``spec`` maps a field name to ``{"type": ..., "required": ...}`` --
        exactly the shape an Outcome Contract's ``extract`` block produces.
        """
        try:
            pdf = pdfplumber.open(io.BytesIO(data))
        except Exception as exc:  # pdfplumber raises a variety of parse errors
            raise ExtractionError(f"could not open PDF: {exc}") from exc

        fields: dict[str, ExtractedField] = {}
        missing: list[str] = []
        warnings: list[str] = []

        with pdf:
            page_count = len(pdf.pages)
            pages: list[tuple[int, str, list[dict[str, Any]]]] = []
            for index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                words = page.extract_words(use_text_flow=False) or []
                pages.append((index, text, words))

            if not any(text.strip() for _, text, _ in pages):
                raise ExtractionError(
                    "PDF contains no extractable text (scanned or image-only "
                    "documents are out of scope in V1)"
                )

            for name, field_spec in spec.items():
                field_type = str(field_spec.get("type", "string"))
                pattern = field_spec.get("pattern")
                found = self._find_field(name, field_type, pattern, pages)
                if found is None:
                    if field_spec.get("required", True):
                        missing.append(name)
                    else:
                        warnings.append(f"optional field '{name}' not found")
                    continue
                fields[name] = found

        return ExtractionResult(
            document_sha256=document_sha256,
            uri=uri,
            fields=fields,
            missing=missing,
            warnings=warnings,
            page_count=page_count,
        )

    # -- internals -------------------------------------------------------
    def _find_field(
        self,
        name: str,
        field_type: str,
        pattern: str | None,
        pages: list[tuple[int, str, list[dict[str, Any]]]],
    ) -> ExtractedField | None:
        for page_number, text, words in pages:
            for label in self.labels.get(name, (name,)):
                for line in text.splitlines():
                    if label not in line.lower():
                        continue
                    raw = _value_after_label(line, label)
                    if not raw:
                        continue
                    raw = _CURRENCY_PREFIX.sub("", raw).strip()
                    if pattern and not re.search(pattern, raw):
                        continue
                    value = _coerce(raw, field_type)
                    if value is None or value == "":
                        continue
                    token = _first_token(raw)
                    return ExtractedField(
                        name=name, value=value, raw=raw, page=page_number,
                        bbox=_bbox_for(token, words),
                        method="label-proximity", anchor=label, confidence=0.97,
                    )

            # Strategy 2: layout-aware. Some templates put the value on its own
            # text line, above or beside its label, so reading-order line
            # matching misses it entirely. Look for the nearest value token in
            # the label's horizontal band instead.
            for label in self.labels.get(name, (name,)):
                found = _find_spatial(label, field_type, pattern, words, page_number)
                if found is not None:
                    return ExtractedField(
                        name=name, value=found[0], raw=found[1], page=page_number,
                        bbox=found[2], method="layout-proximity", anchor=label,
                        confidence=0.90,
                    )

            # Fallback: a bare identifier for id-shaped fields.
            if field_type == "string" and name in ("number", "po_ref"):
                match = _IDENTIFIER.search(text)
                if match:
                    return ExtractedField(
                        name=name, value=match.group(0), raw=match.group(0),
                        page=page_number, bbox=_bbox_for(match.group(0), words),
                        method="identifier-pattern", anchor="", confidence=0.75,
                    )
        return None


BAND_TOLERANCE = 26.0
"""Vertical distance, in points, still considered the same visual band."""


def _label_span(
    label: str, words: list[dict[str, Any]]
) -> tuple[float, float, float, float] | None:
    """Locate a multi-word label on the page and return its bounding box."""
    tokens = label.lower().split()
    if not tokens:
        return None
    texts = [str(w.get("text", "")).lower().strip(":") for w in words]
    for start in range(len(texts) - len(tokens) + 1):
        if texts[start:start + len(tokens)] == tokens:
            group = words[start:start + len(tokens)]
            return (
                min(float(w["x0"]) for w in group),
                min(float(w["top"]) for w in group),
                max(float(w["x1"]) for w in group),
                max(float(w["bottom"]) for w in group),
            )
    return None


def _find_spatial(
    label: str,
    field_type: str,
    pattern: str | None,
    words: list[dict[str, Any]],
    page_number: int,
) -> tuple[Any, str, tuple[float, float, float, float]] | None:
    """Find a value in the same visual band as its label.

    Candidates are ranked by distance from the label's right edge, preferring
    values on the same line, then values immediately above or below -- which is
    how a person reads a totals block.
    """
    span = _label_span(label, words)
    if span is None:
        return None
    _, label_top, label_x1, label_bottom = span
    centre = (label_top + label_bottom) / 2

    candidates: list[tuple[float, dict[str, Any], Any, str]] = []
    for word in words:
        text = str(word.get("text", "")).strip()
        if not text or text.lower().strip(":") in label.lower().split():
            continue
        word_centre = (float(word["top"]) + float(word["bottom"])) / 2
        vertical = abs(word_centre - centre)
        if vertical > BAND_TOLERANCE:
            continue
        cleaned = _CURRENCY_PREFIX.sub("", text).strip()
        if pattern and not re.search(pattern, cleaned):
            continue
        value = _coerce(cleaned, field_type)
        if value is None or value == "":
            continue
        horizontal = max(0.0, float(word["x0"]) - label_x1)
        candidates.append((vertical * 4 + horizontal, word, value, cleaned))

    if not candidates:
        return None
    _, word, value, cleaned = min(candidates, key=lambda c: c[0])
    bbox = (float(word["x0"]), float(word["top"]), float(word["x1"]), float(word["bottom"]))
    return value, cleaned, bbox


def _first_token(raw: str) -> str:
    parts = raw.split()
    return parts[0] if parts else raw


def _bbox_for(
    token: str, words: list[dict[str, Any]]
) -> tuple[float, float, float, float] | None:
    """Locate a token's bounding box, so a failure can point at the page."""
    if not token:
        return None
    needle = token.strip().rstrip(":")
    for word in words:
        if str(word.get("text", "")).strip().rstrip(":") == needle:
            return (
                float(word["x0"]), float(word["top"]),
                float(word["x1"]), float(word["bottom"]),
            )
    return None


def _unused(_: date) -> None:  # pragma: no cover
    return None
