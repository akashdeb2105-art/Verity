"""AST node types for the assertion expression language.

The AST is a closed set of dataclasses. It can be parsed, walked, diffed,
type-checked and explained -- and it can be rendered back to source, which is
what lets a report show a human exactly what was evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

#: Every function the assertion language defines. Declared here, beside the
#: parser, because the set of legal names is part of the *language*: anything
#: that reads or writes a contract needs to know it. The verifier supplies the
#: implementations; it does not get to decide the vocabulary.
FUNCTION_NAMES: frozenset[str] = frozenset({
    "within", "abs", "sum", "count", "exists", "is_null", "matches",
    "before", "after", "within_window", "any", "all", "none", "unique",
})


@dataclass(frozen=True)
class Literal:
    value: Decimal | str | bool | None
    position: int = 0


@dataclass(frozen=True)
class Path:
    """A dotted reference such as ``po.total`` or ``inputs.invoice_number``."""

    segments: tuple[str, ...]
    position: int = 0

    @property
    def root(self) -> str:
        return self.segments[0]

    def render(self) -> str:
        return ".".join(self.segments)


@dataclass(frozen=True)
class ListLit:
    """A literal list, e.g. ``["DRAFT", "PENDING"]``.

    Exists so that ``in`` has something to test membership against. There is no
    other collection syntax: collections otherwise come from facts.
    """

    items: tuple[Expr, ...] = ()
    position: int = 0


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[Expr, ...] = ()
    kwargs: tuple[tuple[str, Expr], ...] = ()
    position: int = 0


@dataclass(frozen=True)
class Compare:
    op: str
    left: Expr
    right: Expr
    position: int = 0


@dataclass(frozen=True)
class BoolOp:
    op: str  # "and" | "or"
    operands: tuple[Expr, ...] = field(default_factory=tuple)
    position: int = 0


@dataclass(frozen=True)
class Not:
    operand: Expr
    position: int = 0


Expr = Literal | ListLit | Path | Call | Compare | BoolOp | Not


def walk(node: Expr):  # type: ignore[no-untyped-def]
    """Yield every node in the tree, parents before children."""
    yield node
    if isinstance(node, ListLit):
        for item in node.items:
            yield from walk(item)
    elif isinstance(node, Call):
        for arg in node.args:
            yield from walk(arg)
        for _, value in node.kwargs:
            yield from walk(value)
    elif isinstance(node, Compare):
        yield from walk(node.left)
        yield from walk(node.right)
    elif isinstance(node, BoolOp):
        for operand in node.operands:
            yield from walk(operand)
    elif isinstance(node, Not):
        yield from walk(node.operand)


def unknown_functions(node: Expr) -> set[str]:
    """Function names the expression uses that the language does not define."""
    return {n.name for n in walk(node) if isinstance(n, Call)} - FUNCTION_NAMES


def referenced_roots(node: Expr) -> set[str]:
    """Every root identifier the expression reads, e.g. ``{"po", "doc"}``.

    This is what strength derivation runs on: an assertion is only as strong as
    the weakest source it touches.
    """
    return {n.root for n in walk(node) if isinstance(n, Path)}


def referenced_paths(node: Expr) -> list[Path]:
    return [n for n in walk(node) if isinstance(n, Path)]


def render(node: Expr) -> str:
    """Render an AST back to canonical source. Used in reports and in diffs."""
    if isinstance(node, Literal):
        if node.value is None:
            return "null"
        if isinstance(node.value, bool):
            return "true" if node.value else "false"
        if isinstance(node.value, str):
            escaped = node.value.replace('"', '\\"')
            return f'"{escaped}"'
        return str(node.value)
    if isinstance(node, Path):
        return node.render()
    if isinstance(node, ListLit):
        return "[" + ", ".join(render(i) for i in node.items) + "]"
    if isinstance(node, Call):
        parts = [render(a) for a in node.args]
        parts += [f"{k} = {render(v)}" for k, v in node.kwargs]
        return f"{node.name}({', '.join(parts)})"
    if isinstance(node, Compare):
        return f"{render(node.left)} {node.op} {render(node.right)}"
    if isinstance(node, BoolOp):
        joined = f" {node.op} ".join(render(o) for o in node.operands)
        return f"({joined})"
    if isinstance(node, Not):
        return f"not {render(node.operand)}"
    raise TypeError(f"unrenderable node: {node!r}")  # pragma: no cover
