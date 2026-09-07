"""The executor: walks a plan, and stops before it does harm.

Two rules define this module.

**Nothing consequential happens unverified.** Before the first step that
changes the world, the gate is consulted. Only ``PASS`` opens it. ``FAIL``
halts, and so does ``INCONCLUSIVE`` -- "I could not check" is not permission,
and a system that treats it as permission is lying about what it knows.

**Zero model calls.** Execution is a deterministic walk over a graph that was
decided earlier. Ten replays produce the same node sequence and cost nothing,
which is what makes a nightly canary affordable and a regression suite
meaningful. There is no provider import in this package, and an import-linter
contract keeps it that way.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from verity_connectors import (
    ConnectorError,
    ConnectorRegistry,
    WritableConnector,
    WriteGuard,
    WriteIntent,
    WriteMode,
)
from verity_schema.workgraph import Node, WorkGraph, WriteSpec

from .plan import Plan, Step
from .plan import plan as build_plan
from .ports import ClosedGate, GateResult, GateVerdict, VerificationGate
from .report import RunOutcome, RunReport, StepResult

#: Steps that only look at the world. Anything not listed here and not
#: consequential is carried out as a no-op with its intent recorded, so an
#: unimplemented verb can never be mistaken for a completed one.
READ_ACTIONS: frozenset[str] = frozenset({
    "NAVIGATE", "CLICK", "TYPE", "SELECT", "EXTRACT", "SEARCH",
    "COMPARE", "TRANSFORM", "VERIFY",
})


class ExecutionError(Exception):
    """Execution could not proceed. Distinct from a business-outcome failure."""


class Clock(Protocol):
    def __call__(self) -> float: ...


@dataclass
class RunOptions:
    """Everything a run needs that is not the graph itself."""

    inputs: dict[str, str] = field(default_factory=dict)
    mode: WriteMode = WriteMode.DRY_RUN
    """Dry run by default. Writing is something a caller opts into, out loud."""

    gate: VerificationGate = field(default_factory=ClosedGate)
    """Closed by default: a runtime with no verification configured cannot write."""

    registry: ConnectorRegistry | None = None
    writers: dict[str, WritableConnector] = field(default_factory=dict)
    run_id: str = ""
    max_steps: int = 200
    clock: Clock = time.monotonic


def execute(graph: WorkGraph, options: RunOptions | None = None) -> RunReport:
    """Run a graph, and return what happened -- including what did not.

    Never raises for a business outcome. A halt is a result, not an error, and
    a caller that has to catch an exception to find out it was stopped will
    eventually forget to.
    """
    options = options or RunOptions()
    the_plan = build_plan(graph)
    if len(the_plan) > options.max_steps:
        raise ExecutionError(
            f"{graph.name} plans {len(the_plan)} steps, above the {options.max_steps} limit"
        )

    report = RunReport(
        graph_name=graph.name,
        run_id=options.run_id or f"run_{uuid.uuid4().hex[:12]}",
        dry_run=options.mode is WriteMode.DRY_RUN,
        inputs=dict(options.inputs),
    )
    guard = WriteGuard(mode=options.mode)
    context = _Context(options=options, guard=guard, report=report)

    for step in the_plan.steps:
        if step.consequential and not context.gate_opened:
            gate = _consult_gate(context)
            if not gate.allows_write:
                _halt(context, the_plan, step, gate)
                return report

        report.steps.append(_run_step(context, step))

    report.outcome = (
        RunOutcome.FAILED
        if any(s.status == "error" for s in report.steps)
        else RunOutcome.COMPLETED
    )
    return report


@dataclass
class _Context:
    options: RunOptions
    guard: WriteGuard
    report: RunReport
    gate_opened: bool = False


def _consult_gate(context: _Context) -> GateResult:
    """Ask the gate once, before the first consequential step.

    Once, not per step: the gate answers a question about the *outcome* of the
    run, and asking it repeatedly would invite a caller to treat a later PASS
    as overturning an earlier FAIL.
    """
    result = context.options.gate.check(dict(context.options.inputs))
    context.report.gate = result
    context.gate_opened = result.allows_write
    return result


def _halt(context: _Context, the_plan: Plan, step: Step, gate: GateResult) -> None:
    """Stop before ``step``, and record precisely what was not done."""
    report = context.report
    report.outcome = RunOutcome.HALTED
    report.halted_at = step.id
    report.steps.append(StepResult(
        index=step.index, node_id=step.id, action=step.node.type,
        label=step.node.label, status="halted", consequential=True,
    ))
    report.not_performed = [
        _describe(s.node) for s in the_plan.steps[step.index:] if s.consequential
    ]
    if gate.verdict is GateVerdict.INCONCLUSIVE and not gate.reason:
        report.gate = GateResult(
            verdict=gate.verdict,
            reason="verification could not be completed, which is not permission to write",
            failed_assertions=gate.failed_assertions,
            first_divergence=gate.first_divergence,
            evidence_ref=gate.evidence_ref,
        )


def _run_step(context: _Context, step: Step) -> StepResult:
    started = context.options.clock()
    node = step.node

    try:
        if step.consequential:
            outputs, digest, performed = _perform_write(context, node)
        elif node.type in READ_ACTIONS:
            outputs, digest, performed = _perform_read(context, node), "", False
        else:
            raise ExecutionError(f"no executor for action {node.type!r}")
    except (ConnectorError, ExecutionError) as exc:
        return StepResult(
            index=step.index, node_id=step.id, action=node.type, label=node.label,
            status="error", consequential=step.consequential, error=str(exc),
            duration_ms=_elapsed(context, started),
        )

    return StepResult(
        index=step.index, node_id=step.id, action=node.type, label=node.label,
        status="ok", consequential=step.consequential, performed_write=performed,
        write_digest=digest, outputs=outputs, duration_ms=_elapsed(context, started),
    )


def _perform_write(
    context: _Context, node: Node
) -> tuple[dict[str, Any], str, bool]:
    spec = _write_spec(node)
    writer = context.options.writers.get(spec.connector)
    if writer is None:
        raise ExecutionError(
            f"node {node.id!r} writes to {spec.connector!r}, which has no writer bound"
        )

    intent = WriteIntent(
        connector=spec.connector,
        resource=spec.resource,
        payload=_payload(spec, context.options.inputs),
    )
    result = context.guard.perform(writer, intent)
    return dict(result.record), intent.digest, result.performed


def _perform_read(context: _Context, node: Node) -> dict[str, Any]:
    """Reads are recorded, not re-derived.

    The runtime does not evaluate the contract -- that is the gate's job, and
    it reads through its own connectors. What the executor records here is
    that the step was reached, which is what a trace needs to be useful.
    """
    return {"action": node.type, "intent": node.intent or node.label}


def _write_spec(node: Node) -> WriteSpec:
    if node.write is None:
        raise ExecutionError(
            f"node {node.id!r} is consequential but has no write spec, so there is "
            "no way to know what it would do; refusing to guess"
        )
    return node.write


def _payload(spec: WriteSpec, inputs: dict[str, str]) -> dict[str, Any]:
    return {key: _fill(value, inputs) for key, value in sorted(spec.payload.items())}


def _fill(template: str, inputs: dict[str, str]) -> str:
    """Substitute ``{{ inputs.x }}`` and nothing else.

    Deliberately not a template engine. The set of things a payload may
    interpolate is exactly the run's declared inputs, so a graph cannot reach
    for anything a reviewer did not see when they approved it.
    """
    out = template
    for name, value in inputs.items():
        for spelling in (f"{{{{ inputs.{name} }}}}", f"{{{{inputs.{name}}}}}"):
            out = out.replace(spelling, value)
    return out


def _describe(node: Node) -> str:
    return f"{node.type} {node.label or node.id}".strip()


def _elapsed(context: _Context, started: float) -> int:
    return int((context.options.clock() - started) * 1000)
