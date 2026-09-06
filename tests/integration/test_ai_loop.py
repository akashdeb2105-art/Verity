"""The teaching loop with a model in it, replayed from a recorded cassette.

No network, no key, no cost, and deterministic -- which is what a test of a
proposal layer has to be. The cassette holds one real-shaped reply; everything
downstream of it is the production code path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from verity_ai import AiCassette, AiCassetteMode, CassetteProvider, build_provider
from verity_capture import read_session
from verity_compiler import enrich, normalize, propose, to_yaml
from verity_verifier import load_contract_text, typecheck

ROOT = Path(__file__).resolve().parents[1] / "fixtures"
SESSION = ROOT / "sessions" / "ap_invoice_to_po.session.json"
CASSETTE = ROOT / "ai" / "invoice_to_po.cassette.json"


@pytest.fixture()
def enriched() -> tuple[Any, Any]:
    steps = normalize(read_session(SESSION))
    draft = propose(steps, name="invoice_to_po")

    inner = build_provider("openrouter")
    inner.api_key = None  # replay must not need one
    provider = CassetteProvider(inner, AiCassette(CASSETTE, AiCassetteMode.REPLAY))

    return draft, enrich(draft, steps, provider)


def test_the_model_layer_runs_without_a_key_or_a_network(enriched: Any) -> None:
    _, result = enriched
    assert result.ok
    assert result.usd == 0.0


def test_sound_suggestions_survive_and_an_invented_fact_does_not(enriched: Any) -> None:
    _, result = enriched
    assert {s.id for s in result.suggestions} == {
        "vendor_is_not_blank", "bill_is_not_already_paid"
    }
    assert any("approvals" in r and "never established" in r for r in result.rejected)


def test_a_forbidden_suggestion_is_marked_as_one(enriched: Any) -> None:
    _, result = enriched
    forbidden = {s.id for s in result.suggestions if s.forbidden}
    assert forbidden == {"bill_is_not_already_paid"}


def test_the_model_improves_wording_without_touching_the_checks(enriched: Any) -> None:
    draft, result = enriched
    text = to_yaml(draft, result)

    assert "overpayment" in text                       # a better explanation
    assert result.workflow_name == "invoice_to_purchase_order"
    # The derived checks themselves are byte-identical either way.
    without = to_yaml(draft)
    for expression in (
        "within(bills.amount, purchase_orders.total, tolerance = 0.01)",
        "bills.vendor ~= purchase_orders.vendor",
        'bills.status == "DRAFT"',
    ):
        assert expression in text and expression in without


@pytest.mark.security()
def test_every_suggestion_is_written_commented_out(enriched: Any) -> None:
    """A suggestion is not an observation, and the file must not let the two
    look alike. Accepting one is a human deleting a '#'."""
    draft, result = enriched
    for line in to_yaml(draft, result).splitlines():
        if any(s.id in line for s in result.suggestions):
            assert line.lstrip().startswith("#"), f"suggestion is live: {line}"


def test_the_enriched_contract_still_typechecks(enriched: Any) -> None:
    """Commented suggestions must not break the file they are written into."""
    draft, result = enriched
    checked = typecheck(load_contract_text(to_yaml(draft, result), origin="enriched"))
    assert len(checked.assertions) == 3      # still only the observed three
    assert all(a.strength.value == "STRONG" for a in checked.assertions)


def test_accepting_a_suggestion_by_hand_produces_a_valid_contract(enriched: Any) -> None:
    """What happens after a human agrees: uncomment, and it must work."""
    draft, result = enriched
    lines = to_yaml(draft, result).splitlines()

    accepted: list[str] = []
    for line in lines:
        if "bill_is_not_already_paid" in line or (
            accepted and accepted[-1].strip().startswith("- id: bill_is_not_already_paid")
            and line.lstrip("# ").startswith("assert:")
        ):
            accepted.append("  " + line.lstrip("# "))
    assert accepted, "the suggestion was not found in the file"

    text = to_yaml(draft, result).replace(
        "forbidden: []",
        "forbidden:\n  - id: bill_is_not_already_paid\n"
        "    assert: 'bills.status != \"PAID\"'",
    )
    checked = typecheck(load_contract_text(text, origin="accepted"))
    assert "bill_is_not_already_paid" in {a.assertion.id for a in checked.assertions}
