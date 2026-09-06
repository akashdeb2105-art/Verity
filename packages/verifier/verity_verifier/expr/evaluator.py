"""Deterministic evaluation of parsed assertion expressions.

Every operation here is a pure function of the facts already resolved. There
are no model calls, no network access, no clock reads that affect a result,
and no dynamic dispatch onto arbitrary Python objects: attribute access is
dictionary lookup over resolved facts, nothing more.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from verity_schema.expr.nodes import (
    FUNCTION_NAMES,
    BoolOp,
    Call,
    Compare,
    Expr,
    ListLit,
    Literal,
    Not,
    Path,
)

MAX_REGEX_LENGTH = 512
MAX_REGEX_SUBJECT = 100_000


class EvaluationError(Exception):
    """An expression could not be evaluated.

    This is never silently treated as a failed assertion. An assertion that
    could not be evaluated is reported as *not evaluated*, which is what drives
    the ``INCONCLUSIVE`` verdict rather than a dishonest ``PASS`` or ``FAIL``.
    """


class UnresolvedReferenceError(EvaluationError):
    """A path referred to a fact or field that is not available."""


@dataclass
class Scope:
    """Names visible to an expression.

    ``item`` is set only inside a collection predicate (``any``/``all``/
    ``none``), where a single-segment path resolves against the current item
    before falling back to the enclosing scope.
    """

    facts: Mapping[str, Any] = field(default_factory=dict)
    inputs: Mapping[str, Any] = field(default_factory=dict)
    item: Any = None
    has_item: bool = False

    def child(self, item: Any) -> Scope:
        return Scope(facts=self.facts, inputs=self.inputs, item=item, has_item=True)


# --------------------------------------------------------------------------
# value coercion
# --------------------------------------------------------------------------

def to_decimal(value: Any) -> Decimal | None:
    """Best-effort numeric coercion, shared with report formatting."""
    return _to_decimal(value)


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "").replace(" ", "")
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
    return None


def _normalize_text(value: Any) -> str:
    """Normalization used by ``~=``: NFKC, casefold, trim, collapse whitespace."""
    text = value if isinstance(value, str) else str(value)
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.casefold().split())


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_sequence(value: Any) -> Sequence[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)):
        return [value]
    if isinstance(value, Sequence):
        return value
    if isinstance(value, Iterable):
        return list(value)
    return [value]


def _field_of(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        if name not in item:
            raise UnresolvedReferenceError(f"no field {name!r} on item")
        return item[name]
    raise UnresolvedReferenceError(f"cannot read {name!r} from a non-record value")


# --------------------------------------------------------------------------
# builtins
# --------------------------------------------------------------------------

def _fn_within(args: list[Any], kwargs: dict[str, Any]) -> bool:
    if len(args) != 2:
        raise EvaluationError("within() takes exactly two positional values")
    tolerance = kwargs.get("tolerance", Decimal("0"))
    left, right, tol = (_to_decimal(args[0]), _to_decimal(args[1]), _to_decimal(tolerance))
    if left is None or right is None:
        raise EvaluationError("within() requires numeric values")
    if tol is None:
        raise EvaluationError("within() tolerance must be numeric")
    return abs(left - right) <= tol


def _fn_abs(args: list[Any], _: dict[str, Any]) -> Decimal:
    value = _to_decimal(args[0]) if args else None
    if value is None:
        raise EvaluationError("abs() requires a numeric value")
    return abs(value)


def _fn_sum(args: list[Any], kwargs: dict[str, Any]) -> Decimal:
    if not args:
        raise EvaluationError("sum() requires a collection")
    items = _as_sequence(args[0])
    field_name = kwargs.get("field")
    total = Decimal("0")
    for item in items:
        raw = _field_of(item, field_name) if field_name else item
        number = _to_decimal(raw)
        if number is None:
            raise EvaluationError("sum() encountered a non-numeric value")
        total += number
    return total


def _fn_count(args: list[Any], _: dict[str, Any]) -> Decimal:
    if not args:
        raise EvaluationError("count() requires a collection")
    return Decimal(len(_as_sequence(args[0])))


def _fn_exists(args: list[Any], _: dict[str, Any]) -> bool:
    return bool(args) and args[0] is not None


def _fn_is_null(args: list[Any], _: dict[str, Any]) -> bool:
    return not args or args[0] is None


def _fn_matches(args: list[Any], _: dict[str, Any]) -> bool:
    if len(args) != 2:
        raise EvaluationError("matches() takes a value and a pattern")
    return _regex_match(args[0], args[1])


def _fn_before(args: list[Any], _: dict[str, Any]) -> bool:
    left, right = _two_datetimes("before", args)
    return left < right


def _fn_after(args: list[Any], _: dict[str, Any]) -> bool:
    left, right = _two_datetimes("after", args)
    return left > right


def _fn_within_window(args: list[Any], _: dict[str, Any]) -> bool:
    if len(args) != 3:
        raise EvaluationError("within_window() takes a value, a start and an end")
    moment, start, end = (_to_datetime(a) for a in args)
    if moment is None or start is None or end is None:
        raise EvaluationError("within_window() requires datetime values")
    return start <= moment <= end


def _two_datetimes(name: str, args: list[Any]) -> tuple[datetime, datetime]:
    if len(args) != 2:
        raise EvaluationError(f"{name}() takes exactly two datetime values")
    left, right = _to_datetime(args[0]), _to_datetime(args[1])
    if left is None or right is None:
        raise EvaluationError(f"{name}() requires datetime values")
    return left, right


def _regex_match(subject: Any, pattern: Any) -> bool:
    if not isinstance(pattern, str):
        raise EvaluationError("a regular expression pattern must be a string")
    if len(pattern) > MAX_REGEX_LENGTH:
        raise EvaluationError(f"pattern exceeds {MAX_REGEX_LENGTH} characters")
    text = subject if isinstance(subject, str) else str(subject)
    if len(text) > MAX_REGEX_SUBJECT:
        raise EvaluationError("subject too large to match")
    try:
        return re.search(pattern, text) is not None
    except re.error as exc:
        raise EvaluationError(f"invalid regular expression: {exc}") from exc


EagerFn = Callable[[list[Any], dict[str, Any]], Any]

#: Functions whose arguments are evaluated before the call.
BUILTINS: dict[str, EagerFn] = {
    "within": _fn_within,
    "abs": _fn_abs,
    "sum": _fn_sum,
    "count": _fn_count,
    "exists": _fn_exists,
    "is_null": _fn_is_null,
    "matches": _fn_matches,
    "before": _fn_before,
    "after": _fn_after,
    "within_window": _fn_within_window,
}

#: Functions that receive unevaluated predicate ASTs, because their predicates
#: are evaluated once per item with that item in scope.
LAZY_BUILTINS = frozenset({"any", "all", "none", "unique"})

ALL_FUNCTION_NAMES = frozenset(BUILTINS) | LAZY_BUILTINS

# The language declares its vocabulary in verity_schema.expr; this module
# supplies the implementations. If the two ever disagree, a contract could pass
# type checking and then fail at evaluation, so they are checked at import.
assert ALL_FUNCTION_NAMES == FUNCTION_NAMES, (
    "the evaluator and the language definition disagree: "
    f"{ALL_FUNCTION_NAMES ^ FUNCTION_NAMES}"
)


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------

def evaluate(node: Expr, scope: Scope) -> Any:
    """Evaluate a parsed expression against resolved facts."""
    if isinstance(node, Literal):
        return node.value

    if isinstance(node, ListLit):
        return [evaluate(item, scope) for item in node.items]

    if isinstance(node, Path):
        return _resolve_path(node, scope)

    if isinstance(node, Not):
        return not _truthy(evaluate(node.operand, scope))

    if isinstance(node, BoolOp):
        if node.op == "and":
            return all(_truthy(evaluate(o, scope)) for o in node.operands)
        return any(_truthy(evaluate(o, scope)) for o in node.operands)

    if isinstance(node, Compare):
        return _compare(node.op, evaluate(node.left, scope), evaluate(node.right, scope))

    if isinstance(node, Call):
        return _call(node, scope)

    raise EvaluationError(f"cannot evaluate node of type {type(node).__name__}")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, Decimal):
        return value != 0
    if isinstance(value, (str, list, tuple, dict)):
        return len(value) > 0
    return bool(value)


def _resolve_path(node: Path, scope: Scope) -> Any:
    segments = node.segments
    root = segments[0]

    # Inside a collection predicate a bare name is a field of the current item.
    if scope.has_item and len(segments) >= 1:
        try:
            value: Any = _field_of(scope.item, root)
        except UnresolvedReferenceError:
            value = _resolve_root(root, scope, node)
        else:
            return _walk_segments(value, segments[1:], node)
    else:
        value = _resolve_root(root, scope, node)

    return _walk_segments(value, segments[1:], node)


def _resolve_root(root: str, scope: Scope, node: Path) -> Any:
    if root == "inputs":
        return scope.inputs
    if root in scope.facts:
        return scope.facts[root]
    raise UnresolvedReferenceError(
        f"'{node.render()}' refers to fact '{root}', which is not resolved"
    )


def _walk_segments(value: Any, segments: tuple[str, ...], node: Path) -> Any:
    for segment in segments:
        if value is None:
            raise UnresolvedReferenceError(f"'{node.render()}' traverses a null value")
        if isinstance(value, Mapping):
            if segment not in value:
                raise UnresolvedReferenceError(f"'{node.render()}' has no field '{segment}'")
            value = value[segment]
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            # Project a field across a list: ``po.line_items.amount``.
            value = [_field_of(item, segment) for item in value]
            continue
        raise UnresolvedReferenceError(f"'{node.render()}' cannot traverse '{segment}'")
    return value


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "~=":
        return _normalize_text(left) == _normalize_text(right)

    if op == "matches":
        return _regex_match(left, right)

    if op == "in":
        container = _as_sequence(right)
        if any(isinstance(x, Decimal) for x in container) or isinstance(left, Decimal):
            left_num = _to_decimal(left)
            return any(_to_decimal(x) == left_num for x in container) if left_num is not None \
                else left in container
        return left in container

    if op in ("==", "!="):
        result = _equal(left, right)
        return result if op == "==" else not result

    left_num, right_num = _to_decimal(left), _to_decimal(right)
    if left_num is None or right_num is None:
        left_dt, right_dt = _to_datetime(left), _to_datetime(right)
        if left_dt is None or right_dt is None:
            raise EvaluationError(f"cannot order {left!r} and {right!r} with '{op}'")
        left_num, right_num = Decimal(left_dt.timestamp()), Decimal(right_dt.timestamp())
    return {
        "<": left_num < right_num,
        "<=": left_num <= right_num,
        ">": left_num > right_num,
        ">=": left_num >= right_num,
    }[op]


def _equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    left_num, right_num = _to_decimal(left), _to_decimal(right)
    if left_num is not None and right_num is not None:
        return left_num == right_num
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    return bool(left == right)


def _call(node: Call, scope: Scope) -> Any:
    name = node.name

    if name in LAZY_BUILTINS:
        return _call_lazy(node, scope)

    fn = BUILTINS.get(name)
    if fn is None:
        raise EvaluationError(f"unknown function '{name}()'")
    args = [evaluate(a, scope) for a in node.args]
    kwargs = {k: evaluate(v, scope) for k, v in node.kwargs}
    return fn(args, kwargs)


def _call_lazy(node: Call, scope: Scope) -> Any:
    name = node.name
    if not node.args:
        raise EvaluationError(f"{name}() requires a collection")

    items = _as_sequence(evaluate(node.args[0], scope))
    predicates = node.args[1:]

    if name == "unique":
        return _unique(items, predicates, scope)

    if not predicates:
        results = [_truthy(item) for item in items]
    else:
        results = []
        for item in items:
            inner = scope.child(item)
            results.append(all(_truthy(evaluate(p, inner)) for p in predicates))

    if name == "any":
        return any(results)
    if name == "all":
        return all(results)
    return not any(results)  # none


def _unique(items: Sequence[Any], predicates: tuple[Expr, ...], scope: Scope) -> bool:
    """``unique(collection, field_a, field_b)`` -- no two items share the key."""
    seen: set[tuple[Any, ...]] = set()
    for item in items:
        inner = scope.child(item)
        if predicates:
            key = tuple(_hashable(evaluate(p, inner)) for p in predicates)
        else:
            key = (_hashable(item),)
        if key in seen:
            return False
        seen.add(key)
    return True


def _hashable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, str):
        return _normalize_text(value)
    return value
