"""Determinism: the same input and the same environment give the same result.

If this ever fails it is a P0 bug, not a flake. A non-deterministic verifier
cannot support regression tests, cannot support canaries, and cannot be
trusted to say anything at all.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

REPEATS = 10


def _comparable(report: Any) -> str:
    """Everything about a report except the wall-clock it happened at."""
    payload = report.model_dump(mode="json", by_alias=True)
    for volatile in ("started_at", "finished_at"):
        payload.pop(volatile, None)
    payload["budgets"].pop("duration_ms", None)
    for fact in payload["facts"]:
        fact.pop("duration_ms", None)
    return json.dumps(payload, sort_keys=True)


@pytest.mark.determinism()
def test_ten_identical_runs_produce_identical_reports(run_verification: Any) -> None:
    reports = [_comparable(run_verification().report) for _ in range(REPEATS)]
    assert len(set(reports)) == 1


@pytest.mark.determinism()
def test_ten_identical_runs_produce_identical_evidence_addresses(
    run_verification: Any,
) -> None:
    """Evidence is content-addressed, so identical facts must hash identically."""
    addresses = [
        tuple(r.id for r in run_verification().store.records) for _ in range(REPEATS)
    ]
    assert len(set(addresses)) == 1


@pytest.mark.determinism()
def test_a_failing_run_is_equally_deterministic(
    sandbox_client: Any, run_verification: Any
) -> None:
    sandbox_client.post("/admin/perturb/amount_changed")
    reports = [_comparable(run_verification().report) for _ in range(REPEATS)]
    assert len(set(reports)) == 1


@pytest.mark.determinism()
def test_the_contract_fingerprint_is_stable(run_verification: Any) -> None:
    fingerprints = {run_verification().report.contract_fingerprint for _ in range(5)}
    assert len(fingerprints) == 1


@pytest.mark.determinism()
def test_verdicts_do_not_drift_across_repeated_perturbation_cycles(
    sandbox_client: Any, run_verification: Any
) -> None:
    """Perturb, verify, restore, verify -- ten times, with no accumulated state."""
    for _ in range(5):
        sandbox_client.post("/admin/perturb/amount_changed")
        assert run_verification().report.verdict.value == "FAIL"
        sandbox_client.post("/admin/unperturb/amount_changed")
        assert run_verification().report.verdict.value == "PASS"
