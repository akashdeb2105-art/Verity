"""``verity`` -- the command line interface.

The CLI is the product's primary surface. Everything it does lives in the
packages it calls; there is no business logic here, so the CLI, the API, the
web app and the GitHub Action cannot drift apart.

Exit codes mirror the verdict:

===  ==============================================================
  0  PASS -- or a verdict excluded from ``--fail-on``
  1  FAIL -- at least one blocking assertion is false
  2  DRIFT -- outcome holds, environment changed
  3  INCONCLUSIVE -- a required fact could not be resolved
  4  usage or contract error -- nothing was verified
===  ==============================================================
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from verity_schema import Trace, Verdict, VerificationReport, __version__, worst
from verity_verifier import (
    ContractError,
    VerifyOptions,
    discover_contracts,
    load_contract,
    typecheck,
    verify_checked,
)

from .output import Printer, render_report
from .reporters import github_annotations, summary_markdown, write_json, write_junit
from .run import add_run_commands
from .teach import add_arguments as add_teach_arguments
from .teach import cmd_inspect, cmd_teach

EXIT_USAGE = 4
DEFAULT_FAIL_ON = "fail,inconclusive"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    handler: Any = args.handler
    try:
        return int(handler(args))
    except ContractError as exc:
        print(f"verity: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:  # pragma: no cover
        return 130


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verity",
        description="Your AI agent says DONE. Verity checks whether it actually did.",
    )
    parser.add_argument(
        "--version", action="version", version=f"verity {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    verify_cmd = sub.add_parser(
        "verify", help="evaluate one Outcome Contract",
        description="Evaluate a contract against live sources, an execution trace, or both.",
    )
    _add_common(verify_cmd)
    verify_cmd.add_argument("--contract", required=True, help="path to an Outcome Contract")
    verify_cmd.add_argument("--trace", help="path to a verity-trace/v1 JSON file")
    verify_cmd.add_argument("--baseline", help="a golden trace to compare against")
    verify_cmd.add_argument(
        "--live", action="store_true",
        help="resolve facts from bound connectors (the default when no trace is given)",
    )
    verify_cmd.set_defaults(handler=cmd_verify)

    eval_cmd = sub.add_parser(
        "eval", help="evaluate every contract in a suite",
        description="Run a whole regression suite. This is the CI entry point.",
    )
    _add_common(eval_cmd)
    eval_cmd.add_argument("--suite", required=True, help="directory of contracts, or one file")
    eval_cmd.set_defaults(handler=cmd_eval)

    lint_cmd = sub.add_parser(
        "lint", help="type-check contracts without reading anything",
        description="Parse and type-check contracts. Undefined facts and unknown "
                    "functions are errors here, before any system is touched.",
    )
    lint_cmd.add_argument("paths", nargs="+")
    lint_cmd.set_defaults(handler=cmd_lint)

    teach_cmd = sub.add_parser(
        "teach", help="record a demonstration and propose a contract",
        description="Open a browser, watch someone do the task once, and propose "
                    "a WorkGraph and an Outcome Contract from what was observed.",
    )
    add_teach_arguments(teach_cmd)
    teach_cmd.set_defaults(handler=cmd_teach)

    inspect_cmd = sub.add_parser(
        "inspect", help="show what a recording compiles to",
        description="Compile a saved recording without recording anything new.",
    )
    inspect_cmd.add_argument("session", help="path to a recording")
    inspect_cmd.add_argument("--name", default="recorded_workflow")
    inspect_cmd.add_argument("--contract", help="write the proposed contract here")
    inspect_cmd.add_argument("--graph", help="write the WorkGraph here")
    inspect_cmd.add_argument("--ai", action="store_true",
                             help="ask a model for extra suggestions")
    inspect_cmd.add_argument("--ai-provider")
    inspect_cmd.add_argument("--ai-model")
    inspect_cmd.add_argument("--no-color", action="store_true")
    inspect_cmd.set_defaults(handler=cmd_inspect)

    add_run_commands(sub)

    doctor_cmd = sub.add_parser("doctor", help="check the local environment")
    doctor_cmd.add_argument("--sandbox-url", default=_default_sandbox_url())
    doctor_cmd.set_defaults(handler=cmd_doctor)

    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input", action="append", default=[], metavar="KEY=VALUE",
        help="bind a contract input (repeatable)",
    )
    parser.add_argument("--sandbox-url", default=_default_sandbox_url())
    parser.add_argument("--evidence-dir", default=os.environ.get("VERITY_EVIDENCE_DIR",
                                                                 ".verity/evidence"))
    parser.add_argument("--bundle", help="write an evidence bundle to this directory")
    parser.add_argument("--json", dest="json_out", help="write a JSON report to this path")
    parser.add_argument("--junit", help="write JUnit XML to this path")
    parser.add_argument(
        "--github-annotations", action="store_true",
        help="emit GitHub Actions annotations anchored to the contract line",
    )
    parser.add_argument("--summary", help="write a markdown summary table to this path")
    parser.add_argument(
        "--fail-on", default=DEFAULT_FAIL_ON,
        help=f"verdicts that should exit non-zero (default: {DEFAULT_FAIL_ON}). "
             "DRIFT warns by default rather than breaking the build.",
    )
    parser.add_argument("--cassette", help="cassette file for hermetic record/replay")
    parser.add_argument(
        "--cassette-mode", choices=("off", "record", "replay"),
        default=os.environ.get("VERITY_CASSETTE_MODE", "off"),
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--no-color", action="store_true")


def _default_sandbox_url() -> str:
    return os.environ.get("VERITY_SANDBOX_URL", "http://127.0.0.1:8099")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_verify(args: argparse.Namespace) -> int:
    return _run_contracts([Path(args.contract)], args)


def cmd_eval(args: argparse.Namespace) -> int:
    paths = discover_contracts(args.suite)
    if not paths:
        print(f"verity: no contracts found under {args.suite}", file=sys.stderr)
        return EXIT_USAGE
    return _run_contracts(paths, args)


def cmd_lint(args: argparse.Namespace) -> int:
    printer = Printer()
    failed = 0
    for target in args.paths:
        for path in discover_contracts(target):
            try:
                checked = typecheck(load_contract(path))
            except ContractError as exc:
                failed += 1
                printer.line(printer.style(f"  FAIL  {path}", "fail"))
                printer.line(f"        {exc}")
                continue
            printer.line(
                printer.style("  OK    ", "pass")
                + f"{path}  "
                + printer.style(
                    f"({len(checked.assertions)} assertions, "
                    f"{len(checked.contract.facts)} facts)", "dim")
            )
            for warning in checked.warnings:
                printer.line(printer.style(f"        warning: {warning}", "drift"))
    return 1 if failed else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import httpx

    printer = Printer()
    printer.line()
    printer.line(printer.style("  verity doctor", "bold"))
    printer.line(f"    python           {sys.version.split()[0]}")
    evidence_dir = os.environ.get("VERITY_EVIDENCE_DIR", ".verity/evidence")
    printer.line(f"    evidence dir     {evidence_dir}")

    ok = True
    try:
        response = httpx.get(f"{args.sandbox_url}/api/health", timeout=3.0)
        payload = response.json()
        printer.line(
            printer.style("    sandbox          reachable  ", "pass")
            + printer.style(f"{payload['state_hash'][:22]}...", "dim")
        )
        if payload.get("perturbations"):
            printer.line(
                printer.style(
                    f"    perturbations    applied: {', '.join(payload['perturbations'])}",
                    "drift")
            )
    except Exception as exc:
        ok = False
        printer.line(printer.style(f"    sandbox          unreachable ({exc})", "fail"))
        printer.line(printer.style("                     start it with: make sandbox", "dim"))

    printer.line(printer.style("    model providers  none configured (verification is "
                              "deterministic)", "dim"))
    printer.line()
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# shared execution
# ---------------------------------------------------------------------------

def _run_contracts(paths: list[Path], args: argparse.Namespace) -> int:
    printer = Printer(colour=False if args.no_color else None)
    registry = _build_registry(args)
    trace = _load_trace(getattr(args, "trace", None))
    baseline = _load_trace(getattr(args, "baseline", None))
    inputs = _parse_inputs(args.input)
    if trace is not None:
        # Trace inputs are defaults; anything passed on the command line wins.
        inputs = {**trace.inputs, **inputs}

    reports: list[VerificationReport] = []
    contract_paths: dict[str, str] = {}
    last_outcome = None

    for path in paths:
        checked = typecheck(load_contract(path))
        outcome = verify_checked(
            checked,
            VerifyOptions(
                registry=registry, evidence_dir=args.evidence_dir, trace=trace,
                baseline=baseline, inputs=inputs,
            ),
        )
        reports.append(outcome.report)
        contract_paths[outcome.report.contract_name] = str(path)
        last_outcome = outcome
        render_report(outcome.report, printer, verbose=args.verbose)

    _write_outputs(reports, contract_paths, args, last_outcome, printer)

    overall = worst(r.verdict for r in reports)
    fail_on = _parse_fail_on(args.fail_on)
    if overall in fail_on:
        return overall.exit_code
    if overall is not Verdict.PASS:
        printer.line(
            printer.style(
                f"  {overall.value} is not in --fail-on ({args.fail_on}); exiting 0", "dim")
        )
    return 0


def _write_outputs(
    reports: list[VerificationReport],
    contract_paths: dict[str, str],
    args: argparse.Namespace,
    outcome: Any,
    printer: Printer,
) -> None:
    if args.json_out:
        write_json(reports, args.json_out)
    if args.junit:
        write_junit(reports, args.junit)
    if args.summary:
        Path(args.summary).write_text(summary_markdown(reports), encoding="utf-8")
    if args.github_annotations:
        for line in github_annotations(reports, contract_paths):
            print(line)
    if args.bundle and outcome is not None:
        from verity_evidence import write_bundle

        target = write_bundle(
            args.bundle, report=outcome.report, store=outcome.store,
            created_at=datetime.now(timezone.utc),
        )
        printer.line(printer.style(f"  evidence bundle written to {target}", "dim"))
    if getattr(args, "cassette", None) and args.cassette_mode == "record":
        cassette = getattr(args, "_cassette", None)
        if cassette is not None:
            cassette.save()
            printer.line(printer.style(f"  cassette saved: {args.cassette}", "dim"))


def _build_registry(args: argparse.Namespace) -> Any:
    from verity_connectors import Cassette, CassetteConnector, CassetteMode, build_sandbox_registry

    registry = build_sandbox_registry(args.sandbox_url)

    mode = CassetteMode(getattr(args, "cassette_mode", "off"))
    if mode is not CassetteMode.OFF and getattr(args, "cassette", None):
        cassette = Cassette(args.cassette, mode)
        args._cassette = cassette
        for name in registry.names:
            inner = registry.get(name)
            if inner is not None and hasattr(inner, "routes"):
                registry.register(CassetteConnector(inner, cassette))
    return registry


def _load_trace(path: str | None) -> Trace | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text("utf-8"))
    return Trace.model_validate(payload)


def _parse_inputs(pairs: list[str]) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ContractError(f"--input expects KEY=VALUE, got '{pair}'")
        key, value = pair.split("=", 1)
        inputs[key.strip()] = value
    return inputs


def _parse_fail_on(raw: str) -> set[Verdict]:
    values: set[Verdict] = set()
    for token in raw.split(","):
        name = token.strip().upper()
        if not name:
            continue
        try:
            values.add(Verdict[name])
        except KeyError as exc:
            raise ContractError(
                f"unknown verdict '{token.strip()}' in --fail-on "
                f"(choose from: pass, fail, drift, inconclusive)"
            ) from exc
    return values


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
