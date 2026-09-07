"""The whole loop: a recorded demonstration becomes a contract that runs.

This is the M1 claim, executed rather than described. A person did the task
once in a browser; the contract below was written by the compiler from that
recording alone, with no model call; and it is evaluated here by the verifier,
which shares no code with the compiler that produced it.

The one edit is deliberate and is part of what is being tested. The compiler
guesses a capability name from the page address, cannot know what the
organisation calls its connector, and says so in the draft. A draft that
pretended to know would be worse.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from verity_capture import read_session
from verity_compiler import analyse, normalize, propose, to_yaml
from verity_verifier import VerifyOptions, load_contract_text, typecheck, verify_checked

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "sessions" / "ap_invoice_to_po.session.json"
)
DEMO_INPUTS = {"invoice_number": "INV-4471", "po_number": "PO-2211"}


@pytest.fixture()
def proposed_contract() -> str:
    """The draft, with the single rename the draft itself asks for."""
    steps = normalize(read_session(FIXTURE), FIXTURE)
    return to_yaml(propose(steps, name="invoice_to_po_recorded")).replace(
        "capability: bills", "capability: accounting"
    )


def _verify(contract_text: str, registry: Any) -> Any:
    checked = typecheck(load_contract_text(contract_text, origin="proposed"))
    return verify_checked(
        checked,
        VerifyOptions(registry=registry, evidence_dir=tempfile.mkdtemp(), inputs=dict(DEMO_INPUTS)),
    ).report


def test_the_proposed_contract_passes_against_a_clean_sandbox(
    proposed_contract: str, registry: Any
) -> None:
    report = _verify(proposed_contract, registry)
    assert report.verdict.value == "PASS", report.divergence.explanation
    assert report.unresolved_facts == []


def test_it_proves_the_amount_the_ledger_holds_matches_the_purchase_order(
    proposed_contract: str, registry: Any
) -> None:
    report = _verify(proposed_contract, registry)
    ids = {a.id for a in report.assertions if a.passed}
    assert "bills_amount_matches_purchase_orders_total" in ids
    assert "bills_vendor_matches_purchase_orders" in ids
    assert "bills_status_is_draft" in ids


def test_it_catches_a_duplicate_bill(
    proposed_contract: str, registry: Any, sandbox_client: Any
) -> None:
    """Nothing in the recording showed a duplicate. The cardinality the
    compiler wrote down catches one anyway."""
    sandbox_client.post("/admin/perturb/duplicate_invoice")
    report = _verify(proposed_contract, registry)

    assert report.verdict.value == "INCONCLUSIVE"
    unresolved = {f.id for f in report.unresolved_facts}
    assert "bills" in unresolved
    assert "found 2" in next(f.error for f in report.unresolved_facts if f.id == "bills")


def test_it_catches_an_ambiguous_purchase_order(
    proposed_contract: str, registry: Any, sandbox_client: Any
) -> None:
    sandbox_client.post("/admin/perturb/ambiguous_record")
    report = _verify(proposed_contract, registry)
    assert report.verdict.value == "INCONCLUSIVE"
    assert "purchase_orders" in {f.id for f in report.unresolved_facts}


def test_the_draft_catches_a_changed_invoice_total(
    proposed_contract: str, registry: Any, sandbox_client: Any
) -> None:
    """The flagship case, caught by a contract nobody wrote by hand.

    The invoice is altered so it no longer agrees with the purchase order it
    was raised against. Nothing in the recording demonstrated this -- the
    person only ever did the task correctly -- and the check exists because
    the recording carried the invoice itself, so the compiler could see the
    same total in two places that do not share an author.

    This is the property the product is for: an agent that reads only the ERP
    would see a self-consistent set of screens and report success.
    """
    sandbox_client.post("/admin/perturb/amount_changed")
    report = _verify(proposed_contract, registry)

    assert report.verdict.value == "FAIL"
    failed = {a.id for a in report.assertions if not a.passed}
    assert "doc_total_matches_purchase_orders" in failed


def test_the_draft_without_the_document_would_miss_it(
    registry: Any, sandbox_client: Any
) -> None:
    """The counterpart, kept so the value of reading the document is visible.

    Compiled from the same recording but without the file on disk, the draft
    has no document fact, every ERP screen still agrees with every other, and
    the altered invoice passes unnoticed. The draft says so rather than
    letting a reader assume otherwise.
    """
    draft = propose(normalize(read_session(FIXTURE)), name="invoice_to_po_recorded")
    text = to_yaml(draft).replace("capability: bills", "capability: accounting")

    sandbox_client.post("/admin/perturb/amount_changed")

    assert _verify(text, registry).verdict.value == "PASS"
    assert any("document was opened" in note for note in draft.notes)


def test_the_reference_contract_catches_what_the_draft_misses(
    registry: Any, sandbox_client: Any, checked_contract: Any
) -> None:
    """The counterpart to the test above: this is what a completed contract buys."""
    sandbox_client.post("/admin/perturb/amount_changed")
    report = verify_checked(
        checked_contract,
        VerifyOptions(registry=registry, evidence_dir=tempfile.mkdtemp(), inputs=dict(DEMO_INPUTS)),
    ).report
    assert report.verdict.value == "FAIL"
    assert report.divergence.first_assertion_failure == "amount_match"


def test_the_whole_loop_makes_no_model_call(proposed_contract: str, registry: Any) -> None:
    report = _verify(proposed_contract, registry)
    assert report.budgets.model_calls == 0
    assert report.budgets.cost_usd == 0.0


def test_the_proposal_is_a_subset_of_the_hand_written_contract() -> None:
    """Precision over recall, stated as a property.

    Every relationship the compiler proposes must also be asserted by the
    contract a person wrote by hand. The draft may say less; it must not say
    anything the reviewed contract contradicts.
    """
    steps = normalize(read_session(FIXTURE))
    _, comparisons, constants = analyse(steps)

    proposed = {("amount", "total"), ("vendor", "vendor")}
    actual = {
        (c.left_key.split("-")[-1], c.right_key.split("-")[-1]) for c in comparisons
    }
    assert actual == proposed

    assert [(c.key.split("-")[-1], c.value) for c in constants] == [("status", "DRAFT")]
