"""A completed run, written to disk so a later run can be diffed against it.

Replay is not re-doing. ``verity replay`` re-executes the same graph with the
same inputs, always dry, and compares what happened to what a stored run
recorded. The comparison is deliberately narrow: it reports a changed path, a
changed outcome, a changed verdict, a changed payload, and -- for a run that
drove a browser -- a changed page *structure*. It never reports a changed
duration, because a canary that cried wolf every time a page loaded a
millisecond slower would be turned off within a week.

Two rules from the approved M2c plan live here.

**A tier boundary is not drift.** A run made with ``--browser`` observed pages;
one made without recorded that it *would* have. Diffing those two as if a
missing ``dom_hash`` were a structural change would be this product's own
failure aimed inward, so a cross-tier replay is refused, not compared, and is
never called identical.

**Files, not a store.** A run is a small directory of JSON under
``.verity/runs/``. There is no database and no index (ADR-0007); cleanup is
``rm``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .plan import Plan
from .ports import TIER_BROWSER, TIER_RECORDED
from .report import RunReport

#: Where run records are written unless a caller says otherwise.
DEFAULT_RUNS_DIR = ".verity/runs"


@dataclass(frozen=True)
class RunStepView:
    """One step of a run, reduced to the fields a diff looks at."""

    node_id: str
    action: str
    status: str
    consequential: bool
    write_digest: str = ""
    dom_hash: str = ""
    extracted: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunRecord:
    """A completed run, in the shape ``diff_runs`` compares.

    Built either from a fresh :class:`RunReport` (``record_from_report``) or
    from a directory on disk (``read_run_record``). The two paths must produce
    the same structure, or a replay would diff against a different shape than
    it wrote.
    """

    run_id: str
    graph_name: str
    executor_tier: str
    inputs: dict[str, str]
    outcome: str
    verdict: str
    halted_at: str
    halted_by: str
    planned_sequence: tuple[str, ...]
    reached_sequence: tuple[str, ...]
    steps: tuple[RunStepView, ...]
    audit_head: str
    meta: dict[str, Any] = field(default_factory=dict)

    def step(self, node_id: str) -> RunStepView | None:
        return next((s for s in self.steps if s.node_id == node_id), None)

    @property
    def drove_a_browser(self) -> bool:
        return self.executor_tier == TIER_BROWSER


def record_from_report(
    report: RunReport, the_plan: Plan, *, meta: dict[str, Any] | None = None
) -> RunRecord:
    """Reduce a just-finished run to the comparable shape."""
    steps = tuple(
        RunStepView(
            node_id=s.node_id,
            action=s.action,
            status=s.status,
            consequential=s.consequential,
            write_digest=s.write_digest,
            dom_hash=str(s.outputs.get("dom_hash", "")),
            extracted={str(k): str(v) for k, v in dict(s.outputs.get("extracted", {})).items()},
        )
        for s in report.steps
    )
    return RunRecord(
        run_id=report.run_id,
        graph_name=report.graph_name,
        executor_tier=report.executor_tier,
        inputs=dict(report.inputs),
        outcome=report.outcome.value,
        verdict=report.verifier_says,
        halted_at=report.halted_at,
        halted_by=report.halted_by,
        planned_sequence=tuple(s.id for s in the_plan.steps),
        reached_sequence=tuple(report.node_sequence),
        steps=steps,
        audit_head=report.audit_head,
        meta=dict(meta or {}),
    )


def write_run_record(
    report: RunReport,
    the_plan: Plan,
    *,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
    meta: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Path:
    """Write a run to ``<runs_dir>/<run_id>/`` and return that directory.

    Five files: ``meta.json`` (enough for ``replay`` to re-run), ``report.json``
    (the full report), ``plan.json`` (the intended order, including steps a halt
    never reached), ``trace.json`` (only when a browser was driven) and
    ``audit.jsonl``.
    """
    target = Path(runs_dir) / report.run_id
    target.mkdir(parents=True, exist_ok=True)
    stamped = (now or datetime.now(timezone.utc)).isoformat()

    meta_doc = {
        "run_id": report.run_id,
        "graph_name": report.graph_name,
        "executor_tier": report.executor_tier,
        "dry_run": report.dry_run,
        "inputs": dict(report.inputs),
        "created_at": stamped,
        **dict(meta or {}),
    }
    _write_json(target / "meta.json", meta_doc)
    _write_json(target / "report.json", report.to_dict())
    _write_json(
        target / "plan.json",
        {
            "planned_sequence": [s.id for s in the_plan.steps],
            "consequential": [s.id for s in the_plan.steps if s.consequential],
        },
    )
    if report.trace is not None:
        _write_json(target / "trace.json", report.trace.model_dump(mode="json", by_alias=True))
    (target / "audit.jsonl").write_text(report.audit.to_jsonl(), encoding="utf-8")
    return target


def read_run_record(path: str | Path) -> RunRecord:
    """Load a run written by :func:`write_run_record`."""
    base = Path(path)
    report_doc = json.loads((base / "report.json").read_text("utf-8"))
    plan_doc = json.loads((base / "plan.json").read_text("utf-8"))
    meta_doc: dict[str, Any] = {}
    meta_path = base / "meta.json"
    if meta_path.is_file():
        meta_doc = json.loads(meta_path.read_text("utf-8"))

    audit_head = str(report_doc.get("audit", {}).get("head", ""))
    audit_path = base / "audit.jsonl"
    if audit_path.is_file():
        audit_head = AuditLog.from_jsonl(audit_path.read_text("utf-8")).head

    steps = tuple(
        RunStepView(
            node_id=str(s.get("node", "")),
            action=str(s.get("action", "")),
            status=str(s.get("status", "")),
            consequential=bool(s.get("consequential", False)),
            write_digest=str(s.get("write_digest", "")),
            dom_hash=str((s.get("outputs") or {}).get("dom_hash", "")),
            extracted={
                str(k): str(v)
                for k, v in dict((s.get("outputs") or {}).get("extracted", {})).items()
            },
        )
        for s in report_doc.get("steps", [])
    )
    return RunRecord(
        run_id=str(report_doc.get("run_id", "")),
        graph_name=str(report_doc.get("graph", "")),
        executor_tier=str(report_doc.get("executor_tier", TIER_RECORDED)),
        inputs={str(k): str(v) for k, v in dict(meta_doc.get("inputs", {})).items()},
        outcome=str(report_doc.get("outcome", "")),
        verdict=str(report_doc.get("verifier_says", "")),
        halted_at=str(report_doc.get("halted_at", "")),
        halted_by=str(report_doc.get("halted_by", "")),
        planned_sequence=tuple(str(n) for n in plan_doc.get("planned_sequence", [])),
        reached_sequence=tuple(str(n) for n in report_doc.get("node_sequence", [])),
        steps=steps,
        audit_head=audit_head,
        meta=meta_doc,
    )


@dataclass(frozen=True)
class RunDifference:
    """One way two runs of the same graph disagree."""

    kind: str
    """path | tier | step-status | structural | extract | verdict | write | outcome"""

    node_id: str
    detail: str
    baseline: str
    replay: str


@dataclass(frozen=True)
class RunDiff:
    """The result of comparing two runs."""

    baseline_run_id: str
    replay_run_id: str
    baseline_tier: str
    replay_tier: str
    differences: tuple[RunDifference, ...] = ()
    comparable: bool = True
    """False when the two runs were produced by different executor tiers. A diff
    that is not comparable is never identical, and carries a single ``tier``
    difference rather than a list of structural ones that would be noise."""

    @property
    def identical(self) -> bool:
        return self.comparable and not self.differences

    @property
    def exit_code(self) -> int:
        return 0 if self.identical else 1

    def render(self) -> list[str]:
        lines = [f"  baseline {self.baseline_run_id} ({self.baseline_tier})",
                 f"  replay   {self.replay_run_id} ({self.replay_tier})", ""]
        if not self.comparable:
            drove = "baseline" if self.baseline_tier == TIER_BROWSER else "replay"
            noop = "replay" if drove == "baseline" else "baseline"
            lines.append(f"  not comparable: the {noop} did not drive a browser, the {drove} did")
            lines.append("  a recorded-no-op run and a browser run are different claims")
            return lines
        if not self.differences:
            lines.append("  identical: no difference between the two runs")
            return lines
        for d in self.differences:
            where = f" {d.node_id}" if d.node_id else ""
            lines.append(f"  {d.kind}{where}: {d.detail}")
            lines.append(f"      baseline  {d.baseline}")
            lines.append(f"      replay    {d.replay}")
        return lines


def diff_runs(baseline: RunRecord, replay: RunRecord) -> RunDiff:
    """Compare two runs of the same graph. Timing is never a difference."""

    def _diff(differences: tuple[RunDifference, ...], *, comparable: bool = True) -> RunDiff:
        return RunDiff(
            baseline_run_id=baseline.run_id,
            replay_run_id=replay.run_id,
            baseline_tier=baseline.executor_tier,
            replay_tier=replay.executor_tier,
            differences=differences,
            comparable=comparable,
        )

    if baseline.executor_tier != replay.executor_tier:
        return _diff(
            comparable=False,
            differences=(
                RunDifference(
                    kind="tier",
                    node_id="",
                    detail="one run drove a browser and the other did not",
                    baseline=baseline.executor_tier,
                    replay=replay.executor_tier,
                ),
            ),
        )

    out: list[RunDifference] = []

    if baseline.reached_sequence != replay.reached_sequence:
        out.append(RunDifference(
            kind="path", node_id="",
            detail="the two runs took a different sequence of steps",
            baseline=" -> ".join(baseline.reached_sequence) or "(none)",
            replay=" -> ".join(replay.reached_sequence) or "(none)",
        ))

    ordered_ids = list(dict.fromkeys(
        [s.node_id for s in baseline.steps] + [s.node_id for s in replay.steps]
    ))
    compare_structure = baseline.executor_tier == TIER_BROWSER
    for node_id in ordered_ids:
        a = baseline.step(node_id)
        b = replay.step(node_id)
        a_status = a.status if a else "(not reached)"
        b_status = b.status if b else "(not reached)"
        if a_status != b_status:
            out.append(RunDifference(
                kind="step-status", node_id=node_id,
                detail="this step ended differently",
                baseline=a_status, replay=b_status,
            ))
        if a is None or b is None:
            continue
        if a.write_digest != b.write_digest:
            out.append(RunDifference(
                kind="write", node_id=node_id,
                detail="a different payload would be written",
                baseline=a.write_digest or "(none)", replay=b.write_digest or "(none)",
            ))
        if compare_structure and a.dom_hash != b.dom_hash:
            out.append(RunDifference(
                kind="structural", node_id=node_id,
                detail="the page structure changed underneath this step",
                baseline=a.dom_hash or "(none)", replay=b.dom_hash or "(none)",
            ))
        if a.extracted != b.extracted:
            out.append(RunDifference(
                kind="extract", node_id=node_id,
                detail="this step read a different value",
                baseline=json.dumps(a.extracted, sort_keys=True),
                replay=json.dumps(b.extracted, sort_keys=True),
            ))

    if baseline.verdict != replay.verdict:
        out.append(RunDifference(
            kind="verdict", node_id="",
            detail="verification concluded differently",
            baseline=baseline.verdict, replay=replay.verdict,
        ))

    if (baseline.outcome, baseline.halted_at, baseline.halted_by) != (
        replay.outcome, replay.halted_at, replay.halted_by
    ):
        out.append(RunDifference(
            kind="outcome", node_id="",
            detail="the run ended differently",
            baseline=_outcome_phrase(baseline),
            replay=_outcome_phrase(replay),
        ))

    return _diff(tuple(out))


def _outcome_phrase(record: RunRecord) -> str:
    if record.halted_at:
        return f"{record.outcome} before {record.halted_at} ({record.halted_by})"
    return record.outcome


def _write_json(path: Path, doc: Any) -> None:
    path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")


__all__ = [
    "DEFAULT_RUNS_DIR",
    "RunDiff",
    "RunDifference",
    "RunRecord",
    "RunStepView",
    "diff_runs",
    "read_run_record",
    "record_from_report",
    "write_run_record",
]
