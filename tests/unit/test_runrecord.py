"""Writing a run to disk, reading it back, and diffing two of them.

Replay is a comparison, and this is where the comparison is defined: what is a
difference, what is deliberately not one (timing, run ids, audit hashes), and
what happens when the two runs were not produced the same way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from verity_connectors import WriteMode
from verity_runtime import (
    AlwaysPassGate,
    Approval,
    InMemoryApprovalStore,
    RunOptions,
    diff_runs,
    execute,
    pending_writes,
    plan,
    read_run_record,
    record_from_report,
    write_run_record,
)
from verity_runtime.ports import BrowserObservation
from verity_schema.trace import Trace
from verity_schema.workgraph import BrowserAction, Edge, Node, WorkGraph, WriteSpec

DEMO_INPUTS = {"invoice_number": "INV-4471"}


class RecordingWriter:
    name = "ledger"
    channel = "api"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def write(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((resource, dict(payload)))
        return {"id": f"bill_{len(self.calls):04d}", **payload}


class ScriptedBrowser:
    """A driver that replays a fixed list of observations, one per read step."""

    def __init__(self, tier: str, observations: list[BrowserObservation]) -> None:
        self.tier = tier
        self._observations = list(observations)
        self._i = 0

    def perform(self, **_: Any) -> BrowserObservation:
        obs = self._observations[self._i]
        self._i += 1
        return obs

    def finish(self) -> Trace | None:
        return None

    def close(self) -> None:
        return None


def _graph() -> WorkGraph:
    return WorkGraph(
        name="invoice_to_po_browser",
        nodes=[
            Node(id="open", type="NAVIGATE", label="Open", browser=BrowserAction(url="/ui/x")),
            Node(id="read", type="EXTRACT", label="Read the row",
                 browser=BrowserAction(extract={"total": "inv-total"})),
            Node(id="create_bill", type="CREATE_RECORD", label="Create the bill",
                 write=WriteSpec(connector="ledger", resource="bill", payload={
                     "vendor": "Acme", "ref": "{{ inputs.invoice_number }}", "amount": "14800.00",
                 })),
        ],
        edges=[Edge(**{"from": "open", "to": "read"}),
               Edge(**{"from": "read", "to": "create_bill"})],
    )


def _run(graph: WorkGraph, browser: Any) -> Any:
    options = RunOptions(
        inputs=dict(DEMO_INPUTS), mode=WriteMode.LIVE, gate=AlwaysPassGate(),
        writers={"ledger": RecordingWriter()}, run_id="run_a", browser=browser,
    )
    store = InMemoryApprovalStore()
    for p in pending_writes(graph, options):
        store.grant(Approval(run_id="run_a", node_id=p.node_id, digest=p.digest,
                             approver="ops@example.com"))
    options.approvals = store
    return execute(graph, options)


def _browser(total: str = "14800.00", dom: str = "dom1:aaaa") -> ScriptedBrowser:
    return ScriptedBrowser("browser", [
        BrowserObservation(action="NAVIGATE", url="/ui/x", dom_hash=dom),
        BrowserObservation(action="EXTRACT", url="/ui/x", dom_hash=dom,
                           extracted={"total": total}),
    ])


# ------------------------------------------------------------- round trip

def test_a_run_record_round_trips_through_disk(tmp_path: Path) -> None:
    graph = _graph()
    report = _run(graph, _browser())
    directory = write_run_record(report, plan(graph), runs_dir=str(tmp_path),
                                 meta={"graph_path": "g.yaml"})

    on_disk = read_run_record(directory)
    in_memory = record_from_report(report, plan(graph))

    assert on_disk.reached_sequence == in_memory.reached_sequence
    assert on_disk.executor_tier == in_memory.executor_tier == "browser"
    assert on_disk.step("read").extracted == {"total": "14800.00"}
    assert on_disk.verdict == in_memory.verdict == "PASS"
    assert (directory / "meta.json").is_file()
    assert (directory / "audit.jsonl").is_file()


def test_replaying_an_unchanged_run_reports_no_difference() -> None:
    graph = _graph()
    a = record_from_report(_run(graph, _browser()), plan(graph))
    b = record_from_report(_run(graph, _browser()), plan(graph))
    result = diff_runs(a, b)

    assert result.comparable
    assert result.identical
    assert result.exit_code == 0


# ------------------------------------------------------------- differences

def test_a_changed_extracted_value_is_a_difference() -> None:
    graph = _graph()
    a = record_from_report(_run(graph, _browser(total="14800.00")), plan(graph))
    b = record_from_report(_run(graph, _browser(total="148000.00")), plan(graph))
    kinds = {d.kind for d in diff_runs(a, b).differences}

    assert "extract" in kinds
    assert not diff_runs(a, b).identical


def test_a_changed_page_structure_is_a_difference() -> None:
    graph = _graph()
    a = record_from_report(_run(graph, _browser(dom="dom1:1111")), plan(graph))
    b = record_from_report(_run(graph, _browser(dom="dom1:2222")), plan(graph))
    diffs = diff_runs(a, b).differences

    assert any(d.kind == "structural" for d in diffs)


def test_timing_and_run_id_are_never_a_difference() -> None:
    """The whole reason replay is usable as a canary."""
    graph = _graph()
    a = record_from_report(_run(graph, _browser()), plan(graph))
    b = record_from_report(_run(graph, _browser()), plan(graph))
    # Different run ids, different audit heads by construction; still identical.
    object.__setattr__(b, "run_id", "run_b_much_later")
    object.__setattr__(b, "audit_head", "sha256:deadbeef")
    assert diff_runs(a, b).identical


def test_a_replay_across_the_tier_boundary_is_refused_not_diffed() -> None:
    """Addition B: 'I read the page' and 'I recorded that I would have' differ.

    A browser run replayed without a browser must say so -- not report a screen
    full of structural drift, and never report identical.
    """
    graph = _graph()
    browser_run = record_from_report(_run(graph, _browser()), plan(graph))

    recorded = _run(graph, _RecordedNoOp())
    recorded_run = record_from_report(recorded, plan(graph))

    result = diff_runs(browser_run, recorded_run)
    assert result.comparable is False
    assert result.identical is False
    assert [d.kind for d in result.differences] == ["tier"]
    assert "did not drive a browser" in "\n".join(result.render())


class _RecordedNoOp:
    tier = "recorded"

    def perform(self, *, verb: str, intent: str, **_: Any) -> BrowserObservation:
        return BrowserObservation(action=verb, note=intent)

    def finish(self) -> Trace | None:
        return None

    def close(self) -> None:
        return None


def test_a_structural_difference_is_not_raised_at_the_recorded_tier() -> None:
    """Two recorded runs never carry a dom_hash, so 'structural' cannot fire."""
    graph = _graph()
    a = record_from_report(_run(graph, _RecordedNoOp()), plan(graph))
    b = record_from_report(_run(graph, _RecordedNoOp()), plan(graph))
    result = diff_runs(a, b)
    assert result.identical
    assert all(d.kind != "structural" for d in result.differences)


@pytest.mark.parametrize("missing", ["report.json"])
def test_a_directory_without_a_report_is_not_a_run_record(tmp_path: Path, missing: str) -> None:
    (tmp_path / "meta.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        read_run_record(tmp_path)
