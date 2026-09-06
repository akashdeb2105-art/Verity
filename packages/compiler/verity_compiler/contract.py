"""Writing the proposed Outcome Contract.

The output is a **draft**, and it says so at the top of the file. Every
assertion carries the observation that produced it, so a reviewer can judge it
without replaying the recording, and the header lists what a single
demonstration cannot show -- because the dangerous failure here is a person
assuming the draft is complete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from verity_schema import RiskLevel

from .normalize import Step
from .values import ProposedComparison, ProposedConstant, ProposedInput, analyse

#: Things one successful demonstration can never establish, listed in the draft
#: so the gap is visible rather than assumed away.
UNOBSERVABLE = (
    "duplicate checks -- nothing in a single successful run shows what a duplicate "
    "would look like",
    "forbidden outcomes -- a payment or a deletion that must NOT happen cannot be "
    "observed in a run where it did not happen",
    "branches -- one recording shows one path; the alternatives were never taken",
    "tolerances -- an exact match was demonstrated, so any allowance is a judgement "
    "you must make",
)


@dataclass
class ContractDraft:
    name: str
    risk: RiskLevel
    inputs: list[ProposedInput] = field(default_factory=list)
    comparisons: list[ProposedComparison] = field(default_factory=list)
    constants: list[ProposedConstant] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    fields_by_system: dict[str, list[str]] = field(default_factory=dict)
    keys_by_system: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def assertion_count(self) -> int:
        return len(self.comparisons) + len(self.constants)


def propose(steps: list[Step], *, name: str = "recorded_workflow") -> ContractDraft:
    """Build a draft contract from a normalised recording."""
    inputs, comparisons, constants = analyse(steps)

    systems = _systems_with_reads(steps, comparisons, constants)
    fields_by_system: dict[str, list[str]] = {s: [] for s in systems}
    for comparison in comparisons:
        _add_field(fields_by_system, comparison.left_system, comparison.left_key)
        _add_field(fields_by_system, comparison.right_system, comparison.right_key)
    for constant in constants:
        _add_field(fields_by_system, constant.system, constant.key)

    keys_by_system = _keys_by_system(inputs, systems)

    risk = max((s.risk for s in steps), key=_risk_order, default=RiskLevel.LOW)

    notes: list[str] = []
    if not comparisons:
        notes.append(
            "no value appeared in two systems, so no cross-system check could be "
            "proposed; the assertions below may not prove much"
        )
    documents = [s.label.removeprefix("Open document ").strip()
                 for s in steps if s.note == "document-not-read"]
    if documents:
        notes.append(
            "a document was opened during the recording (" + ", ".join(documents)
            + ") but its contents were not read, so nothing in it produced a fact. "
            "If the check depends on what the document says, add a document source "
            "and the fields you need."
        )

    missing_keys = [s for s in systems if s not in keys_by_system]
    if missing_keys:
        notes.append(
            "no identifier was seen for: " + ", ".join(missing_keys)
            + " -- the read for each of these needs a key you supply"
        )

    return ContractDraft(
        name=name, risk=risk, inputs=inputs, comparisons=comparisons,
        constants=constants, systems=systems, fields_by_system=fields_by_system,
        keys_by_system=keys_by_system, notes=notes,
    )


def to_yaml(draft: ContractDraft, enrichment: Any = None) -> str:
    """Render the draft as a contract file a person can read and edit.

    ``enrichment`` is an optional :class:`~verity_compiler.enrich.Enrichment`.
    Anything a model suggested is written **commented out**, under a heading
    that says so. Accepting a suggestion is then an explicit act -- somebody
    deletes a ``#`` -- rather than a check that quietly starts being enforced
    because a model was confident about it.
    """
    lines: list[str] = []
    add = lines.append
    explanations = dict(getattr(enrichment, "explanations", {}) or {})

    add(f"# DRAFT -- proposed from one recorded demonstration of '{draft.name}'.")
    add("#")
    add("# Every assertion below was derived from a value that literally appeared")
    add("# more than once during the recording. Nothing was guessed. That keeps what")
    add("# is here trustworthy, and it is also why this draft is INCOMPLETE.")
    add("#")
    add("# A single successful demonstration cannot show:")
    for caveat in UNOBSERVABLE:
        wrapped = _wrap(caveat, 72)
        for chunk in wrapped:
            add(f"#   {chunk}" if chunk == wrapped[0] else f"#     {chunk}")
    add("#")
    add("# Read every line before trusting it. Delete what is wrong, add what is")
    add("# missing, then run: verity lint <this file>")
    add("")
    add("apiVersion: verity/v1")
    add("kind: OutcomeContract")
    add("")
    add("metadata:")
    add(f"  name: {getattr(enrichment, 'workflow_name', None) or draft.name}")
    add("  version: 0.1.0")
    description = getattr(enrichment, "description", None)
    if description:
        add(f"  description: {description}")
        add("  # description suggested by a model; the checks below were not")
    else:
        add("  description: Proposed from a recorded demonstration. Review before use.")
    add(f"  risk: {draft.risk.value}")

    if draft.inputs:
        add("")
        add("inputs:")
        for proposed_input in draft.inputs:
            add(f"  # {proposed_input.reason}")
            add(
                f"  {proposed_input.name}: "
                f"{{ type: {proposed_input.type}, required: true }}"
                f"        # e.g. {proposed_input.example}"
            )

    add("")
    add("# Each system the recording touched. 'capability' is a guess from the page")
    add("# address -- rename it to whatever your connector actually provides.")
    add("sources:")
    for system in draft.systems:
        add(f"  {system}: {{ kind: connector, capability: {system} }}")

    add("")
    add("facts:")
    for system in draft.systems:
        fields = draft.fields_by_system.get(system) or []
        key = draft.keys_by_system.get(system)
        add(f"  - id: {system}")
        add(f"    source: {system}")
        if key:
            add(f"    read: {{ resource: {_singular(system)}, "
                f"key: '{{{{ inputs.{key} }}}}' }}")
        else:
            add("    # TODO: no identifier was observed for this system.")
            add(f"    read: {{ resource: {_singular(system)}, key: 'TODO' }}")
        if fields:
            add(f"    select: [{', '.join(fields)}]")
        add("    expect_cardinality: 1")
        add("")

    add("expected:")
    if draft.assertion_count == 0:
        add("  # Nothing could be proposed from this recording. See the notes above.")
        add("  []")
    for comparison in draft.comparisons:
        add(f"  - id: {_comparison_id(comparison)}")
        add(f"    assert: '{_comparison_expression(comparison)}'")
        add("    because: >-")
        add(
            "      "
            + (explanations.get(_comparison_id(comparison)) or _comparison_reason(comparison))
        )
        if not comparison.both_explicit:
            add("    # Lower confidence: this value was on screen, but the person did")
            add("    # not click it. Confirm they meant to compare it.")
        add("")
    for constant in draft.constants:
        field_name = _field_name(constant.system, constant.key)
        constant_id = f"{constant.system}_{field_name}_is_{constant.value.lower()}"
        add(f"  - id: {constant_id}")
        add(f"    assert: '{constant.system}.{field_name} == \"{constant.value}\"'")
        add("    because: >-")
        add(f"      {explanations.get(constant_id) or _default_constant_reason(constant)}")
        add("    # Confirm this is the required end state and not just what happened")
        add("    # to be true that day.")
        add("")

    if any("document was opened" in n for n in draft.notes):
        add("# TODO: a document was opened but not read. See the note at the end of")
        add("#       this file. Document checks are usually the important ones.")
        add("")
    _add_suggestions(add, enrichment)

    add("# TODO: what must NOT have happened. Nothing here can be proposed from a")
    add("# recording, and it is often the most valuable part of a contract.")
    add("forbidden: []")
    add("")
    add("budgets:")
    add("  model_calls: 0        # verification is deterministic")
    add("")
    add("on_failure:")
    add("  halt: true")
    add("  require_human: true")

    if draft.notes:
        add("")
        for note in draft.notes:
            for chunk in _wrap(note, 74):
                add(f"# {chunk}")

    return "\n".join(lines) + "\n"


def _default_constant_reason(constant: ProposedConstant) -> str:
    return (
        f"The recording ended with {_humanise(constant.key)} showing "
        f"'{constant.value}'."
    )


def _add_suggestions(add: Any, enrichment: Any) -> None:
    """Write model suggestions as commented-out YAML.

    Commented on purpose. A suggestion is not an observation, and the file
    format should not let the two look alike.
    """
    suggestions = list(getattr(enrichment, "suggestions", []) or [])
    rejected = list(getattr(enrichment, "rejected", []) or [])
    if not suggestions and not rejected:
        return

    model = getattr(enrichment, "model", "") or "a model"
    add("# " + "-" * 70)
    add(f"# SUGGESTIONS FROM {model}")
    add("#")
    add("# These were NOT observed in the recording. A model proposed them, and")
    add("# each one parsed and referenced only facts the recording established --")
    add("# which makes them worth reading, not worth trusting.")
    add("#")
    add("# To accept one: move it into 'expected' or 'forbidden' and remove the")
    add("# leading '# '. Nothing below is enforced until you do.")
    add("#")

    for suggestion in suggestions:
        block = "forbidden" if suggestion.forbidden else "expected"
        add(f"#   - id: {suggestion.id}          # -> {block}")
        add(f"#     assert: '{suggestion.expression}'")
        if suggestion.because:
            add("#     because: >-")
            for chunk in _wrap(suggestion.because, 62):
                add(f"#       {chunk}")
        add("#")

    if rejected:
        add(f"# {len(rejected)} further suggestion(s) were discarded automatically:")
        for reason in rejected:
            for chunk in _wrap(reason, 66):
                add(f"#   {chunk}" if chunk == _wrap(reason, 66)[0] else f"#     {chunk}")
    add("# " + "-" * 70)
    add("")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _systems_with_reads(
    steps: list[Step],
    comparisons: list[ProposedComparison],
    constants: list[ProposedConstant],
) -> list[str]:
    """Systems that contributed a value to some assertion, in visit order."""
    used = {c.left_system for c in comparisons} | {c.right_system for c in comparisons}
    used |= {c.system for c in constants}
    ordered: list[str] = []
    for step in steps:
        if step.system and step.system in used and step.system not in ordered:
            ordered.append(step.system)
    return ordered


def _keys_by_system(inputs: list[ProposedInput], systems: list[str]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for system in systems:
        for item in inputs:
            if system in item.seen_in:
                keys[system] = item.name
                break
    return keys


def _add_field(mapping: dict[str, list[str]], system: str, key: str) -> None:
    name = _field_name(system, key)
    bucket = mapping.setdefault(system, [])
    if name not in bucket:
        bucket.append(name)


def _field_name(system: str, key: str) -> str:
    """Turn an on-screen key into a field name, dropping a repeated prefix.

    'bill-amount' inside the 'bills' system is just 'amount'.
    """
    tokens = [t for t in re.split(r"[-_\s]+", key.strip().lower()) if t]
    if not tokens:
        return "value"

    ordered = [t for t in re.split(r"[-_\s]+", system.lower()) if t]
    system_tokens = set(ordered) | {t.rstrip("s") for t in ordered}
    # Screens abbreviate: a 'purchase_orders' page labels its fields 'po-total'.
    # Treat the initials of the system name as one of its own tokens.
    if len(ordered) > 1:
        system_tokens.add("".join(t[0] for t in ordered))
    system_tokens.add(ordered[0][:2] if ordered else "")

    first = tokens[0]
    if len(tokens) > 1 and (first in system_tokens or first.rstrip("s") in system_tokens):
        tokens = tokens[1:]
    name = "_".join(tokens)
    return name if re.fullmatch(r"[a-z][a-z0-9_]*", name) else "value"


def _comparison_id(comparison: ProposedComparison) -> str:
    left = _field_name(comparison.left_system, comparison.left_key)
    right = _field_name(comparison.right_system, comparison.right_key)
    return f"{left}_matches_{right}" if left != right else f"{left}_matches"


def _comparison_expression(comparison: ProposedComparison) -> str:
    left_field = _field_name(comparison.left_system, comparison.left_key)
    right_field = _field_name(comparison.right_system, comparison.right_key)
    left = f"{comparison.left_system}.{left_field}"
    right = f"{comparison.right_system}.{right_field}"
    if comparison.kind == "number":
        return f"within({left}, {right}, tolerance = 0.01)"
    return f"{left} ~= {right}"


def _comparison_reason(comparison: ProposedComparison) -> str:
    seen = "and both were clicked on" if comparison.both_explicit else "and both were on screen"
    return (
        f"'{comparison.example}' appeared in both {comparison.left_system} and "
        f"{comparison.right_system} during the recording, {seen}."
    )


def _singular(system: str) -> str:
    return system[:-1] if system.endswith("s") and not system.endswith("ss") else system


def _humanise(token: str) -> str:
    return re.sub(r"[-_]+", " ", token).strip()


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width) or [""]


def _risk_order(risk: RiskLevel) -> int:
    return [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL].index(risk)
