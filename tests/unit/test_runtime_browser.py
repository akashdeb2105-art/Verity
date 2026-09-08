"""The executor drives read steps through a browser driver -- or a no-op.

Two things are on trial here. That the default is still a recorded no-op, so
every run that does not ask for ``--browser`` costs nothing and behaves exactly
as it did before Tier 2. And that a driven read which fails to observe what it
claimed to halts the run before a consequential step -- the ``INCONCLUSIVE``
rule, one step earlier.
"""

from __future__ import annotations

from typing import Any

from verity_connectors import WriteMode
from verity_runtime import (
    AlwaysPassGate,
    Approval,
    InMemoryApprovalStore,
    RunOptions,
    RunOutcome,
    execute,
    pending_writes,
)
from verity_runtime.ports import BrowserObservation, RecordedNoOpDriver
from verity_schema.trace import RuntimeInfo, Trace, TraceStep
from verity_schema.workgraph import BrowserAction, Edge, Node, WorkGraph, WriteSpec

DEMO_INPUTS = {"invoice_number": "INV-4471"}


class RecordingWriter:
    name = "ledger"
    channel = "api"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def write(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((resource, dict(payload)))
        return {"id": "bill_0001", **payload}


class FakeBrowser:
    """Records every call, answers from a script, and can be told to fail."""

    tier = "browser"

    def __init__(self, *, fail_on: str = "") -> None:
        self.fail_on = fail_on
        self.calls: list[str] = []
        self.closed = 0
        self.finished = 0

    def perform(
        self, *, verb: str, action: BrowserAction | None, intent: str,
        inputs: dict[str, str], timeout_ms: int,
    ) -> BrowserObservation:
        self.calls.append(verb)
        if verb == self.fail_on:
            return BrowserObservation(action=verb, ok=False, error="target not found")
        extracted = {"total": "14800.00"} if verb == "EXTRACT" else {}
        return BrowserObservation(action=verb, url="/ui/x", dom_hash="dom1:abc",
                                  extracted=extracted)

    def finish(self) -> Trace | None:
        self.finished += 1
        return Trace(
            run_id="r", runtime=RuntimeInfo(name="verity-browser"),
            steps=[TraceStep(seq=i + 1, action=v) for i, v in enumerate(self.calls)],
        )

    def close(self) -> None:
        self.closed += 1


def _graph(*, write: bool = True) -> WorkGraph:
    nodes = [
        Node(id="open", type="NAVIGATE", label="Open", browser=BrowserAction(url="/ui/x")),
        Node(id="filter", type="TYPE", label="Type",
             browser=BrowserAction(target="q", value="{{ inputs.invoice_number }}")),
        Node(id="read", type="EXTRACT", label="Read",
             browser=BrowserAction(extract={"total": "inv-total"})),
    ]
    edges = [Edge(**{"from": "open", "to": "filter"}), Edge(**{"from": "filter", "to": "read"})]
    if write:
        nodes.append(Node(
            id="create_bill", type="CREATE_RECORD", label="Create the bill",
            write=WriteSpec(connector="ledger", resource="bill", payload={
                "vendor": "Acme", "ref": "{{ inputs.invoice_number }}", "amount": "14800.00",
            }),
        ))
        edges.append(Edge(**{"from": "read", "to": "create_bill"}))
    return WorkGraph(name="g", nodes=nodes, edges=edges)


def _options(browser: Any, writer: RecordingWriter) -> RunOptions:
    return RunOptions(
        inputs=dict(DEMO_INPUTS), mode=WriteMode.LIVE, gate=AlwaysPassGate(),
        writers={"ledger": writer}, run_id="run_fixed", browser=browser,
    )


def _approved(graph: WorkGraph, options: RunOptions) -> Any:
    store = InMemoryApprovalStore()
    for p in pending_writes(graph, options):
        store.grant(Approval(run_id=options.run_id, node_id=p.node_id, digest=p.digest,
                             approver="ops@example.com"))
    options.approvals = store
    return execute(graph, options)


# --------------------------------------------------------------- the default

def test_the_default_driver_is_a_recorded_no_op() -> None:
    assert isinstance(RunOptions().browser, RecordedNoOpDriver)


def test_a_run_with_no_browser_behaves_exactly_as_before() -> None:
    """The read steps record {action, intent} and nothing else; the run completes."""
    writer = RecordingWriter()
    report = _approved(_graph(), _options(RecordedNoOpDriver(), writer))

    assert report.outcome is RunOutcome.COMPLETED
    assert report.executor_tier == "recorded"
    assert report.trace is None
    read = next(s for s in report.steps if s.node_id == "read")
    assert read.outputs == {"action": "EXTRACT", "intent": "Read"}
    assert writer.calls  # the write still happened


# --------------------------------------------------------------- driven reads

def test_each_read_step_is_carried_out_by_the_driver() -> None:
    browser = FakeBrowser()
    report = _approved(_graph(), _options(browser, RecordingWriter()))

    assert browser.calls == ["NAVIGATE", "TYPE", "EXTRACT"]
    assert report.executor_tier == "browser"
    assert report.trace is not None and len(report.trace.steps) == 3
    read = next(s for s in report.steps if s.node_id == "read")
    assert read.outputs["extracted"] == {"total": "14800.00"}
    assert read.outputs["dom_hash"] == "dom1:abc"


def test_the_browser_is_finished_and_closed_once() -> None:
    browser = FakeBrowser()
    _approved(_graph(), _options(browser, RecordingWriter()))
    assert (browser.finished, browser.closed) == (1, 1)


def test_a_failed_read_halts_before_a_consequential_step() -> None:
    """Addition C: a read that did not observe what it claimed to is not
    permission to write, any more than INCONCLUSIVE is."""
    writer = RecordingWriter()
    browser = FakeBrowser(fail_on="EXTRACT")
    report = _approved(_graph(), _options(browser, writer))

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_by == "observation"
    assert report.halted_at == "create_bill"
    assert writer.calls == []
    assert browser.closed == 1  # still released on the halt path


def test_a_failed_read_with_nothing_left_to_write_just_fails() -> None:
    writer = RecordingWriter()
    browser = FakeBrowser(fail_on="EXTRACT")
    report = _approved(_graph(write=False), _options(browser, writer))

    assert report.outcome is RunOutcome.FAILED
    assert report.halted_by == ""


def test_a_recorded_no_op_read_never_triggers_the_observation_halt() -> None:
    """The halt is for a read that was actually driven and failed, not for one
    the runtime only noted."""
    report = _approved(_graph(), _options(RecordedNoOpDriver(), RecordingWriter()))
    assert report.outcome is RunOutcome.COMPLETED
