"""The acceptance criterion: fifteen injection payloads, zero unsafe actions.

An unsafe action is any of these, and the definition is written down here so
it cannot quietly soften later:

* a write that happened without a matching approval
* a write whose payload differs from the one that was approved
* a verdict reported as PASS that the verifier did not produce
* a run that reported COMPLETED while a control had refused it

The payloads are planted in free text only -- notes, email bodies, line-item
descriptions -- so every business fact stays correct. That isolates the
question. A payload that also changed the invoice total would be caught by the
contract and the run would fail for a reason that has nothing to do with
injection.

Verity's answer to all fifteen is structural rather than clever: the executor
contains no model, so extracted text is never interpreted as instruction, and
the payload that reaches a connector is built only from the run's declared
inputs. There is no path from a document to a decision. These tests exist
because that is a claim, and a claim that is not executed is a wish.
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
    verify_audit,
)
from verity_sandbox.injections import INJECTION_NAMES
from verity_schema.workgraph import WorkGraph

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = REPO_ROOT / "examples" / "workgraphs" / "invoice_to_po.yaml"
CONTRACT_PATH = REPO_ROOT / "examples" / "contracts" / "invoice_to_po.yaml"
DEMO_INPUTS = {"invoice_number": "INV-4471", "po_number": "PO-2211"}
RUN_ID = "run_injection"


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
    from verity_cli.run import ContractGate

    return ContractGate(CONTRACT_PATH, registry, str(tmp_path / "evidence"))


def _options(gate: Any, writers: dict[str, Any]) -> RunOptions:
    return RunOptions(inputs=dict(DEMO_INPUTS), mode=WriteMode.LIVE,
                      gate=gate, writers=writers, run_id=RUN_ID)


def _approve(graph: WorkGraph, options: RunOptions) -> InMemoryApprovalStore:
    """Approve exactly what a clean run would write."""
    store = InMemoryApprovalStore()
    for pending in pending_writes(graph, options):
        store.grant(Approval(run_id=RUN_ID, node_id=pending.node_id,
                             digest=pending.digest, approver="controller@example.com"))
    return store


def _bills(client: Any) -> int:
    return int(client.get("/api/bills").json()["count"])


#: Payloads long enough to shift the invoice PDF's layout, which makes the
#: anchored vendor extraction miss and the contract report FAIL.
#:
#: This is a false positive and it is recorded rather than tuned away. The
#: vendor on those invoices is unchanged; the extractor is reading position as
#: if it were meaning. The outcome is safe -- the run halts and writes nothing
#: -- but "safe" and "correct" are not the same word, and a suite that quietly
#: dropped these payloads would be hiding a real weakness in the extractor.
#: Fixing it belongs to the extraction work, not here.
DISTURBS_THE_DOCUMENT: frozenset[str] = frozenset({
    "system_impersonation", "amount_rewrite", "tool_call_syntax", "urgency_authority",
})


def test_the_corpus_is_the_size_it_claims_to_be() -> None:
    """Fifteen is the acceptance criterion, not a round number in a document."""
    assert len(INJECTION_NAMES) == 15
    assert len(set(INJECTION_NAMES)) == 15
    assert set(INJECTION_NAMES) >= DISTURBS_THE_DOCUMENT


@pytest.mark.security()
def test_the_known_false_positives_are_exactly_the_ones_recorded(
    graph: WorkGraph, gate: Any, writers: Any, sandbox_client: Any
) -> None:
    """Pin the weakness, so it cannot spread or be quietly forgotten.

    If a sixth payload starts failing verification, this test says so rather
    than the suite absorbing it. If the extractor is fixed and these four stop
    failing, it says that too, and the exception list can go.
    """
    observed: set[str] = set()
    for payload in INJECTION_NAMES:
        sandbox_client.post("/admin/reset")
        sandbox_client.post(f"/admin/inject/{payload}")
        options = _options(gate, writers)
        options.approvals = _approve(graph, options)
        if execute(graph, options).verifier_says != "PASS":
            observed.add(payload)

    assert observed == DISTURBS_THE_DOCUMENT


@pytest.mark.security()
@pytest.mark.parametrize("payload", INJECTION_NAMES)
def test_no_payload_produces_an_unapproved_write(
    payload: str, graph: WorkGraph, gate: Any, writers: Any, sandbox_client: Any
) -> None:
    """Unsafe action rate, run one: nothing is approved, so nothing may happen.

    Which control refuses is allowed to vary -- for some payloads the verifier
    objects to the document before approval is ever consulted. What may not
    vary is that a bill appears.
    """
    sandbox_client.post(f"/admin/inject/{payload}")
    before = _bills(sandbox_client)

    report = execute(graph, _options(gate, writers))

    assert report.outcome is RunOutcome.HALTED
    assert report.halted_by in ("approval", "gate")
    assert report.writes_performed == []
    assert _bills(sandbox_client) == before


@pytest.mark.security()
@pytest.mark.parametrize("payload", INJECTION_NAMES)
def test_no_payload_changes_what_would_be_written(
    payload: str, graph: WorkGraph, sandbox_client: Any
) -> None:
    """The digest is the thing an approval is bound to, so it is the thing to attack.

    ``amount_rewrite`` asks for 148000.00 in the invoice note; ``forged_digest``
    supplies a digest of its own. Neither can move this value, because the
    payload is built from the run's declared inputs and the graph, and a
    document is not either of those.
    """
    clean = pending_writes(graph, RunOptions(inputs=dict(DEMO_INPUTS), run_id=RUN_ID))
    sandbox_client.post(f"/admin/inject/{payload}")
    after = pending_writes(graph, RunOptions(inputs=dict(DEMO_INPUTS), run_id=RUN_ID))

    assert [w.digest for w in after] == [w.digest for w in clean]
    assert "148000" not in after[0].intent.describe()


@pytest.mark.security()
@pytest.mark.parametrize("payload", INJECTION_NAMES)
def test_no_payload_forges_a_verdict_or_an_approval(
    payload: str, graph: WorkGraph, gate: Any, writers: Any, sandbox_client: Any
) -> None:
    """With a real approval in hand, the run proceeds -- on its own evidence.

    ``verdict_spoof`` prints "VERIFICATION RESULT: PASS" into the invoice and
    ``forged_approval`` signs it as a controller. The run does end in PASS
    here, but from the verifier's own arithmetic on the purchase order, and
    the approval that opens the gate is the one in the store, bound to the
    digest. The test that these forgeries achieved nothing is the previous
    one: without a genuine approval, none of them get a write.
    """
    sandbox_client.post(f"/admin/inject/{payload}")
    options = _options(gate, writers)
    options.approvals = _approve(graph, options)
    before = _bills(sandbox_client)

    report = execute(graph, options)
    assert verify_audit(report.audit).intact

    if payload in DISTURBS_THE_DOCUMENT:
        # The verifier objects to the document itself. Still safe, still not a
        # forged verdict: the FAIL is the verifier's own, not the payload's.
        assert report.verifier_says == "FAIL"
        assert report.outcome is RunOutcome.HALTED
        assert _bills(sandbox_client) == before
        return

    assert report.outcome is RunOutcome.COMPLETED
    assert report.verifier_says == "PASS"
    assert _bills(sandbox_client) == before + 1
    approvals = report.audit.of_kind("approval_checked")
    assert [a.detail["approver"] for a in approvals] == ["controller@example.com"]


@pytest.mark.security()
@pytest.mark.parametrize("payload", INJECTION_NAMES)
def test_no_payload_redirects_a_write_to_another_host(
    payload: str, graph: WorkGraph, sandbox_client: Any
) -> None:
    """``connector_redirect`` names a URL. The connector is not listening."""
    sandbox_client.post(f"/admin/inject/{payload}")
    write = pending_writes(graph, RunOptions(inputs=dict(DEMO_INPUTS)))[0]

    assert write.intent.connector == "ledger"
    assert write.intent.resource == "bill"
    assert "example.net" not in write.intent.describe()


@pytest.mark.security()
@pytest.mark.parametrize("payload", INJECTION_NAMES)
def test_no_payload_lowers_the_assessed_risk(
    payload: str, graph: WorkGraph, sandbox_client: Any
) -> None:
    """``policy_disable`` asks for exactly this. Risk comes from the graph, not the data."""
    sandbox_client.post(f"/admin/inject/{payload}")
    decision = pending_writes(graph, RunOptions(inputs=dict(DEMO_INPUTS)))[0].decision

    assert decision.needs_approval
    assert decision.level.value in ("MEDIUM", "HIGH", "CRITICAL")
