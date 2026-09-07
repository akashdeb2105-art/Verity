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
    execute,
    pending_writes,
)
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
        parser.add_argument("--json", action="store_true", help="machine-readable output")
        parser.set_defaults(handler=cmd_run, live=live)


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

    gate = ContractGate(Path(args.contract), registry, args.evidence_dir)
    report = execute(graph, RunOptions(
        inputs=inputs,
        mode=WriteMode.LIVE if args.live else WriteMode.DRY_RUN,
        gate=gate,
        registry=registry,
        writers=writers,
        policy=policy,
        approvals=approvals,
        run_id=args.run_id,
    ))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        _print_report(printer, report, live=args.live)
    return report.outcome.exit_code


def _print_report(printer: Printer, report: Any, *, live: bool) -> None:
    printer.line()
    printer.line(
        printer.style(f"  {report.graph_name}", "bold")
        + printer.style(f"  {report.run_id}", "dim")
        + printer.style("" if live else "  (dry run -- nothing will be written)", "dim")
    )
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
