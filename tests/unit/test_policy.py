"""Risk classification, and what a policy requires of it.

The property under test throughout is that a graph cannot talk its way down.
A ``risk`` field is a claim made by whoever wrote the graph; the classifier
looks at what the step would actually do.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from verity_runtime import Policy, PolicyError, Requirement, classify
from verity_schema import RiskLevel
from verity_schema.workgraph import Node, WriteSpec


def _write_node(
    *, risk: RiskLevel = RiskLevel.LOW, action: str = "CREATE_RECORD",
    payload: dict[str, str] | None = None, approval_required: bool = False,
) -> Node:
    return Node(
        id="w", type=action, label="Create the bill", risk=risk,
        approval_required=approval_required,
        write=WriteSpec(connector="ledger", resource="bill",
                        payload=payload if payload is not None else {"amount": "100.00"}),
    )


# ------------------------------------------------------------- classification

def test_a_graph_cannot_declare_its_way_out_of_risk() -> None:
    """The whole point. A LOW label on a large payment buys nothing."""
    assessment = classify(_write_node(risk=RiskLevel.LOW, payload={"amount": "250000"}))

    assert assessment.declared is RiskLevel.LOW
    assert assessment.assessed is RiskLevel.CRITICAL
    assert assessment.level is RiskLevel.CRITICAL
    assert assessment.understated


def test_a_declaration_can_raise_risk_but_never_lower_it() -> None:
    """Trust in the cautious direction only."""
    assessment = classify(_write_node(risk=RiskLevel.CRITICAL, payload={"amount": "1.00"}))

    assert assessment.assessed is RiskLevel.MEDIUM
    assert assessment.level is RiskLevel.CRITICAL
    assert not assessment.understated


def test_reading_carries_no_risk() -> None:
    assert classify(Node(id="r", type="EXTRACT")).level is RiskLevel.LOW


def test_a_message_outranks_a_record_because_it_cannot_be_recalled() -> None:
    record = classify(_write_node(action="CREATE_RECORD", payload={}))
    message = classify(_write_node(action="SEND_MESSAGE", payload={}))

    assert record.assessed is RiskLevel.MEDIUM
    assert message.assessed is RiskLevel.HIGH


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("500.00", RiskLevel.MEDIUM),
        ("10000", RiskLevel.HIGH),
        ("14800.00", RiskLevel.HIGH),
        ("100000", RiskLevel.CRITICAL),
        ("148000.00", RiskLevel.CRITICAL),
        ("$148,000.00", RiskLevel.CRITICAL),
    ],
)
def test_size_escalates(amount: str, expected: RiskLevel) -> None:
    assert classify(_write_node(payload={"amount": amount})).assessed is expected


def test_an_unreadable_amount_escalates_rather_than_counting_as_zero() -> None:
    """The same reasoning as INCONCLUSIVE, applied to money.

    An unresolved placeholder has no value at classification time. Reading
    that as "small" would classify every templated payment as harmless, which
    is precisely backwards: not knowing the size of something is a reason for
    more care, not less.
    """
    assessment = classify(_write_node(payload={"amount": "{{ inputs.amount }}"}))

    assert assessment.assessed is RiskLevel.HIGH
    assert any(s.name == "amount_unreadable" for s in assessment.signals)


def test_the_reason_is_carried_not_reconstructed() -> None:
    """A halt has to be explainable to the person reading it."""
    explanation = classify(_write_node(payload={"amount": "14800.00"})).explain()

    assert "CREATE_RECORD changes a system of record" in explanation
    assert "at or above 10000" in explanation


# -------------------------------------------------------------------- policy

def test_the_default_policy_asks_before_it_writes() -> None:
    policy = Policy()
    decision = policy.decide(_write_node(payload={"amount": "500.00"}))

    assert decision.requirement is Requirement.REQUIRE_APPROVAL
    assert decision.needs_approval


def test_the_default_policy_forbids_the_largest_class_outright() -> None:
    """Some things should not be available to an unattended run at all."""
    decision = Policy().decide(_write_node(payload={"amount": "250000"}))

    assert decision.requirement is Requirement.FORBID
    assert decision.blocks


def test_a_gap_in_a_policy_forbids_rather_than_allows() -> None:
    """A policy that has not decided has not granted permission."""
    policy = Policy(name="partial", requirements={RiskLevel.LOW: Requirement.ALLOW})

    assert policy.requirement_for(RiskLevel.HIGH) is Requirement.FORBID


def test_a_node_marked_approval_required_is_never_silently_allowed() -> None:
    permissive = Policy(
        name="permissive",
        requirements=dict.fromkeys(RiskLevel, Requirement.ALLOW),
    )
    node = _write_node(payload={"amount": "1.00"}, approval_required=True)

    assert permissive.decide(node).needs_approval


def test_a_policy_file_with_a_typo_is_refused_not_defaulted() -> None:
    """Silently ignoring an unknown key is the quietest way to disable a control."""
    with pytest.raises(PolicyError, match="unknown policy keys"):
        Policy.from_mapping({"requirments": {}})

    with pytest.raises(PolicyError, match="bad requirement"):
        Policy.from_mapping({"requirements": {"HIGH": "ALOW"}})


def test_a_policy_can_be_configured_without_touching_code() -> None:
    policy = Policy.from_mapping({
        "name": "finance",
        "requirements": {"low": "allow", "medium": "allow", "high": "require_approval",
                         "critical": "forbid"},
        "high_amount": "500",
    })

    assert policy.name == "finance"
    assert policy.high_amount == Decimal("500")
    assert policy.decide(_write_node(payload={"amount": "100.00"})).requirement is Requirement.ALLOW
    assert policy.decide(_write_node(payload={"amount": "600.00"})).needs_approval
