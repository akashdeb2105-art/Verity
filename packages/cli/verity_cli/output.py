"""Terminal rendering for verification results.

Two rules. Colour is an enhancement, never the only signal -- every state is
also carried by a word, so the output is readable in a CI log, a pipe, or by a
screen reader. And a result that could not be evaluated is printed as
``not evaluated``: never as a pass, never as a failure.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from verity_schema import Severity, Strength, Verdict, VerificationReport

RESET = "\033[0m"
_STYLES = {
    "pass": "\033[32m", "fail": "\033[31m", "drift": "\033[33m",
    "inconclusive": "\033[35m", "dim": "\033[2m", "bold": "\033[1m",
}

_VERDICT_STYLE = {
    Verdict.PASS: "pass", Verdict.FAIL: "fail",
    Verdict.DRIFT: "drift", Verdict.INCONCLUSIVE: "inconclusive",
}


def use_colour(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class Printer:
    def __init__(self, stream: TextIO | None = None, colour: bool | None = None) -> None:
        self.stream = stream or sys.stdout
        self.colour = use_colour(self.stream) if colour is None else colour

    def style(self, text: str, name: str) -> str:
        if not self.colour or name not in _STYLES:
            return text
        return f"{_STYLES[name]}{text}{RESET}"

    def line(self, text: str = "") -> None:
        print(text, file=self.stream)


def render_report(report: VerificationReport, printer: Printer, *, verbose: bool = False) -> None:
    """Render one verification. The headline is the verdict, never a percentage."""
    style = _VERDICT_STYLE[report.verdict]
    printer.line()
    printer.line(
        f"  {printer.style(report.verdict.value, style)}  "
        f"{printer.style(report.contract_name, 'bold')} "
        f"{printer.style('v' + report.contract_version, 'dim')}"
    )

    # The most important line in the product: what the runtime claimed, next to
    # what the evidence says.
    if report.status_reported:
        contradiction = "  <- disagreement" if report.contradicts_runtime else ""
        printer.line(
            printer.style(
                f"  runtime said: {report.status_reported}   "
                f"verifier says: {report.verdict.value}{contradiction}",
                "dim" if not report.contradicts_runtime else "fail",
            )
        )
    printer.line()

    for result in report.assertions:
        if result.passed is True:
            mark, tone = "  PASS", "pass"
        elif result.passed is False:
            mark, tone = "  FAIL", "fail" if result.severity is Severity.BLOCKING else "drift"
        else:
            mark, tone = "  ----", "dim"

        badges = [result.strength.value]
        if result.severity is Severity.WARNING:
            badges.append("WARNING")
        if result.is_forbidden:
            badges.append("FORBIDDEN")
        if result.channel.value == "SAME":
            badges.append("SAME-CHANNEL")
        suffix = printer.style(f"[{' '.join(badges)}]", "dim")

        printer.line(f"{printer.style(mark, tone)}  {result.id:<24} {suffix}")

        if result.passed is False or verbose:
            printer.line(printer.style(f"          {result.expression}", "dim"))
            if result.expected_repr is not None:
                printer.line(f"          expected  {result.expected_repr}")
                printer.line(f"          observed  {result.observed_repr}")
                if result.delta_repr:
                    printer.line(printer.style(f"          delta     {result.delta_repr}", "fail"))
            if result.because:
                printer.line(printer.style(f"          why: {result.because}", "dim"))
        if result.error:
            printer.line(printer.style(f"          not evaluated: {result.error}", "dim"))

    unresolved = report.unresolved_facts
    if unresolved:
        printer.line()
        printer.line(printer.style("  unresolved facts", "inconclusive"))
        for fact in unresolved:
            printer.line(f"    {fact.id:<24} {fact.error}")

    if report.divergence.explanation and report.verdict is not Verdict.PASS:
        printer.line()
        printer.line(printer.style("  first divergence", "bold"))
        if report.divergence.first_assertion_failure:
            printer.line(f"    assertion    {report.divergence.first_assertion_failure}")
        if report.divergence.first_environment_change:
            step = report.divergence.first_environment_step
            where = report.divergence.first_environment_change
            printer.line(
                f"    environment  {where}"
                + (f" (step {step})" if step is not None else "")
                + f"  [{report.divergence.environment_change_kind}]"
            )
        printer.line(printer.style(f"    {report.divergence.explanation}", "dim"))

    if report.notes:
        printer.line()
        for note in report.notes:
            printer.line(printer.style(f"  note: {note}", "dim"))

    strong = sum(1 for a in report.assertions if a.strength is Strength.STRONG)
    printer.line()
    printer.line(
        printer.style(
            f"  {len(report.assertions)} assertions ({strong} strong) · "
            f"{len(report.facts)} facts · {report.budgets.duration_ms} ms · "
            f"{report.budgets.model_calls} model calls · ${report.budgets.cost_usd:.2f}",
            "dim",
        )
    )
    printer.line()
