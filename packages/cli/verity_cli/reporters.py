"""Machine-readable outputs: JSON, JUnit XML and GitHub annotations."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

from verity_schema import Severity, Verdict, VerificationReport, __version__


def write_json(reports: list[VerificationReport], path: str | Path) -> None:
    payload = {
        "verity": __version__,
        "verdict": _overall(reports).value,
        "reports": [r.model_dump(mode="json", by_alias=True) for r in reports],
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_junit(reports: list[VerificationReport], path: str | Path) -> None:
    """JUnit XML, so existing CI test reporters render Verity results natively.

    Each assertion is a test case. A failure carries the expected value, the
    observed value and the delta, because those three are what a person needs
    before they open anything else.
    """
    suites = ET.Element("testsuites")
    for report in reports:
        failures = sum(1 for a in report.assertions if a.passed is False)
        errors = sum(1 for a in report.assertions if a.passed is None)
        suite = ET.SubElement(
            suites, "testsuite",
            name=f"{report.contract_name}@{report.contract_version}",
            tests=str(len(report.assertions)),
            failures=str(failures), errors=str(errors),
            time=f"{report.budgets.duration_ms / 1000:.3f}",
        )
        properties = ET.SubElement(suite, "properties")
        for key, value in (
            ("verdict", report.verdict.value),
            ("contract_fingerprint", report.contract_fingerprint),
            ("runtime_reported", report.status_reported or "n/a"),
            ("model_calls", str(report.budgets.model_calls)),
        ):
            ET.SubElement(properties, "property", name=key, value=value)

        for assertion in report.assertions:
            case = ET.SubElement(
                suite, "testcase", classname=report.contract_name, name=assertion.id
            )
            if assertion.passed is False:
                node = ET.SubElement(
                    case, "failure",
                    message=_failure_message(assertion),
                    type=assertion.severity.value,
                )
                node.text = _detail(assertion)
            elif assertion.passed is None:
                node = ET.SubElement(
                    case, "error", message=assertion.error or "not evaluated",
                    type="NOT_EVALUATED",
                )
                node.text = assertion.expression

        for fact in report.unresolved_facts:
            case = ET.SubElement(
                suite, "testcase", classname=f"{report.contract_name}.facts", name=fact.id
            )
            ET.SubElement(case, "error", message=fact.error or "unresolved", type="INCONCLUSIVE")

    ET.ElementTree(suites).write(str(path), encoding="utf-8", xml_declaration=True)


def github_annotations(
    reports: list[VerificationReport], contract_paths: dict[str, str],
    line_lookup: object = None,
) -> list[str]:
    """GitHub Actions workflow commands, anchored to the contract line.

    The annotation lands on the line the developer wrote, not at the top of the
    file, which is the difference between a useful CI comment and noise.
    """
    from verity_verifier import contract_line_of

    lines: list[str] = []
    for report in reports:
        path = contract_paths.get(report.contract_name, "")
        for assertion in report.assertions:
            if assertion.passed is True:
                continue
            level = (
                "error"
                if assertion.passed is False and assertion.severity is Severity.BLOCKING
                else "warning"
            )
            location = ""
            if path:
                line = contract_line_of(path, assertion.id)
                location = f"file={path}" + (f",line={line}" if line else "")
            message = _failure_message(assertion).replace("\n", " ")
            prefix = f"::{level} {location}::" if location else f"::{level}::"
            lines.append(f"{prefix}{report.contract_name}/{assertion.id}: {message}")

        for fact in report.unresolved_facts:
            location = f"file={path}" if path else ""
            prefix = f"::error {location}::" if location else "::error::"
            lines.append(
                f"{prefix}{report.contract_name}: fact '{fact.id}' could not be "
                f"resolved: {fact.error}"
            )
    return lines


def summary_markdown(reports: list[VerificationReport]) -> str:
    """A table for a pull-request comment or a GitHub step summary."""
    rows = [
        "| contract | verdict | runtime said | first divergence |",
        "| --- | --- | --- | --- |",
    ]
    for report in reports:
        divergence = (
            report.divergence.first_assertion_failure
            or report.divergence.first_environment_change
            or "—"
        )
        rows.append(
            f"| `{report.contract_name}` | **{report.verdict.value}** | "
            f"{report.status_reported or '—'} | `{divergence}` |"
        )

    failing = [a for r in reports for a in r.blocking_failures]
    if failing:
        rows.append("")
        rows.append("| assertion | expected | observed | delta |")
        rows.append("| --- | --- | --- | --- |")
        for assertion in failing:
            rows.append(
                f"| `{assertion.id}` | `{assertion.expected_repr}` | "
                f"`{assertion.observed_repr}` | `{assertion.delta_repr or '—'}` |"
            )
    return "\n".join(rows)


def _overall(reports: list[VerificationReport]) -> Verdict:
    from verity_schema import worst

    return worst(r.verdict for r in reports)


def _failure_message(assertion: object) -> str:
    expected = getattr(assertion, "expected_repr", None)
    observed = getattr(assertion, "observed_repr", None)
    delta = getattr(assertion, "delta_repr", None)
    base = str(getattr(assertion, "expression", ""))
    if expected is None:
        return base
    message = f"{base} — expected {expected}, observed {observed}"
    return message + (f", delta {delta}" if delta else "")


def _detail(assertion: object) -> str:
    parts = [str(getattr(assertion, "expression", ""))]
    because = getattr(assertion, "because", "")
    if because:
        parts.append(f"why: {because}")
    refs = getattr(assertion, "evidence_refs", [])
    if refs:
        parts.append(f"evidence: {', '.join(refs)}")
    return "\n".join(parts)
