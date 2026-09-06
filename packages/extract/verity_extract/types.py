"""Typed results of document extraction."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ExtractedField:
    """One field pulled out of a document, with where it physically came from.

    ``page`` and ``bbox`` are what let a failure highlight the exact region of
    an invoice rather than gesturing at the file.
    """

    name: str
    value: Any
    raw: str
    page: int
    bbox: tuple[float, float, float, float] | None
    method: str
    confidence: float = 1.0
    anchor: str = ""
    """Which label the value was found next to. A change of anchor between runs
    means the document template moved, which is drift even when the value is
    still correct."""

    @property
    def as_decimal(self) -> Decimal | None:
        return self.value if isinstance(self.value, Decimal) else None


@dataclass(frozen=True)
class ExtractionResult:
    document_sha256: str
    uri: str
    fields: dict[str, ExtractedField]
    missing: list[str]
    warnings: list[str]
    page_count: int

    @property
    def ok(self) -> bool:
        return not self.missing

    def values(self) -> dict[str, Any]:
        return {name: f.value for name, f in self.fields.items()}


class ExtractionError(Exception):
    """The document could not be parsed at all.

    Always becomes an ``INCONCLUSIVE`` fact. A document we cannot read is a
    statement about our knowledge, never evidence that something passed.
    """
