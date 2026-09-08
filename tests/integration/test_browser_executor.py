"""Tier 2, against a real browser and a real sandbox over a real socket.

Everything here is the shipped object: Chromium, the sandbox served by uvicorn,
the reference contract, the real verifier behind the runtime's gate, and the
example browser WorkGraph. No mock stands in for any of it.

Skipped where Chromium is not installed -- the hermetic PR suite does not need
a browser, and this file says so rather than failing.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright
from verity_browser import BrowserSession
from verity_runtime import (
    Approval,
    InMemoryApprovalStore,
    RunOptions,
    RunOutcome,
)

pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH = REPO_ROOT / "examples" / "workgraphs" / "invoice_to_po_browser.yaml"
CONTRACT = REPO_ROOT / "examples" / "contracts" / "invoice_to_po.yaml"
INPUTS = {"invoice_number": "INV-4471", "po_number": "PO-2211"}


def _chromium_available() -> bool:
    try:
        p = sync_playwright().start()
        try:
            browser = p.chromium.launch()
            browser.close()
            return True
        finally:
            p.stop()
    except Exception:
        return False


if not _chromium_available():  # pragma: no cover - environment dependent
    pytest.skip("Chromium is not installed (playwright install chromium)",
                allow_module_level=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def sandbox_url() -> Iterator[str]:
    import uvicorn
    from verity_sandbox.app import create_app

    port = _free_port()
    config = uvicorn.Config(create_app(seed=1), host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/api/health", timeout=1.0).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:  # pragma: no cover
        raise RuntimeError("sandbox did not come up")
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset(sandbox_url: str) -> None:
    httpx.post(f"{sandbox_url}/admin/reset", timeout=5.0)


def _graph() -> Any:
    from verity_schema.workgraph import WorkGraph

    return WorkGraph.model_validate(yaml.safe_load(GRAPH.read_text("utf-8")))


def _gate(sandbox_url: str, tmp_path: Path) -> Any:
    from verity_cli.run import ContractGate
    from verity_connectors import build_sandbox_registry

    return ContractGate(CONTRACT, build_sandbox_registry(sandbox_url),
                        str(tmp_path / "evidence"))


def _driver(sandbox_url: str, run_id: str) -> Any:
    from verity_cli.browser import PlaywrightDriver

    return PlaywrightDriver(base_url=sandbox_url, run_id=run_id)


def _run(sandbox_url: str, tmp_path: Path, *, run_id: str = "run_browser",
         browser: bool = True) -> Any:
    from verity_connectors import WriteMode, ledger_writer
    from verity_runtime import execute, pending_writes

    graph = _graph()
    options = RunOptions(
        inputs=dict(INPUTS),
        mode=WriteMode.LIVE,
        gate=_gate(sandbox_url, tmp_path),
        writers={"ledger": ledger_writer(sandbox_url)},
        run_id=run_id,
    )
    if browser:
        options.browser = _driver(sandbox_url, run_id)

    store = InMemoryApprovalStore()
    for pending in pending_writes(graph, options):
        store.grant(Approval(run_id=run_id, node_id=pending.node_id,
                             digest=pending.digest, approver="controller@example.com"))
    options.approvals = store
    return execute(graph, options)


def _bills(sandbox_url: str) -> int:
    return int(httpx.get(f"{sandbox_url}/api/bills", timeout=5).json()["count"])


def _state_hash(sandbox_url: str) -> str:
    return str(httpx.get(f"{sandbox_url}/admin/state-hash", timeout=5).json()["state_hash"])


# ------------------------------------------------------- the whole graph

def test_the_browser_drives_the_graph_and_the_write_is_still_gated(
    sandbox_url: str, tmp_path: Path
) -> None:
    before = _bills(sandbox_url)
    report = _run(sandbox_url, tmp_path)

    assert report.outcome is RunOutcome.COMPLETED
    assert report.verifier_says == "PASS"
    assert report.executor_tier == "browser"
    assert report.trace is not None
    assert [s.action for s in report.trace.steps] == [
        "NAVIGATE", "TYPE", "SELECT", "CLICK", "EXTRACT", "NAVIGATE", "EXTRACT",
    ]

    read_invoice = next(s for s in report.steps if s.node_id == "read_invoice")
    assert read_invoice.outputs["extracted"] == {
        "invoice_total": "14800.00", "invoice_vendor": "Acme Supplies",
    }
    assert every_step_has_a_dom_hash(report)
    assert _bills(sandbox_url) == before + 1


def every_step_has_a_dom_hash(report: Any) -> bool:
    driven = [s for s in report.steps if s.outputs.get("action") in {
        "NAVIGATE", "TYPE", "SELECT", "CLICK", "EXTRACT"}]
    return all(s.outputs.get("dom_hash", "").startswith("dom1:") for s in driven)


def test_the_invoices_filter_form_is_read_only(sandbox_url: str) -> None:
    """Addition 4: prove it, do not assert it. The full TYPE/SELECT/CLICK/EXTRACT
    sequence must not move the sandbox state hash by one byte."""
    before = _state_hash(sandbox_url)

    session = BrowserSession(base_url=sandbox_url, run_id="ro_probe")
    try:
        session.navigate("/ui/invoices", timeout_ms=15000)
        session.type("invoice-filter", "INV-4471", timeout_ms=15000)
        session.select("field-filter", "number", timeout_ms=15000)
        session.click("find", timeout_ms=15000)
        seen = session.extract({"total": "inv-total"}, timeout_ms=15000)
    finally:
        session.close()

    assert seen.ok and seen.extracted == {"total": "14800.00"}
    assert _state_hash(sandbox_url) == before


def test_the_altered_invoice_halts_before_the_bill_even_with_a_browser(
    sandbox_url: str, tmp_path: Path
) -> None:
    httpx.post(f"{sandbox_url}/admin/perturb/amount_changed", timeout=5.0)
    before_hash, before_count = _state_hash(sandbox_url), _bills(sandbox_url)

    report = _run(sandbox_url, tmp_path)

    assert report.runtime_said == "DONE"
    assert report.verifier_says == "FAIL"
    assert report.outcome is RunOutcome.HALTED
    assert report.halted_at == "create_bill"
    assert _bills(sandbox_url) == before_count
    assert _state_hash(sandbox_url) == before_hash


# ------------------------------------------------------- replay

def test_replay_of_a_clean_run_is_identical(sandbox_url: str, tmp_path: Path) -> None:
    from verity_runtime import diff_runs, plan, record_from_report

    graph = _graph()
    first = record_from_report(_run(sandbox_url, tmp_path, run_id="r1"), plan(graph))
    httpx.post(f"{sandbox_url}/admin/reset", timeout=5.0)
    second = record_from_report(_run(sandbox_url, tmp_path, run_id="r2"), plan(graph))

    result = diff_runs(first, second)
    assert result.comparable
    assert result.identical, "\n".join(result.render())


def test_a_structural_change_shows_up_as_drift_on_replay(
    sandbox_url: str, tmp_path: Path
) -> None:
    """A second PO-2211 row makes the purchase order page render two tables --
    a structural change on a page the graph visits."""
    from verity_runtime import diff_runs, plan, record_from_report

    graph = _graph()
    baseline = record_from_report(_run(sandbox_url, tmp_path, run_id="base"), plan(graph))

    httpx.post(f"{sandbox_url}/admin/reset", timeout=5.0)
    httpx.post(f"{sandbox_url}/admin/perturb/ambiguous_record", timeout=5.0)
    replay = record_from_report(_run(sandbox_url, tmp_path, run_id="base_replay"), plan(graph))

    result = diff_runs(baseline, replay)
    kinds = {d.kind for d in result.differences}
    assert not result.identical
    assert "structural" in kinds
    assert any(d.node_id == "read_purchase_order" for d in result.differences)


def test_a_browser_run_replayed_without_a_browser_is_a_tier_mismatch(
    sandbox_url: str, tmp_path: Path
) -> None:
    from verity_runtime import diff_runs, plan, record_from_report

    graph = _graph()
    browser_run = record_from_report(_run(sandbox_url, tmp_path, run_id="b1"), plan(graph))
    httpx.post(f"{sandbox_url}/admin/reset", timeout=5.0)
    recorded_run = record_from_report(
        _run(sandbox_url, tmp_path, run_id="b1_norun", browser=False), plan(graph))

    result = diff_runs(browser_run, recorded_run)
    assert result.comparable is False
    assert result.identical is False
    assert [d.kind for d in result.differences] == ["tier"]


# ------------------------------------------------------- the CLI, end to end

def test_verity_run_then_replay_through_the_cli(sandbox_url: str, tmp_path: Path) -> None:
    """meta.json round-trips, replay forces dry-run, and a clean replay is
    identical -- exercised through ``main`` rather than the internals."""
    from verity_cli.main import main

    runs = tmp_path / "runs"
    common = [
        "--contract", str(CONTRACT), "--sandbox", sandbox_url,
        "--input", "invoice_number=INV-4471", "--input", "po_number=PO-2211",
        "--evidence-dir", str(tmp_path / "ev"),
    ]
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        '[{"run_id": "cli_demo", "node_id": "create_bill", '
        '"digest": "sha256:09175befac3289fdec756cedf048c7b2af5fddb3ffa2f544e1b589f134ea79e1", '
        '"approver": "controller@example.com"}]',
        encoding="utf-8",
    )

    code = main([
        "run", str(GRAPH), "--browser", "--run-id", "cli_demo",
        "--approvals", str(approvals), "--runs-dir", str(runs), *common,
    ])
    assert code == 0
    assert (runs / "cli_demo" / "meta.json").is_file()
    assert _bills(sandbox_url) == 2

    httpx.post(f"{sandbox_url}/admin/reset", timeout=5.0)
    identical = main(["replay", "cli_demo", "--runs-dir", str(runs),
                      "--sandbox", sandbox_url, "--evidence-dir", str(tmp_path / "ev2")])
    assert identical == 0
    assert _bills(sandbox_url) == 1  # replay wrote nothing

    mismatch = main(["replay", "cli_demo", "--no-browser", "--runs-dir", str(runs),
                     "--sandbox", sandbox_url, "--evidence-dir", str(tmp_path / "ev3")])
    assert mismatch == 1  # tier boundary: not comparable, never identical


# ------------------------------------------------------- dom_hash parity

def test_the_shared_dom_hash_matches_between_a_live_page_and_the_offline_string(
    sandbox_url: str,
) -> None:
    """Addition 2: one definition, exercised from both packages on the same DOM."""
    from verity_browser.domhash import DOM_HASH_JS_CALL, structural_hash

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            for path in ("/ui/invoices", "/ui/purchase-orders/PO-2211", "/ui/bills"):
                page.goto(f"{sandbox_url}{path}", wait_until="domcontentloaded")
                in_page = str(page.evaluate(DOM_HASH_JS_CALL))
                offline = structural_hash(page.content())
                assert in_page == offline, path
        finally:
            browser.close()
