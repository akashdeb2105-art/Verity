"""The audit chain, and the honest limits of what it proves.

Tampering tests here modify entries the way an attacker with file access
would: edit one, drop one, swap two. The chain has to notice all three, and
the last test states plainly the case it cannot notice, so that the guarantee
is never quietly overstated.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from verity_connectors import WriteMode
from verity_runtime import (
    AlwaysPassGate,
    Approval,
    AuditLog,
    Budget,
    InMemoryApprovalStore,
    RunOptions,
    RunOutcome,
    Stoppable,
    execute,
    pending_writes,
    verify_audit,
)
from verity_schema import RiskLevel
from verity_schema.workgraph import Edge, Node, WorkGraph, WriteSpec


class RecordingWriter:
    name = "ledger"
    channel = "api"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def write(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((resource, dict(payload)))
        return {"id": "bill_0001", **payload}


def _graph(steps: int = 2) -> WorkGraph:
    nodes: list[Node] = [Node(id=f"read_{i}", type="EXTRACT", label=f"Read {i}")
                         for i in range(steps)]
    nodes.append(Node(
        id="create_bill", type="CREATE_RECORD", label="Create the draft bill",
        risk=RiskLevel.MEDIUM,
        write=WriteSpec(connector="ledger", resource="bill",
                        payload={"amount": "500.00"}),
    ))
    edges = [Edge(**{"from": nodes[i].id, "to": nodes[i + 1].id})
             for i in range(len(nodes) - 1)]
    return WorkGraph(name="audited", nodes=nodes, edges=edges)


def _run(graph: WorkGraph, **overrides: Any) -> Any:
    writer = overrides.pop("writer", RecordingWriter())
    options = RunOptions(
        mode=WriteMode.LIVE, gate=AlwaysPassGate(), writers={"ledger": writer},
        run_id="run_audited", **overrides,
    )
    store = InMemoryApprovalStore()
    for pending in pending_writes(graph, options):
        store.grant(Approval(run_id=options.run_id, node_id=pending.node_id,
                             digest=pending.digest, approver="controller"))
    options.approvals = store
    return execute(graph, options)


# ---------------------------------------------------------------- the record

def test_every_decision_in_a_run_is_recorded() -> None:
    report = _run(_graph())
    kinds = [e.kind for e in report.audit.entries]

    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_finished"
    for expected in ("classified", "verified", "approval_checked", "step"):
        assert expected in kinds, expected


def test_a_refusal_is_recorded_as_carefully_as_a_success() -> None:
    """The runs worth auditing are the ones that were stopped."""
    report = execute(_graph(), RunOptions(
        mode=WriteMode.LIVE, gate=AlwaysPassGate(),
        writers={"ledger": RecordingWriter()}, run_id="run_audited",
    ))

    halted = report.audit.of_kind("halted")
    assert report.outcome is RunOutcome.HALTED
    assert len(halted) == 1
    assert halted[0].node_id == "create_bill"
    assert halted[0].detail["by"] == "approval"


def test_the_chain_verifies_when_nothing_has_been_touched() -> None:
    report = _run(_graph())
    result = verify_audit(report.audit)

    assert result.intact
    assert result.checked == len(report.audit)
    assert report.audit_intact


def test_the_head_survives_a_round_trip_through_a_file() -> None:
    report = _run(_graph())
    restored = AuditLog.from_jsonl(report.audit.to_jsonl())

    assert verify_audit(restored).intact
    assert restored.head == report.audit_head


# ------------------------------------------------------------- and tampering

def test_editing_an_entry_is_detected() -> None:
    log = _run(_graph()).audit
    target = 2
    log.entries[target] = dataclasses.replace(
        log.entries[target], detail={"why": "nothing to see here"})

    result = verify_audit(log)
    assert not result.intact
    assert result.broken_at == target
    assert "does not match its own contents" in result.reason


def test_removing_an_entry_from_the_middle_is_detected() -> None:
    log = _run(_graph()).audit
    del log.entries[2]

    result = verify_audit(log)
    assert not result.intact
    assert result.broken_at == 2


def test_reordering_two_entries_is_detected() -> None:
    log = _run(_graph()).audit
    log.entries[1], log.entries[2] = log.entries[2], log.entries[1]

    assert not verify_audit(log).intact


def test_truncating_the_end_is_not_detected_and_that_is_stated() -> None:
    """The limit, tested so it cannot be forgotten.

    Cutting the tail off a chain leaves nothing behind to disagree with, so
    the remainder verifies perfectly. Detecting this needs the head recorded
    somewhere the writer cannot reach. Verity has no such anchor yet, which is
    why ``audit.py`` says so and why the head is published on every report.
    """
    log = _run(_graph()).audit
    full_head = log.head
    del log.entries[-2:]

    assert verify_audit(log).intact
    assert log.head != full_head


# --------------------------------------------------------- stopping mid-flight

def test_a_pulled_kill_switch_stops_before_the_next_step() -> None:
    switch = Stoppable()
    switch.pull("an operator noticed something")
    writer = RecordingWriter()
    report = _run(_graph(), kill_switch=switch, writer=writer)

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_by == "kill switch"
    assert "an operator noticed something" in report.halt_reason
    assert writer.calls == []
    assert report.steps[0].node_id == "read_0"


def test_a_kill_switch_pulled_mid_run_stops_the_rest_of_it() -> None:
    """Stopping is checked between steps, so it takes at most one step."""
    switch = Stoppable()
    seen: list[str] = []

    class PullAfterFirstRead:
        def pulled(self) -> tuple[bool, str]:
            seen.append("asked")
            if len(seen) > 2:
                switch.pull("stopped part way")
            return switch.pulled()

    writer = RecordingWriter()
    report = _run(_graph(steps=4), kill_switch=PullAfterFirstRead(), writer=writer)

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_by == "kill switch"
    assert len(report.node_sequence) < 5
    assert writer.calls == []


def test_a_run_over_its_time_budget_stops_rather_than_finishing() -> None:
    ticks = iter([0.0] + [100.0] * 50)
    report = _run(_graph(), budget=Budget(max_seconds=1.0), clock=lambda: next(ticks))

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_by == "budget"
    assert "above the limit" in report.halt_reason


def test_a_plan_over_its_step_budget_never_starts() -> None:
    from verity_runtime import ExecutionError

    with pytest.raises(ExecutionError, match="above the limit"):
        _run(_graph(steps=6), budget=Budget(max_steps=3))


def test_the_stop_is_in_the_audit_record_too() -> None:
    switch = Stoppable()
    switch.pull("operator")
    report = _run(_graph(), kill_switch=switch)

    halted = report.audit.of_kind("halted")
    assert halted[0].detail["by"] == "kill switch"
    assert verify_audit(report.audit).intact
