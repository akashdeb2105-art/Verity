"""Evaluating assertion expressions.

The language itself -- lexer, parser, AST -- lives in :mod:`verity_schema.expr`,
because parsing a contract is a format concern and both the compiler that
writes contracts and the verifier that evaluates them need it. Only evaluation
lives here, and only the verifier does that.

Re-exported below so callers have one import for the whole language.
"""

from verity_schema.expr import (
    COMPARE_OPS,
    BoolOp,
    Call,
    Compare,
    Expr,
    ExpressionSyntaxError,
    ListLit,
    Literal,
    Not,
    Path,
    parse,
    referenced_paths,
    referenced_roots,
    render,
    tokenize,
    walk,
)

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
