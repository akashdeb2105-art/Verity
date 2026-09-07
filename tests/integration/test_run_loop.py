"""Running the real workflow against the real sandbox, and being stopped.

This is the M2 claim executed rather than described: an agent that reports
success, a verifier that disagrees, and a write that never happens. Every
object here is the real one -- the sandbox's own API, the reference contract,
the example WorkGraph, the actual verifier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from verity_connectors import HttpWriteConnector, WriteMode
from verity_runtime import RunOptions, RunOutcome, execute
from verity_schema.workgraph import WorkGraph

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = REPO_ROOT / "examples" / "workgraphs" / "invoice_to_po.yaml"
CONTRACT_PATH = REPO_ROOT / "examples" / "contracts" / "invoice_to_po.yaml"
DEMO_INPUTS = {"invoice_number": "INV-4471", "po_number": "PO-2211"}


@pytest.fixture()
def graph() -> WorkGraph:
    return WorkGraph.model_validate(yaml.safe_load(GRAPH_PATH.read_text("utf-8")))


@pytest.fixture()
def writers(sandbox_client: Any) -> dict[str, Any]:
    return {"ledger": HttpWriteConnector(
        name="ledger", base_url="http://sandbox",
        routes={"bill": "/api/bills"}, client=sandbox_client,
    )}


@pytest.fixture()
def gate(registry: Any, tmp_path: Path) -> Any:
    """The real verifier, behind the runtime's port.

    Built through the CLI's own adapter, so the thing under test is the
    composition the product actually ships -- not a stand-in that happens to
    agree with it.
    """
    from verity_cli.run import ContractGate

    return ContractGate(CONTRACT_PATH, registry, str(tmp_path / "evidence"))


def _run(graph: WorkGraph, gate: Any, writers: dict[str, Any],
         mode: WriteMode = WriteMode.LIVE) -> Any:
    return execute(graph, RunOptions(
        inputs=dict(DEMO_INPUTS), mode=mode, gate=gate, writers=writers,
    ))


def _bills(client: Any) -> int:
    return int(client.get("/api/bills").json()["count"])


def _state_hash(client: Any) -> str:
    return str(client.get("/admin/state-hash").json()["state_hash"])


def test_a_correct_run_verifies_and_then_writes(
    graph: WorkGraph, gate: Any, writers: Any, sandbox_client: Any
) -> None:
    before = _bills(sandbox_client)
    report = _run(graph, gate, writers)

    assert report.outcome is RunOutcome.COMPLETED
    assert report.verifier_says == "PASS"
    assert _bills(sandbox_client) == before + 1
    assert [s.node_id for s in report.writes_performed] == ["create_bill"]


def test_the_altered_invoice_halts_before_the_bill_exists(
    graph: WorkGraph, gate: Any, writers: Any, sandbox_client: Any
) -> None:
    """The flagship case: $148,000 invoice against a $14,800 purchase order.

    Every step the runtime attempted succeeded, so an executor without a
    verifier would report DONE and leave a payable behind. The point of the
    product is the next two lines.
    """
    sandbox_client.post("/admin/perturb/amount_changed")
    before_hash, before_count = _state_hash(sandbox_client), _bills(sandbox_client)

    report = _run(graph, gate, writers)

    assert report.runtime_said == "DONE"
    assert report.verifier_says == "FAIL"
    assert report.contradicted

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_at == "create_bill"
    assert report.gate is not None
    assert report.gate.first_divergence == "amount_match"

    # The part that matters is not the verdict. It is that nothing happened.
    assert _bills(sandbox_client) == before_count
    assert _state_hash(sandbox_client) == before_hash


def test_a_dry_run_changes_nothing_even_when_everything_passes(
    graph: WorkGraph, gate: Any, writers: Any, sandbox_client: Any
) -> None:
    """'See what it would do' has to be safe to say out loud."""
    before = _state_hash(sandbox_client)
    report = _run(graph, gate, writers, mode=WriteMode.DRY_RUN)

    assert report.outcome is RunOutcome.COMPLETED
    assert report.verifier_says == "PASS"
    assert report.writes_performed == []
    assert _state_hash(sandbox_client) == before


def test_ten_replays_take_the_same_path_and_cost_nothing(
    graph: WorkGraph, gate: Any, writers: Any
) -> None:
    """Determinism is what makes a nightly canary affordable and a diff meaningful."""
    reports = [_run(graph, gate, writers, mode=WriteMode.DRY_RUN) for _ in range(10)]

    sequences = {tuple(r.node_sequence) for r in reports}
    assert sequences == {("open_inbox", "read_invoice", "read_purchase_order", "create_bill")}
    assert {r.verifier_says for r in reports} == {"PASS"}
    assert sum(r.model_calls for r in reports) == 0
    assert sum(r.cost_usd for r in reports) == 0.0


@pytest.mark.parametrize(
    "perturbation", ["amount_changed", "vendor_changed", "duplicate_invoice"],
)
def test_every_fault_stops_at_the_same_place(
    perturbation: str, graph: WorkGraph, gate: Any, writers: Any, sandbox_client: Any
) -> None:
    """Different faults, one outcome: the consequential write is not reached.

    The verdicts differ -- a duplicate is INCONCLUSIVE, not FAIL -- and that
    distinction is preserved. What must not differ is whether anything was
    written.
    """
    sandbox_client.post(f"/admin/perturb/{perturbation}")
    before = _state_hash(sandbox_client)

    report = _run(graph, gate, writers)

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_at == "create_bill"
    assert report.verifier_says != "PASS"
    assert _state_hash(sandbox_client) == before
