"""Document extraction: deterministic, typed, and carrying its provenance."""

from __future__ import annotations

import hashlib

import pytest
from verity_extract import ExtractionError, PdfExtractor
from verity_sandbox.pdfgen import render_invoice_pdf
from verity_sandbox.state import build_seed_state

SPEC = {
    "total": {"type": "decimal", "required": True},
    "number": {"type": "string", "required": True},
    "vendor": {"type": "string", "required": True},
    "po_ref": {"type": "string", "required": True},
}


def _pdf(template: str = "v1") -> bytes:
    invoice = build_seed_state().invoice("INV-4471")
    assert invoice is not None
    invoice.template = template
    return render_invoice_pdf(invoice)


@pytest.mark.determinism()
@pytest.mark.parametrize("template", ["v1", "v2"])
def test_pdf_generation_is_byte_deterministic(template: str) -> None:
    first, second = _pdf(template), _pdf(template)
    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()


@pytest.mark.parametrize("template", ["v1", "v2"])
def test_all_required_fields_are_found_in_both_templates(template: str) -> None:
    result = PdfExtractor().extract(_pdf(template), SPEC)
    assert result.missing == []
    assert result.ok


def test_extracted_values_are_correct() -> None:
    result = PdfExtractor().extract(_pdf("v1"), SPEC)
    values = result.values()
    assert str(values["total"]) == "14800.00"
    assert values["number"] == "INV-4471"
    assert values["po_ref"] == "PO-2211"
    assert values["vendor"] == "Acme Supplies"


def test_the_same_value_is_found_in_both_templates() -> None:
    """A template change must not change the answer, only how it was found."""
    v1 = PdfExtractor().extract(_pdf("v1"), SPEC).values()
    v2 = PdfExtractor().extract(_pdf("v2"), SPEC).values()
    assert v1["total"] == v2["total"]


def test_the_anchor_differs_between_templates_which_is_the_drift_signal() -> None:
    v1 = PdfExtractor().extract(_pdf("v1"), SPEC).fields["total"]
    v2 = PdfExtractor().extract(_pdf("v2"), SPEC).fields["total"]
    assert v1.anchor == "total due"
    assert v2.anchor == "amount payable"
    assert v1.anchor != v2.anchor


def test_every_field_carries_page_and_bounding_box() -> None:
    result = PdfExtractor().extract(_pdf("v1"), SPEC)
    for field in result.fields.values():
        assert field.page == 1
        assert field.bbox is not None
        x0, top, x1, bottom = field.bbox
        assert x1 > x0 and bottom > top


def test_extraction_is_deterministic_across_runs() -> None:
    data = _pdf("v1")
    runs = [PdfExtractor().extract(data, SPEC).values() for _ in range(5)]
    assert all(run == runs[0] for run in runs)


def test_a_document_with_no_text_is_inconclusive_not_guessed_at() -> None:
    with pytest.raises(ExtractionError):
        PdfExtractor().extract(b"%PDF-1.4 not really a pdf", SPEC)


def test_optional_fields_that_are_absent_are_warnings_not_failures() -> None:
    spec = {**SPEC, "shipping": {"type": "decimal", "required": False}}
    result = PdfExtractor().extract(_pdf("v1"), spec)
    assert result.missing == []
    assert any("shipping" in w for w in result.warnings)


def test_provenance_records_the_document_hash() -> None:
    data = _pdf("v1")
    digest = hashlib.sha256(data).hexdigest()
    result = PdfExtractor().extract(data, SPEC, uri="/x.pdf", document_sha256=digest)
    assert result.document_sha256 == digest
