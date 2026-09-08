"""``verity run`` and ``verity dry-run``: execute a workflow, and gate the writes.

This module is the only place in the codebase where the runtime and the
verifier appear together. Neither imports the other -- an import-linter
contract forbids it in both directions -- so composition has to happen
somewhere, and doing it here keeps that somewhere small enough to read.

The gate below is the whole join: it holds a checked contract, runs the real
verifier when asked, and hands the runtime a verdict. The runtime never learns
what produced it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from verity_connectors import ConnectorRegistry, WritableConnector, WriteMode
from verity_runtime import (
    Approval,
    GateResult,
    GateVerdict,
    InMemoryApprovalStore,
    Policy,
    PolicyError,
    RunOptions,
    RunOutcome,
    diff_runs,
    execute,
    pending_writes,
    plan,
    read_run_record,
    record_from_report,
    write_run_record,
)
from verity_runtime.runrecord import DEFAULT_RUNS_DIR
from verity_schema.workgraph import WorkGraph

from .output import Printer

EXIT_USAGE = 64


class ContractGate:
    """Runs the real verifier, and answers in the runtime's vocabulary.

    Translating the verdict rather than passing the verifier's own type
    through is deliberate: it is what keeps the runtime honestly independent
    instead of independent-on-paper.
    """

    def __init__(self, contract_path: Path, registry: ConnectorRegistry,
                 evidence_dir: str) -> None:
        from verity_verifier import load_contract, typecheck

        self._checked = typecheck(load_contract(contract_path))
        self._registry = registry
        self._evidence_dir = evidence_dir
        self.report: Any = None

    def check(self, inputs: dict[str, str]) -> GateResult:
        from verity_verifier import VerifyOptions, verify_checked

        outcome = verify_checked(
            self._checked,
            VerifyOptions(
                registry=self._registry,
                evidence_dir=self._evidence_dir,
                inputs=dict(inputs),
            ),
        )
        report = outcome.report
        self.report = report

        failed = tuple(a.id for a in report.assertions if not a.passed)
        return GateResult(
            verdict=GateVerdict(report.verdict.value),
            reason=report.divergence.explanation or "",
            failed_assertions=failed,
            first_divergence=report.divergence.first_assertion_failure or "",
            evidence_ref=getattr(report, "evidence_bundle", "") or "",
        )


def load_approvals(path: Path) -> InMemoryApprovalStore:
    """Read approvals from a file, refusing anything malformed.

    An approvals file that half-parsed would be the worst of both worlds: some
    controls enforced, some silently dropped. Every record must be complete.
    """
    raw = json.loads(path.read_text("utf-8")) if path.suffix == ".json" \
        else yaml.safe_load(path.read_text("utf-8"))
    records = raw.get("approvals", raw) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise ValueError(f"{path}: expected a list of approvals")

    store = InMemoryApprovalStore()
    for index, record in enumerate(records):
        missing = [k for k in ("run_id", "node_id", "digest", "approver")
                   if not str(record.get(k, "")).strip()]
        if missing:
            raise ValueError(f"{path}: approval {index} is missing {', '.join(missing)}")
        store.grant(Approval(
            run_id=str(record["run_id"]), node_id=str(record["node_id"]),
            digest=str(record["digest"]), approver=str(record["approver"]),
            granted_at=float(record.get("granted_at") or 0.0),
            expires_at=float(record.get("expires_at") or 0.0),
            revoked=bool(record.get("revoked", False)),
            note=str(record.get("note") or ""),
        ))
    return store


def load_policy(path: Path) -> Policy:
    raw = json.loads(path.read_text("utf-8")) if path.suffix == ".json" \
        else yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(raw, dict):
        raise PolicyError(f"{path}: a policy must be a mapping")
    return Policy.from_mapping(raw)


def add_run_commands(sub: Any) -> None:
    pending = sub.add_parser(
        "pending", help="show what a run would write, and what it needs to proceed")
    pending.add_argument("graph", help="path to a WorkGraph (YAML or JSON)")
    pending.add_argument("--input", action="append", default=[], metavar="k=v")
    pending.add_argument("--run-id", default="", help="the run these approvals will apply to")
    pending.add_argument("--policy", default="", help="path to a policy file")
    pending.add_argument("--json", action="store_true")
    pending.set_defaults(handler=cmd_pending)

    for name, help_text, live in (
        ("dry-run", "execute a workflow without writing anything", False),
        ("run", "execute a workflow, writing only if verification passes", True),
    ):
        parser = sub.add_parser(name, help=help_text)
        parser.add_argument("graph", help="path to a WorkGraph (YAML or JSON)")
        parser.add_argument("--contract", required=True,
                            help="the Outcome Contract that gates consequential writes")
        parser.add_argument("--input", action="append", default=[], metavar="k=v",
                            help="an input value; repeatable")
        parser.add_argument("--sandbox", default="", help="sandbox base URL")
        parser.add_argument("--evidence-dir", default=".verity/evidence")
        parser.add_argument("--approvals", default="",
                            help="path to a file of approvals, each bound to a payload digest")
        parser.add_argument("--policy", default="",
                            help="path to a policy file; the built-in default is used otherwise")
        parser.add_argument("--run-id", default="",
                            help="fix the run id, so approvals can be prepared in advance")
        parser.add_argument(
            "--browser", action="store_true",
            help="drive a real browser through the NAVIGATE/CLICK/TYPE/SELECT/EXTRACT "
                 "steps (Tier 2). Needs Chromium: pip install \".[browser]\" && "
                 "playwright install chromium. Without it, read steps are recorded no-ops.",
        )
        parser.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR,
                            help="where to write the replayable run record")
        parser.add_argument("--no-record", action="store_true",
                            help="do not write a run record for this run")
        parser.add_argument("--json", action="store_true", help="machine-readable output")
        parser.set_defaults(handler=cmd_run, live=live)

    replay = sub.add_parser(
        "replay", help="re-run a recorded run and diff it against what was recorded")
    replay.add_argument("run", help="a run id under --runs-dir, or a path to a run record")
    replay.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    replay.add_argument("--sandbox", default="", help="override the recorded sandbox URL")
    replay.add_argument("--browser", dest="browser", action="store_true", default=None,
                        help="force the replay to drive a browser")
    replay.add_argument("--no-browser", dest="browser", action="store_false",
                        help="force the replay NOT to drive a browser (expect a tier mismatch)")
    replay.add_argument("--evidence-dir", default=".verity/evidence")
    replay.add_argument("--json", action="store_true")
    replay.set_defaults(handler=cmd_replay)


def cmd_pending(args: argparse.Namespace) -> int:
    """List the consequential writes a run would make. Contacts nothing."""
    printer = Printer()
    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"verity: no such workgraph: {graph_path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        inputs = _parse_inputs(args.input)
        policy = load_policy(Path(args.policy)) if args.policy else Policy()
    except (ValueError, PolicyError) as exc:
        print(f"verity: {exc}", file=sys.stderr)
        return EXIT_USAGE

    run_id = args.run_id or "RUN_ID"
    writes = pending_writes(
        _load_graph(graph_path), RunOptions(inputs=inputs, policy=policy, run_id=run_id))

    if args.json:
        print(json.dumps([{
            "run_id": run_id, "node_id": w.node_id, "digest": w.digest,
            "risk": w.decision.level.value, "requirement": w.decision.requirement.value,
            "declared": w.decision.assessment.declared.value,
            "understated": w.decision.assessment.understated,
            "write": w.intent.describe(), "why": w.decision.assessment.explain(),
        } for w in writes], indent=2, sort_keys=True))
        return 0

    printer.line()
    if not writes:
        printer.line("  Nothing in this graph changes the world.")
        printer.line()
        return 0

    for write in writes:
        style = "fail" if write.decision.blocks else "dim"
        printer.line(printer.style(f"  {write.node_id}", "bold")
                     + printer.style(f"  {write.decision.level.value}", style)
                     + printer.style(f"  {write.decision.requirement.value}", "dim"))
        printer.line(f"    {write.intent.describe()}")
        printer.line(printer.style(f"    {write.decision.assessment.explain()}", "dim"))
        if write.decision.assessment.understated:
            printer.line(printer.style(
                "    the graph declared "
                f"{write.decision.assessment.declared.value}; assessed higher", "fail"))
        printer.line(printer.style(f"    {write.digest}", "dim"))
        printer.line()

    needing = [w for w in writes if w.needs_approval]
    if needing:
        template = json.dumps([{
            "run_id": run_id, "node_id": w.node_id, "digest": w.digest,
            "approver": "you@example.com",
        } for w in needing], indent=2)
        printer.line(printer.style(
            "  To approve, record each digest against this run id:", "dim"))
        for line in template.splitlines():
            printer.line(printer.style(f"    {line}", "dim"))
        printer.line()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from verity_connectors import DEFAULT_SANDBOX_URL, build_sandbox_registry, ledger_writer

    printer = Printer()
    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"verity: no such workgraph: {graph_path}", file=sys.stderr)
        return EXIT_USAGE

    try:
        inputs = _parse_inputs(args.input)
    except ValueError as exc:
        print(f"verity: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        approvals = load_approvals(Path(args.approvals)) if args.approvals \
            else InMemoryApprovalStore()
        policy = load_policy(Path(args.policy)) if args.policy else Policy()
    except (OSError, ValueError, PolicyError) as exc:
        print(f"verity: {exc}", file=sys.stderr)
        return EXIT_USAGE

    graph = _load_graph(graph_path)
    base_url = args.sandbox or DEFAULT_SANDBOX_URL
    registry = build_sandbox_registry(base_url)
    writers: dict[str, WritableConnector] = {"ledger": ledger_writer(base_url)}
    the_plan = plan(graph)

    options = RunOptions(
        inputs=inputs,
        mode=WriteMode.LIVE if args.live else WriteMode.DRY_RUN,
        gate=ContractGate(Path(args.contract), registry, args.evidence_dir),
        registry=registry,
        writers=writers,
        policy=policy,
        approvals=approvals,
        run_id=args.run_id,
    )
    if getattr(args, "browser", False):
        options.browser = _build_browser_driver(base_url, options.run_id or "run")

    report = execute(graph, options)

    if not getattr(args, "no_record", False):
        target = write_run_record(report, the_plan, runs_dir=args.runs_dir, meta={
            "graph_path": str(graph_path),
            "contract_path": str(args.contract),
            "policy_path": str(args.policy or ""),
            "sandbox_url": base_url,
            "browser": bool(getattr(args, "browser", False)),
        })
        if not args.json:
            printer.line(printer.style(f"  run record: {target}", "dim"))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        _print_report(printer, report, live=args.live)
    return report.outcome.exit_code


def _build_browser_driver(base_url: str, run_id: str) -> Any:
    """Construct the Playwright-backed driver, translating a missing install
    into a usage error rather than a stack trace."""
    try:
        from .browser import PlaywrightDriver
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            f"verity: --browser needs the browser executor ({exc.name} is missing). "
            'Install it with: pip install ".[browser]" && playwright install chromium'
        ) from exc
    return PlaywrightDriver(base_url=base_url, run_id=run_id)


def cmd_replay(args: argparse.Namespace) -> int:
    """Re-run a recorded run, always dry, and report how the two differ.

    Replay is a comparison, not a repetition. It is forced to ``DRY_RUN``, so no
    write ever happens here whatever the recorded run did. It also grants itself
    approval for the digests the plan produces -- not to authorise anything (a
    dry run authorises nothing), but so the consequential step is *reached* and
    the gate's verdict on this run can be compared to the recorded one, rather
    than the two always diverging at an approval prompt.

    A run made with a browser and one made without are different claims about
    what was observed, so a replay across that boundary is refused rather than
    diffed -- and never called identical.
    """
    from verity_connectors import DEFAULT_SANDBOX_URL, build_sandbox_registry, ledger_writer

    printer = Printer()
    record_dir = Path(args.run)
    if not record_dir.is_dir():
        record_dir = Path(args.runs_dir) / args.run
    if not (record_dir / "report.json").is_file():
        print(f"verity: no run record at {record_dir}", file=sys.stderr)
        return EXIT_USAGE

    baseline = read_run_record(record_dir)
    meta = baseline.meta
    graph_path = Path(str(meta.get("graph_path") or ""))
    contract_path = Path(str(meta.get("contract_path") or ""))
    if not graph_path.is_file() or not contract_path.is_file():
        print(
            "verity: the recorded run does not name a graph and contract that still "
            f"exist (graph={graph_path}, contract={contract_path})",
            file=sys.stderr,
        )
        return EXIT_USAGE

    use_browser = meta.get("browser", False) if args.browser is None else args.browser
    base_url = args.sandbox or str(meta.get("sandbox_url") or "") or DEFAULT_SANDBOX_URL

    try:
        policy = load_policy(Path(meta["policy_path"])) if meta.get("policy_path") else Policy()
    except (OSError, ValueError, PolicyError) as exc:
        print(f"verity: {exc}", file=sys.stderr)
        return EXIT_USAGE

    graph = _load_graph(graph_path)
    the_plan = plan(graph)
    registry = build_sandbox_registry(base_url)

    options = RunOptions(
        inputs=dict(baseline.inputs),
        mode=WriteMode.DRY_RUN,
        gate=ContractGate(contract_path, registry, args.evidence_dir),
        registry=registry,
        writers={"ledger": ledger_writer(base_url)},
        policy=policy,
        run_id=f"{baseline.run_id}_replay",
    )
    if use_browser:
        options.browser = _build_browser_driver(base_url, options.run_id)

    store = InMemoryApprovalStore()
    for pending in pending_writes(graph, options):
        store.grant(Approval(
            run_id=options.run_id, node_id=pending.node_id, digest=pending.digest,
            approver="replay@verity",
        ))
    options.approvals = store

    report = execute(graph, options)
    replay_record = record_from_report(report, the_plan)
    result = diff_runs(baseline, replay_record)

    if args.json:
        print(json.dumps({
            "baseline": baseline.run_id,
            "replay": replay_record.run_id,
            "comparable": result.comparable,
            "identical": result.identical,
            "differences": [
                {"kind": d.kind, "node_id": d.node_id, "detail": d.detail,
                 "baseline": d.baseline, "replay": d.replay}
                for d in result.differences
            ],
        }, indent=2, sort_keys=True))
    else:
        printer.line()
        for line in result.render():
            printer.line(line)
        printer.line()
    return result.exit_code


def _print_report(printer: Printer, report: Any, *, live: bool) -> None:
    printer.line()
    printer.line(
        printer.style(f"  {report.graph_name}", "bold")
        + printer.style(f"  {report.run_id}", "dim")
        + printer.style("" if live else "  (dry run -- nothing will be written)", "dim")
    )
    if report.executor_tier == "browser":
        seen = 0 if report.trace is None else len(report.trace.steps)
        printer.line(printer.style(f"  tier 2: drove a browser, {seen} page steps", "dim"))
    printer.line()

    for step in report.steps:
        mark = {"ok": "  ok  ", "halted": " HALT ", "error": " ERR  "}.get(step.status, "  ?   ")
        style = {"ok": "dim", "halted": "fail", "error": "fail"}[step.status]
        written = ""
        if step.consequential:
            written = "  wrote" if step.performed_write else "  not written"
        printer.line(
            printer.style(mark, style)
            + f"{step.index + 1:>2}  {step.action:<14}{step.label}"
            + printer.style(written, "dim")
        )

    printer.line()
    verdict_style = "pass" if report.verifier_says == "PASS" else "fail"
    printer.line(
        "  runtime said  " + printer.style(report.runtime_said, "dim")
        + printer.style("      verifier says  ", "dim")
        + printer.style(report.verifier_says, verdict_style)
    )

    if report.gate and report.gate.reason:
        printer.line(f"  {report.gate.reason}")
    if report.gate and report.gate.failed_assertions:
        printer.line(printer.style(
            "  failed: " + ", ".join(report.gate.failed_assertions), "dim"))

    if report.outcome is RunOutcome.HALTED:
        printer.line()
        printer.line(printer.style(f"  Halted before {report.halted_at}.", "fail"))
        if report.halt_reason:
            printer.line(printer.style(f"    {report.halted_by}: {report.halt_reason}", "dim"))
        for description in report.not_performed:
            printer.line(printer.style(f"    did not: {description}", "dim"))
    elif live and report.writes_performed:
        printer.line()
        for step in report.writes_performed:
            printer.line(printer.style(
                f"  wrote {step.node_id}  {step.write_digest[:23]}", "dim"))
    printer.line()


def _load_graph(path: Path) -> WorkGraph:
    raw = path.read_text("utf-8")
    data = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
    return WorkGraph.model_validate(data)


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name.strip():
            raise ValueError(f"--input expects name=value, got {pair!r}")
        out[name.strip()] = value
    return out
