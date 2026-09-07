"""Turning a WorkGraph into an ordered list of steps.

Planning is separate from execution and has no I/O, so the order a run will
take can be inspected -- and tested -- without anything happening. ``verity
dry-run`` shows exactly this list.

The order is deterministic. Two runs of the same graph must produce the same
sequence, or replay proves nothing and a comparison between runs is
meaningless.
"""

from __future__ import annotations

from dataclasses import dataclass

from verity_schema import RiskLevel
from verity_schema.workgraph import Edge, Node, WorkGraph

#: Actions that change the world. Everything else only looks at it.
#:
#: The list is deliberately about *effect*, not about risk score: CLICK is not
#: here even though a click can submit a form, because the runtime never
#: reaches a system of record through a click -- writes go through the
#: connector boundary, where they can be intercepted.
CONSEQUENTIAL: frozenset[str] = frozenset({
    "CREATE_RECORD", "UPDATE_RECORD", "SEND_MESSAGE", "CALL_API",
})


class PlanError(Exception):
    """The graph cannot be turned into a sequence, so nothing is attempted."""


@dataclass(frozen=True)
class Step:
    """One node, with the facts the executor needs decided in advance."""

    index: int
    node: Node
    consequential: bool

    @property
    def id(self) -> str:
        return self.node.id

    @property
    def needs_approval(self) -> bool:
        return self.node.approval_required or self.node.risk is RiskLevel.HIGH


@dataclass(frozen=True)
class Plan:
    """The whole run, decided before any of it happens."""

    graph_name: str
    steps: tuple[Step, ...] = ()

    def __len__(self) -> int:
        return len(self.steps)

    @property
    def consequential_steps(self) -> tuple[Step, ...]:
        return tuple(s for s in self.steps if s.consequential)

    @property
    def first_consequential(self) -> Step | None:
        return next(iter(self.consequential_steps), None)

    def step(self, node_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == node_id), None)


def is_consequential(node: Node) -> bool:
    return node.type in CONSEQUENTIAL


def plan(graph: WorkGraph) -> Plan:
    """Order a graph's nodes into the sequence a run will follow.

    A topological sort, with ties broken by edge priority and then by node id.
    The tie-break is the point: a graph often admits several valid orders, and
    a runtime that picks a different one each time cannot be replayed.
    """
    nodes = {n.id: n for n in graph.nodes}
    if len(nodes) != len(graph.nodes):
        duplicates = _duplicates([n.id for n in graph.nodes])
        raise PlanError(f"duplicate node ids: {', '.join(duplicates)}")

    for edge in graph.edges:
        for end in (edge.from_, edge.to):
            if end not in nodes:
                raise PlanError(f"edge refers to unknown node {end!r}")

    ordered = _topological(graph, nodes)
    return Plan(
        graph_name=graph.name,
        steps=tuple(
            Step(index=i, node=node, consequential=is_consequential(node))
            for i, node in enumerate(ordered)
        ),
    )


def _topological(graph: WorkGraph, nodes: dict[str, Node]) -> list[Node]:
    incoming: dict[str, int] = dict.fromkeys(nodes, 0)
    outgoing: dict[str, list[Edge]] = {node_id: [] for node_id in nodes}
    for edge in graph.edges:
        incoming[edge.to] += 1
        outgoing[edge.from_].append(edge)

    ready = sorted(node_id for node_id, count in incoming.items() if count == 0)
    order: list[Node] = []

    while ready:
        current = ready.pop(0)
        order.append(nodes[current])
        for edge in sorted(outgoing[current], key=lambda e: (-e.priority, e.to)):
            incoming[edge.to] -= 1
            if incoming[edge.to] == 0:
                ready.append(edge.to)
                ready.sort()

    if len(order) != len(nodes):
        unreached = sorted(set(nodes) - {n.id for n in order})
        raise PlanError(
            "the graph has a cycle; these nodes can never be reached: "
            + ", ".join(unreached)
        )
    return order


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for value in values:
        (repeated if value in seen else seen).add(value)
    return sorted(repeated)


__all__ = [
    "CONSEQUENTIAL", "Plan", "PlanError", "Step", "is_consequential", "plan",
]
