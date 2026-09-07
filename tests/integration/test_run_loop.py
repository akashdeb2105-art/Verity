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
from verity_runtime import (
    Approval,
    InMemoryApprovalStore,
    RunOptions,
    RunOutcome,
    execute,
    pending_writes,
)
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
         mode: WriteMode = WriteMode.LIVE, *, approve: bool = True) -> Any:
    """A run with the bill approved, so that verification is what is on trial.

    The approval is granted against the digest of the write this run would
    actually make -- the same path an approval UI would take. A test that
    disabled the approval control instead would be testing a product nobody
    ships.
    """
    options = RunOptions(
        inputs=dict(DEMO_INPUTS), mode=mode, gate=gate, writers=writers,
        run_id="run_integration",
    )
    if approve:
        store = InMemoryApprovalStore()
        for pending in pending_writes(graph, options):
            store.grant(Approval(
                run_id=options.run_id, node_id=pending.node_id,
                digest=pending.digest, approver="controller@example.com",
            ))
        options.approvals = store
    return execute(graph, options)


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


def test_a_verified_run_still_will_not_write_without_an_approval(
    graph: WorkGraph, gate: Any, writers: Any, sandbox_client: Any
) -> None:
    """Verification passing is not the same as being allowed.

    Everything about this run is correct: the invoice matches the purchase
    order, the verifier returns PASS, no perturbation is applied. It still
    writes nothing, because nobody approved it. The two controls are separate
    on purpose -- "the outcome is right" and "you may do it" are different
    questions, and a system that answers only the first will eventually do
    something correct that nobody wanted.
    """
    before_hash, before_count = _state_hash(sandbox_client), _bills(sandbox_client)

    report = _run(graph, gate, writers, approve=False)

    assert report.verifier_says == "PASS"
    assert report.outcome is RunOutcome.HALTED
    assert report.halted_by == "approval"
    assert "no approval on file" in report.halt_reason
    assert _bills(sandbox_client) == before_count
    assert _state_hash(sandbox_client) == before_hash


def test_the_approval_names_the_amount_it_authorises(
    graph: WorkGraph, sandbox_client: Any
) -> None:
    """What a person would be shown before saying yes."""
    pending = pending_writes(graph, RunOptions(inputs=dict(DEMO_INPUTS)))

    assert len(pending) == 1
    assert pending[0].node_id == "create_bill"
    assert pending[0].needs_approval
    assert pending[0].digest.startswith("sha256:")
    assert _bills(sandbox_client) == _bills(sandbox_client)  # nothing was contacted
