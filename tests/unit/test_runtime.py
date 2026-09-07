"""Planning and execution, with nothing real behind them.

The executor is tested here against stub gates and a stub writer, so that
every branch of "may this write happen" is exercised without a sandbox. The
integration counterpart runs the same code against the real thing.
"""

from __future__ import annotations

from typing import Any

import pytest
from verity_connectors import WriteMode
from verity_runtime import (
    AlwaysPassGate,
    Approval,
    ClosedGate,
    GateResult,
    GateVerdict,
    InMemoryApprovalStore,
    PlanError,
    RunOptions,
    RunOutcome,
    execute,
    pending_writes,
    plan,
)
from verity_schema import RiskLevel
from verity_schema.workgraph import Edge, Node, WorkGraph, WriteSpec

DEMO_INPUTS = {"invoice_number": "INV-4471"}


class RecordingWriter:
    """A writable connector that records instead of doing."""

    name = "ledger"
    channel = "api"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def write(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((resource, dict(payload)))
        return {"id": f"bill_{len(self.calls):04d}", **payload}


class StubGate:
    def __init__(self, verdict: GateVerdict, reason: str = "") -> None:
        self._result = GateResult(verdict=verdict, reason=reason)
        self.calls = 0

    def check(self, inputs: dict[str, str]) -> GateResult:
        self.calls += 1
        return self._result


def _graph(*, write: bool = True) -> WorkGraph:
    nodes = [
        Node(id="open", type="NAVIGATE", label="Open the inbox"),
        Node(id="read", type="EXTRACT", label="Read the purchase order"),
    ]
    edges = [Edge(**{"from": "open", "to": "read"})]
    if write:
        nodes.append(Node(
            id="create_bill", type="CREATE_RECORD", label="Create the draft bill",
            risk=RiskLevel.MEDIUM,
            write=WriteSpec(connector="ledger", resource="bill", payload={
                "vendor": "Acme Supplies",
                "ref": "{{ inputs.invoice_number }}",
                "amount": "14800.00",
            }),
        ))
        edges.append(Edge(**{"from": "read", "to": "create_bill"}))
    return WorkGraph(name="invoice_to_po", nodes=nodes, edges=edges)


def _options(gate: Any, writer: RecordingWriter, mode: WriteMode = WriteMode.LIVE) -> RunOptions:
    return RunOptions(inputs=dict(DEMO_INPUTS), mode=mode, gate=gate,
                      writers={"ledger": writer}, run_id="run_fixed")


def _run(graph: WorkGraph, options: RunOptions) -> Any:
    """Execute with every pending write already approved.

    Tests about the gate should fail for gate reasons, so approval is granted
    here rather than left to also be missing. Each control is tested where it
    is the only thing that could stop the run.
    """
    store = InMemoryApprovalStore()
    for pending in pending_writes(graph, options):
        store.grant(Approval(
            run_id=options.run_id, node_id=pending.node_id,
            digest=pending.digest, approver="ops@example.com",
        ))
    options.approvals = store
    return execute(graph, options)


# ------------------------------------------------------------------ planning

def test_the_plan_is_the_same_every_time() -> None:
    """Replay is worthless if the order can vary between runs."""
    graph = _graph()
    orders = {tuple(s.id for s in plan(graph).steps) for _ in range(10)}
    assert orders == {("open", "read", "create_bill")}


def test_only_world_changing_verbs_are_consequential() -> None:
    steps = {s.id: s.consequential for s in plan(_graph()).steps}
    assert steps == {"open": False, "read": False, "create_bill": True}


def test_a_cycle_is_refused_before_anything_runs() -> None:
    graph = WorkGraph(
        name="loop",
        nodes=[Node(id="a", type="NAVIGATE"), Node(id="b", type="NAVIGATE")],
        edges=[Edge(**{"from": "a", "to": "b"}), Edge(**{"from": "b", "to": "a"})],
    )
    with pytest.raises(PlanError, match="cycle"):
        plan(graph)


def test_an_edge_to_a_missing_node_is_refused() -> None:
    graph = WorkGraph(
        name="dangling", nodes=[Node(id="a", type="NAVIGATE")],
        edges=[Edge(**{"from": "a", "to": "ghost"})],
    )
    with pytest.raises(PlanError, match="unknown node"):
        plan(graph)


# ----------------------------------------------------------------- the gate

def test_a_runtime_with_no_gate_configured_cannot_write() -> None:
    """The default must be refusal.

    A missing decision is not permission. If the safe direction were the one
    you had to opt into, the first misconfigured deployment would write.
    """
    writer = RecordingWriter()
    report = execute(_graph(), _options(ClosedGate(), writer))

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_at == "create_bill"
    assert writer.calls == []


@pytest.mark.parametrize(
    ("verdict", "writes"),
    [
        (GateVerdict.PASS, True),
        (GateVerdict.FAIL, False),
        (GateVerdict.DRIFT, False),
        (GateVerdict.INCONCLUSIVE, False),
    ],
)
def test_only_a_pass_opens_the_gate(verdict: GateVerdict, writes: bool) -> None:
    """INCONCLUSIVE is the one that matters.

    'I could not check' is not 'it is fine'. Every system that collapses the
    two is lying about what it knows, and this parametrisation is what stops
    that collapse happening by accident later.
    """
    writer = RecordingWriter()
    graph = _graph()
    report = _run(graph, _options(StubGate(verdict), writer))

    assert bool(writer.calls) is writes
    assert (report.outcome is RunOutcome.COMPLETED) is writes


def test_the_gate_is_consulted_once_not_per_step() -> None:
    gate = StubGate(GateVerdict.PASS)
    graph = _graph()
    _run(graph, _options(gate, RecordingWriter()))
    assert gate.calls == 1


def test_a_graph_with_nothing_consequential_never_consults_the_gate() -> None:
    """Reading changes nothing, so there is nothing to authorise."""
    gate = StubGate(GateVerdict.FAIL)
    report = execute(_graph(write=False), _options(gate, RecordingWriter()))

    assert gate.calls == 0
    assert report.outcome is RunOutcome.COMPLETED


# ------------------------------------------------------------------ reporting

def test_the_report_puts_the_two_claims_side_by_side() -> None:
    """The product's thesis, as two fields that can disagree."""
    report = execute(_graph(), _options(StubGate(GateVerdict.FAIL), RecordingWriter()))

    assert report.runtime_said == "DONE"
    assert report.verifier_says == "FAIL"
    assert report.contradicted


def test_a_halt_says_what_was_not_done() -> None:
    report = execute(_graph(), _options(StubGate(GateVerdict.FAIL), RecordingWriter()))

    assert report.not_performed == ["CREATE_RECORD Create the draft bill"]
    assert report.outcome.exit_code == 2


def test_a_dry_run_performs_nothing_even_when_verification_passes() -> None:
    writer = RecordingWriter()
    graph = _graph()
    report = _run(graph, _options(AlwaysPassGate(), writer, WriteMode.DRY_RUN))

    assert report.outcome is RunOutcome.COMPLETED
    assert writer.calls == []
    assert report.writes_performed == []


# ------------------------------------------------------------------ payloads

def test_a_payload_interpolates_only_declared_inputs() -> None:
    writer = RecordingWriter()
    graph = _graph()
    _run(graph, _options(AlwaysPassGate(), writer))

    _, payload = writer.calls[0]
    assert payload["ref"] == "INV-4471"
    assert payload["amount"] == "14800.00"


def test_an_unknown_placeholder_is_left_alone_rather_than_blanked() -> None:
    """A payload that quietly loses a field would be written anyway.

    Leaving the placeholder visible makes the mistake obvious in the audit
    record and in any approval a person is shown.
    """
    graph = WorkGraph(
        name="odd",
        nodes=[Node(id="w", type="CREATE_RECORD", write=WriteSpec(
            connector="ledger", resource="bill",
            payload={"ref": "{{ inputs.not_supplied }}"}))],
    )
    writer = RecordingWriter()
    _run(graph, _options(AlwaysPassGate(), writer))

    assert writer.calls[0][1]["ref"] == "{{ inputs.not_supplied }}"


def test_a_consequential_node_with_no_write_spec_is_an_error_not_a_guess() -> None:
    graph = WorkGraph(name="vague", nodes=[Node(id="w", type="CREATE_RECORD")])
    report = execute(graph, _options(AlwaysPassGate(), RecordingWriter()))

    assert report.steps[-1].status == "error"
    assert "no write spec" in report.steps[-1].error


def test_execution_costs_nothing_and_calls_no_model() -> None:
    graph = _graph()
    report = _run(graph, _options(AlwaysPassGate(), RecordingWriter()))
    assert report.model_calls == 0
    assert report.cost_usd == 0.0
