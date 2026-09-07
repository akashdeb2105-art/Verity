"""``verity teach`` and ``verity inspect``.

Recording is interactive by nature: a person does the job while Verity watches.
The command therefore stays out of the way -- it opens a browser, prints what
it is seeing, and waits for the person to say they are finished.

What it produces is a *draft*, and the command says so plainly at the end. The
failure mode worth guarding against is not a bad proposal; it is a person
assuming a proposal is complete.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from verity_capture import CaptureError, read_session, write_session
from verity_compiler import (
    ContractDraft,
    Enrichment,
    Step,
    analyse,
    build,
    enrich,
    normalize,
    propose,
    summarise,
    to_yaml,
)
from verity_schema import RiskLevel

from .output import Printer


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", required=True, help="where the demonstration starts")
    parser.add_argument("--name", default="recorded_workflow", help="a name for the workflow")
    parser.add_argument("--out", default="recording.session.json",
                        help="where to write the recording")
    parser.add_argument("--contract", help="also write a proposed contract to this path")
    parser.add_argument("--graph", help="also write the WorkGraph to this path")
    parser.add_argument("--allow-domain", action="append", default=[],
                        help="restrict the session to these domains (repeatable)")
    parser.add_argument("--headless", action="store_true",
                        help="run without a visible window (for scripted recordings)")
    parser.add_argument(
        "--ai", action="store_true",
        help="ask a model to suggest extra checks and clearer wording. "
             "Suggestions are written commented out; nothing is enforced until "
             "you accept it.",
    )
    parser.add_argument(
        "--ai-provider",
        help="gemini | fireworks | openrouter | openai | ollama, or a '+'-separated "
             "fallback chain such as gemini+openrouter",
    )
    parser.add_argument("--ai-model", help="override the model name")
    parser.add_argument("--no-color", action="store_true")


def cmd_teach(args: argparse.Namespace) -> int:
    from verity_capture import BrowserRecorder, RecorderOptions

    printer = Printer(colour=False if args.no_color else None)
    recorder = BrowserRecorder(
        RecorderOptions(
            start_url=args.url, headless=args.headless,
            allowed_domains=args.allow_domain,
        )
    )

    printer.line()
    printer.line(printer.style("  Recording a demonstration", "bold"))
    printer.line(printer.style(f"    start      {args.url}", "dim"))
    printer.line(printer.style(
        "    redaction  on, and it cannot be switched off", "dim"))
    if args.allow_domain:
        printer.line(printer.style(
            f"    domains    {', '.join(args.allow_domain)} (everything else blocked)", "dim"))
    printer.line()

    try:
        recorder.start()
    except CaptureError as exc:
        printer.line(printer.style(f"  cannot record: {exc}", "fail"))
        return 4

    printer.line("  Do the task now. Press Enter here when you are finished.")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        printer.line()

    session = recorder.stop()
    write_session(session, args.out, recorder.attachment_blobs)

    redaction = recorder.redaction_summary
    printer.line()
    printer.line(
        f"  {len(session.interactions)} interactions recorded  ->  {args.out}"
    )
    if redaction["redacted"]:
        reasons = ", ".join(f"{v}x {k}" for k, v in sorted(redaction["by_reason"].items()))
        printer.line(
            printer.style(
                f"  {redaction['redacted']} values redacted ({reasons})", "drift"
            )
        )
    else:
        printer.line(printer.style("  nothing needed redacting", "dim"))

    return _compile_and_report(session, args, printer, Path(args.out))


def cmd_inspect(args: argparse.Namespace) -> int:
    """Show what a recording compiles to, without recording anything."""
    printer = Printer(colour=False if args.no_color else None)
    path = Path(args.session)
    if not path.exists():
        printer.line(printer.style(f"  no such recording: {path}", "fail"))
        return 4

    session = read_session(path)
    printer.line()
    printer.line(
        printer.style(f"  {path.name}", "bold")
        + printer.style(f"  ({len(session.interactions)} interactions)", "dim")
    )
    return _compile_and_report(session, args, printer, path)


def _compile_and_report(
    session: object,
    args: argparse.Namespace,
    printer: Printer,
    session_path: Path | None = None,
) -> int:
    steps = normalize(session, session_path)  # type: ignore[arg-type]
    inputs, comparisons, constants = analyse(steps)
    draft = propose(steps, name=args.name)
    graph = build(steps, inputs, name=args.name)
    enrichment = _maybe_enrich(draft, steps, args, printer)

    printer.line()
    printer.line(printer.style("  Steps", "bold"))
    for step in steps:
        gate = printer.style("  approval", "drift") if step.risk is not RiskLevel.LOW else ""
        value = printer.style(f"  = {step.value}", "dim") if step.value else ""
        printer.line(f"    {step.index + 1:>2}  {step.verb:<14} {step.label}{value}{gate}")

    printer.line()
    printer.line(printer.style("  What it worked out", "bold"))
    for item in inputs:
        printer.line(f"    input      {item.name:<18} e.g. {item.example}")
    for comparison in comparisons:
        mark = "" if comparison.both_explicit else printer.style("  (on screen only)", "dim")
        printer.line(
            f"    compares   {comparison.left_system}.{comparison.left_key}"
            f" == {comparison.right_system}.{comparison.right_key}{mark}"
        )
    for constant in constants:
        printer.line(f"    end state  {constant.system}.{constant.key} == {constant.value}")
    if not (inputs or comparisons or constants):
        printer.line(printer.style("    nothing could be proposed from this recording", "drift"))

    if args.graph:
        import json

        Path(args.graph).write_text(
            json.dumps(graph.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        printer.line()
        printer.line(printer.style(f"  graph      {args.graph}  {summarise(graph)}", "dim"))

    if args.contract:
        Path(args.contract).write_text(to_yaml(draft, enrichment), encoding="utf-8")
        printer.line()
        printer.line(f"  Proposed contract  ->  {args.contract}")
        printer.line(
            printer.style(
                f"  {draft.assertion_count} assertions, every one traced to a value seen twice",
                "dim")
        )

    printer.line()
    printer.line(printer.style("  This is a draft.", "drift"))
    printer.line(printer.style(
        "  One successful run cannot show duplicates, forbidden outcomes,", "dim"))
    printer.line(printer.style(
        "  branches or tolerances. Read it before you trust it.", "dim"))
    printer.line()
    return 0


def _maybe_enrich(
    draft: ContractDraft, steps: list[Step], args: argparse.Namespace, printer: Printer
) -> Enrichment | None:
    """Ask a model for suggestions, if one is configured and asked for.

    Never fatal. Enrichment improves a result that already exists, so a missing
    key or an unreachable endpoint costs suggestions and nothing else.
    """
    if not getattr(args, "ai", False):
        return None

    from verity_ai import AiCassette, AiCassetteMode, Budget, CassetteProvider, build_chain

    provider = build_chain(
        getattr(args, "ai_provider", None), model=getattr(args, "ai_model", None)
    )
    cassette_path = os.environ.get("VERITY_AI_CASSETTE")
    if cassette_path:
        mode = AiCassetteMode(os.environ.get("VERITY_AI_CASSETTE_MODE", "replay"))
        cassette = CassetteProvider(provider, AiCassette(cassette_path, mode))
        provider = cassette

    printer.line()
    printer.line(
        printer.style(
            f"  Asking {provider.name} ({provider.model}) for suggestions", "dim"
        )
    )

    result = enrich(draft, steps, provider, budget=Budget())

    if not result.ok:
        printer.line(printer.style(f"  no suggestions: {result.error}", "drift"))
        return result

    printer.line(printer.style(f"  {result.summary}", "dim"))
    for suggestion in result.suggestions:
        where = "forbidden" if suggestion.forbidden else "expected"
        printer.line(f"    suggests   {suggestion.id}  ({where})")
    for reason in result.rejected:
        printer.line(printer.style(f"    discarded  {reason}", "dim"))
    return result


def _unused(_: object = sys) -> None:  # pragma: no cover
    return None
