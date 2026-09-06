"""Schema round-trips, invariants and the enforced zero-model-call budget."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from verity_schema import (
    Budgets,
    OutcomeContract,
    RiskLevel,
    Trace,
    Verdict,
    VerificationReport,
    WorkGraph,
    worst,
)

from tests.conftest import CONTRACT_PATH


def test_verdict_exit_codes_are_stable() -> None:
    assert (Verdict.PASS.exit_code, Verdict.FAIL.exit_code,
            Verdict.DRIFT.exit_code, Verdict.INCONCLUSIVE.exit_code) == (0, 1, 2, 3)


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ([], Verdict.PASS),
        ([Verdict.PASS, Verdict.DRIFT], Verdict.DRIFT),
        ([Verdict.DRIFT, Verdict.INCONCLUSIVE], Verdict.INCONCLUSIVE),
        ([Verdict.INCONCLUSIVE, Verdict.FAIL], Verdict.FAIL),
        ([Verdict.PASS, Verdict.PASS], Verdict.PASS),
    ],
)
def test_worst_verdict_ordering(verdicts: list[Verdict], expected: Verdict) -> None:
    assert worst(verdicts) is expected


def test_contract_round_trips_byte_identically() -> None:
    from verity_verifier import load_contract

    contract = load_contract(CONTRACT_PATH)
    once = contract.model_dump(mode="json", by_alias=True)
    twice = OutcomeContract.model_validate(once).model_dump(mode="json", by_alias=True)
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)


def test_contract_fingerprint_is_order_independent() -> None:
    from verity_verifier import load_contract

    contract = load_contract(CONTRACT_PATH)
    reparsed = OutcomeContract.model_validate(
        json.loads(json.dumps(contract.model_dump(mode="json", by_alias=True)))
    )
    assert contract.fingerprint() == reparsed.fingerprint()


def test_model_calls_budget_must_be_zero() -> None:
    """Verification is deterministic in V1. A non-zero budget is rejected."""
    with pytest.raises(ValidationError, match="must be 0"):
        Budgets(model_calls=1)


def test_unknown_contract_keys_are_errors_not_warnings() -> None:
    with pytest.raises(ValidationError):
        OutcomeContract.model_validate(
            {"kind": "OutcomeContract",
             "metadata": {"name": "x", "version": "0.1.0"},
             "typo_here": True}
        )


def test_facts_must_reference_declared_sources() -> None:
    with pytest.raises(ValidationError, match="undefined source"):
        OutcomeContract.model_validate({
            "kind": "OutcomeContract",
            "metadata": {"name": "x", "version": "0.1.0"},
            "facts": [{"id": "a", "source": "nope", "read": {"resource": "r", "key": "k"}}],
        })


def test_risk_levels_that_require_strong_verification() -> None:
    assert not RiskLevel.LOW.requires_strong_verification
    assert RiskLevel.MEDIUM.requires_strong_verification
    assert RiskLevel.CRITICAL.requires_strong_verification


def test_trace_status_is_captured_but_is_only_data() -> None:
    trace = Trace.model_validate({
        "run_id": "r1", "runtime": {"name": "playwright"},
        "status_reported": "success", "steps": [],
    })
    assert trace.status_reported == "success"


def test_report_contradiction_flag() -> None:
    report = VerificationReport(
        verdict=Verdict.FAIL, contract_name="c", contract_version="0.1.0",
        contract_fingerprint="sha256:x", status_reported="success",
        started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T00:00:01Z",
    )
    assert report.contradicts_runtime is True

    agreeing = report.model_copy(update={"verdict": Verdict.PASS})
    assert agreeing.contradicts_runtime is False


def test_workgraph_is_defined_but_separate_from_the_contract() -> None:
    graph = WorkGraph(name="demo")
    assert graph.contract_ref is None
    assert graph.nodes == []
