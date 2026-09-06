"""End-to-end verification against the sandbox.

This module carries the M0 acceptance criteria: a clean sandbox verifies, and
each perturbation produces the correct verdict, localized to the correct
place.
"""

from __future__ import annotations

from typing import Any

import pytest
from verity_sandbox.perturb import PERTURBATION_NAMES, PERTURBATIONS
from verity_schema import Channel, Severity, Strength, Verdict


def test_a_clean_sandbox_passes(run_verification: Any) -> None:
    report = run_verification().report
    assert report.verdict is Verdict.PASS
    assert report.blocking_failures == []
    assert report.unresolved_facts == []


def test_every_assertion_in_the_reference_contract_is_strong(run_verification: Any) -> None:
    """A MEDIUM-risk contract whose assertions are all WEAK proves nothing."""
    report = run_verification().report
    assert all(a.strength is Strength.STRONG for a in report.assertions)


def test_verification_makes_no_model_calls(run_verification: Any) -> None:
    report = run_verification().report
    assert report.budgets.model_calls == 0
    assert report.budgets.cost_usd == 0.0


def test_reading_back_through_a_second_channel_is_recorded(run_verification: Any) -> None:
    """A value written via one surface and proven via another is worth more."""
    report = run_verification().report
    by_id = {a.id: a for a in report.assertions}
    assert by_id["amount_persisted"].channel is Channel.INDEPENDENT
    assert by_id["amount_match"].channel is Channel.INDEPENDENT


@pytest.mark.parametrize("name", PERTURBATION_NAMES)
def test_each_perturbation_produces_its_expected_verdict(
    name: str, sandbox_client: Any, run_verification: Any
) -> None:
    sandbox_client.post(f"/admin/perturb/{name}")
    report = run_verification().report
    expected = PERTURBATIONS[name].verdict_for(has_trace=False)
    assert report.verdict.value == expected, (
        f"{name}: expected {expected}, got {report.verdict.value} "
        f"({report.divergence.explanation})"
    )


@pytest.mark.parametrize(
    ("name", "assertion_id"),
    [
        ("amount_changed", "amount_match"),
        ("vendor_changed", "doc_vendor_match"),
        ("duplicate_invoice", "no_duplicate"),
        ("missing_field", "po_reference_present"),
    ],
)
def test_the_first_failing_assertion_is_localized_correctly(
    name: str, assertion_id: str, sandbox_client: Any, run_verification: Any
) -> None:
    sandbox_client.post(f"/admin/perturb/{name}")
    report = run_verification().report
    assert report.divergence.first_assertion_failure == assertion_id


def test_an_unresolvable_fact_yields_inconclusive_never_pass(
    sandbox_client: Any, run_verification: Any
) -> None:
    """The most important negative result in the product."""
    sandbox_client.post("/admin/perturb/ambiguous_record")
    report = run_verification().report

    assert report.verdict is Verdict.INCONCLUSIVE
    assert report.verdict is not Verdict.PASS
    unresolved = report.unresolved_facts
    assert [f.id for f in unresolved] == ["po"]
    assert unresolved[0].cardinality == 2
    assert "found 2" in (unresolved[0].error or "")


def test_assertions_that_could_not_be_evaluated_are_not_counted_as_passing(
    sandbox_client: Any, run_verification: Any
) -> None:
    sandbox_client.post("/admin/perturb/ambiguous_record")
    report = run_verification().report
    unevaluated = [a for a in report.assertions if a.passed is None]
    assert unevaluated
    assert all(a.error for a in unevaluated)


def test_the_flagship_failure_reports_expected_observed_and_delta(
    sandbox_client: Any, run_verification: Any
) -> None:
    """The number a person actually reacts to."""
    sandbox_client.post("/admin/perturb/amount_changed")
    report = run_verification().report

    assert report.verdict is Verdict.FAIL
    failure = next(a for a in report.assertions if a.id == "amount_match")
    assert failure.severity is Severity.BLOCKING
    assert "14,800.00" in (failure.expected_repr or "")
    assert failure.observed_repr == "148,000.00"
    assert failure.delta_repr == "+133,200.00"
    assert failure.evidence_refs


def test_a_template_change_is_drift_not_failure(
    sandbox_client: Any, run_verification: Any
) -> None:
    """The outcome still holds; the environment moved. That is exactly DRIFT."""
    sandbox_client.post("/admin/perturb/pdf_format_shift")
    report = run_verification().report

    assert report.verdict is Verdict.DRIFT
    assert report.blocking_failures == []
    assert report.divergence.environment_change_kind == "document_template"
    assert any("template changed" in note for note in report.notes)


def test_a_ui_rename_does_not_affect_a_business_outcome(
    sandbox_client: Any, run_verification: Any
) -> None:
    """Live verification reads systems of record, so a renamed button is invisible.

    Reporting PASS here is correct, not a miss: nothing about the business
    state changed. Only a trace can observe this at all.
    """
    sandbox_client.post("/admin/perturb/ui_label_changed")
    assert run_verification().report.verdict is Verdict.PASS


def test_every_resolved_fact_carries_evidence(run_verification: Any) -> None:
    report = run_verification().report
    for fact in report.facts:
        assert fact.resolved
        assert fact.evidence_refs, f"fact {fact.id} has no evidence"


def test_document_evidence_carries_page_and_bounding_box(run_verification: Any) -> None:
    outcome = run_verification()
    document_evidence = [
        r for r in outcome.store.records if r.provenance and r.provenance.page
    ]
    assert document_evidence
    for record in document_evidence:
        assert record.provenance is not None
        assert record.provenance.bbox is not None


def test_the_evidence_bundle_verifies(run_verification: Any, tmp_path: Any) -> None:
    from verity_evidence import check_bundle, write_bundle

    outcome = run_verification()
    bundle = write_bundle(tmp_path / "bundle", report=outcome.report, store=outcome.store)
    manifest = check_bundle(bundle)
    assert manifest.entries
