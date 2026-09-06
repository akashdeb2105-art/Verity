"""The assertion expression language: small, closed, deterministic.

Hand-tokenized and hand-parsed. There is no path from a contract file to the
Python interpreter -- no ``eval``, no ``exec``, no ``compile``, no dynamic
attribute access. See :mod:`verity_verifier.expr.lexer` for why.
"""

from .evaluator import (
    ALL_FUNCTION_NAMES as ALL_FUNCTION_NAMES_SAFE,
)
from .evaluator import (
    BUILTINS,
    EvaluationError,
    Scope,
    UnresolvedReferenceError,
    evaluate,
    to_decimal,
)
from .lexer import ExpressionSyntaxError, tokenize
from .nodes import (
    BoolOp,
    Call,
    Compare,
    Expr,
    ListLit,
    Literal,
    Not,
    Path,
    referenced_paths,
    referenced_roots,
    render,
    walk,
)
from .parser import COMPARE_OPS, parse

__all__ = [
    "ALL_FUNCTION_NAMES_SAFE",
    "BUILTINS",
    "COMPARE_OPS",
    "BoolOp",
    "Call",
    "Compare",
    "EvaluationError",
    "Expr",
    "ExpressionSyntaxError",
    "ListLit",
    "Literal",
    "Not",
    "Path",
    "Scope",
    "UnresolvedReferenceError",
    "evaluate",
    "parse",
    "referenced_paths",
    "referenced_roots",
    "render",
    "to_decimal",
    "tokenize",
    "walk",
]
