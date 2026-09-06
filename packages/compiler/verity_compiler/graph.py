"""Steps to a WorkGraph.

A single demonstration is a straight line, and this builds a straight line.
That is not a limitation being papered over: one recording shows one path, so
inventing a branch here would be inventing a fact. Branches arrive when a
second recording diverges, or when a person adds one.
"""

from __future__ import annotations

from verity_schema import Edge, Node, RiskLevel, VariableSpec, WorkGraph
from verity_schema.common import Label

from .normalize import Step
from .values import ProposedInput

#: Verbs that change something. Recorded, but never executable in V1.
SIDE_EFFECT_VERBS = frozenset({"CREATE_RECORD", "UPDATE_RECORD", "SEND_MESSAGE"})


def build(
    steps: list[Step],
    inputs: list[ProposedInput],
    *,
    name: str = "recorded_workflow",
    contract_ref: str | None = None,
) -> WorkGraph:
    nodes: list[Node] = []
    edges: list[Edge] = []

    for step in steps:
        node_id = f"n{step.index + 1}"
        nodes.append(
            Node(
                id=node_id,
                type=step.verb,  # type: ignore[arg-type]
                label=step.label,
                intent=_intent(step),
                outputs=[step.element_hint] if step.verb == "EXTRACT" and step.element_hint else [],
                execution_strategies=["browser"],
                confidence=step.confidence,
                risk=step.risk,
                approval_required=step.risk is not RiskLevel.LOW,
                timeout_ms=15_000,
            )
        )
        if step.index > 0:
            edges.append(Edge.model_validate({"from": f"n{step.index}", "to": node_id}))

    variables = [
        VariableSpec(
            name=item.name, type=item.type, source_kind="input",
            source_ref=", ".join(item.seen_in), label=Label.INFERRED, confidence=0.9,
        )
        for item in inputs
    ]

    return WorkGraph(
        name=name, version="0.1.0", contract_ref=contract_ref,
        variables=variables, nodes=nodes, edges=edges,
        metadata={
            "compiled_from": "demonstration",
            "branches_observed": 0,
            "note": (
                "One demonstration shows one path. No branch was invented; add "
                "them by recording an alternative or by editing this graph."
            ),
        },
    )


def _intent(step: Step) -> str:
    if step.verb == "EXTRACT":
        return f"Read {step.label.removeprefix('Read ')} from {step.system}"
    if step.verb in SIDE_EFFECT_VERBS:
        return f"{step.label} in {step.system} (changes state; approval required)"
    if step.verb == "SEARCH":
        return f"Find the record in {step.system}"
    if step.verb == "NAVIGATE":
        return f"Go to {step.label.removeprefix('Open ').removeprefix('Follow ')}"
    return step.label


def summarise(graph: WorkGraph) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in graph.nodes:
        counts[node.type] = counts.get(node.type, 0) + 1
    return counts
