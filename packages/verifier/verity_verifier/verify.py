"""The verifier: contract + (trace | live sources) -> verdict, with evidence.

This module is the portability boundary made concrete. It imports the schema,
the evidence store, the connectors and the extractor -- and nothing that
executes a workflow. There is no import path from here to a browser, an agent
or a model client, and ``import-linter`` fails the build if one appears.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from verity_connectors import ConnectorRegistry, DocumentConnector
from verity_evidence import EvidenceStore
from verity_extract import PdfExtractor
from verity_schema import (
    AssertionResult,
    BudgetReport,
    Channel,
    OutcomeContract,
    Severity,
    Strength,
    Trace,
    Verdict,
    VerificationReport,
)

from .divergence import detect_environment_changes, localize
from .expr import Call, Compare, EvaluationError, Scope, evaluate, render, to_decimal
from .resolve import FactResolver, infer_write_channel
from .typecheck import CheckedAssertion, CheckedContract, typecheck


@dataclass
class VerifyOptions:
    """Everything the verifier needs that is not the contract itself."""

    registry: ConnectorRegistry
    evidence_dir: str = ".verity/evidence"
    documents: DocumentConnector | None = None
    extractor: PdfExtractor | None = None
    trace: Trace | None = None
    baseline: Trace | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    fixed_time: datetime | None = None
    """Pins timestamps, so determinism tests compare like with like."""


@dataclass(frozen=True)
class VerificationOutcome:
    """A report plus the evidence store that backs it.

    Kept as a pair rather than hidden on the report, because the report is an
    immutable data artifact and the store is a live handle to bytes on disk.
    """

    report: VerificationReport
    store: EvidenceStore


def verify(contract: OutcomeContract, options: VerifyOptions) -> VerificationOutcome:
    """Evaluate a contract. Never raises for a business failure -- it reports one."""
    checked = typecheck(contract)
    return verify_checked(checked, options)


def verify_checked(
    checked: CheckedContract, options: VerifyOptions
) -> VerificationOutcome:
    contract = checked.contract
    started_at = options.fixed_time or datetime.now(timezone.utc)
    started = time.perf_counter()

    store = EvidenceStore(options.evidence_dir, fixed_time=options.fixed_time)
    documents = options.documents or _documents_from(options.registry)
    resolver = FactResolver(
        options.registry, store, documents=documents, extractor=options.extractor
    )

    inputs = _coerce_inputs(contract, options.inputs)
    resolution = resolver.resolve_all(contract, inputs, trace=options.trace)

    scope = Scope(facts=resolution.values, inputs=inputs)
    write_channel = infer_write_channel(options.trace)

    results = [
        _evaluate_assertion(item, scope, resolution, write_channel)
        for item in checked.assertions
    ]

    weak_failures = [
        r for r in results
        if r.passed is False and r.strength is Strength.WEAK
    ]
    changes = detect_environment_changes(
        options.trace, options.baseline, resolution.drift_notes, weak_failures
    )
    divergence = localize(results, resolution.results, changes)

    verdict = decide(results, resolution.results, changes)

    duration_ms = int((time.perf_counter() - started) * 1000)
    finished_at = options.fixed_time or datetime.now(timezone.utc)

    budgets = BudgetReport(
        duration_ms=duration_ms,
        duration_budget_ms=contract.budgets.duration_ms,
        model_calls=0,
        model_calls_budget=contract.budgets.model_calls,
        cost_usd=0.0,
        exceeded=(
            ["duration_ms"] if duration_ms > contract.budgets.duration_ms else []
        ),
    )

    notes = [*checked.warnings, *resolution.drift_notes]

    report = VerificationReport(
        verdict=verdict,
        contract_name=contract.metadata.name,
        contract_version=contract.metadata.version,
        contract_fingerprint=contract.fingerprint(),
        run_id=options.trace.run_id if options.trace else None,
        runtime_name=options.trace.runtime.name if options.trace else None,
        status_reported=options.trace.status_reported if options.trace else None,
        started_at=started_at,
        finished_at=finished_at,
        inputs=_jsonable(inputs),
        facts=resolution.results,
        assertions=results,
        divergence=divergence,
        budgets=budgets,
        notes=notes,
    )
    return VerificationOutcome(report=report, store=store)


def decide(
    assertions: list[AssertionResult],
    facts: list[Any],
    changes: list[Any],
) -> Verdict:
    """Combine results into exactly one of the four verdicts.

    Order matters and is deliberate: a blocking failure outranks everything; an
    unresolvable fact outranks a changed environment; and DRIFT is only ever
    reported when the outcome itself still holds.
    """
    blocking_failures = [
        a for a in assertions
        if a.passed is False and a.severity is Severity.BLOCKING
    ]
    if blocking_failures:
        return Verdict.FAIL

    if any(not f.resolved for f in facts):
        return Verdict.INCONCLUSIVE

    if any(a.passed is None for a in assertions):
        # An assertion that could not be evaluated is never counted as passing.
        return Verdict.INCONCLUSIVE

    if changes or any(a.passed is False for a in assertions):
        return Verdict.DRIFT

    return Verdict.PASS


def _evaluate_assertion(
    item: CheckedAssertion,
    scope: Scope,
    resolution: Any,
    write_channel: str,
) -> AssertionResult:
    assertion = item.assertion
    channel = _channel_for(item, resolution, write_channel)
    expected_repr, observed_repr, delta_repr = _explain_comparison(item, scope)

    evidence_refs: list[str] = []
    for fact_id in sorted(item.fact_refs):
        found = next((f for f in resolution.results if f.id == fact_id), None)
        if found is not None:
            evidence_refs.extend(found.evidence_refs)

    try:
        raw = evaluate(item.ast, scope)
    except EvaluationError as exc:
        return AssertionResult(
            id=assertion.id, expression=render(item.ast), passed=None,
            severity=assertion.severity, strength=item.strength, channel=channel,
            because=assertion.because, error=str(exc), evidence_refs=evidence_refs,
            expected_repr=expected_repr, observed_repr=observed_repr,
            is_forbidden=item.is_forbidden,
        )

    passed = bool(raw)
    return AssertionResult(
        id=assertion.id, expression=render(item.ast), passed=passed,
        severity=assertion.severity, strength=item.strength, channel=channel,
        because=assertion.because, evidence_refs=evidence_refs,
        expected_repr=expected_repr, observed_repr=observed_repr,
        delta_repr=delta_repr if not passed else None,
        is_forbidden=item.is_forbidden,
    )


def _channel_for(item: CheckedAssertion, resolution: Any, write_channel: str) -> Channel:
    """Independent-channel detection.

    Reading a value back through the same surface that wrote it is
    self-confirmation. Verity records that rather than assuming it away.
    """
    channels = {
        resolution.channels.get(fact_id)
        for fact_id in item.fact_refs
        if resolution.channels.get(fact_id)
    }
    if len(channels) > 1:
        return Channel.INDEPENDENT
    if not channels:
        return Channel.UNKNOWN
    only = next(iter(channels))
    if write_channel == "unknown":
        return Channel.UNKNOWN
    return Channel.SAME if only == write_channel else Channel.INDEPENDENT


def _explain_comparison(
    item: CheckedAssertion, scope: Scope
) -> tuple[str | None, str | None, str | None]:
    """Render expected, observed and the delta for the assertion's top node.

    This is the number a person actually reacts to: ``+133,200.00`` says more
    than ``amount_match: false``. It is computed for the shapes that carry a
    meaningful pair of values -- a comparison, and the aggregate builtins --
    and left empty rather than guessed at for anything else.
    """
    node = item.ast

    if isinstance(node, Compare):
        return _pair(node.left, node.right, scope)

    if isinstance(node, Call):
        if node.name == "within" and len(node.args) == 2:
            # within(observed, expected, tolerance = t)
            return _pair(node.args[0], node.args[1], scope, tolerance=node.kwargs)
        if node.name in ("none", "any", "all") and node.args:
            return _explain_collection(node, scope)
        if node.name == "exists" and node.args:
            try:
                value = evaluate(node.args[0], scope)
            except EvaluationError:
                return None, None, None
            return "a value", _format(value), None

    return None, None, None


def _pair(
    observed_node: Any, expected_node: Any, scope: Scope, tolerance: Any = ()
) -> tuple[str | None, str | None, str | None]:
    try:
        observed_value = evaluate(observed_node, scope)
        expected_value = evaluate(expected_node, scope)
    except EvaluationError:
        return None, None, None

    # Values arriving from an API are often numeric strings while values from
    # a document are already Decimals. Coerce both so the two sides of a
    # comparison are rendered -- and subtracted -- on the same terms.
    observed_number = to_decimal(observed_value)
    expected_number = to_decimal(expected_value)

    expected = _format(expected_number if expected_number is not None else expected_value)
    observed = _format(observed_number if observed_number is not None else observed_value)

    for key, node in tolerance or ():
        if key == "tolerance":
            with contextlib.suppress(EvaluationError):  # tolerance is a literal
                expected = f"{expected} (+/- {_format(evaluate(node, scope))})"

    delta = None
    if observed_number is not None and expected_number is not None:
        delta = f"{observed_number - expected_number:+,.2f}"
    return expected, observed, delta


def _explain_collection(
    node: Call, scope: Scope
) -> tuple[str | None, str | None, str | None]:
    """For none()/any()/all(), report how many items matched out of how many."""
    try:
        items = evaluate(node.args[0], scope)
    except EvaluationError:
        return None, None, None
    total = len(items) if isinstance(items, list) else 1

    matched = 0
    for item in items if isinstance(items, list) else [items]:
        inner = scope.child(item)
        try:
            if all(bool(evaluate(p, inner)) for p in node.args[1:]):
                matched += 1
        except EvaluationError:
            return None, None, None

    expected = {"none": "0 matching", "any": "at least 1 matching",
                "all": f"all {total} matching"}[node.name]
    return expected, f"{matched} of {total} matching", None


def _format(value: Any) -> str:
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _coerce_inputs(contract: OutcomeContract, provided: dict[str, Any]) -> dict[str, Any]:
    coerced: dict[str, Any] = {}
    for name, spec in contract.inputs.items():
        if name not in provided:
            if spec.required:
                continue
            continue
        raw = provided[name]
        if spec.type == "decimal" and not isinstance(raw, Decimal):
            try:
                coerced[name] = Decimal(str(raw))
            except (InvalidOperation, ValueError, TypeError):
                # A malformed numeric input is left as the string the caller
                # gave us, so the assertion that reads it fails honestly rather
                # than being silently coerced into something plausible.
                coerced[name] = raw
            continue
        coerced[name] = raw
    for name, value in provided.items():
        coerced.setdefault(name, value)
    return coerced


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in value.items()}


def _documents_from(registry: ConnectorRegistry) -> DocumentConnector | None:
    found = registry.get("documents")
    return found if isinstance(found, DocumentConnector) else None


def missing_inputs(contract: OutcomeContract, provided: dict[str, Any]) -> list[str]:
    return sorted(
        name for name, spec in contract.inputs.items()
        if spec.required and name not in provided
    )
