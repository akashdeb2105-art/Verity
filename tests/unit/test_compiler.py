"""Compiling a recorded demonstration.

The fixture is a real recording, made by driving a real browser through the
sandbox. It is checked in because the compiler must be provable on a machine
with no browser, and because a recording that changes underneath the tests
would make every assertion here meaningless.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from verity_capture import read_session
from verity_compiler import analyse, build, normalize, propose, summarise, to_yaml
from verity_compiler.normalize import system_of
from verity_compiler.values import normalise_value
from verity_schema import RiskLevel
from verity_verifier import load_contract_text, typecheck

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "sessions" / "ap_invoice_to_po.session.json"
)


@pytest.fixture()
def steps() -> list:
    """The recording compiled with its documents, as the CLI compiles it."""
    return normalize(read_session(FIXTURE), FIXTURE)


@pytest.fixture()
def steps_without_documents() -> list:
    """The same recording compiled without access to the documents on disk.

    Passing no path is how a caller says 'I have the events but not the
    files'. What the compiler must not do then is guess.
    """
    return normalize(read_session(FIXTURE))


# ---------------------------------------------------------------- normalising

def test_the_recording_compiles_to_the_expected_shape(steps: list) -> None:
    assert [s.verb for s in steps] == [
        "NAVIGATE",       # open the inbox
        "EXTRACT",        # read the email subject
        "EXTRACT",        # open the invoice document
        "NAVIGATE",       # go to the purchase order
        "EXTRACT",        # read the PO total
        "EXTRACT",        # read the PO vendor
        "NAVIGATE",       # go to the ledger
        "EXTRACT",        # read the bill amount
        "EXTRACT",        # read the bill status
        "CREATE_RECORD",  # press Create Bill
    ]


def test_a_write_button_is_recognised_and_gated(steps: list) -> None:
    write = steps[-1]
    assert write.verb == "CREATE_RECORD"
    assert write.risk is RiskLevel.MEDIUM


def test_reads_are_low_risk(steps: list) -> None:
    assert all(s.risk is RiskLevel.LOW for s in steps if s.verb in ("EXTRACT", "NAVIGATE"))


def test_systems_are_derived_from_the_address(steps: list) -> None:
    assert {s.system for s in steps} == {"inbox", "purchase_orders", "bills", "doc"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://host/ui/purchase-orders/PO-1", "purchase_orders"),
        ("http://host/bills", "bills"),
        ("https://app.example.com/", "app"),
        (None, None),
    ],
)
def test_system_naming(url: str | None, expected: str | None) -> None:
    assert system_of(url) == expected


def test_an_opened_document_carries_no_values_from_the_page_behind_it(
    steps_without_documents: list,
) -> None:
    """The click happens on the linking page. That page's text is on screen but
    it is not the document's content, and treating it as such invents
    comparisons the person never made."""
    document = next(s for s in steps_without_documents if s.note == "document-not-read")
    assert document.observed == {}


def test_a_fetched_document_yields_its_own_fields(steps: list) -> None:
    """The invoice is read from the file, not from the page that linked to it.

    This is the independent channel the whole product rests on: the PDF was
    not written by the ERP, so a value that agrees with the ERP is evidence
    rather than a system confirming itself.
    """
    document = next(s for s in steps if s.system == "doc")

    assert document.observed == {
        "total": "14800.00",
        "number": "INV-4471",
        "vendor": "Acme Supplies",
        "po_ref": "PO-2211",
        "date": "2026-08-12",
    }
    # Cited by content, so a contract can say which bytes it read.
    assert len(document.document_sha256) == 64
    assert document.document_url.endswith("/docs/invoices/INV-4471.pdf")
    # Nothing from the inbox page leaked in with it.
    assert "inbox-subject" not in document.observed


def test_reading_the_document_is_what_produces_the_invoice_comparison(
    steps: list, steps_without_documents: list
) -> None:
    """The check that matters only exists because the document was read."""

    def pairs(compiled: list) -> set:
        _, comparisons, _ = analyse(compiled)
        return {(c.left_system, c.right_system) for c in comparisons}

    assert ("doc", "purchase_orders") in pairs(steps)
    assert not any("doc" in pair for pair in pairs(steps_without_documents))


def test_compilation_is_deterministic() -> None:
    first = [(s.verb, s.label, s.value) for s in normalize(read_session(FIXTURE))]
    second = [(s.verb, s.label, s.value) for s in normalize(read_session(FIXTURE))]
    assert first == second


# ------------------------------------------------------------------- values

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14800.00", ("14800", "number")),
        ("14,800.00", ("14800", "number")),
        ("$14800", ("14800", "number")),
        ("PO-2211", ("PO-2211", "identifier")),
        # Case-insensitive: one screen shows PO-2211, the next shows po-2211,
        # and they are the same purchase order.
        ("po-2211", ("PO-2211", "identifier")),
        ("  Acme   Supplies ", ("acme supplies", "text")),
        ("", None),
        ("-", None),
    ],
)
def test_value_normalisation(raw: str, expected: tuple[str, str] | None) -> None:
    assert normalise_value(raw) == expected


def test_it_finds_the_record_identifiers(steps: list) -> None:
    inputs, _, _ = analyse(steps)
    assert {i.name for i in inputs} == {"po_number", "invoice_number"}
    assert {i.example for i in inputs} == {"PO-2211", "INV-4471"}


def test_a_value_seen_in_two_systems_becomes_a_comparison(steps: list) -> None:
    _, comparisons, _ = analyse(steps)
    pairs = {
        (c.left_system, c.left_key, c.right_system, c.right_key) for c in comparisons
    }
    assert ("bills", "bill-amount", "purchase_orders", "po-total") in pairs
    assert ("bills", "bill-vendor", "purchase_orders", "po-vendor") in pairs


def test_a_clicked_number_never_becomes_a_constant(steps: list) -> None:
    """Asserting `amount == 14800` would pass today and be wrong tomorrow."""
    _, _, constants = analyse(steps)
    assert all(not c.value.replace(".", "").isdigit() for c in constants)


def test_only_a_deliberately_read_state_word_becomes_a_constant(steps: list) -> None:
    _, _, constants = analyse(steps)
    assert [(c.system, c.value) for c in constants] == [("bills", "DRAFT")]


def test_an_identifier_is_an_input_not_a_comparison(steps: list) -> None:
    inputs, comparisons, _ = analyse(steps)
    identifiers = {i.example for i in inputs}
    assert not any(c.example in identifiers for c in comparisons)


# -------------------------------------------------------------------- graph

def test_one_demonstration_produces_one_path(steps: list) -> None:
    inputs, _, _ = analyse(steps)
    graph = build(steps, inputs, name="t")
    assert len(graph.nodes) == len(steps)
    assert len(graph.edges) == len(steps) - 1
    assert graph.metadata["branches_observed"] == 0


def test_no_branch_is_invented(steps: list) -> None:
    """One recording shows one path. Inventing an alternative invents a fact."""
    inputs, _, _ = analyse(steps)
    graph = build(steps, inputs, name="t")
    targets = [e.to for e in graph.edges]
    assert len(targets) == len(set(targets))


def test_side_effects_require_approval(steps: list) -> None:
    inputs, _, _ = analyse(steps)
    graph = build(steps, inputs, name="t")
    for node in graph.nodes:
        if node.type in ("CREATE_RECORD", "UPDATE_RECORD", "SEND_MESSAGE"):
            assert node.approval_required


def test_graph_summary(steps: list) -> None:
    inputs, _, _ = analyse(steps)
    assert summarise(build(steps, inputs, name="t")) == {
        "NAVIGATE": 3, "EXTRACT": 6, "CREATE_RECORD": 1
    }


# ----------------------------------------------------------------- contract

def test_the_proposed_contract_is_valid_and_typechecks(steps: list) -> None:
    """A draft nobody can run is worse than no draft."""
    checked = typecheck(load_contract_text(to_yaml(propose(steps, name="t")), origin="draft"))
    assert len(checked.assertions) == 7


def test_every_proposed_assertion_is_strong(steps: list) -> None:
    checked = typecheck(load_contract_text(to_yaml(propose(steps, name="t")), origin="draft"))
    assert all(a.strength.value == "STRONG" for a in checked.assertions)


def test_the_draft_says_it_is_a_draft(steps: list) -> None:
    text = to_yaml(propose(steps, name="t"))
    assert text.startswith("# DRAFT")
    for unobservable in ("duplicate", "forbidden", "branches", "tolerances"):
        assert unobservable in text


def test_the_draft_admits_it_did_not_read_the_document(
    steps_without_documents: list,
) -> None:
    """When the file is not there, the draft says so instead of going quiet."""
    draft = propose(steps_without_documents, name="t")
    assert any("document was opened" in note for note in draft.notes)
    assert "INV-4471.pdf" in " ".join(draft.notes)


def test_the_draft_says_nothing_about_unread_documents_when_it_read_them(
    steps: list,
) -> None:
    draft = propose(steps, name="t")
    assert not any("document was opened" in note for note in draft.notes)
    assert draft.document is not None
    assert draft.document.url == "/docs/invoices/INV-4471.pdf"


def test_the_document_fact_is_a_document_not_a_connector(steps: list) -> None:
    """A PDF is read from bytes, not queried through an API.

    Writing it as a connector would produce a contract that cannot run, and
    would hide the one property that makes the comparison meaningful.
    """
    text = to_yaml(propose(steps, name="t"))

    assert "doc: { kind: document, format: pdf }" in text
    assert "capability: doc" not in text
    # The host belongs to the binding, not the contract.
    assert "http://sandbox" not in text


def test_every_proposed_assertion_has_a_distinct_id(steps: list) -> None:
    """Two systems can hold the same field name; two checks cannot share a name."""
    import re

    ids = re.findall(r"- id: (\S+)", to_yaml(propose(steps, name="t")))
    assert len(ids) == len(set(ids))


def test_the_draft_leaves_forbidden_empty_rather_than_guessing(steps: list) -> None:
    assert "forbidden: []" in to_yaml(propose(steps, name="t"))


def test_field_names_drop_a_repeated_system_prefix(steps: list) -> None:
    """'po-total' inside the purchase_orders system is just 'total'."""
    text = to_yaml(propose(steps, name="t"))
    assert "purchase_orders.total" in text
    assert "purchase_orders.po_total" not in text


def test_the_draft_requires_no_model_call(steps: list) -> None:
    text = to_yaml(propose(steps, name="t"))
    assert "model_calls: 0" in text


def test_proposal_is_deterministic(steps: list) -> None:
    assert to_yaml(propose(steps, name="t")) == to_yaml(propose(steps, name="t"))


def test_an_empty_recording_proposes_nothing_and_says_so() -> None:
    draft = propose([], name="empty")
    assert draft.assertion_count == 0
    assert "Nothing could be proposed" in to_yaml(draft)
