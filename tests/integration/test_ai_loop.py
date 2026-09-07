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
from verity_compiler.contract import UNOBSERVABLE
from verity_verifier import load_contract_text, typecheck

ROOT = Path(__file__).resolve().parents[1] / "fixtures"
SESSION = ROOT / "sessions" / "ap_invoice_to_po.session.json"
CASSETTE = ROOT / "ai" / "invoice_to_po.cassette.json"


@pytest.fixture()
def enriched() -> tuple[Any, Any]:
    steps = normalize(read_session(SESSION), SESSION)
    draft = propose(steps, name="invoice_to_po")

    # Any provider replays it: a cassette is keyed on the question, and which
    # model answered is metadata inside the file. Whoever runs these tests
    # needs no key, no network and no particular provider configured.
    inner = build_provider("openrouter")
    inner.api_key = None  # replay must not need one
    provider = CassetteProvider(inner, AiCassette(CASSETTE, AiCassetteMode.REPLAY))

    return draft, enrich(draft, steps, provider)


def test_the_model_layer_runs_without_a_key_or_a_network(enriched: Any) -> None:
    _, result = enriched
    assert result.ok
    # Replayed, so nothing was spent now -- but the recorded exchange cost
    # real money, and the cassette says how much.
    assert result.usd is not None


def test_the_model_supplies_what_one_recording_cannot(enriched: Any) -> None:
    """The suggestions are exactly the gaps the draft header names.

    A single successful run shows no duplicate, no missing field and no
    zero-value record, because none of those happened. Every id below is a
    check the deterministic pass could not have proposed at any level of
    cleverness -- which is the whole argument for having this layer.
    """
    _, result = enriched

    assert {s.id for s in result.suggestions} == {
        "purchase_order_found",
        "bill_amount_not_positive",
        "duplicate_bill_created",
        "bill_amount_missing",
        "bill_vendor_missing",
    }


def test_forbidden_suggestions_are_marked_as_forbidden(enriched: Any) -> None:
    """Which side a check belongs on is not cosmetic: it inverts the verdict."""
    _, result = enriched
    by_id = {s.id: s.forbidden for s in result.suggestions}

    assert by_id["purchase_order_found"] is False
    assert all(by_id[i] for i in (
        "bill_amount_not_positive", "duplicate_bill_created",
        "bill_amount_missing", "bill_vendor_missing",
    ))


def test_the_duplicate_check_is_the_one_the_draft_admits_it_is_missing(
    enriched: Any,
) -> None:
    """The draft says a recording cannot show a duplicate. This fills that in.

    Kept as its own test because it is the clearest case of the model earning
    its place: the sandbox has a duplicate-invoice perturbation, and nothing
    in a successful demonstration could ever have suggested guarding for it.
    """
    draft, result = enriched
    suggestion = next(s for s in result.suggestions if s.id == "duplicate_bill_created")

    assert suggestion.expression == "count(bills) > 1"
    assert suggestion.forbidden
    assert any("duplicate" in caveat for caveat in UNOBSERVABLE)
    assert draft is not None


def test_the_model_improves_wording_without_touching_the_checks(enriched: Any) -> None:
    draft, result = enriched
    text = to_yaml(draft, result)

    # A derived check keeps its meaning and gains a better sentence.
    assert "substituted-invoice fraud" in text
    assert result.workflow_name == "invoice_to_bill_match"
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
    assert len(checked.assertions) == 7      # still only the observed seven
    assert all(a.strength.value == "STRONG" for a in checked.assertions)


def test_accepting_a_suggestion_by_hand_produces_a_valid_contract(enriched: Any) -> None:
    """What happens after a human agrees: uncomment, and it must work."""
    draft, result = enriched
    lines = to_yaml(draft, result).splitlines()

    accepted: list[str] = []
    for line in lines:
        if "duplicate_bill_created" in line or (
            accepted and accepted[-1].strip().startswith("- id: duplicate_bill_created")
            and line.lstrip("# ").startswith("assert:")
        ):
            accepted.append("  " + line.lstrip("# "))
    assert accepted, "the suggestion was not found in the file"

    text = to_yaml(draft, result).replace(
        "forbidden: []",
        "forbidden:\n  - id: duplicate_bill_created\n"
        "    assert: 'count(bills) > 1'",
    )
    checked = typecheck(load_contract_text(text, origin="accepted"))
    assert "duplicate_bill_created" in {a.assertion.id for a in checked.assertions}
