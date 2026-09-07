"""Approval bound to one payload, in one run.

The attack these tests exist to defeat is not the missing approval. It is the
approval that exists, is genuine, was given by a real person who read a real
screen -- and no longer describes what is about to happen.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from verity_connectors import WriteMode, payload_digest
from verity_runtime import (
    AlwaysPassGate,
    Approval,
    InMemoryApprovalStore,
    NoApprovals,
    Policy,
    Requirement,
    RunOptions,
    RunOutcome,
    check_approval,
    execute,
    pending_writes,
)
from verity_schema import RiskLevel
from verity_schema.workgraph import Node, WorkGraph, WriteSpec

RUN = "run_fixed"
NODE = "create_bill"


class RecordingWriter:
    name = "ledger"
    channel = "api"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def write(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((resource, dict(payload)))
        return {"id": "bill_0001", **payload}


def _graph(amount: str) -> WorkGraph:
    return WorkGraph(
        name="invoice_to_po",
        nodes=[Node(
            id=NODE, type="CREATE_RECORD", label="Create the draft bill",
            risk=RiskLevel.MEDIUM,
            write=WriteSpec(connector="ledger", resource="bill", payload={
                "vendor": "Acme Supplies", "amount": amount,
            }),
        )],
    )


#: A policy whose ceiling is above every amount in this module, so that the
#: control being tested here is the approval binding and not the threshold.
#: With default thresholds the policy would refuse $148,000 outright -- which
#: is the point of having both -- but then these tests would prove the wrong
#: thing.
APPROVAL_IS_THE_ONLY_CONTROL = Policy(name="test", critical_amount=Decimal("1000000"))


def _options(writer: RecordingWriter, store: Any) -> RunOptions:
    return RunOptions(
        mode=WriteMode.LIVE, gate=AlwaysPassGate(), writers={"ledger": writer},
        run_id=RUN, approvals=store, policy=APPROVAL_IS_THE_ONLY_CONTROL,
    )


def _approved(amount: str) -> InMemoryApprovalStore:
    """An approval for a bill of exactly ``amount``, as a person would give it."""
    graph = _graph(amount)
    store = InMemoryApprovalStore()
    for pending in pending_writes(graph, RunOptions(run_id=RUN)):
        store.grant(Approval(
            run_id=RUN, node_id=pending.node_id, digest=pending.digest,
            approver="controller@example.com",
        ))
    return store


# --------------------------------------------------------------- the binding

def test_an_approved_amount_cannot_be_replayed_at_a_different_amount() -> None:
    """The flagship.

    A controller approves a $14,800 bill. The graph is then altered to write
    $148,000 -- by a compromised planner, a tampered file, an injected
    instruction, it does not matter which. The approval is real and it is for
    this run and this node. It still must not authorise this write.

    The threshold is lifted here so the binding is the only thing that can
    stop it. Under the default policy $148,000 never reaches this check.
    """
    writer = RecordingWriter()
    report = execute(_graph("148000.00"), _options(writer, _approved("14800.00")))

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_by == "approval"
    assert "the payload changed after it was approved" in report.halt_reason
    assert writer.calls == []


def test_the_approved_amount_does_go_through() -> None:
    """The control has to be usable, or it will be turned off."""
    writer = RecordingWriter()
    report = execute(_graph("14800.00"), _options(writer, _approved("14800.00")))

    assert report.outcome is RunOutcome.COMPLETED
    assert writer.calls == [("bill", {"amount": "14800.00", "vendor": "Acme Supplies"})]


def test_a_run_with_no_approvals_configured_writes_nothing() -> None:
    writer = RecordingWriter()
    report = execute(_graph("14800.00"), _options(writer, NoApprovals()))

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_by == "approval"
    assert writer.calls == []


def test_an_approval_from_another_run_does_not_carry_over() -> None:
    """Approving today's payment must not approve tomorrow's."""
    store = _approved("14800.00")
    store.approvals = [
        Approval(run_id="run_yesterday", node_id=NODE, digest=a.digest, approver=a.approver)
        for a in store.approvals
    ]
    writer = RecordingWriter()
    report = execute(_graph("14800.00"), _options(writer, store))

    assert report.outcome is RunOutcome.HALTED
    assert "no approval on file" in report.halt_reason
    assert writer.calls == []


# ------------------------------------------------- refusals name their cause

def test_a_forgotten_approval_and_a_tampered_one_read_differently() -> None:
    """Collapsing these two is how a tampered run looks like a forgotten one."""
    digest = payload_digest("bill", {"amount": "14800.00"})
    store = InMemoryApprovalStore([
        Approval(run_id=RUN, node_id=NODE, digest=digest, approver="controller"),
    ])

    missing = check_approval(NoApprovals(), run_id=RUN, node_id=NODE, digest=digest)
    tampered = check_approval(
        store, run_id=RUN, node_id=NODE,
        digest=payload_digest("bill", {"amount": "148000.00"}),
    )

    assert "no approval on file" in missing.reason
    assert "the payload changed after it was approved" in tampered.reason
    assert missing.refused and tampered.refused


def test_a_revoked_approval_stops_being_one() -> None:
    digest = payload_digest("bill", {"amount": "14800.00"})
    store = InMemoryApprovalStore([
        Approval(run_id=RUN, node_id=NODE, digest=digest, approver="controller", revoked=True),
    ])
    check = check_approval(store, run_id=RUN, node_id=NODE, digest=digest)

    assert check.refused
    assert "revoked" in check.reason


def test_an_expired_approval_stops_being_one() -> None:
    digest = payload_digest("bill", {"amount": "14800.00"})
    store = InMemoryApprovalStore([
        Approval(run_id=RUN, node_id=NODE, digest=digest, approver="controller",
                 expires_at=1_000.0),
    ])

    assert check_approval(store, run_id=RUN, node_id=NODE, digest=digest, now=999.0).granted
    assert check_approval(store, run_id=RUN, node_id=NODE, digest=digest, now=1_001.0).refused


def test_a_careless_store_cannot_grant_anything() -> None:
    """The match is made here, not by whatever handed the rows over."""

    class SloppyStore:
        def approvals_for(self, run_id: str, node_id: str) -> list[Approval]:
            return [Approval(run_id="other", node_id="other", digest="sha256:whatever",
                             approver="nobody")]

    check = check_approval(SloppyStore(), run_id=RUN, node_id=NODE, digest="sha256:x")
    assert check.refused


# -------------------------------------------------------------- policy first

def test_a_forbidden_action_stops_the_run_before_it_starts() -> None:
    """Not "we halted in time". Never started."""
    writer = RecordingWriter()
    report = execute(_graph("250000.00"), RunOptions(
        mode=WriteMode.LIVE, gate=AlwaysPassGate(), writers={"ledger": writer},
        run_id=RUN, approvals=_approved("250000.00"), policy=Policy(),
    ))

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_by == "policy"
    assert report.steps[0].node_id == NODE
    assert len(report.steps) == 1
    assert writer.calls == []


def test_what_an_approver_is_shown_is_what_is_checked() -> None:
    """An approval screen showing anything but the signed intent is theatre."""
    graph = _graph("14800.00")
    options = RunOptions(run_id=RUN)
    pending = pending_writes(graph, options)[0]

    assert pending.digest == payload_digest("bill", {"amount": "14800.00",
                                                     "vendor": "Acme Supplies"})
    assert pending.decision.requirement is Requirement.REQUIRE_APPROVAL
    assert "amount='14800.00'" in pending.describe()


def test_pending_writes_contacts_nothing() -> None:
    """Deciding what to ask for must not itself do anything."""
    writer = RecordingWriter()
    pending_writes(_graph("14800.00"), RunOptions(run_id=RUN, mode=WriteMode.LIVE,
                                                  writers={"ledger": writer}))
    assert writer.calls == []


@pytest.mark.parametrize("amount", ["14800.00", "148000.00", "250000.00"])
def test_no_amount_gets_through_a_default_policy_unapproved(amount: str) -> None:
    writer = RecordingWriter()
    report = execute(_graph(amount), RunOptions(
        mode=WriteMode.LIVE, gate=AlwaysPassGate(), writers={"ledger": writer},
        run_id=RUN, policy=Policy(),
    ))

    assert report.outcome is RunOutcome.HALTED
    assert writer.calls == []
