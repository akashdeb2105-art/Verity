"""The sandbox is a fixture, so its determinism is a correctness property."""

from __future__ import annotations

from typing import Any

import pytest
from verity_sandbox.perturb import PERTURBATION_NAMES, apply_perturbations
from verity_sandbox.state import build_seed_state


@pytest.mark.determinism()
def test_the_same_seed_produces_the_same_state() -> None:
    assert build_seed_state(1).hash() == build_seed_state(1).hash()


@pytest.mark.determinism()
def test_seeding_is_stable_across_many_rebuilds() -> None:
    hashes = {build_seed_state(1).hash() for _ in range(10)}
    assert len(hashes) == 1


def test_there_are_eight_perturbations() -> None:
    assert len(PERTURBATION_NAMES) == 8


@pytest.mark.parametrize("name", PERTURBATION_NAMES)
def test_every_perturbation_changes_state(name: str) -> None:
    assert apply_perturbations([name]).hash() != build_seed_state().hash()


@pytest.mark.parametrize("name", PERTURBATION_NAMES)
def test_every_perturbation_is_exactly_reversible(name: str) -> None:
    baseline = build_seed_state().hash()
    apply_perturbations([name])
    assert apply_perturbations([]).hash() == baseline


@pytest.mark.parametrize("name", PERTURBATION_NAMES)
def test_perturbations_are_idempotent(name: str) -> None:
    assert apply_perturbations([name]).hash() == apply_perturbations([name]).hash()


def test_the_api_and_the_ui_expose_the_same_records(sandbox_client: Any) -> None:
    """Independent-channel verification is only possible if both channels exist."""
    api = sandbox_client.get("/api/bills", params={"ref": "INV-4471"}).json()["records"][0]
    ui = sandbox_client.get("/ui/bills").text
    assert api["amount"] in ui
    assert api["status"] in ui


def test_reset_restores_the_seed_hash(sandbox_client: Any) -> None:
    baseline = sandbox_client.get("/api/health").json()["state_hash"]
    sandbox_client.post("/admin/perturb/amount_changed")
    assert sandbox_client.get("/api/health").json()["state_hash"] != baseline
    assert sandbox_client.post("/admin/reset").json()["state_hash"] == baseline


def test_the_invoice_pdf_is_served_and_is_a_pdf(sandbox_client: Any) -> None:
    response = sandbox_client.get("/docs/invoices/INV-4471.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_an_ambiguous_record_is_exposed_not_hidden(sandbox_client: Any) -> None:
    """The fixture must not paper over ambiguity: that is the verifier's finding."""
    sandbox_client.post("/admin/perturb/ambiguous_record")
    payload = sandbox_client.get("/api/purchase_orders/PO-2211").json()
    assert payload["count"] == 2


def test_the_baseline_matches_the_flagship_demo_numbers() -> None:
    state = build_seed_state()
    po = state.po("PO-2211")
    invoice = state.invoice("INV-4471")
    assert po is not None and invoice is not None
    assert po.total == "14800.00"
    assert invoice.total == "14800.00"


def test_the_amount_perturbation_produces_the_flagship_mismatch() -> None:
    state = apply_perturbations(["amount_changed"])
    invoice = state.invoice("INV-4471")
    assert invoice is not None
    assert invoice.total == "148000.00"
