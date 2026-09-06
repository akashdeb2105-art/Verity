"""Deterministic, provenance-preserving document extraction.

No vector search, no chunking, no reranking, no RAG. Fields are located by
layout and label rules and carry the page and bounding box they came from.
"""

from .pdf import DEFAULT_LABELS, PdfExtractor
from .types import ExtractedField, ExtractionError, ExtractionResult

__all__ = [
    "DEFAULT_LABELS", "ExtractedField", "ExtractionError", "ExtractionResult",
    "PdfExtractor",
]
